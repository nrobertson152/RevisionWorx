# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ADR-019 protocol-only positive oracles and honest threat-model controls.

These tests do NOT simulate an outside actor moving somebody else's live lock into a
private custody name. That capability already defeats any directory-entry lock.
The separate pinned rev3 tests retain those out-of-protocol dual-effective-writer
counterexamples; do not relabel them as passing protocol safety guarantees.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 unavailable", allow_module_level=True)

from revisionworx.workspace import (
    LockLostError, OrphanClearConflict, RecordPut, Workspace,
    WorkspaceLockedError,
)

B_ID = "root_" + "b" * 32
C_ID = "root_" + "c" * 32


def make_workspace(tmp_path):
    root = tmp_path / "share"
    with Workspace.create(root, "ADR-019 QA") as _:
        pass
    return root, root / ".revisionworx" / "locks"


def effective(root, b_token, c_token):
    with Workspace.open(root) as reader:
        entries = list(reader.entries_after(0))
        return {
            "B": reader.get("root", B_ID) is not None,
            "C": reader.get("root", C_ID) is not None,
            "revoked_B": b_token in Workspace._revoked_tokens(reader.meta_dir),
            "journal_B": any(e.get("op") == "qa.B" and e.get("writer_token") == b_token
                             for e in entries),
            "journal_C": any(e.get("op") == "qa.C" and e.get("writer_token") == c_token
                             for e in entries),
        }


def put_b(b):
    return b.commit("qa.B", [RecordPut("root", B_ID, {"name": "B"}, expect_rev=0)])


def put_c(c):
    return c.commit("qa.C", [RecordPut("root", C_ID, {"name": "C"}, expect_rev=0)])


def test_admin_custody_disposal_without_outside_rename_preserves_live_lock(tmp_path):
    """A valid live lock is untouched when an unrelated unreadable orphan is cleared."""
    root, locks = make_workspace(tmp_path)
    b = Workspace.open(root, writable=True)
    tok_b = b._lock_token
    orphan = locks / (".releasing-" + "d" * 24)
    orphan.write_bytes(b"{torn prior writer")
    try:
        result = Workspace.clear_orphaned_release(root, orphan.name, "confirmed prior writer stopped")
        assert result["outcome"] == "completed"
        assert Workspace.read_writer_lock(root)["token"] == tok_b
        with pytest.raises(WorkspaceLockedError):
            Workspace.open(root, writable=True)
        put_b(b)
    finally:
        b.close()
    with Workspace.open(root, writable=True) as c:
        tok_c = c._lock_token
        put_c(c)
    found = effective(root, tok_b, tok_c)
    assert found["B"] and found["C"] and not found["revoked_B"]
    assert found["journal_B"] and found["journal_C"]


def _run_protocol_release_interleaving(tmp_path, monkeypatch, crash):
    """Pause B after actual commit preflight; use B's real release operation.

    B's _unlink_own_lock first moves its own writer.lock into .releasing-*.
    Either permit C to acquire while B is paused just before releasing deletion,
    or simulate B's crash at that exact boundary. NO custody substitution occurs.
    """
    root, locks = make_workspace(tmp_path)
    b = Workspace.open(root, writable=True)
    b_token = b._lock_token
    real_rev = b._current_rev
    real_unlink = Path.unlink
    dispatched = []
    successor = []

    def at_release_delete(p, *args, **kwargs):
        if p.name.startswith(".releasing-") and not dispatched:
            dispatched.append(p.name)
            assert p.exists()
            if crash:
                raise SystemExit("simulated release crash after move")
            with Workspace.open(root, writable=True) as c:
                successor.append(c._lock_token)
                put_c(c)
        return real_unlink(p, *args, **kwargs)

    def after_preflight(kind, record_id):
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", at_release_delete)
            if crash:
                with pytest.raises(SystemExit, match="simulated release crash"):
                    b._unlink_own_lock(locks / "writer.lock")
            else:
                b._unlink_own_lock(locks / "writer.lock")
        if crash:
            # Fresh opener must discover the abandoned .releasing-* evidence.
            with Workspace.open(root, writable=True) as c:
                successor.append(c._lock_token)
                put_c(c)
        return real_rev(kind, record_id)

    b._current_rev = after_preflight
    try:
        try:
            put_b(b)
        except LockLostError:
            pass   # a physically published but fenced B entry also meets the invariant
        assert dispatched, "the real release method never reached the paused unlink"
        assert len(successor) == 1
    finally:
        b._lock_token = None  # B is deliberately stale; do not mutate successor's lock
        b.close()
    return root, b_token, successor[0]


