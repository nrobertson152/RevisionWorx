# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent protocol-only ADR-019 P1 negative control and rev. 4 acceptance.

Old-source unsafe witnesses deliberately PASS and paired positive oracles strictly
XFAIL on d2b7a1c. On rev. 4, old witnesses SKIP and the unchanged journal-effective
safety oracles MUST PASS normally; additional assertions require durable B revocation,
no effective B revision, C effective commit, and expected LockLostError for B.
No actor renames foreign custody names outside the product protocol.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import threading
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 absent on QA branch base", allow_module_level=True)

from revisionworx.workspace import RecordPut, Workspace, WorkspaceError

B_ID = "root_" + "b" * 32
C_ID = "root_" + "c" * 32


def make_workspace(tmp_path):
    root = tmp_path / "workspace"
    with Workspace.create(root, "QA protocol-only race"):
        pass
    return root, root / ".revisionworx" / "locks"


def effective(root, b_token, c_token):
    with Workspace.open(root) as reader:
        entries = list(reader.entries_after(0))
        return {
            "B": reader.get("root", B_ID) is not None,
            "C": reader.get("root", C_ID) is not None,
            "revoked_B": b_token in Workspace._revoked_tokens(reader.meta_dir),
            "B_entry": any(e.get("op") == "qa.B" and e.get("writer_token") == b_token for e in entries),
            "C_entry": any(e.get("op") == "qa.C" and e.get("writer_token") == c_token for e in entries),
        }


def put_b(ws):
    return ws.commit("qa.B", [RecordPut("root", B_ID, {"name": "B"}, expect_rev=0)])


def put_c(ws):
    return ws.commit("qa.C", [RecordPut("root", C_ID, {"name": "C"}, expect_rev=0)])


def _revoked_cleanup_unguarded():
    return 'os.replace(lock, lock.with_name(f"writer.lock.revoked-' in inspect.getsource(Workspace._acquire_writer_lock)


def _run_revoked_cleanup_race(tmp_path, monkeypatch):
    """D reads revoked A, pauses. B legitimately archives A, acquires and passes
    commit preflight. D's delayed protocol cleanup moves B's live lock, and
    C (D's new lock owner) commits. B resumes past preflight and commits.
    """
    root, locks = make_workspace(tmp_path)
    lock = locks / "writer.lock"
    a = Workspace.open(root, writable=True)
    a_token = a._lock_token
    real_replace = os.replace

    def deny_post_revocation_archive(src, dst, *args, **kwargs):
        if (Path(src) == lock and Path(dst).name.startswith(
                ("writer.lock.broken-", ".releasing-"))):
            raise PermissionError("injected sharing failure after durable break")
        return real_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", deny_post_revocation_archive)
        Workspace.break_writer_lock(root, a_token, "administrator confirmed A stopped")
    a.close()
    assert Workspace.read_writer_lock(root)["token"] == a_token
    assert a_token in Workspace._revoked_tokens(root / ".revisionworx")

    d_at_old_revoked = threading.Event()
    resume_d = threading.Event()
    b_preflight = threading.Event()
    resume_b = threading.Event()
    witnessed = []
    errors = []
    result = {}

    def pause_old_rename(src, dst, *args, **kwargs):
        if (Path(src) == lock and Path(dst).name.startswith(
                ("writer.lock.revoked-", ".releasing-")) and not witnessed):
            witnessed.append(True)
            d_at_old_revoked.set()
            if not resume_d.wait(12):
                raise AssertionError("timed out awaiting D's resumed cleanup")
        return real_replace(src, dst, *args, **kwargs)

    def d_run():
        try:
            with Workspace.open(root, writable=True) as c:
                result["c_token"] = c._lock_token
                put_c(c)
        except BaseException as exc:
            errors.append(("D", repr(exc)))

    def b_run(b):
        real_rev = b._current_rev
        def pause_after_preflight(kind, record_id):
            b_preflight.set()
            if not resume_b.wait(12):
                raise AssertionError("timed out waiting to resume B commit")
            return real_rev(kind, record_id)
        b._current_rev = pause_after_preflight
        try:
            put_b(b)
        except WorkspaceError as exc:
            result["b_exception"] = type(exc).__name__
        except BaseException as exc:
            errors.append(("B", repr(exc)))

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", pause_old_rename)
        d_thread = threading.Thread(target=d_run, name="qa-old-revoked-cleaner")
        d_thread.start()
        try:
            assert d_at_old_revoked.wait(12), "D never reached stale revoked A rename"
            b = Workspace.open(root, writable=True)
            result["b_token"] = b._lock_token
            b_thread = threading.Thread(target=b_run, args=(b,), name="qa-preflighted-B")
            b_thread.start()
            try:
                assert b_preflight.wait(12), "B did not pass real commit preflight"
                resume_d.set()
                d_thread.join(12)
                assert not d_thread.is_alive(), "D cleanup did not finish"
            finally:
                resume_b.set()
                b_thread.join(12)
                b.close()
        finally:
            resume_d.set()
            d_thread.join(12)
    assert not errors, errors
    assert witnessed and "b_token" in result and "c_token" in result
    observed = effective(root, result["b_token"], result["c_token"])
    observed["B_error"] = result.get("b_exception")
    return observed


