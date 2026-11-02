# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent Phase 1 data integrity and fault injection regressions."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
from revisionworx import atomicio, projects
from revisionworx import workspace as workspace_mod
from revisionworx.workspace import RecordPut, Workspace, WorkspaceCorruptError, WorkspaceLockedError

def _unversioned(record):
    return {k: v for k, v in record.items() if k != "_rev"}
from .fixtures import STRUCTURES, fingerprint, populate

def _journal_first_revocation_supported():
    """ADR-015 rev.2 publishes revocation before moving writer.lock."""
    return hasattr(Workspace, "_revoked_tokens")


def _stable_index_rebuild_supported():
    """ADR-016 bounded snapshot retry, absent in the original Phase 1 source."""
    from revisionworx.index import LocalIndex
    return "stable snapshot" in (LocalIndex.rebuild.__doc__ or "")


def _windows_lock_release_retry_supported():
    return hasattr(Workspace, "_unlink_own_lock")


@pytest.mark.parametrize("layout", sorted(STRUCTURES))
@pytest.mark.parametrize("mode", ["indexed", "managed"])
def test_adoption_never_changes_original_tree(tmp_path, layout, mode):
    root = tmp_path / "Engineering"
    fixture = populate(root, layout)
    with Workspace.create(tmp_path / "store", "QA") as ws:
        projects.add_root(ws, "Engineering", root)
        for folder in fixture["projects"]:
            preview = projects.preview_adoption(ws, folder)
            assert not preview["conflicts"]
            assert fingerprint(root) == fixture["before"]
            record = projects.adopt_project(ws, folder, name=folder.name, mode=mode)
            baseline = ws.read_blob(record["baseline"]["blob"])
            assert "No historical revisions are asserted." in baseline["note"]
            assert len(baseline["files"]) == 7
            assert all(len(item["sha256"]) == 64 for item in baseline["files"])
            assert record["rel_path"] == folder.relative_to(root).as_posix()
            assert fingerprint(root) == fixture["before"]
    assert fingerprint(root) == fixture["before"]

def test_disk_full_before_publication_is_not_commit(tmp_path, monkeypatch):
    store = tmp_path / "store"
    rid = "prj_" + "f" * 32
    with Workspace.create(store, "QA") as ws:
        before = ws.head_seq()
        real = atomicio.publish_exclusive
        def fail(path, data):
            if Path(path).parent.name == "journal":
                raise OSError(28, "synthetic ENOSPC")
            return real(path, data)
        with monkeypatch.context() as m:
            m.setattr(atomicio, "publish_exclusive", fail)
            with pytest.raises(OSError):
                ws.commit("qa.enospc", [RecordPut("project", rid, {"name": "never"})])
        assert ws.head_seq() == before
        assert ws.get("project", rid) is None
    with Workspace.open(store) as reader:
        assert reader.get("project", rid) is None

def test_journal_publish_then_apply_failure_is_recovered(tmp_path, monkeypatch):
    store = tmp_path / "store"
    rid = "prj_" + "a" * 32
    writer = Workspace.create(store, "QA")
    before = writer.head_seq()
    try:
        txid = "a" * 32
        with monkeypatch.context() as m:
            def crash(_entry):
                raise OSError("simulated crash")
            m.setattr(writer, "_apply", crash)
            if hasattr(workspace_mod, "CommittedNotApplied"):
                with pytest.raises(workspace_mod.CommittedNotApplied) as outcome:
                    writer.commit("qa.crash", [RecordPut("project", rid, {"name": "durable"})], txid=txid)
                assert outcome.value.txid == txid
            else:
                # Original Phase 1 API (the QA branch's current checkout).
                with pytest.raises(OSError):
                    writer.commit("qa.crash", [RecordPut("project", rid, {"name": "durable"})])
        assert writer.head_seq() == before + 1
        if hasattr(writer, "transaction_outcome"):
            assert writer.transaction_outcome(txid) == {"state": "committed", "seq": before + 1}
        with Workspace.open(store) as reader:
            assert _unversioned(reader.get("project", rid)) == {"name": "durable"}
    finally:
        writer.close()
    with Workspace.open(store, writable=True) as recovered:
        assert _unversioned(recovered.get("project", rid)) == {"name": "durable"}
        assert recovered.applied_seq() == recovered.head_seq()

