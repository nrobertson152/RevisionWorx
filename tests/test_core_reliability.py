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
