# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-022: a late write from a stale writer cannot change what any reader or writer decides.

Found while preparing core integration (PR #7). Writer B publishes journal entry N and pauses before
materialising it. An administrator revokes B and C takes over and updates the same record. B then
resumes: its publish was effective, so it writes N's now-outdated data over C's record file. No file
primitive can stop that write (the check and the write are never atomic), so the record files must not
be what decisions are based on. They are a cache of the journal.

Each test states what would go wrong against the journal, the only authority.
"""
import pytest

from revisionworx import atomicio
from revisionworx.workspace import (REV_FIELD, CommittedNotApplied, ConflictError, LockLostError, RecordDelete,
                                    RecordPut, Workspace, WorkspaceCorruptError, entry_digest)

RID = "root_" + "a" * 32
OTHER = "root_" + "b" * 32


class Died(BaseException):
    pass


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "w"
    with Workspace.create(root, "t"):
        pass
    return root


def paused_after_publish(root, op_item):
    """B with an effective journal entry that is not yet materialised, then revoked."""
    b = Workspace.open(root, writable=True)
    real = b._apply
    held = []

    def hold(entry):
        held.append(entry)
        raise Died
    b._apply = hold
    with pytest.raises(CommittedNotApplied):
        b.commit("t.b", [op_item])
    b._apply = real
    Workspace.break_writer_lock(root, b._lock_token, "B unresponsive")
    return b, held[0]


def late_apply(b, entry, monkeypatch, die_before_state):
    """B resumes materialising. Optionally dies between the record write and the state write."""
    with monkeypatch.context() as patch:
        if die_before_state:
            real = atomicio.atomic_write_json

            def write(path, obj):
                if path.name == "state.json":
                    raise Died
                return real(path, obj)
            patch.setattr(atomicio, "atomic_write_json", write)
            with pytest.raises(Died):
                b._apply(entry)
        else:
            b._apply(entry)


@pytest.mark.parametrize("dies", [True, False], ids=["crash-before-state-write", "no-crash"])
def test_late_put_cannot_change_reads(root, monkeypatch, dies):
    b, entry = paused_after_publish(root, RecordPut("root", RID, {"name": "B"}, expect_rev=0))
    with Workspace.open(root, writable=True) as c:
        rev = c.get("root", RID)[REV_FIELD]
        seq = c.commit("t.c", [RecordPut("root", RID, {"name": "C"}, expect_rev=rev)])
        late_apply(b, entry, monkeypatch, dies)
        assert c.get("root", RID) == {"name": "C", REV_FIELD: seq}
        assert c.list("root")[RID] == {"name": "C", REV_FIELD: seq}
    with Workspace.open(root) as reader:
        assert reader.get("root", RID) == {"name": "C", REV_FIELD: seq}
        assert reader.list("root")[RID] == {"name": "C", REV_FIELD: seq}


def test_late_put_cannot_cause_a_lost_update(root, monkeypatch):
    b, entry = paused_after_publish(root, RecordPut("root", RID, {"name": "B"}, expect_rev=0))
    with Workspace.open(root, writable=True) as c:
        first_rev = c.get("root", RID)[REV_FIELD]
        c.commit("t.c", [RecordPut("root", RID, {"name": "C"}, expect_rev=first_rev)])
        late_apply(b, entry, monkeypatch, die_before_state=True)
    with Workspace.open(root, writable=True) as d:
        with pytest.raises(ConflictError):      # based on the outdated B revision: must be refused
            d.commit("t.d", [RecordPut("root", RID, {"name": "D"}, expect_rev=first_rev)])
        current = d.get("root", RID)
        assert current["name"] == "C"
        d.commit("t.d", [RecordPut("root", RID, {"name": "D"}, expect_rev=current[REV_FIELD])])
        assert d.get("root", RID)["name"] == "D"


def test_late_put_cannot_resurrect_a_deleted_record(root, monkeypatch):
    b, entry = paused_after_publish(root, RecordPut("root", RID, {"name": "B"}, expect_rev=0))
    with Workspace.open(root, writable=True) as c:
        rev = c.get("root", RID)[REV_FIELD]
        c.commit("t.c", [RecordDelete("root", RID, expect_rev=rev)])
        late_apply(b, entry, monkeypatch, die_before_state=True)
        assert c.get("root", RID) is None
        assert RID not in c.list("root")


def test_late_delete_cannot_remove_a_newer_record(root, monkeypatch):
    with Workspace.open(root, writable=True) as a:
        rev = a.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
    b, entry = paused_after_publish(root, RecordDelete("root", RID, expect_rev=rev))
    with Workspace.open(root, writable=True) as c:
        seq = c.commit("t.c", [RecordPut("root", RID, {"name": "C"}, expect_rev=0)])
        late_apply(b, entry, monkeypatch, die_before_state=True)
        assert c.get("root", RID) == {"name": "C", REV_FIELD: seq}
        assert c.list("root")[RID]["name"] == "C"


def test_verify_still_reports_and_repair_restores_the_files(root, monkeypatch):
    """Reads no longer depend on the files, but a damaged cache must stay visible and repairable."""
    b, entry = paused_after_publish(root, RecordPut("root", RID, {"name": "B"}, expect_rev=0))
    with Workspace.open(root, writable=True) as c:
        rev = c.get("root", RID)[REV_FIELD]
        seq = c.commit("t.c", [RecordPut("root", RID, {"name": "C"}, expect_rev=rev)])
        late_apply(b, entry, monkeypatch, die_before_state=True)
        assert any(p["problem"] == "mismatch" and p["id"] == RID for p in c.verify()["problems"])
        assert c.verify(repair=True)["repaired"] is True
        file = c.meta_dir / "records" / "root" / f"{RID}.json"
        assert atomicio.read_json(file) == {"name": "C", REV_FIELD: seq}


def test_lost_writer_does_not_materialise_before_learning_it_lost(root):
    """commit() must check its lock before replaying, so a stale handle does no record writes."""
    b, entry = paused_after_publish(root, RecordPut("root", RID, {"name": "B"}, expect_rev=0))
    with Workspace.open(root, writable=True) as c:
        rev = c.get("root", RID)[REV_FIELD]
        c.commit("t.c", [RecordPut("root", RID, {"name": "C"}, expect_rev=rev)])
        atomicio.atomic_write_json(c.meta_dir / "state.json", {"applied_seq": 0})  # B's late state write
        replayed = []
        b._apply = replayed.append
        with pytest.raises(LockLostError):
            b.commit("t.b2", [RecordPut("root", OTHER, {"name": "B2"}, expect_rev=0)])
        assert replayed == []


def test_fenced_entries_never_reach_the_view(root):
    b = Workspace.open(root, writable=True)
    real = b._current_rev

    def revoked_just_after_preflight(kind, rid):
        Workspace.break_writer_lock(root, b._lock_token, "B unresponsive")
        b._current_rev = real
        return real(kind, rid)
    b._current_rev = revoked_just_after_preflight
    with pytest.raises(LockLostError):
        b.commit("t.b", [RecordPut("root", RID, {"name": "B"}, expect_rev=0)])
    with Workspace.open(root) as reader:
        assert reader.head_seq() > 2             # the late entry physically exists...
        assert reader.get("root", RID) is None   # ...and is not part of any view
        assert RID not in reader.list("root")


def test_view_follows_a_journal_restored_shorter(root):
    """A long-lived handle must not keep serving entries a restored journal no longer contains."""
    with Workspace.open(root, writable=True) as w:
        seq = w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
        assert w.get("root", RID)["name"] == "A"
        (w.meta_dir / "journal" / f"{seq:020d}.json").unlink()
        (w.meta_dir / "records" / "root" / f"{RID}.json").unlink()
        assert w.get("root", RID) is None


def test_each_new_entry_is_read_once_on_a_long_lived_handle(root, monkeypatch):
    """The view is incremental: reads do not rescan the journal (ADR-020 measured that as O(n^2))."""
    with Workspace.open(root, writable=True) as w:
        for i in range(5):
            w.commit("t.a", [RecordPut("root", "root_" + f"{i:032x}", {"name": str(i)}, expect_rev=0)])
        w.get("root", RID)
        reads = []
        real = w.read_entry
        monkeypatch.setattr(w, "read_entry", lambda s: (reads.append(s), real(s))[1])
        w.get("root", RID)
        w.get("root", RID)
        assert len(reads) <= 2                   # at most the anchor check for each call


def _counting(monkeypatch, ws):
    """Count what reads cost: listings of the journal, and entries read."""
    calls = {"listed": 0, "read": []}
    real_seqs, real_read = ws.journal_seqs, ws.read_entry

    def seqs():
        calls["listed"] += 1
        return real_seqs()
    monkeypatch.setattr(ws, "journal_seqs", seqs)
    monkeypatch.setattr(ws, "read_entry", lambda s: (calls["read"].append(s), real_read(s))[1])
    return calls


def test_reads_on_an_open_handle_neither_list_the_journal_nor_read_its_last_entry_again(root, monkeypatch):
    """Was: every get() listed the journal and read its last entry again to check it hadn't been restored. After one large
    entry (an import of 2,000 units is one entry of 6,000 records), each read cost as much as reading that entry, so
    `unit import` of 2,000 units took 97 s and `unit verify` 181 s, against 1.3 s and 1.9 s for 200; a listing per read
    also grew with every entry. Nothing has changed, so nothing needs reading."""
    with Workspace.open(root, writable=True) as w:
        for i in range(30):
            w.commit("t.a", [RecordPut("root", "root_" + f"{i:032x}", {"name": str(i)}, expect_rev=0)])
        w.commit("t.big", [RecordPut("root", "root_" + f"{10**6 + i:032x}", {"name": "x" * 40}, expect_rev=0)
                           for i in range(2000)])
        with Workspace.open(root) as reader:
            assert reader.get("root", "root_" + f"{3:032x}")["name"] == "3"   # the view is built once
            calls = _counting(monkeypatch, reader)
            for _ in range(50):
                assert reader.get("root", "root_" + f"{3:032x}")["name"] == "3"
            assert len(reader.list("root")) == 2030
            assert reader.transaction_outcome("no-such-txid") == {"state": "not-found"}
            assert calls == {"listed": 0, "read": []}


def test_an_open_handle_still_sees_each_commit_by_another_handle(root, monkeypatch):
    """What is published after the view is read is still found, by name, and read once."""
    with Workspace.open(root) as reader, Workspace.open(root, writable=True) as w:
        w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
        assert reader.get("root", RID)["name"] == "A"
        calls = _counting(monkeypatch, reader)
        seq = w.commit("t.a", [RecordPut("root", RID, {"name": "B"}, expect_rev=seq_of(reader, RID)),
                               RecordPut("root", OTHER, {"name": "O"}, expect_rev=0)])
        assert reader.get("root", RID)["name"] == "B"
        assert reader.get("root", OTHER)["name"] == "O"
        assert set(reader.list("root")) == {RID, OTHER}
        assert calls["read"] == [seq]            # the new entry, once; the one before it isn't read again
        w.commit("t.d", [RecordDelete("root", OTHER, expect_rev=seq)])
        assert reader.get("root", OTHER) is None
        assert set(reader.list("root")) == {RID}


def seq_of(ws, rid):
    return ws.get("root", rid)[REV_FIELD]


def test_an_open_handle_notices_its_last_entry_replaced_by_a_restore(root, tmp_path):
    """A restore that puts a different entry where the view's last one was: the view is thrown away, as before, without
    the journal getting any longer."""
    import shutil
    with Workspace.open(root, writable=True) as w:
        w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
    backup = tmp_path / "backup"
    shutil.copytree(root / ".revisionworx", backup)
    with Workspace.open(root, writable=True) as w:
        w.commit("t.a", [RecordPut("root", RID, {"name": "B"}, expect_rev=seq_of(w, RID))])
    with Workspace.open(root) as reader:
        assert reader.get("root", RID)["name"] == "B"
        head = reader.head_seq()
        shutil.rmtree(root / ".revisionworx")
        shutil.copytree(backup, root / ".revisionworx")
        with Workspace.open(root, writable=True) as w:
            w.commit("t.restored", [RecordPut("root", RID, {"name": "C, after the restore"}, expect_rev=seq_of(w, RID))])
            assert w.head_seq() == head          # the same length: only the last entry tells them apart
        assert reader.get("root", RID)["name"] == "C, after the restore"


def test_a_commit_reads_the_last_entry_again_whatever_its_file_says(root, monkeypatch):
    """Reads trust an unchanged file; a write doesn't. Before deciding anything, commit lists the journal and reads the
    last entry again, so a gap or damage stops it as before."""
    with Workspace.open(root, writable=True) as w:
        w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
        w.get("root", RID)
        calls = _counting(monkeypatch, w)
        seq = w.commit("t.a", [RecordPut("root", OTHER, {"name": "O"}, expect_rev=0)])
        assert calls["listed"] >= 1 and seq - 1 in calls["read"]


def test_a_gap_after_the_view_stops_the_next_commit(root):
    """Reads walk forward by name, so they don't list the journal; the next commit does, and refuses to write past a gap."""
    with Workspace.open(root, writable=True) as w:
        w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
        assert w.get("root", RID)["name"] == "A"
        head = w.head_seq()
        journal = root / ".revisionworx" / "journal"
        gap = atomicio.read_json(journal / f"{head:020d}.json")
        gap["seq"] = head + 2
        gap["digest"] = entry_digest({k: v for k, v in gap.items() if k != "digest"})   # whole and valid, past a gap
        atomicio.atomic_write_json(journal / f"{head + 2:020d}.json", gap)
        with pytest.raises(WorkspaceCorruptError, match="gaps"):
            w.commit("t.a", [RecordPut("root", OTHER, {"name": "O"}, expect_rev=0)])
        assert not (journal / f"{head + 3:020d}.json").exists()


def _tamper_last_entry(root):
    entry = sorted((root / ".revisionworx" / "journal").glob("*.json"))[-1]
    data = atomicio.read_json(entry)
    data["op"] = "tampered"
    atomicio.atomic_write_json(entry, data)


def test_damaged_journal_keeps_reads_and_refuses_writes(root):
    """CORE_API recovery-blocked contract. Found by testing this core against the desktop-alpha GUI."""
    with Workspace.open(root, writable=True) as w:
        w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
        assert w.get("root", RID)["name"] == "A"
        _tamper_last_entry(root)
        assert w.get("root", RID)["name"] == "A"
        assert w.list("root")[RID]["name"] == "A"
        with pytest.raises(WorkspaceCorruptError):
            w.commit("t.b", [RecordPut("root", OTHER, {"name": "B"}, expect_rev=0)])
        assert w.get("root", OTHER) is None
    with Workspace.open(root) as reader:         # a fresh handle too, with no earlier view to lean on
        assert reader.get("root", RID)["name"] == "A"
        assert reader.list("root")[RID]["name"] == "A"


def test_damaged_journal_with_unapplied_entries_refuses_reads(root):
    """Files alone would be behind the journal here, so answering from them would be wrong."""
    w = Workspace.open(root, writable=True)
    w._apply = lambda entry: (_ for _ in ()).throw(Died())
    with pytest.raises(CommittedNotApplied):
        w.commit("t.a", [RecordPut("root", RID, {"name": "A"}, expect_rev=0)])
    _tamper_last_entry(root)
    with Workspace.open(root) as reader, pytest.raises(WorkspaceCorruptError):
        reader.get("root", RID)
    w._lock_token = None
    w.close()
