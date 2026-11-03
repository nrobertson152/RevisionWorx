# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent QA merge gate for core 22a5273, compared with fe8a179.

Exercises Windows/POSIX file atomicity, post-journal crashes and txid request
identity, and ADR-022's journal authority against late stale materialisation.
All workspaces use disposable local paths. No production files are modified.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("QA branch base does not include Phase 2", allow_module_level=True)

from revisionworx import atomicio
from revisionworx.workspace import (
    CommittedNotApplied, LockLostError, RecordCheck, RecordPut, TxidReuseError, Workspace,
)

RID = "root_" + "d" * 32
TXID = "a" * 32


@pytest.mark.parametrize("windows, failures, attempts", [
    (False, 0, 1), (False, 2, 1), (True, 0, 1), (True, 2, 3),
])
def test_sharing_retry_scope_and_single_success(windows, failures, attempts, monkeypatch):
    """On POSIX a genuine permission denial fails immediately; on Windows sharing retries are bounded."""
    monkeypatch.setattr(atomicio, "IS_WINDOWS", windows)
    monkeypatch.setattr(atomicio, "SHARING_RETRIES", 4)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    called = []

    def attempt():
        called.append(1)
        if len(called) <= failures:
            raise PermissionError("injected contention")
        return {"ok": True}

    if not windows and failures:
        with pytest.raises(PermissionError, match="contention"):
            atomicio.retry_sharing(attempt)
    else:
        assert atomicio.retry_sharing(attempt) == {"ok": True}
    assert len(called) == attempts


@pytest.mark.parametrize("windows, expected", [(False, 1), (True, 4)])
def test_sharing_retry_permanent_failure_preserves_cause(windows, expected, monkeypatch):
    monkeypatch.setattr(atomicio, "IS_WINDOWS", windows)
    monkeypatch.setattr(atomicio, "SHARING_RETRIES", 4)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    calls, sentinel = [], PermissionError("permission not granted")

    def denied():
        calls.append(True)
        raise sentinel

    with pytest.raises(PermissionError) as caught:
        atomicio.retry_sharing(denied)
    assert caught.value is sentinel
    assert len(calls) == expected