def test_revoked_cleanup_unsafe_two_effective_protocol_writers(tmp_path, monkeypatch):
    """PASS means a real P1 protocol defect is reproduced, NOT accepted safety."""
    if not _revoked_cleanup_unguarded():
        pytest.skip("unverified stale-token rename implementation changed")
    observed = _run_revoked_cleanup_race(tmp_path, monkeypatch)
    assert observed["B"] and observed["C"]
    assert observed["B_entry"] and observed["C_entry"]
    assert not observed["revoked_B"]


def test_revoked_cleanup_positive_safety_no_unrevoked_two_writers(tmp_path, monkeypatch, request):
    if _revoked_cleanup_unguarded():
        request.node.add_marker(pytest.mark.xfail(
            strict=True, raises=AssertionError,
            reason="d2b7a1c: stale token A read then rename deletes live unrevoked B lock"))
    observed = _run_revoked_cleanup_race(tmp_path, monkeypatch)
    assert not (observed["B"] and observed["C"] and not observed["revoked_B"]), observed
    if not _revoked_cleanup_unguarded():
        assert observed["C"] and observed["C_entry"], observed
        assert observed["revoked_B"] and not observed["B"], observed
        assert observed["B_error"] == "LockLostError", observed


def _displaced_archival_unguarded():
    source = inspect.getsource(Workspace._restore_foreign_lock)
    return ("os.replace(private, evidence)" in source and
            "Workspace._publish_revocation(" in source and
            source.index("os.replace(private, evidence)") < source.index("Workspace._publish_revocation("))