def test_journal_gap_fails_closed(tmp_path):
    store = tmp_path / "store"
    with Workspace.create(store, "QA") as writer:
        writer.commit("qa.one", [RecordPut("settings", "workspace", {"a": 1})])
        writer.commit("qa.two", [RecordPut("settings", "workspace", {"a": 2})])
    journal = store / ".revisionworx" / "journal"
    missing = journal / "00000000000000000002.json"
    missing.unlink()
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(store, writable=True)
    assert not missing.exists()

def test_exclusive_writer_rejected(tmp_path):
    store = tmp_path / "store"
    with Workspace.create(store, "QA"):
        original = Workspace.read_writer_lock(store)
        with pytest.raises(WorkspaceLockedError):
            Workspace.open(store, writable=True)
        assert Workspace.read_writer_lock(store) == original

def test_unmanaged_archive_not_enumerated_during_open(tmp_path, monkeypatch):
    archive = tmp_path / "Engineering"
    populate(archive, "customer")
    store = tmp_path / "store"
    with Workspace.create(store, "QA") as ws:
        projects.add_root(ws, "Legacy", archive)
    real_scandir = os.scandir
    real_listdir = os.listdir
    def reject(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            resolved = Path(path).resolve()
            if resolved == archive or archive in resolved.parents:
                raise AssertionError("startup enumerated unmanaged folder")
    def checked_scandir(path):
        reject(path)
        return real_scandir(path)
    def checked_listdir(path):
        reject(path)
        return real_listdir(path)
    with monkeypatch.context() as m:
        m.setattr(os, "scandir", checked_scandir)
        m.setattr(os, "listdir", checked_listdir)
        with Workspace.open(store) as ws:
            assert ws.list("project") == {}

@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="Known ADR-008 R2 TOCTOU: lock break between verification and journal publish")
def test_paused_writer_break_after_lock_check_must_not_publish(tmp_path, monkeypatch):
    """Safety oracle: old writer must not publish after its lock was revoked.

    Controlled local pause at the *last* lock check demonstrates the outstanding
    non-atomic verify/publish window. XPASS means re-evaluate/remove the xfail.
    This is NOT an SMB/NFS test or a concurrency certification.
    """
    store = tmp_path / "store"
    if not hasattr(Workspace, "_check_lock_still_held"):
        pytest.skip("ADR-008 lock-token recheck absent on original Phase 1 QA branch")
    writer = Workspace.create(store, "QA")
    token = Workspace.read_writer_lock(store)["token"]
    # Force revocation AFTER the modern _revocations preflight but BEFORE
    # the writer chooses a new journal sequence. This deliberately proves the
    # remaining PHYSICAL late-entry behavior, even with logical fencing.
    original_current_rev = writer._current_rev
    initial_rev = writer._current_rev("settings", "workspace")
    after_break = []
    def break_after_validation(kind, rid):
        rev = original_current_rev(kind, rid)
        if not after_break:
            Workspace.break_writer_lock(store, token, reason="QA late physical publish race")
            after_break.append(Workspace.open(store).head_seq())
        return rev
    try:
        lock_lost = False
        with monkeypatch.context() as m:
            m.setattr(writer, "_current_rev", break_after_validation)
            try:
                writer.commit("qa.stale", [
                    RecordPut("settings", "workspace", {"stale_writer": True}, expect_rev=initial_rev)
                ])
            except workspace_mod.LockLostError:
                lock_lost = True
        with Workspace.open(store) as reader:
            if not after_break:
                raise RuntimeError("QA failpoint did not inject the audited administrative break")
            assert lock_lost, "revoked writer incorrectly returned from commit"
            assert reader.head_seq() == after_break[0], "revoked writer published a physical journal entry"
            assert not reader.get("settings", "workspace").get("stale_writer", False)
    finally:
        writer.close()