def test_atomic_replace_failure_is_non_destructive_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_bytes(b"old-state")
    real_replace = os.replace
    monkeypatch.setattr(atomicio, "IS_WINDOWS", False)

    def fail_replacement(src, dst, *args, **kwargs):
        if Path(dst) == target:
            raise PermissionError("destination busy")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replacement)
    with pytest.raises(PermissionError, match="destination busy"):
        atomicio.atomic_write_bytes(target, b"new-state")
    assert target.read_bytes() == b"old-state"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_windows_simulated_atomic_replace_retries_then_publishes_once(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(atomicio, "IS_WINDOWS", True)
    monkeypatch.setattr(atomicio, "SHARING_RETRIES", 5)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    real_replace, calls = os.replace, []

    def replace(src, dst, *args, **kwargs):
        if Path(dst) == target:
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError("simulated Windows sharing denial")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    atomicio.atomic_write_bytes(target, b"new")
    assert len(calls) == 3
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_atomic_exclusive_publish_keeps_existing_bytes_and_cleans_temp(tmp_path):
    target = tmp_path / "journal.json"
    atomicio.publish_exclusive(target, b"first-entry")
    with pytest.raises(atomicio.ExclusiveCreateError):
        atomicio.publish_exclusive(target, b"different-entry")
    assert target.read_bytes() == b"first-entry"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["journal.json"]


def test_postpublish_txid_recovers_once_and_rejects_changed_recordcheck(tmp_path):
    root = tmp_path / "new transaction"
    with Workspace.create(root, "QA transaction"):
        pass
    ops = [
        RecordCheck("settings", "workspace", 1),
        RecordPut("root", RID, {"name": "initial"}, expect_rev=0),
    ]
    with Workspace.open(root, writable=True) as writer:
        original = writer._apply

        def crash_after_durable_publish(entry):
            raise OSError("injected crash after durable journal entry")

        writer._apply = crash_after_durable_publish
        with pytest.raises(CommittedNotApplied) as exc:
            writer.commit("qa.tx", ops, txid=TXID)
        seq = exc.value.seq
        assert exc.value.txid == TXID
        writer._apply = original
        assert writer.transaction_outcome(TXID) == {"state": "committed", "seq": seq}
        assert writer.head_seq() == seq

    with Workspace.open(root, writable=True) as reopened:
        assert reopened.get("root", RID)["name"] == "initial"
        assert reopened.commit("qa.tx", ops, txid=TXID) == seq
        assert reopened.head_seq() == seq, "identical txid retry must not create another entry"
        changed_check = [
            RecordCheck("settings", "workspace", 0),
            RecordPut("root", RID, {"name": "initial"}, expect_rev=0),
        ]
        with pytest.raises(TxidReuseError):
            reopened.commit("qa.tx", changed_check, txid=TXID)
        with pytest.raises(TxidReuseError):
            reopened.commit("qa.tx", list(reversed(ops)), txid=TXID)
        assert reopened.head_seq() == seq
        assert reopened.transaction_outcome(TXID) == {"state": "committed", "seq": seq}


def test_stale_effective_materialisation_cannot_override_new_journal_view(tmp_path, monkeypatch):
    """B's real journal commit is effective, but B's delayed _apply runs after C updates the same record.

    Baseline fe8a179 returned stale B bytes from records/; new core must return C per journal,
    even when the physical file has been overwritten by late B materialisation.
    """
    root = tmp_path / "stale cache after token break"
    with Workspace.create(root, "QA materialisation"):
        pass
    with Workspace.open(root, writable=True) as initial:
        first = initial.commit("qa.initial", [
            RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
    b = Workspace.open(root, writable=True)
    txid = "b" * 32
    original_apply = b._apply

    def fail_after_publishing(entry):
        raise OSError("after journal publish before materialisation")

    b._apply = fail_after_publishing
    try:
        with pytest.raises(CommittedNotApplied) as pending:
            b.commit("qa.B", [
                RecordPut("root", RID, {"name": "B"}, expect_rev=first)], txid=txid)
        b._apply = original_apply
        b_seq = pending.value.seq
        assert b.transaction_outcome(txid) == {"state": "committed", "seq": b_seq}

        Workspace.break_writer_lock(root, b._lock_token, "B was stopped for QA handoff")
        with Workspace.open(root, writable=True) as c:
            assert c.get("root", RID)["name"] == "B"
            c_seq = c.commit("qa.C", [
                RecordPut("root", RID, {"name": "C"}, expect_rev=b_seq)])
            assert c.get("root", RID)["name"] == "C"
            # Simulate the preflighted writer's delayed cache-only _apply; journal B remains effective.
            # The update is NOT an externally fabricated file mutation.
            b._apply(b.read_entry(b_seq))
            cached = atomicio.read_json(c.meta_dir / "records" / "root" / f"{RID}.json")
            assert cached["name"] == "B", "fault must genuinely overwrite the newer materialisation"
            assert c.get("root", RID) == {"name": "C", "_rev": c_seq}
            assert c.list("root")[RID] == {"name": "C", "_rev": c_seq}
            assert c.transaction_outcome(txid) == {"state": "committed", "seq": b_seq}
            assert any(p["problem"] == "mismatch" and p.get("id") == RID
                       for p in c.verify()["problems"])
            # recover() may repair the cache before verify(repair=True) evaluates its problems.
            repaired = c.verify(repair=True)
            assert repaired["problems"] == [] or repaired["repaired"] is True
            assert c.get("root", RID)["name"] == "C"
        with Workspace.open(root) as reader:
            assert reader.get("root", RID)["name"] == "C"
            assert reader.list("root")[RID]["name"] == "C"
    finally:
        b._lock_token = None   # ownership was journal-revoked; do not touch C's successor lock
        b.close()
