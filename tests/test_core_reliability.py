# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions from the adversarial core review after fe8a179 (docs/CORE_INVARIANTS.md).

Each test failed on 22a5273 and names the defect it pins. The cases are about promises the core makes to
its callers: what a retry returns, when "corrupt" may be claimed, and what the journal says versus what a
file or a cache says.
"""
import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

import proc_helpers
from revisionworx import atomicio, content, documents as D, projects
from revisionworx.clientstate import CheckoutTokens
from revisionworx.index import IndexBusyError, LocalIndex, open_index
from revisionworx.projects import NewProject
from revisionworx.workspace import (REV_FIELD, LockLostError, RecordPut, TxidReuseError, Workspace,
                                    WorkspaceCorruptError, WorkspaceError, entry_digest)

RID = "root_" + "a" * 32
OTHER = "root_" + "b" * 32


@pytest.fixture
def world(share, ws):
    """A managed project with one registered document (revision 1) on a writable handle."""
    project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    path = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "bracket.ipt"
    path.write_bytes(b"IPT v1")
    doc, _ = D.register_document(ws, project["id"], path)
    return share, ws, project, doc, path


def paused_past_checks(handle, during):
    """Run ``during`` while ``handle`` is inside commit(), past its lock, revocation and txid checks."""
    real = handle._current_rev

    def hook(kind, rid):
        handle._current_rev = real
        during()
        return real(kind, rid)
    handle._current_rev = hook


# ------------------------------------------------------------------------ checkin retries (CORE_API 4)
def test_checkin_retry_returns_the_original_revision(world):
    """Was NotCheckedOut: the promised 'you get the original result' never happened for check-in."""
    share, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"IPT v2")
    txid = uuid.uuid4().hex
    first = D.checkin(ws, doc["id"], co["token"], "second", txid=txid)
    path.write_bytes(b"edited again, must not be captured by a retry")
    assert D.checkin(ws, doc["id"], co["token"], "second", txid=txid) == first
    ws.close()
    with Workspace.open(share, writable=True) as fresh:     # after a crash, in a new process
        assert D.checkin(fresh, doc["id"], co["token"], "second", txid=txid) == first
        assert [r["id"] for r in D.history(fresh, doc["id"])][-1] == first["id"]
        assert len(D.history(fresh, doc["id"])) == 2


def test_checkin_retry_after_a_fenced_attempt_reports_it_never_took_effect(world):
    """Was TxidReuseError, which CORE_API calls a programming error."""
    share, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"IPT v2")
    txid = uuid.uuid4().hex
    paused_past_checks(ws, lambda: Workspace.break_writer_lock(share, ws._lock_token, "unresponsive"))
    with pytest.raises(LockLostError):
        D.checkin(ws, doc["id"], co["token"], "second", txid=txid)
    ws._lock_token = None
    ws.close()
    with Workspace.open(share, writable=True) as fresh:
        assert fresh.transaction_outcome(txid)["state"] == "fenced"
        with pytest.raises(LockLostError, match="never took effect"):
            D.checkin(fresh, doc["id"], co["token"], "second", txid=txid)
        assert len(D.history(fresh, doc["id"])) == 1
        rev = D.checkin(fresh, doc["id"], co["token"], "second", txid=uuid.uuid4().hex)   # a new attempt works
        assert rev["number"] == 2


@pytest.mark.parametrize("change", ["description", "document", "operation"])
def test_checkin_refuses_a_txid_spent_on_a_different_request(world, change):
    _, ws, project, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"IPT v2")
    txid = uuid.uuid4().hex
    if change == "operation":
        ws.commit("something.else", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)], txid=txid)
        with pytest.raises(TxidReuseError):
            D.checkin(ws, doc["id"], co["token"], "second", txid=txid)
        return
    D.checkin(ws, doc["id"], co["token"], "second", txid=txid)
    if change == "description":
        with pytest.raises(TxidReuseError):
            D.checkin(ws, doc["id"], co["token"], "a different change", txid=txid)
    else:
        other = path.with_name("other.ipt")
        other.write_bytes(b"other")
        doc2, _ = D.register_document(ws, project["id"], other)
        with pytest.raises(TxidReuseError):
            D.checkin(ws, doc2["id"], "whatever", "second", txid=txid)
    assert len(D.history(ws, doc["id"])) == 2


def test_a_stopped_checkin_reports_the_retry_that_finished_for_it(world):
    """Found by systematic exploration: stopped after its first look, the original attempt resumed to find the checkout
    consumed by its own retry, and reported NotCheckedOut for a change that had been saved."""
    share, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"IPT v2")
    txid = uuid.uuid4().hex
    real, done = D._require_checkout, {}

    def retry_finishes_first(handle, doc_id, token):
        monkey.setattr(D, "_require_checkout", real)
        Workspace.break_writer_lock(share, ws._lock_token, "unresponsive")
        with Workspace.open(share, writable=True) as retry:
            done["rev"] = D.checkin(retry, doc_id, token, "second", txid=txid)
        return real(handle, doc_id, token)
    with pytest.MonkeyPatch.context() as monkey:
        monkey.setattr(D, "_require_checkout", retry_finishes_first)
        result = D.checkin(ws, doc["id"], co["token"], "second", txid=txid)
    assert done and result == done["rev"]
    ws._lock_token = None


def _just_before_choosing_a_sequence(handle, during):
    """Run ``during`` when ``handle`` is past every check and about to pick its journal sequence."""
    real, calls = handle.head_seq, []

    def head_seq():
        calls.append(1)
        if len(calls) == 2:                     # the first call only decides whether to recover
            handle.head_seq = real
            during()
        return real()
    handle.head_seq = head_seq
    return calls


def test_two_attempts_with_one_txid_report_the_one_that_took_effect(share):
    """A hung attempt resumes after its retry succeeded. Was: the newest entry won, so a change that took
    effect was reported as 'fenced', and a retry raised LockLostError."""
    request = [RecordPut("root", RID, {"name": "x"})]
    txid = uuid.uuid4().hex
    with Workspace.create(share, "t"):
        pass
    hung = Workspace.open(share, writable=True)
    done = {}

    def retry_elsewhere():
        Workspace.break_writer_lock(share, hung._lock_token, "unresponsive")
        with Workspace.open(share, writable=True) as retry:
            done["seq"] = retry.commit("t", request, txid=txid)
    _just_before_choosing_a_sequence(hung, retry_elsewhere)
    with pytest.raises(LockLostError):
        hung.commit("t", request, txid=txid)
    hung._lock_token = None
    hung.close()
    with Workspace.open(share, writable=True) as w:
        assert w.head_seq() == done["seq"] + 1      # the hung attempt really is in the journal, later
        assert w.transaction_outcome(txid) == {"state": "committed", "seq": done["seq"]}
        assert w.commit("t", request, txid=txid) == done["seq"]
        assert w.get("root", RID)[REV_FIELD] == done["seq"]


# ------------------------------------------------------------------------ "corrupt" must mean corrupt
def _taken_just_before_publish(monkeypatch, handle, take):
    """Something else takes the journal sequence ``handle`` chose, at the last possible moment."""
    real, fired = atomicio.publish_exclusive, []

    def publish(path, data):
        if Path(path).parent.name == "journal" and not fired:
            fired.append(Path(path).name)
            monkeypatch.setattr(atomicio, "publish_exclusive", real)
            take(int(Path(path).stem))
        return real(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", publish)
    return fired


def _raw_entry(seq, token):
    entry = {"seq": seq, "txid": uuid.uuid4().hex, "time": "t", "actor": {}, "op": "t.late",
             "records": [], "blobs": [], "writer_token": token}
    entry["digest"] = entry_digest(entry)
    return atomicio.dumps_json(entry)


def test_commit_survives_an_administrative_entry_taking_its_sequence(share, monkeypatch):
    """Was WorkspaceCorruptError 'created by another writer' on a healthy writer: revoking some other token
    or clearing an orphan publishes without the lock, and may land on the sequence a writer picked."""
    ws = Workspace.create(share, "t")
    fired = _taken_just_before_publish(monkeypatch, ws, lambda seq: Workspace._publish_revocation(
        ws.meta_dir, {"token": "f" * 32}, reason="unrelated", evidence="x"))
    seq = ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    assert fired and seq == int(fired[0].split(".")[0]) + 1
    assert ws.get("root", RID)["name"] == "x"
    assert ws.verify()["problems"] == []
    ws.close()


def test_an_administrative_entry_landing_before_a_commit_is_not_skipped(share):
    """Was: applied_seq jumped over it, so its audit record was never materialised and verify() reported a
    problem on a healthy workspace from then on."""
    ws = Workspace.create(share, "t")
    calls = _just_before_choosing_a_sequence(ws, lambda: Workspace._publish_revocation(
        ws.meta_dir, {"token": "f" * 32}, reason="unrelated", evidence="x"))
    seq = ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    assert len(calls) == 2
    assert ws.applied_seq() == seq == ws.head_seq()
    assert ws.verify()["problems"] == []
    ws.close()


def test_commit_survives_a_fenced_late_entry_taking_its_sequence(share, monkeypatch):
    """Was the same false alarm. A revoked writer's late publish is harmless by construction."""
    ws = Workspace.create(share, "t")
    Workspace._publish_revocation(ws.meta_dir, {"token": "d" * 32}, reason="stale", evidence="x")

    def late_entry(seq):
        atomicio.publish_exclusive(ws.meta_dir / "journal" / f"{seq:020d}.json", _raw_entry(seq, "d" * 32))
    _taken_just_before_publish(monkeypatch, ws, late_entry)
    seq = ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    assert ws.get("root", RID) == {"name": "x", REV_FIELD: seq}
    ws.close()


