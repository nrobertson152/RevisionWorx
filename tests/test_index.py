# SPDX-License-Identifier: GPL-3.0-or-later
import os
import random
import sqlite3
import time

import pytest

from revisionworx import index as index_mod
from revisionworx import documents, projects  # noqa: F401 - documents registers its record kinds
from revisionworx.index import LocalIndex, fts_query, open_index
from revisionworx.projects import NewProject
from revisionworx.workspace import LockLostError, RecordDelete, RecordPut, Workspace


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


# ---------------------------------------------------------------- issue #9: cursor matches contents
def _journal_state(ws, kind):
    """Replay of the effective journal entries: what the cache must describe."""
    state = {}
    for entry in ws.entries_after(0):
        for item in entry["records"]:
            if item["kind"] == kind:
                if item.get("delete"):
                    state.pop(item["id"], None)
                else:
                    state[item["id"]] = item["data"]
    return state


def _assert_cache_matches_journal(ws, idx):
    assert idx.cursor_seq() == ws.head_seq()
    roots = {r[0]: r[1] for r in idx.db.execute("SELECT id, name FROM roots")}
    assert roots == {k: v["name"] for k, v in _journal_state(ws, "root").items()}
    checkouts = {r[0] for r in idx.db.execute("SELECT document_id FROM checkouts")}
    assert checkouts == set(_journal_state(ws, "checkout"))


@pytest.mark.parametrize("change", ["insert", "update", "delete"])
def test_rebuild_with_a_commit_mid_snapshot_matches_the_journal(ws, cache_dir, change):
    keep, target = "root_" + "1" * 32, "root_" + "2" * 32
    rev = ws.commit("t", [RecordPut("root", target, {"name": "before"}, expect_rev=0)])
    co = "doc_" + "3" * 32
    ws.commit("t", [RecordPut("checkout", co, {"owner": {"user": "u", "host": "h"}, "acquired": "t"}, expect_rev=0)])
    mutation = {"insert": RecordPut("root", keep, {"name": "new"}, expect_rev=0),
                "update": RecordPut("root", target, {"name": "after"}, expect_rev=rev),
                "delete": RecordDelete("root", target, expect_rev=rev)}[change]
    real, fired = ws.list, []

    def list_then_commit(kind):
        result = real(kind)
        if not fired:
            fired.append(True)
            ws.commit("t.mid", [mutation])
        return result
    with LocalIndex(ws, cache_dir) as idx:
        ws.list = list_then_commit
        idx.rebuild()
        del ws.list
        assert fired
        _assert_cache_matches_journal(ws, idx)
        assert idx.sync()["mode"] == "current"


def test_sync_stays_correct_across_a_fenced_entry(share, cache_dir):
    """A fenced entry is in the journal but is not a change. The cache must skip it and still reach head."""
    rid, late = "root_" + "1" * 32, "root_" + "2" * 32
    w = Workspace.create(share, "W")
    with LocalIndex(w, cache_dir) as idx:
        idx.rebuild()
        real = w._current_rev

        def revoked_after_preflight(kind, record_id):
            Workspace.break_writer_lock(share, w._lock_token, "unresponsive")
            w._current_rev = real
            return real(kind, record_id)
        w._current_rev = revoked_after_preflight
        with pytest.raises(LockLostError):
            w.commit("t.fenced", [RecordPut("root", late, {"name": "fenced"}, expect_rev=0)])
        with Workspace.open(share, writable=True) as next_writer:
            next_writer.commit("t", [RecordPut("root", rid, {"name": "kept"}, expect_rev=0)])
        idx.sync()
        _assert_cache_matches_journal(w, idx)
        assert idx.db.execute("SELECT 1 FROM roots WHERE id=?", (late,)).fetchone() is None
    w._lock_token = None
    w.close()


# ---------------------------------------------------------------- incremental equals rebuild equals journal
def _tables(idx):
    dump = {}
    for table in ("roots", "projects", "files", "documents", "revisions", "checkouts"):
        dump[table] = sorted(idx.db.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
    for table, cols in (("projects_fts", "id, name, number, customer, description"),
                        ("files_fts", "project_id, path"), ("documents_fts", "id, number, title, rel_path")):
        dump[table] = sorted(idx.db.execute(f"SELECT {cols} FROM {table}").fetchall(), key=repr)
    return dump


@pytest.mark.parametrize("seed", range(6))
def test_incremental_sync_always_equals_a_fresh_rebuild(ws, share, tmp_path, seed):
    """Differential: after each step of a random history the incremental cache must equal one built from nothing. Searching
    through either must give the same results, whichever way the cache happened to be brought up to date."""
    rng = random.Random(seed)
    project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, project)
    incremental = LocalIndex(ws, tmp_path / "incremental")
    incremental.sync()
    docs, tokens, files = [], {}, 0

    def register():
        nonlocal files
        files += 1
        path = folder / "02 CAD" / "Parts" / f"part{files}.ipt"
        path.write_bytes(b"v%d" % files)
        docs.append(documents.register_document(ws, project["id"], path, title=f"part {files}",
                                                capture=rng.random() < 0.8)[0]["id"])

    def checkout():
        free = [d for d in docs if d not in tokens and ws.get("document", d)["status"] == "active"]
        if free:
            d = rng.choice(free)
            tokens[d] = documents.checkout(ws, d)["token"]

    def checkin():
        if tokens:
            d = rng.choice(sorted(tokens))
            doc = ws.get("document", d)
            documents.working_path(ws, doc).write_bytes(os.urandom(8))
            documents.checkin(ws, d, tokens.pop(d), "changed")

    def cancel():
        if tokens:
            d = rng.choice(sorted(tokens))
            documents.cancel_checkout(ws, d, tokens.pop(d))

    def transition():
        revs = [r for r in ws.list("revision").values()]
        if revs:
            rev = rng.choice(revs)
            targets = {"wip": "in_review", "in_review": "approved", "approved": "released"}.get(rev["state"])
            if targets and not (targets == "released" and any(
                    r["number"] > rev["number"] and r["state"] == "released"
                    for r in revs if r["document_id"] == rev["document_id"])):
                documents.transition(ws, rev["id"], targets)

    def retitle():
        if docs:
            documents.update_document(ws, rng.choice(docs), title=f"retitled {rng.random():.6f}", number="")

    def archive_project():
        projects.update_project(ws, project["id"], description=f"{rng.random():.6f}")
    actions = [register, register, checkout, checkin, checkin, cancel, transition, transition, retitle, archive_project]
    for step in range(40):
        rng.choice(actions)()
        if rng.random() < 0.4:
            incremental.sync()
            with LocalIndex(ws, tmp_path / f"fresh{step}") as fresh:
                fresh.rebuild()
                assert _tables(incremental) == _tables(fresh), f"seed {seed}, step {step}"
                assert incremental.cursor_seq() == fresh.cursor_seq() == ws.head_seq()
    incremental.close()
