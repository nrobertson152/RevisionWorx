# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent *protocol-only* ADR-019 post-d2b7a1c P1 race reproducers.

Deliberately passing unsafe tests prove two EFFECTIVE writers. Paired strict
XFAILs are unmet positive safety oracles, not passing safety certificates.
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
        if Path(src) == lock and Path(dst).name.startswith("writer.lock.broken-"):
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
        if (Path(src) == lock and Path(dst).name.startswith("writer.lock.revoked-")
                and not witnessed):
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
    return effective(root, result["b_token"], result["c_token"])


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
