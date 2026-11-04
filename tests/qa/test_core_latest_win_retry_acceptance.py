# SPDX-License-Identifier: GPL-3.0-or-later
"""QA positive new-head gate: two Windows availability defects first proved on 22a5273.

Use a single tiny retry budget with deterministic failure injection. This
module does not alter or assume implementation details beyond the public
Workspace.read_writer_lock and genuine interrupted release protocol.
"""
import importlib.util
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 not installed", allow_module_level=True)

from revisionworx import atomicio
from revisionworx.workspace import RecordPut, Workspace

RID = "root_" + "f" * 32


def _retry_budget(monkeypatch):
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_RETRIES", 3)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)


def test_writer_lock_read_one_end_to_end_retry_budget(tmp_path, monkeypatch):
    _retry_budget(monkeypatch)
    workspace_meta = tmp_path / ".revisionworx"
    calls = []

    def blocked(path):
        calls.append(str(path))
        raise PermissionError("simulated persistent Windows sharing denial")
    monkeypatch.setattr(atomicio, "_open_for_read", blocked)
    with pytest.raises(PermissionError, match="persistent"):
        Workspace.read_writer_lock(workspace_meta)
    assert len(calls) == 3, (
        f"one retry budget required, observed {len(calls)} file opens"
    )


def test_fencing_orphan_recovers_after_one_transient_windows_denial(tmp_path, monkeypatch):
    _retry_budget(monkeypatch)
    root = tmp_path / "local-core-workspace"
    with Workspace.create(root, "QA current head retry"):
        pass
    a = Workspace.open(root, writable=True)
    token = a._lock_token
    locks = a.meta_dir / "locks"
    original_replace = os.replace

    def crash_after_genuine_release(src, dst, *args, **kwargs):
        if Path(src) == locks / "writer.lock" and Path(dst).name.startswith(".releasing-"):
            original_replace(src, dst, *args, **kwargs)
            raise SystemExit("QA injected death after real release move")
        return original_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_after_genuine_release)
        with pytest.raises(SystemExit, match="release move"):
            a._unlink_own_lock(locks / "writer.lock")
    assert list(locks.glob(".releasing-*"))
    a._lock_token = None
    a.close()
    assert token not in Workspace._revoked_tokens(root / ".revisionworx")

    denied = []
    def once(src, dst, *args, **kwargs):
        if (Path(src).name.startswith(".releasing-")
                and Path(dst).name.startswith(".fencing-") and not denied):
            denied.append(1)
            raise PermissionError("one temporary Windows custody rename failure")
        return original_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", once)
        with Workspace.open(root, writable=True) as c:
            c.commit("qa.after-transient", [
                RecordPut("root", RID, {"name": "C"}, expect_rev=0)])
    assert denied == [1]
    with Workspace.open(root) as reader:
        assert reader.get("root", RID)["name"] == "C"
        assert token in Workspace._revoked_tokens(reader.meta_dir)
    assert not list(locks.glob(".releasing-*"))
