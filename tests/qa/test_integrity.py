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
    actual_check = writer._check_lock_still_held
    calls = []
    after_break = []
    def break_after_check():
        actual_check()
        if not calls:
            calls.append(True)
            Workspace.break_writer_lock(store, token, reason="QA deterministic paused-writer race")
            after_break.append(Workspace.open(store).head_seq())
    try:
        lock_lost = False
        with monkeypatch.context() as m:
            m.setattr(writer, "_check_lock_still_held", break_after_check)
            try:
                writer.commit("qa.stale", [
                    RecordPut("settings", "workspace", {"stale_writer": True})
                ])
            except workspace_mod.LockLostError:
                lock_lost = True
        with Workspace.open(store) as reader:
            if not after_break:
                raise RuntimeError("QA failpoint did not inject the audited administrative break")
            assert lock_lost, "revoked writer incorrectly returned from commit"
            assert reader.head_seq() == after_break[0], "revoked writer published a journal entry"
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
    strict=True, raises=AssertionError,
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
    original_check = writer._check_lock_still_held
    break_seq = []
    late_id = new_id("root")
    txid = "e" * 32

    def break_immediately_after_preflight():
        original_check()
        if not break_seq:
            Workspace.break_writer_lock(store, old_token, reason="QA fencing check")
            break_seq.append(Workspace.open(store).head_seq())

    try:
        with monkeypatch.context() as m:
            m.setattr(writer, "_check_lock_still_held", break_immediately_after_preflight)
            with pytest.raises(workspace_mod.LockLostError, match="fenced"):
                writer.commit("qa.fenced.late.write", [
                    RecordPut("root", late_id, {"id": late_id, "name": "not authoritative"})
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
