# SPDX-License-Identifier: GPL-3.0-or-later
import multiprocessing as mp
import os

import pytest

import proc_helpers
from revisionworx import atomicio
from revisionworx.workspace import (FORMAT_VERSION, CommittedNotApplied, ReadOnlyError, RecordPut, Workspace,
                                    WorkspaceCorruptError, WorkspaceError, WorkspaceFormatError,
                                    WorkspaceLockedError)


def test_create_refuses_existing(share):
    Workspace.create(share, "A").close()
    with pytest.raises(WorkspaceError):
        Workspace.create(share, "B")


def test_newer_format_is_refused_without_changes(share):
    Workspace.create(share, "A").close()
    info_path = share / ".revisionworx" / "workspace.json"
    info = atomicio.read_json(info_path)
    info["format_version"] = FORMAT_VERSION + 1
    atomicio.atomic_write_json(info_path, info)
    before = sorted(p.name for p in (share / ".revisionworx" / "journal").iterdir())
    with pytest.raises(WorkspaceFormatError):
        Workspace.open(share, writable=True)
    assert sorted(p.name for p in (share / ".revisionworx" / "journal").iterdir()) == before
    assert Workspace.read_writer_lock(share) is None


def test_commit_and_read_back(share):
    with Workspace.create(share, "A") as ws:
        seq = ws.commit("t", [RecordPut("project", "prj_" + "a" * 32, {"name": "x"})])
        assert ws.get("project", "prj_" + "a" * 32) == {"name": "x", "_rev": seq}
        assert ws.head_seq() == seq == ws.applied_seq()
    with Workspace.open(share) as ro:
        assert ro.get("project", "prj_" + "a" * 32) == {"name": "x", "_rev": seq}
        with pytest.raises(ReadOnlyError):
            ro.commit("t", [])


def test_invalid_record_ids_and_kinds_rejected(share):
    with Workspace.create(share, "A") as ws:
        for bad in ("../x", "A B", "x/y"):
            with pytest.raises(ValueError):
                ws.commit("t", [RecordPut("project", bad, {})])
        with pytest.raises(ValueError):
            ws.commit("t", [RecordPut("bogus", "abc", {})])


def test_single_writer_lock(share):
    ws = Workspace.create(share, "A")
    with pytest.raises(WorkspaceLockedError) as info:
        Workspace.open(share, writable=True)
    assert info.value.holder["token"]
    with Workspace.open(share) as reader:  # readers are not blocked
        assert reader.name == "A"
    ws.close()
    assert Workspace.read_writer_lock(share) is None
    Workspace.open(share, writable=True).close()


def test_writer_lock_race_between_processes(share):
    Workspace.create(share, "A").close()
    ctx = mp.get_context("spawn")
    n = 6
    barrier, release, results = ctx.Barrier(n), ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=proc_helpers.race_open_writable,
                         args=(str(share), barrier, release, results)) for _ in range(n)]
    for p in procs:
        p.start()
    outcomes = [results.get(timeout=60) for _ in range(n)]
    release.set()
    for p in procs:
        p.join(60)
    assert outcomes.count("acquired") == 1
    assert outcomes.count("locked") == n - 1
    assert Workspace.read_writer_lock(share) is None


def test_close_does_not_remove_someone_elses_lock(share):
    ws = Workspace.create(share, "A")
    lock = share / ".revisionworx" / "locks" / "writer.lock"
    foreign = {**atomicio.read_json(lock), "token": "someone-else"}
    atomicio.atomic_write_json(lock, foreign)
    ws.close()
    assert atomicio.read_json(lock)["token"] == "someone-else"


def test_break_lock_requires_matching_token_and_reason_and_is_audited(share):
    abandoned = Workspace.create(share, "A")  # simulate a crashed client: never closed
    holder = Workspace.read_writer_lock(share)
    with pytest.raises(WorkspaceError):
        Workspace.break_writer_lock(share, holder["token"], reason="  ")
    with pytest.raises(WorkspaceError):
        Workspace.break_writer_lock(share, "wrong-token", reason="client crashed")
    assert Workspace.read_writer_lock(share)["token"] == holder["token"]
    Workspace.break_writer_lock(share, holder["token"], reason="client crashed")
    assert Workspace.read_writer_lock(share) is None
    locks = sorted(p.name for p in (share / ".revisionworx" / "locks").iterdir())
    assert len(locks) == 1 and locks[0].startswith("writer.lock.broken-")  # evidence kept
    with Workspace.open(share) as ws:
        audits = list(ws.list("audit").values())
        assert audits[0]["event"] == "writer-lock-broken"
        assert audits[0]["previous_holder"]["token"] == holder["token"]
    abandoned._lock_token = None  # the stale handle must not delete the new state on close
    abandoned.close()


