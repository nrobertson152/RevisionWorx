# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent fault-barrier verification for ADR-019 rev.4."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 not available", allow_module_level=True)

from revisionworx.workspace import Workspace, WorkspaceCorruptError, WorkspaceLockedError, RecordPut


def test_revocation_publish_failure_preserves_custody(tmp_path, monkeypatch):
    if not hasattr(Workspace, "_displace_writer_lock"):
        pytest.skip("requires rev.4")
    root = tmp_path / "fault-workspace"
    with Workspace.create(root, "QA"):
        pass
    with Workspace.open(root, writable=True) as b:
        token = b._lock_token
        locks = b.meta_dir / "locks"
        actual = Workspace._publish_revocation

        def deny(cls, meta, holder, reason, evidence):
            if holder.get("token") == token:
                raise PermissionError("injected revocation publication failure")
            return actual(meta, holder, reason=reason, evidence=evidence)

        with monkeypatch.context() as patch:
            patch.setattr(Workspace, "_publish_revocation", classmethod(deny))
            with pytest.raises(PermissionError, match="revocation publication"):
                Workspace._displace_writer_lock(b.meta_dir, {}, "QA fault")
        assert not (locks / "writer.lock").exists()
        custody = list(locks.glob(".releasing-*")) + list(locks.glob(".fencing-*"))
        assert any(json.loads(p.read_text())["token"] == token for p in custody)
        assert token not in Workspace._revoked_tokens(b.meta_dir)
        with Workspace.open(root, writable=True) as c:
            c.commit("qa.fault", [RecordPut("root", "root_" + "8" * 32, {"ok": True}, expect_rev=0)])
        assert token in Workspace._revoked_tokens(b.meta_dir)
        with Workspace.open(root) as read:
            assert read.get("root", "root_" + "8" * 32) is not None


def test_busy_rename_preserves_original_authority(tmp_path, monkeypatch):
    if not hasattr(Workspace, "_displace_writer_lock"):
        pytest.skip("requires rev.4")
    root = tmp_path / "busy-workspace"
    with Workspace.create(root, "QA"):
        pass
    with Workspace.open(root, writable=True) as b:
        token = b._lock_token
        locks = b.meta_dir / "locks"
        original = os.replace
        attempts = []

        def busy(src, dst, *args, **kwargs):
            if Path(src) == locks / "writer.lock" and Path(dst).name.startswith(".releasing-"):
                attempts.append(1)
                raise PermissionError("simulated sharing conflict")
            return original(src, dst, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", busy)
            patch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
            with pytest.raises(PermissionError, match="sharing conflict"):
                Workspace._displace_writer_lock(b.meta_dir, {}, "QA share conflict")
        assert len(attempts) == Workspace.LOCK_RELEASE_ATTEMPTS
        assert Workspace.read_writer_lock(root)["token"] == token
        assert token not in Workspace._revoked_tokens(b.meta_dir)
        with pytest.raises(WorkspaceLockedError):
            Workspace.open(root, writable=True)
        b.commit("qa.live", [RecordPut("root", "root_" + "9" * 32, {"ok": True}, expect_rev=0)])


def test_unreadable_fencing_barrier_denies_writable_open(tmp_path):
    root = tmp_path / "bad-custody"
    with Workspace.create(root, "QA"):
        pass
    locks = root / ".revisionworx" / "locks"
    orphan = locks / (".fencing-" + "a" * 32)
    orphan.write_text("not a valid lock file", encoding="utf-8")
    with pytest.raises(WorkspaceCorruptError, match="unreadable"):
        Workspace.open(root, writable=True)
    assert orphan.read_text(encoding="utf-8") == "not a valid lock file"
    assert Workspace.read_writer_lock(root) is None
