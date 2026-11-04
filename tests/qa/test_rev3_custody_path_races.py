# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ADR-019 rev3 post-verification pathname-replacement QA.

Each unsafe_reproducer intentionally asserts a demonstrated safety VIOLATION,
not acceptance. Paired strict expected-failure tests assert the positive safety
requirement. Test doubles intervene only at the actual filesystem boundary.
The two real writer commits are checked as effective journal records.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 unavailable on the Phase 1 QA base", allow_module_level=True)

from revisionworx import atomicio
from revisionworx.workspace import RecordPut, Workspace, WorkspaceError

A_TOKEN = "a" * 32
RID_B = "root_" + "b" * 32
RID_C = "root_" + "c" * 32
UNREADABLE = b"{older abandoned lock, operator approved its clear"


def _holder(token):
    return {
        "token": token, "user": "qa", "host": "local",
        "pid": 12345, "acquired": "2026-11-02T01:00:00Z",
    }


def _root(tmp_path):
    root = tmp_path / "workspace"
    with Workspace.create(root, "QA adversarial custody writer race"):
        pass
    return root, root / ".revisionworx" / "locks"


def _unsafe_dispose_present():
    return (hasattr(Workspace, "_dispose_custody")
            and "custody.unlink(missing_ok=True)"
            in inspect.getsource(Workspace._dispose_custody))


def _unsafe_fence_present():
    return (hasattr(Workspace, "_fence_token_orphan")
            and "os.replace(custody, locks / evidence)"
            in inspect.getsource(Workspace._fence_token_orphan))


def _results(root, b_token, c_token, injected):
    with Workspace.open(root) as r:
        effective = {
            "B": r.get("root", RID_B) is not None,
            "C": r.get("root", RID_C) is not None,
        }
        entries = list(r.entries_after(0))
        tokens = {
            "B": any(e.get("op") == "qa.late.B" and e.get("writer_token") == b_token
                     for e in entries),
            "C": any(e.get("op") == "qa.successor.C" and e.get("writer_token") == c_token
                     for e in entries),
        }
        revoked = b_token in Workspace._revoked_tokens(r.meta_dir)
    return {"injected": injected, "effective": effective, "tokens": tokens, "revoked_B": revoked}


def _run_disposal_race(tmp_path, monkeypatch):
    """B passes genuine commit preflight; admin clears orphan; replaced custody deletes B's lock."""
    root, locks = _root(tmp_path)
    old = locks / (".releasing-" + "d" * 16)
    b = Workspace.open(root, writable=True)
    b_token = b._lock_token
    old.write_bytes(UNREADABLE)
    c_token = None
    injected = []
    original_check = b._check_lock_still_held
    real_unlink = Path.unlink
    real_replace = os.replace

    def swap_at_final_delete(path, *args, **kwargs):
        p = Path(path)
        if p.name.startswith(".clearing-") and not injected:
            injected.append(True)
            # Deliberately replace the *directory entry* after it was hashed.
            # This is B's actual active lock, not merely a synthetic token.
            real_replace(locks / "writer.lock", p)
        return real_unlink(path, *args, **kwargs)

    def after_B_preflight():
        nonlocal c_token
        original_check()
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", swap_at_final_delete)
            Workspace.clear_orphaned_release(root, old.name, "operator confirmed earlier lock stopped")
        try:
            with Workspace.open(root, writable=True) as c:
                c_token = c._lock_token
                c.commit("qa.successor.C", [
                    RecordPut("root", RID_C, {"name": "C"}, expect_rev=0)])
        except (WorkspaceError, OSError):
            pass

    try:
        with monkeypatch.context() as patch:
            patch.setattr(b, "_check_lock_still_held", after_B_preflight)
            try:
                b.commit("qa.late.B", [
                    RecordPut("root", RID_B, {"name": "B"}, expect_rev=0)])
            except WorkspaceError:
                pass
    finally:
        b.close()
    return _results(root, b_token, c_token, bool(injected))