def test_commit_still_refuses_when_an_effective_writer_took_its_sequence(share, monkeypatch):
    """The control: this one really does mean the single-writer invariant broke."""
    ws = Workspace.create(share, "t")

    def effective_entry(seq):
        atomicio.publish_exclusive(ws.meta_dir / "journal" / f"{seq:020d}.json", _raw_entry(seq, "e" * 32))
    _taken_just_before_publish(monkeypatch, ws, effective_entry)
    with pytest.raises(WorkspaceCorruptError, match="another writer"):
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    assert ws.get("root", RID) is None
    ws.close()


# ------------------------------------------------------------------------ the journal over any file
def test_reads_ignore_a_record_file_the_journal_never_wrote(share):
    with Workspace.create(share, "t") as ws:
        path = ws.meta_dir / "records" / "root" / f"{RID}.json"
        path.parent.mkdir(exist_ok=True)
        atomicio.atomic_write_json(path, {"name": "rogue", REV_FIELD: 1})
        assert ws.get("root", RID) is None
        assert RID not in ws.list("root")
        assert [p["problem"] for p in ws.verify()["problems"]] == ["not-in-journal"]    # still visible
        ws.verify(repair=True)
        assert ws.verify()["problems"] == []


def test_reads_ignore_a_record_file_claiming_a_future_revision(share):
    """A file claiming an entry the journal does not have, e.g. records newer than a restored journal."""
    with Workspace.create(share, "t") as ws:
        seq = ws.commit("t", [RecordPut("root", RID, {"name": "journal"}, expect_rev=0)])
        path = ws.meta_dir / "records" / "root" / f"{RID}.json"
        atomicio.atomic_write_json(path, {"name": "abandoned timeline", REV_FIELD: seq + 50})
        assert ws.get("root", RID) == {"name": "journal", REV_FIELD: seq}
        assert ws.list("root")[RID] == {"name": "journal", REV_FIELD: seq}


