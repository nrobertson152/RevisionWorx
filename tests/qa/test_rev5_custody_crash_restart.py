# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ADR-019 rev.4 custody crash windows and process restart safety.

Failures are injected in the actual filesystem-transition operations. Subsequent
recovery is performed by a NEW Python process with an actual writable workspace,
not by rewriting journal content or fabricating lock evidence. All participants
use only RevisionWorx protocol methods; no hostile lock-directory actor.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 absent on QA branch base", allow_module_level=True)

from revisionworx.workspace import LockLostError, RecordPut, Workspace

B_ID = "root_" + "6" * 32
C_ID = "root_" + "7" * 32


@pytest.fixture
def live_b(tmp_path):
    if not hasattr(Workspace, "_displace_writer_lock"):
        pytest.skip("ADR-019 rev.4 custody implementation not present")
    root = tmp_path / "custody restart workspace"
    with Workspace.create(root, "QA restart authority"):
        pass
    b = Workspace.open(root, writable=True)
    try:
        yield root, b, root / ".revisionworx" / "locks"
    finally:
        b.close()


def _blocking(locks: Path):
    return list(locks.glob(".releasing-*")) + list(locks.glob(".fencing-*"))


def _assert_custody(locks: Path, token: str) -> None:
    files = _blocking(locks)
    assert files, "crash must leave scanner-visible custody"
    assert any(json.loads(p.read_text(encoding="utf-8")).get("token") == token
               for p in files), "custody must contain exact displaced writer token"


def _restart_and_assert_fenced(root: Path, b: Workspace, locks: Path) -> None:
    """An unrelated interpreter must fence B before C can commit effectively."""
    code = """
import sys
from pathlib import Path
from revisionworx.workspace import Workspace, RecordPut
root = Path(sys.argv[1])
with Workspace.open(root, writable=True) as c:
    c.commit("qa.restart.C", [
        RecordPut("root", "root_" + "7" * 32, {"name": "C"}, expect_rev=0)
    ])
"""
    process = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True,
                             text=True, timeout=30)
    assert process.returncode == 0, (
        f"separate-process recovery failed: stdout={process.stdout!r} stderr={process.stderr!r}")
    with Workspace.open(root) as reader:
        assert reader.get("root", C_ID) is not None, "restarted writer must commit"
        assert reader.get("root", B_ID) is None, "displaced writer must not be effective"
        assert b._lock_token in Workspace._revoked_tokens(reader.meta_dir), (
            "restarted acquirer must durably revoke displaced actual token")
        assert not _blocking(locks), "no unresolved scanner-visible custody after restart"
        entries = list(reader.entries_after(0))
        assert any(e.get("op") == "qa.restart.C" for e in entries)
    with pytest.raises(LockLostError):
        b.commit("qa.stale.B", [RecordPut("root", B_ID, {"name": "B"}, expect_rev=0)])


def test_restart_after_live_lock_moved_before_any_fencing(live_b, monkeypatch):
    root, b, locks = live_b
    real_replace = os.replace
    calls = []

    def crash_after_public_lock_move(src, dst, *args, **kwargs):
        if Path(src) == locks / "writer.lock" and Path(dst).name.startswith(".releasing-"):
            real_replace(src, dst, *args, **kwargs)
            calls.append("moved")
            raise SystemExit("process dies immediately after actual writer.lock custody move")
        return real_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_after_public_lock_move)
        with pytest.raises(SystemExit, match="custody move"):
            Workspace._displace_writer_lock(b.meta_dir, {}, "QA custody crash injection")
    assert calls == ["moved"]
    assert not (locks / "writer.lock").exists()
    assert b._lock_token not in Workspace._revoked_tokens(b.meta_dir)
    _assert_custody(locks, b._lock_token)
    _restart_and_assert_fenced(root, b, locks)


def test_restart_after_releasing_to_fencing_move_before_revocation(live_b, monkeypatch):
    root, b, locks = live_b
    real_replace = os.replace

    # Genuine Workspace._unlink_own_lock release interrupted right AFTER its
    # first rename: the still-unrevoked B is now a real orphan.
    def crash_release(src, dst, *args, **kwargs):
        if Path(src) == locks / "writer.lock" and Path(dst).name.startswith(".releasing-"):
            real_replace(src, dst, *args, **kwargs)
            raise SystemExit("owner dies after release rename")
        return real_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_release)
        with pytest.raises(SystemExit, match="release rename"):
            b._unlink_own_lock(locks / "writer.lock")
    _assert_custody(locks, b._lock_token)

    # First successor really acquires writer.lock and invokes the production
    # fence scan, then dies after moving B to .fencing but before revoking B.
    transitions = []
    def crash_fencer(src, dst, *args, **kwargs):
        if (Path(src).name.startswith(".releasing-")
                and Path(dst).name.startswith(".fencing-")):
            real_replace(src, dst, *args, **kwargs)
            transitions.append("fencing")
            raise SystemExit("fencer dies after moving B to scanned fencing custody")
        return real_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_fencer)
        with pytest.raises(SystemExit, match="fencing custody"):
            Workspace.open(root, writable=True)
    assert transitions == ["fencing"]
    _assert_custody(locks, b._lock_token)
    assert b._lock_token not in Workspace._revoked_tokens(b.meta_dir)
    _restart_and_assert_fenced(root, b, locks)


def test_restart_after_durable_revocation_before_evidence_archive(live_b, monkeypatch):
    root, b, locks = live_b
    real_replace = os.replace
    evidence = "writer.lock.qa-revocation-before-evidence"

    def crash_archive(src, dst, *args, **kwargs):
        if Path(src).name.startswith(".fencing-") and Path(dst).name == evidence:
            raise SystemExit("fencer dies after journal revocation before evidence archive")
        return real_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_archive)
        with pytest.raises(SystemExit, match="before evidence archive"):
            Workspace._displace_writer_lock(
                b.meta_dir, {b._lock_token: evidence}, "QA post-revocation crash")
    assert b._lock_token in Workspace._revoked_tokens(b.meta_dir)
    _assert_custody(locks, b._lock_token)
    _restart_and_assert_fenced(root, b, locks)


def test_transient_windows_style_share_denial_retries_then_fences(live_b, monkeypatch):
    root, b, locks = live_b
    real_replace = os.replace
    denials = []

    def transient_share_failure(src, dst, *args, **kwargs):
        if (Path(src) == locks / "writer.lock"
                and Path(dst).name.startswith(".releasing-") and len(denials) < 2):
            denials.append("sharing violation")
            raise PermissionError("WinError 32 simulated: writer file busy")
        return real_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", transient_share_failure)
        Workspace._displace_writer_lock(b.meta_dir, {}, "QA transient sharing denial")
    assert len(denials) == 2
    assert b._lock_token in Workspace._revoked_tokens(b.meta_dir)
    _restart_and_assert_fenced(root, b, locks)