def _run_displaced_archive_crash(tmp_path, monkeypatch):
    """A passes its real release preflight; admin breaks A and B legitimately
    acquires. Delayed A release displaces B; C acquires but pauses before fencing.
    A archives B outside the scanned .releasing namespace and crashes before
    revocation. C scans clean and commits; preflighted B also commits.
    Every filesystem move is the actual production protocol move.
    """
    root, locks = make_workspace(tmp_path)
    lock = locks / "writer.lock"
    a = Workspace.open(root, writable=True)
    a_token = a._lock_token

    a_before_move = threading.Event()
    resume_a_move = threading.Event()
    b_preflight = threading.Event()
    resume_b = threading.Event()
    b_displaced = threading.Event()
    resume_a_restore = threading.Event()
    c_has_lock = threading.Event()
    resume_c_scan = threading.Event()
    a_crashed = threading.Event()
    error = []
    state = {}
    real_replace = os.replace
    real_restore = a._restore_foreign_lock
    real_fence = Workspace._fence_orphaned_releases
    real_revoke = Workspace._publish_revocation
    release_seen = []

    def pause_release_replace(src, dst, *args, **kwargs):
        if (Path(src) == lock and Path(dst).name.startswith(".releasing-")
                and not release_seen):
            release_seen.append(True)
            a_before_move.set()
            if not resume_a_move.wait(12):
                raise AssertionError("A release move was not resumed")
        return real_replace(src, dst, *args, **kwargs)

    def paused_restore(private, target, holder):
        assert holder.get("token") == state["b_token"], holder
        state["private_B"] = private
        b_displaced.set()
        if not resume_a_restore.wait(12):
            raise AssertionError("A foreign restoration was not resumed")
        return real_restore(private, target, holder)

    def fail_before_revocation(cls, meta, holder, reason, evidence):
        if (holder.get("token") == state.get("b_token")
                and threading.current_thread().name == "qa-old-releaser"):
            a_crashed.set()
            raise SystemExit("release crashed after archiving B and before revocation")
        return real_revoke(meta, holder, reason=reason, evidence=evidence)

    def pause_successor_fence(meta, own_token):
        if own_token != state.get("b_token") and not c_has_lock.is_set():
            state["c_token"] = own_token
            c_has_lock.set()
            if not resume_c_scan.wait(12):
                raise AssertionError("C was not resumed to fence orphans")
        return real_fence(meta, own_token)

    def release_a():
        try:
            a._unlink_own_lock(lock)
        except SystemExit as exc:
            state["a_crash"] = str(exc)
        except BaseException as exc:
            error.append(("A", repr(exc)))

    def commit_b(b):
        real_rev = b._current_rev
        def pause_after_checked(kind, record_id):
            b_preflight.set()
            if not resume_b.wait(12):
                raise AssertionError("B commit never resumed")
            return real_rev(kind, record_id)
        b._current_rev = pause_after_checked
        try:
            put_b(b)
        except WorkspaceError as exc:
            state["b_exception"] = type(exc).__name__
        except BaseException as exc:
            error.append(("B", repr(exc)))

    def acquire_and_commit_c():
        try:
            with Workspace.open(root, writable=True) as c:
                state["c_token"] = c._lock_token
                put_c(c)
        except BaseException as exc:
            error.append(("C", repr(exc)))

    # The pause is inside the actual A lock-release method *after* it tests
    # current journal revocations and *before* it renames writer.lock.
    a._restore_foreign_lock = paused_restore
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", pause_release_replace)
        a_thread = threading.Thread(target=release_a, name="qa-old-releaser")
        a_thread.start()
        b = None
        b_thread = None
        c_thread = None
        try:
            assert a_before_move.wait(12), "A release never passed revocation preflight"
            Workspace.break_writer_lock(root, a_token, "admin confirmed A was stopped")
            b = Workspace.open(root, writable=True)
            state["b_token"] = b._lock_token
            b_thread = threading.Thread(target=commit_b, args=(b,), name="qa-delayed-B")
            b_thread.start()
            assert b_preflight.wait(12), "B never passed its true commit preflight"

            # A's stale releaser displaces B's *live* lock. This happens using
            # A's own production _unlink_own_lock(), not an outside rename.
            resume_a_move.set()
            assert b_displaced.wait(12), "A did not take B's lock into release custody"

            patch.setattr(Workspace, "_publish_revocation", classmethod(fail_before_revocation))
            patch.setattr(Workspace, "_fence_orphaned_releases", staticmethod(pause_successor_fence))
            c_thread = threading.Thread(target=acquire_and_commit_c, name="qa-successor-C")
            c_thread.start()
            assert c_has_lock.wait(12), "C never acquired writer.lock before its fence scan"

            # C's lock makes restoring B exclusive-fail, so A archives B in a
            # nonblocking writer.lock.displaced-* name before its journal revocation.
            resume_a_restore.set()
            a_thread.join(12)
            assert not a_thread.is_alive() and a_crashed.is_set(), (
                "A must crash after B evidence archive and before durable revocation")
            if _displaced_archival_unguarded():
                assert list(locks.glob("writer.lock.displaced-*")), "B evidence was not archived"
                assert not list(locks.glob(".releasing-*")), "orphan still blocks C"
            else:
                import json
                blocking = list(locks.glob(".releasing-*")) + list(locks.glob(".fencing-*"))
                assert blocking, "interrupted displacement lost scanner-visible B custody"
                assert any(json.loads(p.read_text(encoding="utf-8")).get("token") == state["b_token"]
                           for p in blocking), "the actual B token must remain visible to the scanner"

            resume_c_scan.set()
            c_thread.join(12)
            assert not c_thread.is_alive(), "C did not finish committing"
        finally:
            resume_a_move.set()
            resume_a_restore.set()
            resume_c_scan.set()
            resume_b.set()
            if b_thread is not None:
                b_thread.join(12)
            if c_thread is not None:
                c_thread.join(12)
            a_thread.join(12)
            if b is not None:
                b.close()
            a.close()
    assert not error, error
    assert release_seen and "b_token" in state and "c_token" in state
    observed = effective(root, state["b_token"], state["c_token"])
    observed["B_error"] = state.get("b_exception")
    return observed


def test_displaced_archive_crash_unsafe_two_effective_protocol_writers(tmp_path, monkeypatch):
    """PASS is proof of protocol-only breach, never a safety acceptance."""
    if not _displaced_archival_unguarded():
        pytest.skip("unverified archive-before-revoke implementation changed")
    observed = _run_displaced_archive_crash(tmp_path, monkeypatch)
    assert observed["B"] and observed["C"]
    assert observed["B_entry"] and observed["C_entry"]
    assert not observed["revoked_B"]


def test_displaced_archive_crash_positive_safety_no_two_writers(tmp_path, monkeypatch, request):
    if _displaced_archival_unguarded():
        request.node.add_marker(pytest.mark.xfail(
            strict=True, raises=AssertionError,
            reason="d2b7a1c: displaced B archived outside scanner then crash before revocation"))
    observed = _run_displaced_archive_crash(tmp_path, monkeypatch)
    assert not (observed["B"] and observed["C"] and not observed["revoked_B"]), observed
    if not _displaced_archival_unguarded():
        assert observed["C"] and observed["C_entry"], observed
        assert observed["revoked_B"] and not observed["B"], observed
        assert observed["B_error"] == "LockLostError", observed


