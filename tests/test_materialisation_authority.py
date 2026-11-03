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
                                    RecordPut, Workspace, WorkspaceCorruptError)

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