def test_a_reader_sees_a_commit_that_lands_during_its_read(share, monkeypatch):
    """Ignoring unknown files must not hide a record committed between the view and the file read."""
    with Workspace.create(share, "t") as writer:
        reader = Workspace.open(share)
        reader.get("root", RID)
        real, fired = atomicio.read_json, []

        def read(path):
            if Path(path).name == f"{RID}.json" and not fired:
                fired.append(1)
                monkeypatch.setattr(atomicio, "read_json", real)
                seq = writer.commit("t", [RecordPut("root", RID, {"name": "new"}, expect_rev=0)])
                fired.append(seq)
            return real(path)
        monkeypatch.setattr(atomicio, "read_json", read)
        got = reader.get("root", RID)
        assert fired and got == {"name": "new", REV_FIELD: fired[1]}
        reader.close()


# ------------------------------------------------------------------------ the index is only a cache
@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


def _restore(share, backup):
    shutil.rmtree(share / ".revisionworx")
    shutil.copytree(backup, share / ".revisionworx")


@pytest.mark.parametrize("new_commits", [1, 2, 3], ids=["shorter", "same-length", "longer"])
def test_index_notices_a_restored_journal_at_any_length(share, cache, tmp_path, new_commits):
    """Was: only 'cache is ahead of the journal' triggered a rebuild, so a restored workspace that had grown
    back to or past the cursor kept serving a record that exists in no journal, as a current result."""
    backup = tmp_path / "backup"
    with Workspace.create(share, "t") as ws:
        pass
    shutil.copytree(share / ".revisionworx", backup)
    with Workspace.open(share, writable=True) as ws:
        ws.commit("t", [RecordPut("root", "root_" + "1" * 32, {"name": "abandoned"}, expect_rev=0)])
        ws.commit("t", [RecordPut("root", "root_" + "2" * 32, {"name": "abandoned"}, expect_rev=0)])
        with open_index(ws, cache) as idx:
            assert idx.db.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 2
    _restore(share, backup)
    with Workspace.open(share, writable=True) as ws:
        for i in range(new_commits):
            ws.commit("t", [RecordPut("root", "root_" + f"{9 - i:032x}", {"name": "real"}, expect_rev=0)])
        with open_index(ws, cache) as idx:
            names = {r[0] for r in idx.db.execute("SELECT name FROM roots")}
            assert names == {"real"}, "the cache serves records from an abandoned timeline"
            assert idx.cursor_seq() == ws.head_seq()
            assert idx.sync()["mode"] == "current"


def test_index_from_an_older_build_is_rebuilt_once(share, cache):
    """A cursor with no anchor proves nothing, so it cannot be trusted. It must then settle."""
    with Workspace.create(share, "t") as ws:
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
        with open_index(ws, cache) as idx:
            idx.db.execute("DELETE FROM meta WHERE key='journal_digest'")
            idx.db.commit()
        with LocalIndex(ws, cache) as idx:
            assert idx.sync()["mode"] == "rebuild"
            assert idx.sync()["mode"] == "current"


def test_a_busy_index_is_reported_and_never_deleted(share, cache, monkeypatch):
    """Was: 'database is locked' counted as corruption, so opening deleted a database another process was
    using (raw PermissionError on Windows, a silently forked cache elsewhere)."""
    with Workspace.create(share, "t") as ws:
        open_index(ws, cache).close()
        path = LocalIndex(ws, cache)
        db = path.path
        path.close()
        holder = sqlite3.connect(db, timeout=0)
        holder.execute("PRAGMA journal_mode=DELETE")
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("UPDATE meta SET value=value")
        identity = os.stat(db).st_ino, os.stat(db).st_size
        monkeypatch.setattr("revisionworx.index.BUSY_TIMEOUT", 0.05)
        with pytest.raises(IndexBusyError):
            LocalIndex(ws, cache)
        assert (os.stat(db).st_ino, os.stat(db).st_size) == identity
        holder.rollback()
        holder.close()
        monkeypatch.undo()
        with LocalIndex(ws, cache) as idx:
            assert idx.sync()["mode"] == "current"


def _index_world(share):
    ws = Workspace.create(share, "t")
    projects.add_root(ws, "Engineering", share / "Engineering")
    project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, project)
    for i in range(12):
        f = folder / "02 CAD" / "Parts" / f"bracket{i}.ipt"
        f.write_bytes(b"v%d" % i)
        D.register_document(ws, project["id"], f, title="bracket " * 8)
    return ws


def test_any_damaged_page_heals_without_a_wrong_answer(share, cache):
    """Was: about 40% of single damaged pages raised 'database disk image is malformed' on every later use,
    until someone found and deleted the cache by hand. A disposable cache has to dispose of itself."""
    ws = _index_world(share)
    with open_index(ws, cache) as idx:
        idx.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db, pages, page = idx.path, idx.db.execute("PRAGMA page_count").fetchone()[0], \
            idx.db.execute("PRAGMA page_size").fetchone()[0]
    pristine = cache / "pristine"
    shutil.copy(db, pristine)
    for n in range(1, pages):
        shutil.copy(pristine, db)
        with open(db, "r+b") as fh:
            fh.seek(page * n)
            fh.write(b"\xff" * page)
        with open_index(ws, cache) as idx:
            assert len(idx.documents()) == 12, f"page {n}"
            assert len(idx.search_projects("HPU")) == 1, f"page {n}"
            assert idx.search_files("bracket") == [], f"page {n}"
            assert len(idx.revisions(idx.documents()[0]["id"])) == 1, f"page {n}"
            assert idx.coverage()["registered_projects"], f"page {n}"
            assert idx.count_projects() == 1, f"page {n}"
    ws.close()


