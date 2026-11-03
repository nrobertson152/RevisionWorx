# SPDX-License-Identifier: GPL-3.0-or-later
"""Native-crossplatform test for transient Windows orphan custody rename denial.

A real product releaser is interrupted after moving a genuine lock to
.releasing-*. A new owner needs to fence this token before gaining authority.
A single simulated WinError5 at .releasing -> .fencing must not escape as an
unrecoverable checkout failure. No external actor fabricates custody files.
"""
import importlib.util
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 not installed", allow_module_level=True)

from revisionworx.workspace import RecordPut, Workspace

RID = "root_" + "e" * 32


def _real_interrupted_release(tmp_path, monkeypatch):
    root = tmp_path / "local-workspace"
    with Workspace.create(root, "QA interrupted release"):
        pass
    a = Workspace.open(root, writable=True)
    old = a._lock_token
    locks = a.meta_dir / "locks"
    real = os.replace

    def crash_after_move(src, dst, *args, **kwargs):
        if Path(src) == locks / "writer.lock" and Path(dst).name.startswith(".releasing-"):
            real(src, dst, *args, **kwargs)
            raise SystemExit("injected process death after genuine lock release move")
        return real(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_after_move)
        with pytest.raises(SystemExit, match="genuine lock release"):
            a._unlink_own_lock(locks / "writer.lock")
    assert list(locks.glob(".releasing-*"))
    assert Workspace.read_writer_lock(root) is None
    assert old not in Workspace._revoked_tokens(a.meta_dir)
    a._lock_token = None  # the original writer is deliberately dead
    a.close()
    return root, locks, old, real


def test_transient_orphan_fence_rename_must_retry_once(tmp_path, monkeypatch):
    """P1 liveness: a single Windows sharing denial must not lose the checkout attempt."""
    root, locks, old_token, real = _real_interrupted_release(tmp_path, monkeypatch)
    denied = []

    def deny_once(src, dst, *args, **kwargs):
        if (Path(src).name.startswith(".releasing-")
                and Path(dst).name.startswith(".fencing-") and not denied):
            denied.append(1)
            raise PermissionError(5, "injected transient WinError5 during custody rename")
        return real(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", deny_once)
        with Workspace.open(root, writable=True) as c:
            c.commit("qa.after-transient", [RecordPut("root", RID, {"name": "C"}, expect_rev=0)])
    assert denied == [1]
    with Workspace.open(root) as reader:
        assert reader.get("root", RID)["name"] == "C"
        assert old_token in Workspace._revoked_tokens(reader.meta_dir)
    assert not list(locks.glob(".releasing-*"))


def test_failed_fence_move_preserves_barrier_until_retry(tmp_path, monkeypatch):
    """Safety: when denial persists, no authority is granted, evidence survives for recovery."""
    root, locks, old_token, real = _real_interrupted_release(tmp_path, monkeypatch)
    denied = []

    def always_deny(src, dst, *args, **kwargs):
        if Path(src).name.startswith(".releasing-") and Path(dst).name.startswith(".fencing-"):
            denied.append(1)
            raise PermissionError(5, "injected persistent WinError5")
        return real(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", always_deny)
        with pytest.raises(PermissionError, match="WinError5"):
            Workspace.open(root, writable=True)
    assert denied, "must reach real orphan scan"
    assert Workspace.read_writer_lock(root) is None
    assert list(locks.glob(".releasing-*")), "unrevoked lock must remain in scanner-visible custody"
    assert old_token not in Workspace._revoked_tokens(root / ".revisionworx")
    with Workspace.open(root, writable=True) as c:
        c.commit("qa.after-retry", [RecordPut("root", RID, {"name": "C"}, expect_rev=0)])
    with Workspace.open(root) as reader:
        assert reader.get("root", RID)["name"] == "C"
        assert old_token in Workspace._revoked_tokens(reader.meta_dir)
