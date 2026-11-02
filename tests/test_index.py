# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sqlite3
import time

import pytest

from revisionworx import index as index_mod
from revisionworx import projects
from revisionworx.index import LocalIndex, fts_query, open_index
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace


def _adopted(ws, share, name, files):
    d = share / "Engineering" / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_bytes(b"x")
    return projects.adopt_project(ws, d, name=name)


def test_rebuild_and_incremental_sync_agree(ws, share, cache_dir):
    projects.create_project(ws, NewProject(name="Hydraulic press", number="J-100", customer="NASA"))
    with open_index(ws, cache_dir) as idx:
        assert idx.status()["stale"] is False
        _adopted(ws, share, "Legacy pump", ["impeller.ipt", "volute.iam"])
        projects.create_project(ws, NewProject(name="Crane slew", number="J-101", customer="Liebherr"))
        assert idx.status()["stale"] is True
        assert idx.sync() == {"mode": "incremental", "applied": 2}
        incremental = idx.search_projects("")
        incr_files = idx.search_files("impeller")
        idx.rebuild()
        assert idx.search_projects("") == incremental
        assert idx.search_files("impeller") == incr_files
        assert [r["path"] for r in incr_files] == ["impeller.ipt"]


def test_exact_and_text_search(ws, cache_dir):
    projects.create_project(ws, NewProject(name="Hydraulic press", number="J-100", customer="NASA"))
    projects.create_project(ws, NewProject(name="Pneumatic gripper", number="J-200", customer="Acme"))
    with open_index(ws, cache_dir) as idx:
        assert [p["name"] for p in idx.project_by_number("j-100")] == ["Hydraulic press"]
        assert [p["name"] for p in idx.search_projects("hydr")] == ["Hydraulic press"]
        assert [p["name"] for p in idx.search_projects("acme")] == ["Pneumatic gripper"]
        assert idx.search_projects("nothing-like-this") == []


def test_archived_hidden_unless_requested(ws, cache_dir):
    rec = projects.create_project(ws, NewProject(name="Old job"))
    projects.update_project(ws, rec["id"], status="archived")
    with open_index(ws, cache_dir) as idx:
        assert idx.search_projects("old") == []
        assert [p["status"] for p in idx.search_projects("old", include_archived=True)] == ["archived"]


@pytest.mark.parametrize("text", ['"', "NEAR(a b)", "a OR", "*", "col:x", "') DROP TABLE projects; --", "AND"])
def test_fts_operator_injection_is_neutralised(ws, cache_dir, text):
    projects.create_project(ws, NewProject(name="Alpha"))
    with open_index(ws, cache_dir) as idx:
        idx.search_projects(text)  # must not raise
        idx.search_files(text)
        assert idx.count_projects() == 1


def test_fts_query_builder():
    assert fts_query("  ") is None
    assert fts_query("press J-100") == '"press"* "J"* "100"*'


def test_pagination(ws, cache_dir):
    for i in range(25):
        projects.create_project(ws, NewProject(name=f"Job {i:02d}", rel_path=f"jobs/{i:02d}"))
    with open_index(ws, cache_dir) as idx:
        page1 = idx.search_projects("", limit=10, offset=0)
        page3 = idx.search_projects("", limit=10, offset=20)
        assert [p["name"] for p in page1][:2] == ["Job 00", "Job 01"]
        assert len(page3) == 5


def test_deleted_or_corrupt_cache_is_rebuilt(ws, cache_dir):
    projects.create_project(ws, NewProject(name="Keep"))
    idx = open_index(ws, cache_dir)
    path = idx.path
    idx.close()
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(str(path) + suffix)
        except FileNotFoundError:
            pass
    path.write_bytes(b"garbage, not sqlite" * 100)
    with open_index(ws, cache_dir) as idx:
        assert [p["name"] for p in idx.search_projects("")] == ["Keep"]
    path.unlink()
    with open_index(ws, cache_dir) as idx:
        assert idx.count_projects() == 1


def test_cache_ahead_of_workspace_triggers_rebuild(ws, cache_dir):
    projects.create_project(ws, NewProject(name="A"))
    with open_index(ws, cache_dir) as idx:
        idx.db.execute("UPDATE meta SET value='999' WHERE key='journal_seq'")
        idx.db.commit()
        result = idx.sync()
        assert result["mode"] == "rebuild"
        assert idx.cursor_seq() == ws.head_seq()


def test_cache_for_other_workspace_is_discarded(ws, share, tmp_path, cache_dir):
    projects.create_project(ws, NewProject(name="Mine"))
    with open_index(ws, cache_dir) as idx:
        path = idx.path
    other_share = tmp_path / "other"
    other = Workspace.create(other_share, "Other")
    try:
        other.info["workspace_id"] = ws.id  # simulate a copied cache file under the same name
        db = sqlite3.connect(path)
        db.execute("UPDATE meta SET value='ws_different' WHERE key='workspace_id'")
        db.commit()
        db.close()
        with LocalIndex(ws, cache_dir) as idx:
            assert idx.count_projects() == 0  # schema recreated, not trusted
            idx.sync()
            assert idx.count_projects() == 1
    finally:
        other.close()


def test_index_never_lives_in_the_workspace(ws, share, cache_dir):
    with open_index(ws, cache_dir) as idx:
        assert share not in idx.path.parents
        assert idx.status()["authoritative"] is False


def test_coverage_statement(ws, share, cache_dir):
    _adopted(ws, share, "L1", ["a.ipt"])
    with open_index(ws, cache_dir) as idx:
        cov = idx.coverage()
        assert cov["registered_projects"] == {"indexed": 1}
        assert "never searched" in cov["note"]


def test_bulk_registration_and_lookup_latency_smoke(ws, cache_dir):
    """Smoke check only (2,000 projects, local tmpfs) - NOT a benchmark of the 10,000-project target."""
    from revisionworx.workspace import RecordPut, new_id, now_iso
    puts = []
    for i in range(2000):
        pid = new_id("prj")
        puts.append(RecordPut("project", pid, {
            "id": pid, "name": f"Project {i}", "number": f"N-{i:05d}", "customer": f"Cust {i % 40}",
            "description": "", "mode": "managed", "status": "active", "root_id": ws.root_id,
            "rel_path": f"p/{i}", "origin": "created", "created": now_iso(), "baseline": None}))
    ws.commit("bulk", puts)
    with open_index(ws, cache_dir) as idx:
        t0 = time.perf_counter()
        hit = idx.project_by_number("N-01234")
        elapsed = time.perf_counter() - t0
        assert [p["name"] for p in hit] == ["Project 1234"]
        assert elapsed < 1.0  # generous sanity bound; real targets need the benchmark suite


def test_default_cache_dir_is_local(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    if os.name != "nt":
        assert index_mod.default_cache_dir() == tmp_path / "xdg" / "revisionworx"