def _break_archive_unguarded():
    src = inspect.getsource(Workspace.break_writer_lock)
    return ('current.get("token") == expected_token' in src and
            "os.replace(lock, broken)" in src)


def _run_admin_break_snapshot_race(tmp_path, monkeypatch):
    """Admin has journal-revoked A and read A in writer.lock, pauses before
    evidence move. B legally archives revoked A and takes its own lock.
    Admin resumes its *normal* os.replace and inadvertently moves B to a
    nonblocking .broken-* name without revoking B. C and paused B both commit.
    No outside / custom custody mutation: all operations are production API.
    """
    root, locks = make_workspace(tmp_path)
    lock = locks / "writer.lock"
    a = Workspace.open(root, writable=True)
    a_token = a._lock_token
    real_replace = os.replace
    admin_at_break = threading.Event()
    resume_admin = threading.Event()
    b_preflight = threading.Event()
    resume_b = threading.Event()
    witnessed = []
    errors = []
    state = {}

    def pause_break_evidence_move(src, dst, *args, **kwargs):
        if (Path(src) == lock and Path(dst).name.startswith(
                ("writer.lock.broken-", ".releasing-")) and not witnessed):
            witnessed.append(True)
            admin_at_break.set()
            if not resume_admin.wait(12):
                raise AssertionError("paused administrative break never resumed")
        return real_replace(src, dst, *args, **kwargs)

    def admin_run():
        try:
            state["admin"] = Workspace.break_writer_lock(
                root, a_token, "previous A writer was verified stopped")
        except BaseException as exc:
            errors.append(("admin", repr(exc)))

    def b_run(b):
        actual_rev = b._current_rev
        def pause_after_preflight(kind, record_id):
            b_preflight.set()
            if not resume_b.wait(12):
                raise AssertionError("B preflighted commit did not resume")
            return actual_rev(kind, record_id)
        b._current_rev = pause_after_preflight
        try:
            put_b(b)
        except WorkspaceError as exc:
            state["b_error"] = type(exc).__name__
        except BaseException as exc:
            errors.append(("B", repr(exc)))

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", pause_break_evidence_move)
        admin_thread = threading.Thread(target=admin_run, name="qa-delayed-admin-break")
        admin_thread.start()
        b = None
        b_thread = None
        try:
            assert admin_at_break.wait(12), "admin did not reach post-revocation move"
            assert a_token in Workspace._revoked_tokens(root / ".revisionworx")
            b = Workspace.open(root, writable=True)  # legitimate automatic cleanup of revoked A
            state["b_token"] = b._lock_token
            b_thread = threading.Thread(target=b_run, args=(b,), name="qa-break-successor-B")
            b_thread.start()
            assert b_preflight.wait(12), "B did not pass real commit checks"
            resume_admin.set()
            admin_thread.join(12)
            assert not admin_thread.is_alive(), "admin archive did not finish"
            if _break_archive_unguarded():
                assert list(locks.glob("writer.lock.broken-*")), "B was not moved into broken evidence"
            with Workspace.open(root, writable=True) as c:
                state["c_token"] = c._lock_token
                put_c(c)
        finally:
            resume_admin.set()
            resume_b.set()
            admin_thread.join(12)
            if b_thread is not None:
                b_thread.join(12)
            if b is not None:
                b.close()
            a.close()
    assert witnessed and not errors, errors
    observed = effective(root, state["b_token"], state["c_token"])
    observed["B_error"] = state.get("b_error")
    return observed


def test_admin_break_race_unsafe_two_effective_protocol_writers(tmp_path, monkeypatch):
    """PASS is an additional independent protocol-only P1 unsafe witness."""
    if not _break_archive_unguarded():
        pytest.skip("unguarded admin archive mechanism changed")
    observed = _run_admin_break_snapshot_race(tmp_path, monkeypatch)
    assert observed["B"] and observed["C"], observed
    assert observed["B_entry"] and observed["C_entry"]
    assert not observed["revoked_B"]


def test_admin_break_race_positive_safety_no_unrevoked_two_writers(tmp_path, monkeypatch, request):
    if _break_archive_unguarded():
        request.node.add_marker(pytest.mark.xfail(
            strict=True, raises=AssertionError,
            reason="d2b7a1c: admin break re-reads A before moving unrevoked successor B lock"))
    observed = _run_admin_break_snapshot_race(tmp_path, monkeypatch)
    assert not (observed["B"] and observed["C"] and not observed["revoked_B"]), observed
    if not _break_archive_unguarded():
        assert observed["C"] and observed["C_entry"], observed
        assert observed["revoked_B"] and not observed["B"], observed
        assert observed["B_error"] == "LockLostError", observed