def test_second_process_cannot_take_active_writer_lock(tmp_path):
    """Real separate interpreter; in-process locks alone cannot satisfy this."""
    import subprocess
    import sys

    store = tmp_path / "store"
    with Workspace.create(store, "QA") as owner:
        expected_lock = Workspace.read_writer_lock(store)
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from revisionworx.workspace import Workspace, WorkspaceLockedError\n"
            "try:\n"
            "    with Workspace.open(Path(sys.argv[1]), writable=True):\n"
            "        sys.exit(40)  # second process illegally acquired write authority\n"
            "except WorkspaceLockedError:\n"
            "    sys.exit(0)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(store)],
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, (
            f"second interpreter result={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        assert Workspace.read_writer_lock(store) == expected_lock
        assert owner.head_seq() >= 1
    with Workspace.open(store, writable=True) as successor:
        successor.commit("qa.process.reacquire", [
            RecordPut("settings", "workspace", {"reacquired": True})
        ])
    with Workspace.open(store) as reader:
        assert reader.get("settings", "workspace")["reacquired"] is True


@pytest.mark.xfail(
    not _stable_index_rebuild_supported(), strict=True, raises=AssertionError,
    reason="Known LocalIndex.rebuild() snapshot/cursor mismatch: newer records can be indexed under older cursor",
)
def test_index_rebuild_does_not_include_records_newer_than_its_watermark(tmp_path, monkeypatch):
    """Index projection must not claim a cursor older than any records it contains.

    Inject a durable authoritative write immediately after rebuild captures
    head_seq. Production currently reads a later materialised record snapshot
    without also advancing the index cursor. This is a *local deterministic*
    repro, not proof of shared-filesystem behavior. Keep xfail until corrected.
    """
    from revisionworx.index import LocalIndex
    from revisionworx.workspace import new_id

    store = tmp_path / "store"
    with Workspace.create(store, "QA index watermark") as ws:
        with LocalIndex(ws, cache_dir=tmp_path / "cache") as index:
            original_head = ws.head_seq
            injected_id = new_id("root")
            injected_seq = []
            old_head = original_head()

            def head_then_commit():
                seq = original_head()
                if not injected_seq:
                    # Avoid recursively invoking the failpoint while committing.
                    monkeypatch.setattr(ws, "head_seq", original_head)
                    committed = ws.commit("qa.index.concurrent", [
                        RecordPut("root", injected_id,
                                  {"id": injected_id, "name": "newer than cursor"})
                    ])
                    injected_seq.append(committed)
                return seq

            with monkeypatch.context() as m:
                m.setattr(ws, "head_seq", head_then_commit)
                index.rebuild()

            if injected_seq != [old_head + 1]:
                raise RuntimeError("QA failpoint did not produce the expected intervening journal entry")
            included = index.db.execute(
                "SELECT id FROM roots WHERE id=?", (injected_id,)
            ).fetchone() is not None
            cursor = index.cursor_seq()
            # A cursor may represent an older consistent snapshot, or the new
            # commit may be indexed if the cursor advances with it. Never mix.
            assert not included or cursor >= injected_seq[0], (
                f"cache includes seq {injected_seq[0]} record {injected_id} "
                f"while publishing older cursor {cursor}"
            )


def test_revoked_writer_publication_is_logically_fenced_on_replay(tmp_path, monkeypatch):
    """ADR-015: a late physical journal file must NEVER become authoritative state.

    The strict xfail above still demonstrates the non-atomic *physical publish*
    after an audited override. This independent, passing regression validates
    the separate standalone-mode mitigation: logical rejection, fenced txid,
    reader filtering, cache filtering and recovery. It does not certify SMB.
    """
    if not hasattr(Workspace, "is_fenced"):
        pytest.skip("ADR-015 logical journal-order fencing is absent on this Phase 1 baseline")
    from revisionworx.index import LocalIndex
    from revisionworx.workspace import new_id

    store = tmp_path / "store"
    writer = Workspace.create(store, "QA fenced replay")
    old_token = Workspace.read_writer_lock(store)["token"]
    original_current_rev = writer._current_rev
    break_seq = []
    late_id = new_id("root")
    txid = "e" * 32

    def break_after_validation(kind, rid):
        rev = original_current_rev(kind, rid)
        if not break_seq:
            Workspace.break_writer_lock(store, old_token, reason="QA fencing check")
            break_seq.append(Workspace.open(store).head_seq())
        return rev

    try:
        with monkeypatch.context() as m:
            m.setattr(writer, "_current_rev", break_after_validation)
            with pytest.raises(workspace_mod.LockLostError, match="fenced"):
                writer.commit("qa.fenced.late.write", [
                    RecordPut("root", late_id, {"id": late_id, "name": "not authoritative"}, expect_rev=0)
                ], txid=txid)
        assert break_seq, "the audited override must have happened"

        with Workspace.open(store) as reader:
            assert reader.head_seq() == break_seq[0] + 1
            assert reader.transaction_outcome(txid)["state"] == "fenced"
            assert reader.get("root", late_id) is None
            assert reader.is_fenced(reader.read_entry(reader.head_seq()))
            assert any(p["problem"] == "fenced-entry" for p in reader.verify()["problems"])
            with LocalIndex(reader, cache_dir=tmp_path / "cache") as index:
                index.rebuild()
                assert index.db.execute(
                    "SELECT id FROM roots WHERE id=?", (late_id,)
                ).fetchone() is None
    finally:
        writer.close()

    with Workspace.open(store, writable=True) as recovered:
        assert recovered.get("root", late_id) is None
        assert recovered.transaction_outcome(txid)["state"] == "fenced"


@pytest.mark.xfail(
    not _journal_first_revocation_supported(), strict=True, raises=AssertionError,
    reason="Issue #8: standalone override moves writer.lock before audit; a stale writer can commit in the gap",
)
def test_writer_revoked_before_admin_audit_cannot_take_effect(tmp_path):
    """Secondary issue #8 race: revocation rename precedes the audit entry.

    Unlike the post-audit positive control, this intentionally pauses the
    administrator AFTER moving writer.lock but BEFORE writing the lock-break
    audit. The old writer passed preflight and then resumes. An acceptance
    implementation must not apply its late write, regardless of journal order.
    This is deterministic LOCAL concurrency, not an SMB certification.
    """
    if not hasattr(Workspace, "is_fenced"):
        pytest.skip("ADR-015 journal-order fencing not present on original Phase 1")
    import threading
    from unittest.mock import patch
    from revisionworx.workspace import new_id

    store = tmp_path / "store"
    writer = Workspace.create(store, "QA administrative boundary")
    old_token = Workspace.read_writer_lock(store)["token"]
    rid = new_id("root")
    original_check = writer._check_lock_still_held
    original_open = Workspace.open
    original_replace = workspace_mod.os.replace
    journal_first = _journal_first_revocation_supported()
    preflight = threading.Event()
    renamed = threading.Event()
    release_admin = threading.Event()
    writer_done = threading.Event()
    errors = []
    outcomes = []
    admin_thread = None
    stale_thread = None

    def checked_then_pause():
        original_check()
        preflight.set()
        if not renamed.wait(15):
            raise TimeoutError("administrator never moved the writer lock")

    def intercept_replace(src, dst):
        # Rev.2: wait AFTER the revocation is committed but BEFORE moving
        # writer.lock. A resumed old writer must now be refused/fenced.
        if threading.current_thread() is admin_thread and Path(src).name == "writer.lock":
            renamed.set()
            if not release_admin.wait(15):
                raise TimeoutError("QA admin release barrier did not open")
        return original_replace(src, dst)

    def intercept_open(*args, **kwargs):
        if threading.current_thread() is admin_thread and kwargs.get("writable"):
            # break_writer_lock() opens its new writer only AFTER renaming the
            # original lock; hold that audit transaction until the old writer finishes.
            renamed.set()
            if not release_admin.wait(15):
                raise TimeoutError("administrator test pause was not released")
        return original_open(*args, **kwargs)

    def late_writer():
        try:
            seq = writer.commit("qa.stale.before.audit", [
                RecordPut("root", rid, {"id": rid, "name": "revoked session"})
            ])
            outcomes.append(("published", seq))
        except workspace_mod.LockLostError:
            outcomes.append(("rejected", None))
        except Exception as exc:
            errors.append(("late-writer", repr(exc)))
        finally:
            writer_done.set()

    def override():
        try:
            Workspace.break_writer_lock(store, old_token, "QA race before audit")
        except Exception as exc:
            errors.append(("administrator", repr(exc)))
        finally:
            release_admin.set()

    try:
        writer._check_lock_still_held = checked_then_pause
        target = (patch.object(workspace_mod.os, "replace", side_effect=intercept_replace)
                  if journal_first else patch.object(Workspace, "open", side_effect=intercept_open))
        with target:
            stale_thread = threading.Thread(target=late_writer, name="qa-paused-writer")
            stale_thread.start()
            if not preflight.wait(15):
                raise RuntimeError("QA writer preflight barrier was not reached")
            admin_thread = threading.Thread(target=override, name="qa-paused-admin")
            admin_thread.start()
            if not renamed.wait(15):
                raise RuntimeError("QA administrator lock-rename barrier was not reached")
            if not writer_done.wait(15):
                raise RuntimeError("QA writer did not finish while administrator was paused")
            release_admin.set()
            stale_thread.join(15)
            admin_thread.join(15)
        if any(t is not None and t.is_alive() for t in (stale_thread, admin_thread)):
            raise RuntimeError("QA injected worker thread did not terminate")
        if errors or len(outcomes) != 1:
            raise RuntimeError(f"QA race infrastructure failed: {errors!r}, outcomes={outcomes!r}")
        with original_open(store) as reader:
            if journal_first:
                assert outcomes == [("rejected", None)], outcomes
            audits = [reader.read_entry(n) for n in reader.journal_seqs()
                      if reader.read_entry(n)["op"] == "workspace.lock.break"]
            if len(audits) != 1:
                raise RuntimeError("QA expected exactly one audited override")
            # Same acceptance property under both protocols: the old pre-audit
            # implementation fails, the journal-first linearization passes.
            assert reader.get("root", rid) is None, (
                "revoked writer's mutation is visible after lock rename, "
                "before the later audit entry"
            )
    finally:
        release_admin.set()
        renamed.set()
        if stale_thread is not None:
            stale_thread.join(5)
        if admin_thread is not None:
            admin_thread.join(5)
        writer._lock_token = None
        writer.close()


@pytest.mark.xfail(
    not _stable_index_rebuild_supported(), strict=True, raises=AssertionError,
    reason="Issue #9: LocalIndex.rebuild can drop a record deleted after the sampled watermark",
)
def test_index_rebuild_does_not_omit_pre_watermark_records_due_to_late_delete(tmp_path, monkeypatch):
    """Delete-side acceptance oracle for the same non-atomic cache rebuild.

    If the index claims journal sequence N, a record that existed at N must
    remain projected until a delete at N+1 is included in the cursor. An index
    rebuilt from newer materialized records cannot truthfully claim N.
    """
    if not hasattr(workspace_mod, "RecordDelete"):
        pytest.skip("Original Phase 1 did not yet expose journaled RecordDelete")
    from revisionworx.index import LocalIndex
    from revisionworx.workspace import new_id

    store = tmp_path / "store"
    rid = new_id("root")
    with Workspace.create(store, "QA index deleted record") as ws:
        ws.commit("qa.root.before.rebuild", [
            RecordPut("root", rid, {"id": rid, "name": "present at watermark"})
        ])
        with LocalIndex(ws, cache_dir=tmp_path / "cache") as index:
            original_head = ws.head_seq
            baseline_seq = original_head()
            removed_seq = []

            def head_then_delete():
                seq = original_head()
                if not removed_seq:
                    monkeypatch.setattr(ws, "head_seq", original_head)
                    current_rev = ws.get("root", rid)["_rev"]
                    deleted = ws.commit("qa.root.after.snapshot.delete", [
                        workspace_mod.RecordDelete("root", rid, expect_rev=current_rev)
                    ])
                    removed_seq.append(deleted)
                return seq

            with monkeypatch.context() as m:
                m.setattr(ws, "head_seq", head_then_delete)
                index.rebuild()
            if removed_seq != [baseline_seq + 1]:
                raise RuntimeError("QA deleted-record injection did not complete as expected")
            present = index.db.execute(
                "SELECT id FROM roots WHERE id=?", (rid,)
            ).fetchone() is not None
            assert present or index.cursor_seq() >= removed_seq[0], (
                f"cache omitted root {rid} at cursor {index.cursor_seq()} "
                f"although delete was published at seq {removed_seq[0]}"
            )


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS sharing-violation semantics only")
@pytest.mark.xfail(
    not _windows_lock_release_retry_supported(), strict=True, raises=PermissionError,
    reason="Issue #10: Workspace.close does not retry transient Windows writer.lock sharing violations",
)
def test_windows_writer_lock_close_survives_transient_sharing_violation(tmp_path, monkeypatch):
    """Failpoint acceptance for a single transient WinError 32 at lock release.

    Real six-process Windows checkout failures are in issue #10. This small
    deterministic failpoint is only a regression for close retry behavior,
    NOT an SMB concurrency test and NOT a substitute for the real race.
    """
    store = tmp_path / "store"
    writer = Workspace.create(store, "QA Win32 lock cleanup")
    lock = store / ".revisionworx" / "locks" / "writer.lock"
    real_unlink = Path.unlink
    attempts = []

    def transient_share_violation(path, *args, **kwargs):
        if path == lock:
            attempts.append(True)
            if len(attempts) == 1:
                raise PermissionError(13, "QA injected Windows sharing violation", str(path))
        return real_unlink(path, *args, **kwargs)

    try:
        with monkeypatch.context() as m:
            m.setattr(Path, "unlink", transient_share_violation)
            writer.close()
        assert len(attempts) >= 2, "release should retry after the transient sharing violation"
        assert not lock.exists(), "released writer lock must not remain orphaned"
        with Workspace.open(store, writable=True) as successor:
            assert successor.writable
    finally:
        # Cleanup applies only to the private disposable test workspace.
        # A failed close leaves _closed=True in the current implementation.
        if lock.exists():
            real_unlink(lock)



def test_journal_first_revocation_survives_admin_crash_before_lock_move(tmp_path, monkeypatch):
    """ADR-015 rev.2 audit must survive a crash before the evidence rename.

    A revoked old lock may remain physically present. The next writer must
    recover safely without accepting a write from the original credential.
    This is a local deterministic failpoint, not power-loss or SMB proof.
    """
    if not _journal_first_revocation_supported():
        pytest.skip("journal-first ADR-015 administrative revocation not present")
    store = tmp_path / "store"
    old_writer = Workspace.create(store, "QA crash-after-revoke")
    token = Workspace.read_writer_lock(store)["token"]
    actual_replace = workspace_mod.os.replace
    injected = []

    def fail_lock_move(src, dst):
        if Path(src).name == "writer.lock":
            injected.append(True)
            raise KeyboardInterrupt("QA simulated administrator death after audit publication")
        return actual_replace(src, dst)

    try:
        with monkeypatch.context() as m:
            m.setattr(workspace_mod.os, "replace", fail_lock_move)
            with pytest.raises(KeyboardInterrupt, match="simulated administrator death"):
                Workspace.break_writer_lock(store, token, "QA simulated post-audit crash")
        assert len(injected) == 1
        assert Workspace.read_writer_lock(store)["token"] == token

        with Workspace.open(store) as reader:
            entries = [reader.read_entry(n) for n in reader.journal_seqs()]
            audits = [e for e in entries if e["op"] == "workspace.lock.break"]
            assert len(audits) == 1
            assert audits[0].get("revokes") == token

        # _acquire_writer_lock must treat the physically remaining, already
        # revoked token as evidence, acquire a fresh token and recover the audit.
        with Workspace.open(store, writable=True) as successor:
            fresh = Workspace.read_writer_lock(store)["token"]
            assert fresh != token
            with pytest.raises(workspace_mod.LockLostError):
                old_writer.commit("qa.revoked.after.crash", [
                    RecordPut("settings", "workspace", {"must_not_exist": True})
                ])
            assert successor.get("settings", "workspace").get("must_not_exist") is None
            assert successor.verify()["problems"] == []
        with Workspace.open(store) as reader:
            assert reader.get("settings", "workspace").get("must_not_exist") is None
    finally:
        old_writer.close()


def test_index_rebuild_fails_closed_if_snapshot_never_stabilizes(tmp_path, monkeypatch):
    """Issue #9: bounded retry exhaustion cannot publish a false cache cursor.

    Synthetic alternating journal heads force all retry attempts to disagree.
    This validates failure-closed behavior and an unchanged previous index;
    separate insert/delete tests exercise actual authoritative commits.
    """
    if not _stable_index_rebuild_supported():
        pytest.skip("bounded consistent-snapshot rebuild absent on earlier code")
    import time
    from revisionworx.index import LocalIndex

    store = tmp_path / "store"
    with Workspace.create(store, "QA index retry exhaustion") as ws:
        with LocalIndex(ws, cache_dir=tmp_path / "cache") as index:
            index.rebuild()
            before_cursor = index.cursor_seq()
            before_rows = index.db.execute("SELECT id, name FROM roots ORDER BY id").fetchall()
            old_head = ws.head_seq()
            # Prevent other operations from observing artificially changing
            # heads. Only the rebuild's two head samples oscillate.
            snapshots = {kind: ws.list(kind) for kind in
                         ("root", "project", "document", "revision", "checkout")}
            samples = []

            def unstable_head():
                samples.append(True)
                return old_head + (len(samples) % 2)

            with monkeypatch.context() as m:
                m.setattr(ws, "head_seq", unstable_head)
                m.setattr(ws, "list", lambda kind: snapshots[kind])
                m.setattr(time, "sleep", lambda _: None)
                with pytest.raises(workspace_mod.WorkspaceError,
                                   match="changed continuously"):
                    index.rebuild()
            assert len(samples) >= 100, "snapshot retries must be bounded but substantive"
            assert index.cursor_seq() == before_cursor
            assert index.db.execute("SELECT id, name FROM roots ORDER BY id").fetchall() == before_rows
            assert ws.head_seq() == old_head


@pytest.mark.skipif(os.name != "nt", reason="Windows writer.lock sharing semantics only")
def test_windows_persistent_lock_unlink_denial_is_reported_and_retained(tmp_path, monkeypatch):
    """Issue #10: retry exhaustion leaves the rightful lock held, never hidden."""
    if not _windows_lock_release_retry_supported():
        pytest.skip("bounded Windows lock release not yet implemented")
    store = tmp_path / "store"
    writer = Workspace.create(store, "QA persistent lock close failure")
    lock = store / ".revisionworx" / "locks" / "writer.lock"
    token = Workspace.read_writer_lock(store)["token"]
    real_unlink = Path.unlink
    attempts = []

    def persistently_denied(path, *args, **kwargs):
        if path == lock:
            attempts.append(True)
            raise PermissionError(13, "QA injected persistent WinError 32", str(path))
        return real_unlink(path, *args, **kwargs)

    try:
        with monkeypatch.context() as m:
            m.setattr(Workspace, "LOCK_RELEASE_ATTEMPTS", 3)
            m.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
            m.setattr(Path, "unlink", persistently_denied)
            with pytest.raises(workspace_mod.WorkspaceError, match="could not release"):
                writer.close()
        assert len(attempts) == 3
        assert Workspace.read_writer_lock(store)["token"] == token
        with pytest.raises(WorkspaceLockedError):
            Workspace.open(store, writable=True)
    finally:
        # Original ownership is deliberately retained by the application.
        # Remove only the private disposable test's lock after restoring
        # Path.unlink, not by pretending an unsuccessful release succeeded.
        if lock.exists():
            real_unlink(lock)


@pytest.mark.skipif(os.name != "nt", reason="Windows writer.lock sharing semantics only")
def test_windows_lock_release_does_not_unlink_reassigned_token(tmp_path, monkeypatch):
    """Issue #10: if ownership changes during a retry, do not delete successor."""
    if not _windows_lock_release_retry_supported():
        pytest.skip("bounded Windows lock release not yet implemented")
    import json

    store = tmp_path / "store"
    writer = Workspace.create(store, "QA lock ownership change")
    lock = store / ".revisionworx" / "locks" / "writer.lock"
    original = Workspace.read_writer_lock(store)
    successor_token = "f" * 32 if original["token"] != "f" * 32 else "e" * 32
    real_unlink = Path.unlink
    attempts = []

    def ownership_changes_on_first_unlink(path, *args, **kwargs):
        if path == lock:
            attempts.append(True)
            # Simulate a second holder replacing the token immediately after
            # our first ownership read, before a sharing-violation retry.
            lock.write_text(json.dumps({**original, "token": successor_token}), encoding="utf-8")
            raise PermissionError(13, "QA simulated sharing violation and ownership change", str(path))
        return real_unlink(path, *args, **kwargs)

    try:
        with monkeypatch.context() as m:
            m.setattr(Workspace, "LOCK_RELEASE_ATTEMPTS", 3)
            m.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
            m.setattr(Path, "unlink", ownership_changes_on_first_unlink)
            writer.close()
        assert attempts == [True]
        assert lock.exists(), "never delete a lock held by a successor token"
        assert Workspace.read_writer_lock(store)["token"] == successor_token
    finally:
        if lock.exists():
            real_unlink(lock)