def test_index_open_race_between_threads_on_a_new_cache(share, cache):
    """Was: every opener of a brand-new cache deleted and recreated it, so openers raced each other."""
    with Workspace.create(share, "t") as ws:
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
        errors, barrier = [], threading.Barrier(6)

        def opener():
            barrier.wait()
            try:
                with open_index(ws, cache) as idx:
                    assert idx.db.execute("SELECT name FROM roots").fetchall() == [("x",)]
            except BaseException as exc:         # noqa: BLE001
                errors.append(repr(exc))
        threads = [threading.Thread(target=opener) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors, errors


def test_opening_waits_out_a_brief_lock_instead_of_failing(share, cache, monkeypatch):
    """Real Windows, Python 3.13 (Core CI 41577700770): SQLite returns 'locked' at once while another opener switches a new
    database to WAL, without using its busy timeout, so a racing opener reported 'busy' for no lasting reason."""
    with Workspace.create(share, "t") as ws:
        real, denied = LocalIndex._usable, []

        def usable(self, db):
            if len(denied) < 3:
                denied.append(1)
                raise IndexBusyError("injected: another opener is switching to WAL")
            return real(self, db)
        monkeypatch.setattr(LocalIndex, "_usable", usable)
        with open_index(ws, cache) as idx:
            assert len(denied) == 3 and idx.sync()["mode"] == "current"


def test_missing_baseline_blob_does_not_take_search_down(share, cache):
    """verify() reports a missing blob. Until someone repairs it, the rest of the index must still work,
    and coverage must admit the gap rather than imply the project's files were searched."""
    ws = Workspace.create(share, "t")
    projects.add_root(ws, "Engineering", share / "Engineering")
    folder = share / "Engineering" / "Old job"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"x")
    project = projects.adopt_project(ws, folder, name="Old job")
    blob = project["baseline"]["blob"]
    kind, blob_id = blob.split("/")
    (ws.meta_dir / "blobs" / kind / f"{blob_id}.json").unlink()
    with open_index(ws, cache) as idx:
        assert len(idx.search_projects("Old")) == 1
        assert idx.search_files("a") == []
        assert idx.coverage()["baselines_unavailable"] == [project["id"]]
        idx.rebuild()
        assert idx.coverage()["baselines_unavailable"] == [project["id"]]
    ws.close()


# ------------------------------------------------------------------------ Windows sharing primitives
def test_a_persistently_denied_read_is_bounded(share, monkeypatch):
    """Was nested retries: 250 x 250 x 20 ms, about 20 minutes hung on one read of writer.lock."""
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    with Workspace.create(share, "t") as ws:
        real, opens = atomicio._open_for_read, []

        def denied(path):
            if Path(path).name == "writer.lock":
                opens.append(1)
                raise PermissionError(13, "injected: another program has it open with no sharing")
            return real(path)
        monkeypatch.setattr(atomicio, "_open_for_read", denied)
        with pytest.raises(LockLostError):
            ws._check_lock_still_held()
        assert len(opens) == atomicio.SHARING_RETRIES
        monkeypatch.undo()
        ws.writable = True


def test_publish_rides_out_a_scanner_holding_its_temp_file(tmp_path, monkeypatch):
    """Windows branch: scanners open new files right after they close, which fails the rename once or twice.
    Was a raw PermissionError from acquiring the lock or from commit."""
    monkeypatch.setattr(atomicio, "IS_WINDOWS", True)
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    denied = []

    def rename(src, dst):
        if len(denied) < 2:
            denied.append(1)
            raise PermissionError(32, "injected WinError 32")
        if Path(dst).exists():
            raise FileExistsError(dst)
        os.replace(src, dst)
    monkeypatch.setattr(os, "rename", rename)
    target = tmp_path / "entry.json"
    atomicio.publish_exclusive(target, b"first")
    assert target.read_bytes() == b"first" and len(denied) == 2
    with pytest.raises(atomicio.ExclusiveCreateError):
        atomicio.publish_exclusive(target, b"second")
    assert target.read_bytes() == b"first"


def test_failed_temp_cleanup_does_not_hide_the_real_answer(tmp_path, monkeypatch):
    """Was: losing the race for writer.lock, then a scanner holding the temp, surfaced PermissionError
    instead of 'the name is taken', which callers rely on to report who holds the lock."""
    target = tmp_path / "writer.lock"
    target.write_bytes(b"held")
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name.endswith(".tmp"):
            raise PermissionError(32, "injected WinError 32")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(atomicio.ExclusiveCreateError):
        atomicio.publish_exclusive(target, b"mine")


def test_temp_cleanup_never_blocks_opening_a_workspace(share, monkeypatch):
    with Workspace.create(share, "t"):
        pass
    stale = share / ".revisionworx" / "journal" / ".old.tmp"
    stale.write_bytes(b"x")
    os.utime(stale, (1, 1))
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == stale.name:
            raise PermissionError(32, "injected WinError 32")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    with Workspace.open(share, writable=True) as ws:
        assert ws.writable


def test_recovery_clears_abandoned_temp_files_everywhere(share):
    """Was: an interrupted check-in left a full copy of the file in the content store forever. GC did not
    see it either, since it only walks object folders."""
    with Workspace.create(share, "t"):
        pass
    meta = share / ".revisionworx"
    old = [meta / "content" / "sha256" / ".incoming.dead.tmp", meta / "blobs" / "baseline" / ".blb_x.json.ab.tmp"]
    young = meta / "content" / "sha256" / ".incoming.live.tmp"
    for path in old + [young]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 100)
    for path in old:
        os.utime(path, (1, 1))
    with Workspace.open(share, writable=True):
        pass
    assert not any(p.exists() for p in old)
    assert young.exists()                        # could belong to a writer that is still storing it


def test_replacing_the_working_file_leaves_nothing_behind_when_it_fails(world, monkeypatch):
    """Was: with the CAD file open in another program, each attempt left a hidden full-size copy in the
    company's project folder."""
    _, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"edits")
    real = os.replace

    def denied(src, dst, *args, **kwargs):
        if Path(dst) == path:
            raise PermissionError(32, "injected: the CAD program has it open")
        return real(src, dst, *args, **kwargs)
    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(PermissionError):
        D.cancel_checkout(ws, doc["id"], co["token"], revert=True)
    monkeypatch.undo()
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    assert path.read_bytes() == b"edits"
    assert ws.get("checkout", doc["id"]) is not None    # failed cleanly: still checked out, edits intact


