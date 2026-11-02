# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for gaps found in the Phase 1 reliability review (2026-11-01).

Each test reproduced a real defect before the fix:
  R1 stale client silently overwrote a newer change (lost update)
  R2 a writer whose lock was administratively broken could keep committing
  R3 a failure after the commit point was indistinguishable from "not committed"
  R4 a journal entry altered into different valid JSON went undetected
  R5 damaged materialised records could not be detected or repaired
"""
import pytest

from revisionworx import atomicio, projects
from revisionworx.projects import NewProject
from revisionworx.workspace import (CommittedNotApplied, ConflictError, LockLostError, RecordDelete,
                                    RecordPut, Workspace, WorkspaceCorruptError)


def test_r1_stale_update_is_rejected_not_silently_applied(ws, share):
    rec = projects.create_project(ws, NewProject(name="Pump", number="J-1"))
    seen_by_alice = ws.get("project", rec["id"])
    seen_by_bob = ws.get("project", rec["id"])
    projects.update_project(ws, rec["id"], expected_rev=seen_by_alice["_rev"], name="Pump A")
    with pytest.raises(ConflictError):
        projects.update_project(ws, rec["id"], expected_rev=seen_by_bob["_rev"], name="Pump B")
    assert ws.get("project", rec["id"])["name"] == "Pump A"


def test_r1_expect_absent_prevents_double_create(ws):
    rid = "prj_" + "d" * 32
    ws.commit("t", [RecordPut("project", rid, {"n": 1}, expect_rev=0)])
    with pytest.raises(ConflictError):
        ws.commit("t", [RecordPut("project", rid, {"n": 2}, expect_rev=0)])
    assert ws.get("project", rid)["n"] == 1


def test_r1_conflict_rejects_whole_transaction(ws):
    a, b = "prj_" + "a" * 32, "prj_" + "b" * 32
    ws.commit("t", [RecordPut("project", a, {"v": 1})])
    head = ws.head_seq()
    with pytest.raises(ConflictError):
        ws.commit("t", [RecordPut("project", b, {"v": 1}), RecordPut("project", a, {"v": 2}, expect_rev=999)])
    assert ws.head_seq() == head and ws.get("project", b) is None


def test_r2_writer_with_broken_lock_cannot_commit(share):
    paused = Workspace.create(share, "A")
    token = Workspace.read_writer_lock(share)["token"]
    Workspace.break_writer_lock(share, token, reason="client unresponsive")
    head = Workspace.open(share).head_seq()
    with pytest.raises(LockLostError):
        paused.commit("late write", [RecordPut("settings", "workspace", {"x": 1})])
    with Workspace.open(share) as ws:
        assert ws.head_seq() == head
    paused.close()


def test_r2_entries_record_the_writer_token(ws, share):
    seq = ws.commit("t", [RecordPut("settings", "workspace", {})])
    assert ws.read_entry(seq)["writer_token"] == Workspace.read_writer_lock(share)["token"]


def test_r3_failure_after_commit_point_reports_committed_and_is_queryable(ws, monkeypatch):
    rid = "prj_" + "e" * 32

    def boom(entry):
        raise OSError("disk full while materialising")
    monkeypatch.setattr(ws, "_apply", boom)
    with pytest.raises(CommittedNotApplied) as info:
        ws.commit("t", [RecordPut("project", rid, {"saved": True})], txid="a" * 32)
    monkeypatch.undo()
    assert info.value.seq == ws.head_seq()
    assert ws.transaction_outcome("a" * 32) == {"state": "committed", "seq": info.value.seq}
    assert ws.transaction_outcome("b" * 32) == {"state": "not-found"}
    assert ws.get("project", rid) == {"saved": True, "_rev": info.value.seq}


def test_r4_altered_journal_entry_is_detected(ws, share):
    seq = ws.commit("t", [RecordPut("settings", "workspace", {"limit": 10})])
    path = share / ".revisionworx" / "journal" / f"{seq:020d}.json"
    entry = atomicio.read_json(path)
    entry["records"][0]["data"]["limit"] = 10_000  # valid JSON, different meaning
    atomicio.atomic_write_json(path, entry)
    with pytest.raises(WorkspaceCorruptError, match="digest"):
        ws.read_entry(seq)


def test_r5_verify_detects_and_repairs_damaged_records(ws, share):
    rec = projects.create_project(ws, NewProject(name="Keep me"))
    assert ws.verify()["problems"] == []
    path = share / ".revisionworx" / "records" / "project" / f"{rec['id']}.json"
    damaged = atomicio.read_json(path)
    damaged["name"] = "tampered"
    atomicio.atomic_write_json(path, damaged)
    (share / ".revisionworx" / "records" / "project" / ("prj_" + "f" * 32 + ".json")).write_text("{}")
    report = ws.verify()
    kinds = sorted(p["problem"] for p in report["problems"])
    assert kinds == ["mismatch", "not-in-journal"]
    ws.verify(repair=True)
    assert ws.verify()["problems"] == []
    assert ws.get("project", rec["id"])["name"] == "Keep me"


def test_record_delete_is_journaled_and_replayed(ws):
    rid = "prj_" + "9" * 32
    ws.commit("t", [RecordPut("project", rid, {"v": 1})])
    rev = ws.get("project", rid)["_rev"]
    with pytest.raises(ConflictError):
        ws.commit("t", [RecordDelete("project", rid, expect_rev=rev + 50)])
    ws.commit("t", [RecordDelete("project", rid, expect_rev=rev)])
    assert ws.get("project", rid) is None and rid not in ws.list("project")
    assert ws.verify()["problems"] == []


def test_cli_verify_and_expect_rev(tmp_path, capsys):
    import json
    from revisionworx import cli
    share = tmp_path / "s"
    (share / "E").mkdir(parents=True)
    w = ["-w", str(share), "--cache-dir", str(tmp_path / "c")]
    assert cli.main([*w, "init", str(share), "--name", "V"]) == 0
    assert cli.main([*w, "root", "add", "E", str(share / "E")]) == 0
    capsys.readouterr()
    assert cli.main([*w, "--json", "project", "new", "--name", "P"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert cli.main([*w, "project", "set", rec["id"], "--name", "Q", "--expect-rev", str(rec["_rev"])]) == 0
    assert cli.main([*w, "project", "set", rec["id"], "--name", "R", "--expect-rev", str(rec["_rev"])]) == 2
    assert "changed by someone else" in capsys.readouterr().err
    assert cli.main([*w, "verify"]) == 0


def test_controller_reports_conflict_actionably(tmp_path):
    from revisionworx.gui.controller import Controller, describe_error
    c = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    (tmp_path / "s" / "E").mkdir(parents=True)
    c.create_workspace(tmp_path / "s", "C")
    c.add_root("E", tmp_path / "s" / "E")
    rec = c.create_project(NewProject(name="P"))
    c.update_project(rec["id"], rec["_rev"], name="first")
    with pytest.raises(ConflictError) as info:
        c.update_project(rec["id"], rec["_rev"], name="second")
    assert "Reload" in describe_error(info.value)


def test_index_sync_handles_journaled_deletes(ws, cache_dir):
    """Regression: index sync assumed every journal item carried data and crashed on deletes."""
    from revisionworx.index import open_index
    rec = projects.create_project(ws, NewProject(name="Temp"))
    with open_index(ws, cache_dir) as idx:
        assert idx.count_projects() == 1
        ws.commit("t", [RecordDelete("project", rec["id"], expect_rev=rec["_rev"])])
        assert idx.sync()["mode"] == "incremental"
        assert idx.count_projects() == 0 and idx.search_projects("temp") == []