def test_real_release_pause_fences_preflighted_writer(tmp_path, monkeypatch):
    root, tok_b, tok_c = _run_protocol_release_interleaving(tmp_path, monkeypatch, False)
    found = effective(root, tok_b, tok_c)
    assert found["revoked_B"], "C obtained authority before B was durably revoked"
    assert found["C"] and not found["B"], "B and C both became effective"
    assert found["journal_C"]


def test_release_crash_restart_fences_preflighted_writer(tmp_path, monkeypatch):
    root, tok_b, tok_c = _run_protocol_release_interleaving(tmp_path, monkeypatch, True)
    found = effective(root, tok_b, tok_c)
    assert found["revoked_B"] and found["C"] and not found["B"]
    # Repeat startup on a *new* Workspace instance. Revocation stays durable.
    with Workspace.open(root, writable=True) as again:
        assert tok_b in Workspace._revoked_tokens(again.meta_dir)
        assert Workspace.read_writer_lock(root)["token"] == again._lock_token
    assert effective(root, tok_b, tok_c)["revoked_B"]


def test_admin_clear_reason_conflict_rejected_then_identical_restart_recovers(tmp_path, monkeypatch):
    root, locks = make_workspace(tmp_path)
    name = ".releasing-" + "e" * 24
    orphan = locks / name
    payload = b"{corrupted previous writer token"
    orphan.write_bytes(payload)
    def simulated_crash(cls, meta, auth):
        raise SystemExit("crash after durable authorisation")
    with monkeypatch.context() as patch:
        patch.setattr(Workspace, "_drive_orphan_clear", classmethod(simulated_crash))
        with pytest.raises(SystemExit, match="crash after durable"):
            Workspace.clear_orphaned_release(root, name, "previous writer confirmed stopped")
    with Workspace.open(root) as reader:
        before = reader.head_seq()
    with pytest.raises(OrphanClearConflict):
        Workspace.clear_orphaned_release(root, name, "different administrator reason")
    with Workspace.open(root) as reader:
        assert reader.head_seq() == before, "conflict silently published a second decision"
    resolved = Workspace.clear_orphaned_release(root, name, "previous writer confirmed stopped")
    assert resolved["outcome"] == "completed" and resolved["adopted"]
    evidence = locks / resolved["evidence"]
    assert evidence.read_bytes() == payload
    with Workspace.open(root) as reader:
        auths, results = Workspace._orphan_clear_state(reader.meta_dir, reader)
    matching = [a for a in auths if a["file"] == name]
    assert len(matching) == 1
    assert matching[0]["file_sha256"] == hashlib.sha256(payload).hexdigest()
    assert list(results) == [resolved["clear_id"]]


def test_verify_detects_tampered_and_missing_archived_orphan_evidence(tmp_path):
    root, locks = make_workspace(tmp_path)
    name = ".releasing-" + "f" * 24
    (locks / name).write_bytes(b"{bad old lock")
    result = Workspace.clear_orphaned_release(root, name, "operator confirmed stopped")
    assert result["outcome"] == "completed"
    evidence = locks / result["evidence"]
    original = evidence.read_bytes()
    with Workspace.open(root) as reader:
        assert not any(p["problem"].startswith("orphan-evidence") for p in reader.verify()["problems"])
    evidence.write_bytes(original + b"tamper")
    with Workspace.open(root) as reader:
        assert any(p["problem"] == "orphan-evidence-altered" for p in reader.verify()["problems"])
    evidence.unlink()
    with Workspace.open(root) as reader:
        assert any(p["problem"] == "orphan-evidence-missing" for p in reader.verify()["problems"])
