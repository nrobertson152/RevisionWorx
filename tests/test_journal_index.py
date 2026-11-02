# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-020: incremental in-memory journal index (txid lookup, revocations, head probing).

The index is derived, per handle, never persisted and never authoritative; these tests show it can
neither invent nor hide a transaction outcome, and that per-commit work no longer grows with N."""
import shutil
import uuid

import pytest

from revisionworx import atomicio
from revisionworx.authority import classify
from revisionworx.workspace import (LockLostError, RecordPut, TxidReuseError, Workspace,
                                    WorkspaceCorruptError)

A = "prj_" + "a" * 32


def _grow(ws, n):
    for i in range(n):
        ws.commit("g", [RecordPut("project", f"prj_{i:032x}", {"i": i})], txid=uuid.uuid4().hex)


def _count_reads(monkeypatch, ws):
    calls = {"n": 0}
    real = ws.read_entry

    def counted(seq):
        calls["n"] += 1
        return real(seq)
    monkeypatch.setattr(ws, "read_entry", counted)
    return calls


@pytest.mark.parametrize("n", [5, 400])
def test_per_commit_journal_reads_do_not_grow_with_journal(share, monkeypatch, n):
    ws = Workspace.create(share, "W")
    _grow(ws, n)
    calls = _count_reads(monkeypatch, ws)
    for k in range(10):
        ws.commit("c", [RecordPut("project", A, {"k": k})], txid=uuid.uuid4().hex)
    per_commit = calls["n"] / 10
    assert per_commit <= 12, f"{per_commit} journal reads per commit at N={n}"  # was ~2N before ADR-020
    ws.close()


def test_repeated_lookup_duplicate_and_conflicting_retry(share):
    ws = Workspace.create(share, "W")
    _grow(ws, 50)
    ops = [RecordPut("project", A, {"v": 1}, expect_rev=0)]
    s = ws.commit("t", ops, txid="1" * 32)
    for _ in range(5):
        assert ws.transaction_outcome("1" * 32) == {"state": "committed", "seq": s}
    head = ws.head_seq()
    assert ws.commit("t", ops, txid="1" * 32) == s and ws.head_seq() == head
    with pytest.raises(TxidReuseError) as info:
        ws.commit("t", [RecordPut("project", A, {"v": 2})], txid="1" * 32)
    assert classify(info.value) == "txid-reuse"
    assert ws.transaction_outcome("2" * 32) == {"state": "not-found"}
    ws.close()


def test_retry_after_restart_uses_a_fresh_index(share):
    ws = Workspace.create(share, "W")
    ops = [RecordPut("project", A, {"v": 1})]
    s = ws.commit("t", ops, txid="3" * 32)
    ws.close()
    with Workspace.open(share, writable=True) as w2:  # new process/handle: index rebuilt from journal
        assert w2.commit("t", ops, txid="3" * 32) == s
        assert w2.transaction_outcome("3" * 32)["seq"] == s


def test_concurrent_advancement_by_another_handle_is_seen(share):
    ws = Workspace.create(share, "W")
    reader = Workspace.open(share)
    assert reader.transaction_outcome("4" * 32)["state"] == "not-found"  # reader indexes now
    s = ws.commit("t", [RecordPut("project", A, {"v": 1})], txid="4" * 32)
    assert reader.transaction_outcome("4" * 32) == {"state": "committed", "seq": s}  # extended
    assert reader.head_seq() == ws.head_seq()
    reader.close()
    ws.close()


def test_stale_or_corrupted_index_cannot_invent_or_hide_outcomes(share):
    ws = Workspace.create(share, "W")
    s = ws.commit("t", [RecordPut("project", A, {"v": 1})], txid="5" * 32)
    ws._journal_index()
    ws._jidx["txids"]["6" * 32] = s           # corrupted: claims a txid that was never committed
    assert ws.transaction_outcome("6" * 32) == {"state": "not-found"}
    ws._journal_index()
    del ws._jidx["txids"]["5" * 32]           # corrupted: hides a committed txid...
    ws._jidx["seq"] = 0                       # ...combined with a reset watermark: rebuilt from journal
    assert ws.transaction_outcome("5" * 32)["state"] == "committed"
    ws._jidx = None                           # missing index
    assert ws.commit("t", [RecordPut("project", A, {"v": 1})], txid="5" * 32) == s
    ws._journal_index()
    ws._jidx["seq"] = ws.head_seq() + 50      # index claims more than the journal holds: discarded
    assert ws.transaction_outcome("5" * 32)["seq"] == s
    ws.close()


def test_interrupted_index_construction_resumes_correctly(share, monkeypatch):
    ws = Workspace.create(share, "W")
    _grow(ws, 20)
    ws._jidx = None
    real, n = ws.read_entry, {"c": 0}

    def flaky(seq):
        n["c"] += 1
        if n["c"] == 7:
            raise WorkspaceCorruptError("simulated read failure mid-build")
        return real(seq)
    monkeypatch.setattr(ws, "read_entry", flaky)
    with pytest.raises(WorkspaceCorruptError):
        ws._journal_index()
    partial = ws._jidx["seq"]
    assert 0 < partial < ws.head_seq()        # watermark only covers fully indexed entries
    monkeypatch.undo()
    idx = ws._journal_index()                 # resumes from the watermark
    assert idx["seq"] == ws.head_seq() and len(idx["txids"]) == ws.head_seq()  # every entry, exactly once
    ws.close()


def test_restored_journal_of_equal_length_invalidates_index(share, tmp_path):
    ws = Workspace.create(share, "W")
    ws.commit("t", [RecordPut("project", A, {"v": 1})], txid="7" * 32)
    ws.close()
    backup = tmp_path / "backup"
    shutil.copytree(share / ".revisionworx", backup)
    w = Workspace.open(share, writable=True)
    w.commit("t", [RecordPut("project", A, {"v": 2})], txid="8" * 32)
    w.close()
    reader = Workspace.open(share)
    assert reader.transaction_outcome("8" * 32)["state"] == "committed"
    # replace the journal with a different history of the same length (restore + new divergent entry)
    shutil.rmtree(share / ".revisionworx")
    shutil.copytree(backup, share / ".revisionworx")
    with Workspace.open(share, writable=True) as w2:
        w2.commit("t", [RecordPut("project", A, {"v": 3})], txid="9" * 32)
    assert reader.transaction_outcome("8" * 32) == {"state": "not-found"}  # not served from stale index
    assert reader.transaction_outcome("9" * 32)["state"] == "committed"
    reader.close()


def test_missing_entry_is_reported_not_skipped(share):
    ws = Workspace.create(share, "W")
    _grow(ws, 3)
    ws._jidx = None
    head = ws.head_seq()
    victim = share / ".revisionworx" / "journal" / f"{head - 1:020d}.json"
    victim.unlink()
    with pytest.raises(WorkspaceCorruptError, match="missing"):
        ws.transaction_outcome("f" * 32)
    ws._lock_token = None
    ws.close()


def test_revocation_still_fences_through_the_index(share):
    ws = Workspace.create(share, "W")
    ws._journal_index()                        # index built before the revocation exists
    Workspace.break_writer_lock(share, ws._lock_token, reason="test")
    with pytest.raises(LockLostError) as info:
        ws.commit("t", [RecordPut("project", A, {"v": 1})])
    assert classify(info.value) == "fenced"
    ws._lock_token = None
    ws.close()


def test_head_probe_falls_back_after_journal_shrinks(share, tmp_path):
    ws = Workspace.create(share, "W")
    _grow(ws, 5)
    ws.close()
    reader = Workspace.open(share)
    high = reader.head_seq()
    jdir = share / ".revisionworx" / "journal"
    for s in range(high, high - 3, -1):         # simulate restore of an older, shorter journal
        (jdir / f"{s:020d}.json").unlink()
    atomicio.atomic_write_json(share / ".revisionworx" / "state.json", {"applied_seq": high - 3})
    assert reader.head_seq() == high - 3
    reader.close()