# ------------------------------------------------------------------------ interrupted create
@pytest.mark.parametrize("skeleton", [[], ["journal"], ["journal", "records", "blobs", "locks"]],
                         ids=["bare", "partial", "all-folders"])
def test_interrupted_create_can_be_run_again(share, skeleton):
    """Was stuck: create said 'already exists, open it' and open said 'no workspace here'."""
    meta = share / ".revisionworx"
    meta.mkdir(parents=True)
    for sub in skeleton:
        (meta / sub).mkdir()
    (meta / ".workspace.json.dead.tmp").write_bytes(b"{")
    with Workspace.create(share, "t") as ws:
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    with Workspace.open(share) as ws:
        assert ws.get("root", RID)["name"] == "x"


def test_create_never_adopts_a_folder_with_unknown_content(share):
    meta = share / ".revisionworx"
    (meta / "journal").mkdir(parents=True)
    (meta / "journal" / f"{1:020d}.json").write_bytes(b"{}")
    with pytest.raises(WorkspaceError, match="already exists"):
        Workspace.create(share, "t")
    assert (meta / "journal" / f"{1:020d}.json").read_bytes() == b"{}"
    assert not (meta / "workspace.json").exists()


def test_create_still_refuses_an_existing_workspace(share):
    with Workspace.create(share, "t"):
        pass
    with pytest.raises(WorkspaceError, match="already exists"):
        Workspace.create(share, "again")