def test_crash_after_journal_before_apply_is_recovered(share, monkeypatch):
    ws = Workspace.create(share, "A")
    rid = "prj_" + "b" * 32

    def crash(entry):
        raise RuntimeError("simulated power loss")
    monkeypatch.setattr(ws, "_apply", crash)
    with pytest.raises(CommittedNotApplied):  # caller is told the change IS saved
        ws.commit("t", [RecordPut("project", rid, {"name": "survives"})])
    monkeypatch.undo()
    lock_token = Workspace.read_writer_lock(share)["token"]
    # A reader immediately sees the committed change through the journal overlay.
    with Workspace.open(share) as reader:
        assert reader.applied_seq() < reader.head_seq()
        assert reader.get("project", rid)["name"] == "survives"
        assert rid in reader.list("project")
    Workspace.break_writer_lock(share, lock_token, reason="test crash")
    with Workspace.open(share, writable=True) as w2:
        assert w2.applied_seq() == w2.head_seq()
        assert atomicio.read_json(share / ".revisionworx" / "records" / "project" / f"{rid}.json")["name"] == "survives"
    ws._lock_token = None


def test_crash_before_journal_publish_leaves_no_trace(share, monkeypatch):
    with Workspace.create(share, "A") as ws:
        head = ws.head_seq()
        real = atomicio.publish_exclusive

        def crash(path, data):
            if "journal" in str(path):
                raise OSError("disk full")
            return real(path, data)
        monkeypatch.setattr(atomicio, "publish_exclusive", crash)
        with pytest.raises(OSError):
            ws.commit("t", [RecordPut("project", "prj_" + "c" * 32, {"name": "lost"})])
        monkeypatch.undo()
        assert ws.head_seq() == head
        assert ws.get("project", "prj_" + "c" * 32) is None
        assert not [p for p in (share / ".revisionworx" / "journal").iterdir() if p.name.endswith(".tmp")]


def test_orphan_temp_files_cleaned_on_writable_open(share):
    Workspace.create(share, "A").close()
    junk = share / ".revisionworx" / "journal" / ".00000000000000000009.json.dead.tmp"
    junk.write_text("{partial")
    os.utime(junk, (1, 1))
    with Workspace.open(share, writable=True):
        pass
    assert not junk.exists()


def test_journal_gap_is_detected(share):
    with Workspace.create(share, "A") as ws:
        ws.commit("t", [RecordPut("settings", "workspace", {})])
        ws.commit("t", [RecordPut("settings", "workspace", {})])
    (share / ".revisionworx" / "journal" / f"{2:020d}.json").unlink()
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(share)


def test_corrupt_journal_entry_refuses_writes(share):
    with Workspace.create(share, "A") as ws:
        ws.commit("t", [RecordPut("settings", "workspace", {})])
        last = ws.head_seq()
    path = share / ".revisionworx" / "journal" / f"{last:020d}.json"
    path.write_text("{ not json")
    atomicio.atomic_write_json(share / ".revisionworx" / "state.json", {"applied_seq": last - 1})
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(share, writable=True)
    assert Workspace.read_writer_lock(share) is None  # failed open releases its lock


def test_state_ahead_of_journal_is_corruption(share):
    Workspace.create(share, "A").close()
    atomicio.atomic_write_json(share / ".revisionworx" / "state.json", {"applied_seq": 999})
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(share, writable=True)


def test_second_writer_collision_is_fatal_not_silent(share):
    with Workspace.create(share, "A") as ws:
        nxt = ws.head_seq() + 1
        rogue = {"seq": nxt, "records": [], "op": "rogue"}
        atomicio.publish_exclusive(share / ".revisionworx" / "journal" / f"{nxt:020d}.json",
                                   atomicio.dumps_json(rogue))
        # The writer notices the journal moved and replays it, then commits after it.
        seq = ws.commit("t", [RecordPut("settings", "workspace", {"x": 1})])
        assert seq == nxt + 1


def test_blob_roundtrip_and_reference_validation(share):
    with Workspace.create(share, "A") as ws:
        ref = ws.write_blob("baseline", {"files": []})
        assert ws.read_blob(ref) == {"files": []}
        for bad in ("baseline/../../workspace", "../x/y", "baseline/x.json"):
            with pytest.raises((ValueError, FileNotFoundError)):
                ws.read_blob(bad)