def _run_fencing_race(tmp_path, monkeypatch):
    """B passes genuine preflight; its orphan replaces A's already inspected .fencing custody."""
    root, locks = _root(tmp_path)
    first = locks / (".releasing-" + "1" * 16)
    second = locks / (".releasing-" + "2" * 16)
    b = Workspace.open(root, writable=True)
    b_token = b._lock_token
    first.write_bytes(atomicio.dumps_json(_holder(A_TOKEN)))
    original_check = b._check_lock_still_held
    real_replace = os.replace
    injected = []
    c_token = None

    def replace_fencing_custody(src, dst, *args, **kwargs):
        src, dst = Path(src), Path(dst)
        if (src.name.startswith(".fencing-")
                and dst.name.startswith("writer.lock.orphaned-")
                and second.exists() and not injected):
            injected.append(True)
            # Replacing inspected A custody with B's real lock avoids B's
            # original .releasing-* path later in the already-snapshotted scan.
            real_replace(second, src)
        return real_replace(src, dst, *args, **kwargs)

    def after_B_preflight():
        nonlocal c_token
        original_check()
        real_replace(locks / "writer.lock", second)
        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", replace_fencing_custody)
            try:
                with Workspace.open(root, writable=True) as c:
                    c_token = c._lock_token
                    c.commit("qa.successor.C", [
                        RecordPut("root", RID_C, {"name": "C"}, expect_rev=0)])
            except (WorkspaceError, OSError):
                pass

    try:
        with monkeypatch.context() as patch:
            patch.setattr(b, "_check_lock_still_held", after_B_preflight)
            try:
                b.commit("qa.late.B", [
                    RecordPut("root", RID_B, {"name": "B"}, expect_rev=0)])
            except WorkspaceError:
                pass
    finally:
        b.close()
    return _results(root, b_token, c_token, bool(injected))


def _assert_safe(actual):
    assert not (actual["effective"]["B"] and actual["effective"]["C"]), (
        "two effective writer-token commits after an orphan custody pathname replacement")
    if actual["effective"]["C"] and not actual["effective"]["B"]:
        assert actual["revoked_B"], (
            "successor became effective while displaced B lacked a durable revocation")


def test_disposal_unsafe_reproducer_two_effective_writers(tmp_path, monkeypatch):
    """Passing test is evidence of a DEFECT, never a passing safety requirement."""
    if not _unsafe_dispose_present():
        pytest.skip("legacy unguarded custody pathname unlink no longer present")
    actual = _run_disposal_race(tmp_path, monkeypatch)
    assert actual["injected"], "failed to intercept the actual disposal boundary"
    assert actual["effective"] == {"B": True, "C": True}
    assert actual["tokens"] == {"B": True, "C": True}
    assert not actual["revoked_B"]


def test_disposal_safety_must_not_allow_two_effective_writers(tmp_path, monkeypatch, request):
    if _unsafe_dispose_present():
        request.node.add_marker(pytest.mark.xfail(strict=True, raises=AssertionError,
            reason="Outside the Tier-1 threat model (ADR-019 rev. 3): needs a non-protocol process to rename a "
                   "live writer.lock; pinned by tests/test_lock_namespace_threat_model.py"))
    actual = _run_disposal_race(tmp_path, monkeypatch)
    _assert_safe(actual)


def test_fencing_unsafe_reproducer_two_effective_writers(tmp_path, monkeypatch):
    """Passing test is evidence of a DEFECT, never a passing safety requirement."""
    if not _unsafe_fence_present():
        pytest.skip("legacy unchecked .fencing pathname archive no longer present")
    actual = _run_fencing_race(tmp_path, monkeypatch)
    assert actual["injected"], "failed to intercept the actual fencing disposition boundary"
    assert actual["effective"] == {"B": True, "C": True}
    assert actual["tokens"] == {"B": True, "C": True}
    assert not actual["revoked_B"]


def test_fencing_safety_must_not_allow_two_effective_writers(tmp_path, monkeypatch, request):
    if _unsafe_fence_present():
        request.node.add_marker(pytest.mark.xfail(strict=True, raises=AssertionError,
            reason="Outside the Tier-1 threat model (ADR-019 rev. 3): needs a non-protocol process to rename a "
                   "live writer.lock; pinned by tests/test_lock_namespace_threat_model.py"))
    actual = _run_fencing_race(tmp_path, monkeypatch)
    _assert_safe(actual)