def test_create_race_has_one_winner(share):
    barrier, results = threading.Barrier(4), []

    def creator(i):
        barrier.wait()
        try:
            with Workspace.create(share, f"w{i}"):
                results.append("created")
        except (WorkspaceError, OSError) as exc:
            results.append(type(exc).__name__)
    threads = [threading.Thread(target=creator, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results.count("created") == 1, results
    with Workspace.open(share) as ws:
        assert ws.verify()["problems"] == []


def test_create_gives_the_lock_back_if_it_fails_after_acquiring(share, monkeypatch):
    def fail(self, *args, **kwargs):
        raise OSError("injected: disk full")
    monkeypatch.setattr(Workspace, "commit", fail)
    with pytest.raises(OSError):
        Workspace.create(share, "t")
    assert Workspace.read_writer_lock(share) is None


# ------------------------------------------------------------------------ checkout tokens
def test_a_failed_read_does_not_lose_the_other_tokens(tmp_path, monkeypatch):
    """Was: any read failure looked like an empty store, so the next put overwrote every other document's
    token, and only an administrator could then release those checkouts."""
    tokens = CheckoutTokens(tmp_path)
    tokens.put("ws", "doc_a", "A")
    real = atomicio.read_json

    def busy(path):
        raise PermissionError(32, "injected WinError 32")
    monkeypatch.setattr(atomicio, "read_json", busy)
    tokens.put("ws", "doc_b", "B")
    monkeypatch.setattr(atomicio, "read_json", real)
    assert tokens.get("ws", "doc_a") == "A" and tokens.get("ws", "doc_b") == "B"


def test_concurrent_processes_do_not_lose_tokens(tmp_path):
    """Was read-modify-write of one shared file: the GUI and the CLI could erase each other's tokens."""
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(6)
    procs = [ctx.Process(target=proc_helpers.put_tokens, args=(str(tmp_path), i, barrier)) for i in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0] * 6
    tokens = CheckoutTokens(tmp_path)
    for i in range(6):
        for j in range(20):
            assert tokens.get("ws", f"doc_{i}_{j}") == f"token-{i}-{j}"


def test_tokens_saved_by_an_older_build_still_work(tmp_path):
    (tmp_path / "checkouts.json").write_text(json.dumps({"ws": {"doc_a": "A", "doc_b": "B"}}))
    tokens = CheckoutTokens(tmp_path)
    assert tokens.get("ws", "doc_a") == "A"
    tokens.forget("ws", "doc_a")
    assert tokens.get("ws", "doc_a") is None
    assert tokens.get("ws", "doc_b") == "B"
    tokens.put("ws", "doc_b", "B2")
    assert tokens.get("ws", "doc_b") == "B2"
    tokens.forget("ws", "doc_b")
    assert tokens.get("ws", "doc_b") is None


def test_token_store_rejects_nothing_and_escapes_nothing(tmp_path):
    tokens = CheckoutTokens(tmp_path)
    tokens.put("../../evil", "../doc", "T")
    assert tokens.get("../../evil", "../doc") == "T"
    assert all(tmp_path in p.parents for p in tmp_path.rglob("*"))
    assert not (tmp_path.parent / "evil").exists()


# ------------------------------------------------------------------------ orphans that must stay clearable
def test_unreadable_fencing_orphan_can_be_cleared(share):
    """Was a dead end: a crash mid-fence can leave .fencing-*, acquisition told the administrator to run
    clear-orphan on it, and clear-orphan refused the name."""
    with Workspace.create(share, "t"):
        pass
    orphan = share / ".revisionworx" / "locks" / (".fencing-" + "a" * 32)
    orphan.write_bytes(b"not json")
    with pytest.raises(WorkspaceCorruptError, match="clear-orphan"):
        Workspace.open(share, writable=True)
    result = Workspace.clear_orphaned_release(share, orphan.name, "writer confirmed stopped")
    assert result["outcome"] == "completed"
    assert not orphan.exists()
    assert (share / ".revisionworx" / "locks" / result["evidence"]).read_bytes() == b"not json"
    with Workspace.open(share, writable=True) as ws:
        assert ws.verify()["problems"] == []


def test_clear_orphan_still_refuses_other_names(share):
    with Workspace.create(share, "t"):
        pass
    for name in ("writer.lock", "../journal", ".clearing-" + "a" * 32, ".fencing-../../x"):
        with pytest.raises(WorkspaceError, match="not an orphaned release file"):
            Workspace.clear_orphaned_release(share, name, "reason")


def test_content_store_publishes_through_the_one_exclusive_primitive(world, monkeypatch):
    """So what the journal gets on Windows (riding out a scanner, never clobbering), revisions get too."""
    _, ws, _, doc, path = world
    real, moved = atomicio.move_exclusive, []

    def move(src, dst):
        moved.append(Path(dst).name)
        with pytest.raises(FileExistsError):          # exclusive: a taken name is refused
            real(src, path)
        return real(src, dst)
    monkeypatch.setattr(atomicio, "move_exclusive", move)
    store = content.ContentStore(ws)
    path.write_bytes(b"new content")
    stored = store.store_file(path)
    assert moved == [stored.sha256] and store.verify(stored.sha256)


# ------------------------------------------------------------------------ a way out of every refusal
def test_a_correctly_fenced_entry_does_not_fail_verify_forever(share, capsys):
    """Was: one correct fencing left `verify` exiting 1 for the life of the workspace, with nothing able to clear it."""
    from revisionworx import cli
    ws = Workspace.create(share, "t")
    paused_past_checks(ws, lambda: Workspace.break_writer_lock(share, ws._lock_token, "unresponsive"))
    with pytest.raises(LockLostError):
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    ws._lock_token = None
    ws.close()
    with Workspace.open(share) as reader:
        report = reader.verify()
    assert [(p["problem"], p["severity"]) for p in report["problems"]] == [("fenced-entry", "info")]
    assert cli.main(["-w", str(share), "verify"]) in (0, None)   # still listed, but nothing is wrong
    assert "fenced-entry" in capsys.readouterr().out


def test_verify_does_not_call_lag_damage(share, monkeypatch):
    """Was: after a crash, a read-only verify reported a saved change as 'missing' until the next writer opened. The reads
    were right all along. Only what should already be materialised can be wrong."""
    from revisionworx.workspace import CommittedNotApplied
    ws = Workspace.create(share, "t")

    def fail(entry):
        raise OSError("injected: crash while materialising")
    monkeypatch.setattr(ws, "_apply", fail)
    with pytest.raises(CommittedNotApplied):
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    with Workspace.open(share) as reader:
        report = reader.verify()
        assert report["problems"] == [] and report["applied_seq"] < report["head_seq"]
        assert reader.get("root", RID)["name"] == "x"
    monkeypatch.undo()
    ws.close()
    atomicio.atomic_write_json(share / ".revisionworx" / "records" / "settings" / "workspace.json", {"tampered": True, REV_FIELD: 1})
    with Workspace.open(share) as reader:
        assert [p["problem"] for p in reader.verify()["problems"]] == ["mismatch"]     # damage is still damage


def test_verify_still_fails_on_a_real_problem_beside_a_fenced_entry(share):
    from revisionworx import cli
    ws = Workspace.create(share, "t")
    seq = ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    paused_past_checks(ws, lambda: Workspace.break_writer_lock(share, ws._lock_token, "unresponsive"))
    with pytest.raises(LockLostError):
        ws.commit("t", [RecordPut("root", OTHER, {"name": "y"}, expect_rev=0)])
    ws._lock_token = None
    ws.close()
    atomicio.atomic_write_json(share / ".revisionworx" / "records" / "root" / f"{RID}.json", {"name": "bad", REV_FIELD: seq})
    with pytest.raises(SystemExit) as exc:
        cli.main(["-w", str(share), "verify"])
    assert exc.value.code == 1


def test_an_unreadable_writer_lock_has_a_way_out(share, capsys):
    """Was a dead end: every writer was told '?@? holds it', `lock show` raised a raw ValueError and `lock break` could
    not name the token. No RevisionWorx crash writes this file partially, but a disk fault could."""
    from revisionworx import cli
    with Workspace.create(share, "t"):
        pass
    lock = share / ".revisionworx" / "locks" / "writer.lock"
    lock.write_bytes(b"\x00garbage")
    assert Workspace.read_writer_lock(share)["unreadable"] is True
    assert cli.main(["-w", str(share), "lock", "show"]) in (0, None)
    with pytest.raises(WorkspaceError):
        Workspace.open(share, writable=True)
    with pytest.raises(WorkspaceCorruptError, match="clear-orphan") as exc:
        Workspace.break_writer_lock(share, "?", "the file is damaged")
    assert not lock.exists()
    orphan = [p for p in lock.parent.glob(".releasing-*")]
    assert len(orphan) == 1 and orphan[0].name in str(exc.value)    # the message names the file to clear
    with pytest.raises(WorkspaceCorruptError):                     # still blocked: its writer is unknown
        Workspace.open(share, writable=True)
    assert Workspace.clear_orphaned_release(share, orphan[0].name, "writer stopped")["outcome"] == "completed"
    with Workspace.open(share, writable=True) as ws:
        assert ws.verify()["problems"] == []
    capsys.readouterr()


def test_breaking_needs_the_token_when_the_lock_is_readable(share):
    with Workspace.create(share, "t"):
        pass
    holder = Workspace.open(share, writable=True)
    with pytest.raises(WorkspaceError, match="changed since it was inspected"):
        Workspace.break_writer_lock(share, "?", "guessing")
    assert Workspace.read_writer_lock(share)["token"] == holder._lock_token
    holder.close()


# ------------------------------------------------------------------------ durability of new folders
def test_new_folders_are_made_durable_before_anything_refers_to_them(world, monkeypatch):
    """A synced file in a folder whose own entry was never synced can vanish with the folder on power loss, so a journal
    entry could refer to content that is gone. Only the ordering can be tested here; power loss itself is not."""
    if atomicio.IS_WINDOWS:
        pytest.skip("Windows cannot fsync a directory; NTFS journals metadata")
    _, ws, _, doc, path = world
    synced, real = [], atomicio._fsync_dir

    def spy(directory):
        synced.append(Path(directory))
        return real(directory)
    monkeypatch.setattr(atomicio, "_fsync_dir", spy)
    store = content.ContentStore(ws)
    shutil.rmtree(ws.meta_dir / "content")
    path.write_bytes(b"new content in a new folder")
    stored = store.store_file(path)
    final = store.path_for(stored.sha256)
    assert final.parent.parent in synced, "the new aa/ folder's entry was never synced"
    assert ws.meta_dir / "content" / "sha256" in synced and ws.meta_dir / "content" in synced and ws.meta_dir in synced
    assert final.parent in synced
    synced.clear()
    ws.write_blob("baseline", {"a": 1})
    assert ws.meta_dir / "blobs" in synced, "the new blob kind's folder entry was never synced"


# ------------------------------------------------------------------------ leftovers next to working files
def test_old_working_file_temp_files_are_swept_but_nothing_else(world):
    """A crash during revert or restore can leave a temp file beside the working file, in the company's folder."""
    _, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    old = [path.with_name(f".{path.name}.rwx-deadbeef.tmp"), path.with_name(f"..{path.name}.rwx-deadbeef.tmp.ab12.tmp")]
    young = path.with_name(f".{path.name}.rwx-cafecafe.tmp")
    theirs = [path.with_name(".other.ipt.rwx-deadbeef.tmp"), path.with_name(".~lock.bracket.ipt#"),
              path.with_name(f".{path.name}.tmp")]
    for p in [*old, young, *theirs]:
        p.write_bytes(b"x")
    for p in [*old, *theirs]:
        os.utime(p, (1, 1))
    D.cancel_checkout(ws, doc["id"], co["token"], revert=True)
    assert not any(p.exists() for p in old)
    assert young.exists() and all(p.exists() for p in theirs)


# ------------------------------------------------------------------------ saved, with a lost answer
def test_a_saved_checkout_hands_back_its_token_when_materialising_fails(world, monkeypatch):
    """Claude B's A6. The change IS saved. Without its token only an administrator could have released the checkout."""
    from revisionworx.workspace import CommittedNotApplied
    _, ws, _, doc, path = world

    def fail(entry):
        raise OSError("injected: disk full while materialising")
    monkeypatch.setattr(ws, "_apply", fail)
    with pytest.raises(CommittedNotApplied) as exc:
        D.checkout(ws, doc["id"])
    token = exc.value.result["token"]
    monkeypatch.undo()
    assert ws.get("checkout", doc["id"])["token"] == token
    D.cancel_checkout(ws, doc["id"], token)


def test_a_missing_working_file_has_its_own_error(world):
    """Claude B's A4. The GUI was matching the words 'does not exist'."""
    _, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.unlink()
    with pytest.raises(D.MissingFileError):
        D.checkin(ws, doc["id"], co["token"], "edit")
    assert issubclass(D.MissingFileError, D.DocumentError)


# ------------------------------------------------------------------------ immutable history
def test_history_audit_is_clean_on_normal_use(world):
    _, ws, _, doc, path = world
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"v2")
    rev = D.checkin(ws, doc["id"], co["token"], "second")
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev["id"], state)
    assert D.audit_history(ws) == []


@pytest.mark.parametrize("change, problem", [
    ({"sha256": "0" * 64}, "immutable-field-changed"),
    ({"description": "rewritten"}, "immutable-field-changed"),
    ({"transitions": []}, "transitions-rewritten"),
], ids=["content", "description", "transitions"])
def test_history_audit_finds_a_revision_rewritten_through_the_journal(world, change, problem):
    """commit() is generic, so a bug could rewrite a revision. Nothing reported that. A published revision must never change."""
    _, ws, _, doc, path = world
    rid = doc["latest_rev_id"]
    D.transition(ws, rid, "in_review")
    rev = ws.get("revision", rid)
    forged = {k: v for k, v in rev.items() if k != REV_FIELD}
    forged.update(change)
    ws.commit("bug", [RecordPut("revision", rid, forged, expect_rev=rev[REV_FIELD])])
    found = D.audit_history(ws)
    assert [p["problem"] for p in found] == [problem] and found[0]["revision_id"] == rid


def test_history_audit_finds_a_revision_deleted_through_the_journal(world):
    from revisionworx.workspace import RecordDelete
    _, ws, _, doc, path = world
    rid = doc["latest_rev_id"]
    ws.commit("bug", [RecordDelete("revision", rid, expect_rev=ws.get("revision", rid)[REV_FIELD])])
    assert "revision-deleted" in [p["problem"] for p in D.audit_history(ws)]


# ------------------------------------------------------------------------ damage to caches and identity
def test_a_damaged_state_file_costs_a_replay_not_the_workspace(share):
    """state.json is a cache. Was a raw ValueError or KeyError on every open, though the journal was fine."""
    with Workspace.create(share, "t") as ws:
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
    state = share / ".revisionworx" / "state.json"
    for damage in (b"\x00not json", b'{"something": "else"}', b"[]"):
        state.write_bytes(damage)
        with Workspace.open(share) as reader:
            assert reader.get("root", RID)["name"] == "x"
        with Workspace.open(share, writable=True) as ws:
            assert ws.applied_seq() == ws.head_seq()
            assert ws.verify()["problems"] == []


def test_a_damaged_identity_file_is_reported_as_such(share):
    from revisionworx.workspace import WorkspaceFormatError
    with Workspace.create(share, "t"):
        pass
    (share / ".revisionworx" / "workspace.json").write_bytes(b"\x00not json")
    with pytest.raises(WorkspaceFormatError):
        Workspace.open(share)


def _lose_an_entry(share) -> None:
    with Workspace.open(share, writable=True) as ws:
        for name in "abc":
            ws.commit("t", [RecordPut("root", "root_" + name * 32, {"name": name}, expect_rev=0)])
        head = ws.head_seq()
    (share / ".revisionworx" / "journal" / f"{head - 1:020d}.json").unlink()


def test_a_gap_appearing_under_a_long_lived_handle_stops_writes_and_verify(share):
    """Opening already refuses a journal with a gap, for reading too. A handle opened before the entry was lost has to
    notice as well, rather than carry on as if the journal were whole."""
    with Workspace.create(share, "t"):
        pass
    reader, writer = Workspace.open(share), None
    reader.get("root", RID)
    writer = Workspace.open(share, writable=True)
    writer.get("root", RID)
    writer.close()
    _lose_an_entry(share)
    with pytest.raises(WorkspaceCorruptError, match="gaps"):
        Workspace.open(share)
    with pytest.raises(WorkspaceCorruptError, match="gaps"):
        reader.verify()
    assert reader.get("root", "root_" + "a" * 32)["name"] == "a"   # applied already: reads stay available
    reader.close()


@pytest.mark.parametrize("entry", [
    {"seq": 0, "op": "t"}, {"seq": 0, "op": "t", "records": "nope"}, {"seq": 0, "op": "t", "records": [{"id": "x"}]},
    {"seq": 0, "op": "t", "records": [17]}], ids=["no-records", "not-a-list", "no-kind", "not-an-object"])
def test_a_malformed_entry_is_corruption_not_a_crash(share, entry):
    """Was a raw KeyError or TypeError from wherever it was first read."""
    with Workspace.create(share, "t") as ws:
        seq = ws.head_seq() + 1
        atomicio.atomic_write_json(ws.meta_dir / "journal" / f"{seq:020d}.json", {**entry, "seq": seq})
        with pytest.raises(WorkspaceCorruptError):
            ws.read_entry(seq)
        with pytest.raises(WorkspaceCorruptError):
            ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])


# ------------------------------------------------------------------------ being a good neighbour on Windows
def test_reading_working_files_goes_through_the_sharing_reader(world, monkeypatch):
    """Python's open() blocks a rename while it reads. A CAD program saving by rename would fail because RevisionWorx was
    hashing the file. What this asserts holds everywhere; what the reader does is proven on Windows in test_windows_sharing."""
    _, ws, _, doc, path = world
    real, opened = atomicio._open_for_read, []

    def spy(p):
        opened.append(Path(p))
        return real(p)
    monkeypatch.setattr(atomicio, "_open_for_read", spy)
    content.hash_file(path)
    assert opened == [path]
    opened.clear()
    path.write_bytes(b"new bytes")
    content.ContentStore(ws).store_file(path)
    assert path in opened


def test_gc_stops_as_soon_as_it_has_lost_the_lock(world, monkeypatch):
    """It cannot fence a file deletion, but it can stop at the next object rather than carry on for the whole list."""
    share, ws, _, doc, path = world
    store = content.ContentStore(ws)
    orphans = []
    for i in range(3):
        f = path.with_name(f"orphan{i}.bin")
        f.write_bytes(b"orphan %d" % i)
        orphans.append(store.store_file(f).sha256)
        f.unlink()
    real, removed = store.path_for, []
    original_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name in orphans:
            removed.append(self.name)
            Workspace.break_writer_lock(share, ws._lock_token, "unresponsive")   # lost after the first deletion
        return original_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    assert real
    with pytest.raises(LockLostError):
        D.gc_content(ws, apply=True, min_age_seconds=0.0)
    assert len(removed) == 1
    ws._lock_token = None


# ------------------------------------------------------------------------ the command line, saved but not materialised
def test_cli_checkout_keeps_the_token_when_the_change_is_saved_but_not_materialised(share, tmp_path, monkeypatch, capsys):
    from revisionworx import cli
    ws = Workspace.create(share, "t")
    projects.add_root(ws, "Engineering", share / "Engineering")
    project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    path = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "bracket.ipt"
    path.write_bytes(b"v1")
    doc, _ = D.register_document(ws, project["id"], path)
    ws.close()
    state = tmp_path / "state"
    real = Workspace._apply

    def fail(self, entry):
        if entry["op"] == "document.checkout":
            raise OSError("injected: disk full while materialising")
        return real(self, entry)
    monkeypatch.setattr(Workspace, "_apply", fail)
    code = cli.main(["-w", str(share), "--state-dir", str(state), "doc", "checkout", doc["id"]])
    monkeypatch.undo()
    out = capsys.readouterr()
    assert code in (0, None), out
    assert "saved" in (out.out + out.err).lower()
    token = CheckoutTokens(state).get(_workspace_id(share), doc["id"])
    with Workspace.open(share, writable=True) as check:
        assert check.get("checkout", doc["id"])["token"] == token


def _workspace_id(share) -> str:
    return atomicio.read_json(share / ".revisionworx" / "workspace.json")["workspace_id"]


# ------------------------------------------------------------------------ small public names for the UI
def test_health_reports_instead_of_raising(world):
    share, ws, _, doc, path = world
    report = ws.health()
    assert report["ok"] and report["reason"] is None and report["pending"] == 0
    assert report["head_seq"] == report["applied_seq"] == ws.head_seq()
    entry = sorted((ws.meta_dir / "journal").glob("*.json"))[-1]
    data = atomicio.read_json(entry)
    data["op"] = "tampered"
    original = entry.read_bytes()
    atomicio.atomic_write_json(entry, data)
    with Workspace.open(share) as reader:
        damaged = reader.health()
        assert damaged["ok"] is False and "digest" in damaged["reason"]
    entry.write_bytes(original)
    with Workspace.open(share) as reader:
        assert reader.health()["ok"]


def test_index_project_files_uses_the_healing_path(share, cache):
    ws = Workspace.create(share, "t")
    projects.add_root(ws, "Engineering", share / "Engineering")
    folder = share / "Engineering" / "Old job"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"x")
    project = projects.adopt_project(ws, folder, name="Old job")
    with open_index(ws, cache) as idx:
        assert [f["path"] for f in idx.project_files(project["id"])] == ["a.txt"]
    assert D.require_document is D._require_doc
    ws.close()
