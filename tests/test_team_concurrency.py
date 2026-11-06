# SPDX-License-Identifier: GPL-3.0-or-later
"""Several computers at once, and computers dying (ADR-024).

Real processes, each with its own local state as a separate computer would have, on one local disk. That checks the protocol, not a
network: it says nothing about how an SMB or NFS server behaves. The stale-cache tests simulate what's known about client caches.
"""
from __future__ import annotations

import errno
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from revisionworx import accounts, atomicio, documents as D, projects, shared
from revisionworx.projects import NewProject
from revisionworx.shared import SharedWorkspace, StaleViewError
from revisionworx.workspace import CommittedNotApplied, ConflictError, LockLostError, RecordPut, Workspace, WorkspaceError, WorkspaceLockedError
from team_helpers import Crew, computer, process_env

A = "prj_" + "a" * 32
B = "prj_" + "b" * 32


@pytest.fixture
def crew(tmp_path):
    return Crew(tmp_path)


def test_no_update_is_lost_when_everyone_writes_at_once(crew):
    gate = str(crew.tmp_path / "gate")
    procs = [crew.spawn(name, "count", times=25, record=A, gate=gate) for name in crew.sessions]
    crew.go(gate)
    results = [crew.finish(p) for p in procs]
    won = sorted(n for r in results for n in r["won"])
    assert won == list(range(1, 25 * len(crew.sessions) + 1))       # each value was reached exactly once
    _head, _anchor, projects_seen, _co, _rev = crew.all_agree()
    assert projects_seen[A]["n"] == 25 * len(crew.sessions)


def test_two_windows_on_one_computer_share_its_copy_of_the_journal(crew):
    """Two processes reading and writing the same local copy at once. The other people's computers have their own."""
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from revisionworx.shared import Session\n"
        "from revisionworx.workspace import RecordPut, Workspace, ConflictError\n"
        "folder, state, wid, account, public, seed, record = sys.argv[1:8]\n"
        "session = Session(wid, account, account, public, bytes.fromhex(seed))\n"
        "done = 0\n"
        "while done < 15:\n"
        "    try:\n"
        "        with Workspace.open(Path(folder), writable=True, session=session, state_dir=state, wait=60.0) as ws:\n"
        "            rec = ws.get('project', record) or {'n': 0, '_rev': 0}\n"
        "            ws.commit('count', [RecordPut('project', record, {'n': rec['n'] + 1}, expect_rev=rec['_rev'])])\n"
        "        done += 1\n"
        "    except ConflictError:\n"
        "        pass\n")
    session = crew.sessions["u1"]
    args = [sys.executable, "-c", code, str(crew.folder), str(computer(crew.tmp_path, "u1")), session.workspace_id, "u1", session.public, session.seed.hex(), A]
    procs = [subprocess.Popen(args, env=process_env(), stderr=subprocess.PIPE, text=True) for _ in range(3)]
    for proc in procs:
        _out, err = proc.communicate(timeout=240)
        assert proc.returncode == 0, err
    assert crew.all_agree()[2][A]["n"] == 45


def _with_documents(crew, count):
    with crew.open("boss", writable=True) as ws:
        root = projects.add_root(ws, "Projects", crew.folder)
        project = projects.create_project(ws, NewProject(name="HPU", root_id=root))
        folder = projects.project_folder(ws, project)
        docs = []
        for n in range(count):
            path = folder / f"part{n}.ipt"
            path.write_bytes(f"part {n}".encode())
            docs.append(D.register_document(ws, project["id"], path)[0]["id"])
    return docs


def test_a_checkout_has_exactly_one_winner(crew):
    docs = _with_documents(crew, 12)
    gate = str(crew.tmp_path / "gate")
    procs = [crew.spawn(name, "checkout", documents=docs, gate=gate) for name in crew.sessions]
    crew.go(gate)
    results = [crew.finish(p) for p in procs]
    won = [d for r in results for d in r["won"]]
    assert sorted(won) == sorted(docs)                             # one winner each, and nobody thinks they have one they don't
    assert sum(r["conflicts"] for r in results) == 12 * (len(crew.sessions) - 1)
    _head, _anchor, _prj, checkouts, _rev = crew.all_agree()
    owners = {r["account"]: set(r["won"]) for r in results}
    for doc, co in checkouts.items():
        assert doc in owners[co["owner"]["account"]]


def test_a_computer_that_dies_during_its_turn_is_cleared_and_nobody_stays_blocked(crew):
    holder = crew.spawn("u1", "hold")
    deadline = time.monotonic() + 60
    while not (crew.tmp_path / "u1.holding").exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    holder.kill()
    holder.communicate()
    assert Workspace.read_writer_lock(crew.folder)["account"] == "u1"
    waiter = crew.spawn("u2", "commit", record=B, lease=1.5, wait=30.0)
    assert crew.finish(waiter)["won"] == [B]
    with crew.open("u3") as ws:
        audits = [a for a in ws.list("audit").values() if a.get("event") == "writer-lock-cleared"]
    assert len(audits) == 1 and audits[0]["previous_holder"]["account"] == "u1" and audits[0]["automatic"] is True
    assert audits[0]["by"]["account"] == "u2"
    crew.all_agree()


def test_a_slow_holder_that_keeps_beating_is_left_alone(crew, monkeypatch):
    monkeypatch.setattr(shared, "BEAT_SECONDS", 0.1)
    monkeypatch.setattr(shared, "LEASE_SECONDS", 1.0)
    with crew.open("u1", writable=True) as holder:
        with pytest.raises(WorkspaceLockedError) as caught:
            crew.open("u2", writable=True, wait=2.5)
        assert caught.value.holder["account"] == "u1"
        holder.commit("t", [RecordPut("project", A, {"v": 1})])     # still its turn
    with crew.open("u2") as ws:
        assert ws.get("project", A)["v"] == 1


def test_a_silent_holders_commit_is_refused_after_its_lock_is_cleared(crew, monkeypatch):
    """A laptop's lid closes during its turn, and it wakes up after someone else has taken over."""
    monkeypatch.setattr(shared, "LEASE_SECONDS", 0.3)
    monkeypatch.setattr(shared, "BEAT_SECONDS", 3600.0)
    sleeper = crew.open("u1", writable=True)
    with crew.open("u2", writable=True, wait=10.0) as ws:
        ws.commit("t", [RecordPut("project", A, {"who": "u2"})])
    with pytest.raises(LockLostError):
        sleeper.commit("t", [RecordPut("project", A, {"who": "u1"})])
    sleeper.close()
    with crew.open("u3") as ws:
        assert ws.get("project", A)["who"] == "u2"
    crew.all_agree()


def test_a_writer_stopped_at_the_last_moment_cant_publish_on_a_stale_view(crew, monkeypatch):
    """The worst timing there is: past every lock check, about to publish, and stopped while someone else commits. This is what the
    single-computer design needed fencing for. Here the number it's about to publish under is already taken."""
    stopped = crew.open("u1", writable=True)
    real_publish = atomicio.publish_exclusive
    done = []

    def someone_else_gets_in_first(path, data):
        if not done and Path(path).parent.name == "journal":
            done.append(True)
            monkeypatch.setattr(atomicio, "publish_exclusive", real_publish)
            session = crew.sessions["u2"]
            other = SharedWorkspace(stopped.meta_dir, stopped.info, False, None, session=session, state_dir=computer(crew.tmp_path, "u2"))
            other.sync(fresh=True)
            other.writable, other._lock_token = True, "someone-elses-token"
            other._check_lock_still_held = lambda: None             # as if its lock had been granted after this one's was cleared
            other._base = other._replica.head()
            other.commit("t", [RecordPut("project", A, {"who": "u2"})])
            other._lock_token = None
            other.close()
            monkeypatch.setattr(atomicio, "publish_exclusive", someone_else_gets_in_first)
        return real_publish(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", someone_else_gets_in_first)
    with pytest.raises(StaleViewError):
        stopped.commit("t", [RecordPut("project", B, {"who": "u1"})])
    monkeypatch.setattr(atomicio, "publish_exclusive", real_publish)
    stopped.close()
    with crew.open("u3") as ws:
        assert ws.get("project", A)["who"] == "u2" and ws.get("project", B) is None
    crew.all_agree()


def test_a_change_that_arrived_during_this_turn_stops_the_commit(crew):
    """Nobody should have got in during a turn. If they did, what this handle read may be out of date, even with no record in common:
    a duplicate-name check, say. So it stops, rather than relying on every operation having declared what it read."""
    u1 = crew.open("u1", writable=True)
    other = SharedWorkspace(u1.meta_dir, u1.info, False, None, session=crew.sessions["u2"], state_dir=computer(crew.tmp_path, "u2"))
    other.sync(fresh=True)
    other.writable, other._lock_token = True, "someone-elses-token"
    other._check_lock_still_held = lambda: None
    other._base = other._replica.head()
    other.commit("t", [RecordPut("project", A, {"who": "u2"})])
    other._lock_token = None
    other.close()
    with pytest.raises(StaleViewError):
        u1.commit("t", [RecordPut("project", B, {"who": "u1"})])
    u1.close()
    with crew.open("u1", writable=True) as ws:                     # reloading is all it takes
        ws.commit("t", [RecordPut("project", B, {"who": "u1"})])


# ------------------------------------------------------------------ what's known about client caches, simulated
class StaleFolder:
    """A network client's caches: for a while, entries published by others can't be seen. Directory listings on Windows can lag by about
    ten seconds and "not found" answers by about five (the Windows redirector's documented defaults). What can't be cached is exclusive
    create, which the server decides."""

    def __init__(self, monkeypatch, ws, visible_up_to):
        self.up_to = visible_up_to
        self.real_read = SharedWorkspace._read_from_folder
        self.real_list = Workspace.journal_seqs
        limit = self

        def read(handle, seq):
            if seq > limit.up_to:
                raise FileNotFoundError(seq)
            return limit.real_read(handle, seq)

        def listing(handle):
            return [s for s in limit.real_list(handle) if s <= limit.up_to]
        monkeypatch.setattr(SharedWorkspace, "_read_from_folder", read)
        monkeypatch.setattr(Workspace, "journal_seqs", listing)


def test_a_stale_view_cant_publish_and_catches_up_once_the_cache_clears(crew, monkeypatch):
    with crew.open("u2", writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    with crew.open("u1") as ws:
        before = ws._replica.head()
    with crew.open("u2", writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 2}, expect_rev=before)])
    head = before + 1
    monkeypatch.setattr(shared, "COLLISION_PATIENCE", 0.3)
    monkeypatch.setattr(shared, "HINT_PATIENCE", 0.1)
    cache = StaleFolder(monkeypatch, None, before)
    # u1 can't see entry `head`: it thinks A is at revision `before`, and the journal ends there.
    ws = crew.open("u1", writable=True)
    assert ws._replica.head() == before
    with pytest.raises(StaleViewError):
        ws.commit("t", [RecordPut("project", A, {"n": "stale"}, expect_rev=before)])
    cache.up_to = 10 ** 6                                          # the cache clears
    with pytest.raises(StaleViewError):
        ws.commit("t", [RecordPut("project", A, {"n": "stale"}, expect_rev=before)])
    ws.close()
    with crew.open("u1", writable=True) as ws:
        assert ws.get("project", A)["n"] == 2 and ws._replica.head() == head
        with pytest.raises(ConflictError):
            ws.commit("t", [RecordPut("project", A, {"n": "stale"}, expect_rev=before)])
        ws.commit("t", [RecordPut("project", A, {"n": 3}, expect_rev=head)])
    crew.all_agree()


def test_the_hint_gets_a_writer_past_a_stale_not_found_without_a_conflict(crew, monkeypatch):
    """Usually the next writer doesn't even hit that. The last writer left a note saying how far the journal has got."""
    with crew.open("u1") as ws:
        before = ws._replica.head()
    with crew.open("u2", writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    calls = []
    real = SharedWorkspace._read_from_folder

    def stale_once(handle, seq):
        if seq == before + 1 and not calls:
            calls.append(seq)
            raise FileNotFoundError(seq)
        return real(handle, seq)
    monkeypatch.setattr(SharedWorkspace, "_read_from_folder", stale_once)
    with crew.open("u1", writable=True) as ws:
        assert ws._replica.head() == before + 1
        ws.commit("t", [RecordPut("project", A, {"n": 2}, expect_rev=before + 1)])


def test_a_lost_reply_doesnt_turn_a_published_change_into_a_failure(crew, monkeypatch):
    """A network can do the rename and lose the answer. Publishing then says the name is taken, by this very entry."""
    real = atomicio.publish_exclusive
    done = []

    def answer_lost(path, data):
        real(path, data)
        if not done and Path(path).parent.name == "journal":
            done.append(True)
            raise atomicio.ExclusiveCreateError(str(path))
    monkeypatch.setattr(atomicio, "publish_exclusive", answer_lost)
    with crew.open("u1", writable=True) as ws:
        seq = ws.commit("t", [RecordPut("project", A, {"n": 1})])
        assert ws.get("project", A) == {"n": 1, "_rev": seq}
        assert seq == ws.head_seq()
    monkeypatch.setattr(atomicio, "publish_exclusive", real)
    crew.all_agree()


def test_a_lost_reply_to_taking_the_lock_still_gets_the_turn(crew, monkeypatch):
    real = atomicio.publish_exclusive
    done = []

    def answer_lost(path, data):
        real(path, data)
        if not done and Path(path).name == "writer.lock":
            done.append(True)
            raise atomicio.ExclusiveCreateError(str(path))
    monkeypatch.setattr(atomicio, "publish_exclusive", answer_lost)
    with crew.open("u1", writable=True, wait=1.0) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    assert Workspace.read_writer_lock(crew.folder) is None


def test_an_io_error_after_publishing_is_reported_as_saved(crew, monkeypatch):
    real = atomicio.publish_exclusive
    done = []

    def sync_fails(path, data):
        real(path, data)
        if not done and Path(path).parent.name == "journal":
            done.append(True)
            raise OSError(5, "the folder sync failed")
    monkeypatch.setattr(atomicio, "publish_exclusive", sync_fails)
    with crew.open("u1", writable=True) as ws:
        with pytest.raises(CommittedNotApplied):
            ws.commit("t", [RecordPut("project", A, {"n": 1})], txid="ab" * 16)
        assert ws.transaction_outcome("ab" * 16)["state"] == "committed"


# ------------------------------------------------------------------ what real shares did, in .github/workflows/shares.yml
def _says_busy_at_first(real, errno_code, times, only=lambda *_args: True):
    """A file operation that a network filesystem refuses for a moment. The Linux SMB client against Samba answered EBUSY and EIO like this."""
    left = [times]

    def op(*args, **kwargs):
        if left[0] and only(*args):
            left[0] -= 1
            raise OSError(errno_code, os.strerror(errno_code))
        return real(*args, **kwargs)
    return op, left


@pytest.mark.parametrize("code", [errno.EBUSY, errno.EIO, errno.ESTALE])
def test_releasing_the_lock_rides_out_a_share_saying_try_again(crew, monkeypatch, code):
    ws = crew.open("u1", writable=True)
    busy, left = _says_busy_at_first(os.replace, code, 3, lambda src, _dst: Path(src).name == "writer.lock")
    monkeypatch.setattr(os, "replace", busy)
    ws.close()
    assert left[0] == 0 and Workspace.read_writer_lock(crew.folder) is None    # or everyone would wait a minute for it to clear


@pytest.mark.parametrize("code", [errno.EBUSY, errno.EIO])
def test_clearing_a_lock_rides_out_a_share_saying_try_again(crew, monkeypatch, code):
    monkeypatch.setattr(shared, "LEASE_SECONDS", 0.1)
    monkeypatch.setattr(shared, "BEAT_SECONDS", 3600.0)
    crew.open("u1", writable=True)                                 # and it never comes back
    busy, left = _says_busy_at_first(os.replace, code, 2, lambda src, _dst: Path(src).name == "writer.lock")
    monkeypatch.setattr(os, "replace", busy)
    with crew.open("u2", writable=True, wait=10.0) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    assert left[0] == 0


@pytest.mark.parametrize("code", [errno.EBUSY, errno.EIO])
def test_publishing_rides_out_a_share_saying_try_again(crew, monkeypatch, code):
    busy, left = _says_busy_at_first(atomicio.publish_exclusive, code, 2, lambda path, _data: Path(path).parent.name == "journal")
    monkeypatch.setattr(atomicio, "publish_exclusive", busy)
    with crew.open("u1", writable=True) as ws:
        seq = ws.commit("t", [RecordPut("project", A, {"n": 1})])
        assert ws.get("project", A) == {"n": 1, "_rev": seq}
    assert left[0] == 0


def test_taking_the_lock_rides_out_a_share_saying_try_again(crew, monkeypatch):
    busy, left = _says_busy_at_first(atomicio.publish_exclusive, errno.EBUSY, 2, lambda path, _data: Path(path).name == "writer.lock")
    monkeypatch.setattr(atomicio, "publish_exclusive", busy)
    with crew.open("u1", writable=True, wait=10.0) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    assert left[0] == 0


def test_a_share_that_keeps_saying_try_again_is_a_clean_failure_in_the_end(crew, monkeypatch):
    monkeypatch.setattr(shared, "TRANSIENT_PATIENCE", 0.1)
    monkeypatch.setattr(shared, "COLLISION_PATIENCE", 0.2)
    with crew.open("u1", writable=True) as ws:
        busy, _left = _says_busy_at_first(atomicio.publish_exclusive, errno.EBUSY, 10 ** 9, lambda path, _data: Path(path).parent.name == "journal")
        monkeypatch.setattr(atomicio, "publish_exclusive", busy)
        with pytest.raises(OSError):
            ws.commit("t", [RecordPut("project", A, {"n": 1})])
        assert ws.get("project", A) is None


def test_reading_an_entry_rides_out_a_share_saying_try_again(crew, monkeypatch):
    with crew.open("u1") as ws:
        before = ws._replica.head()
    with crew.open("u2", writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    calls = []
    real = os.stat

    def stat_busy_once(path, *args, **kwargs):
        if str(path).endswith(f"{before + 1:020d}.json") and not calls:
            calls.append(path)
            raise OSError(errno.EBUSY, "busy")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(os, "stat", stat_busy_once)
    with crew.open("u1") as ws:
        assert calls and ws.get("project", A)["n"] == 1


def test_a_share_saying_try_again_doesnt_mean_the_lock_is_lost(crew, monkeypatch):
    """Only an answer counts. Being told "lost" would throw away a good turn, and being wrong about it isn't safe either way."""
    monkeypatch.setattr(shared, "TRANSIENT_PATIENCE", 0.1)
    ws = crew.open("u1", writable=True)
    real = Workspace._read_holder

    def busy(_path):
        raise OSError(errno.EIO, "input/output error")
    monkeypatch.setattr(Workspace, "_read_holder", staticmethod(busy))
    with pytest.raises(WorkspaceError, match="can't be checked") as caught:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    assert not isinstance(caught.value, LockLostError)
    monkeypatch.setattr(Workspace, "_read_holder", staticmethod(real))
    ws.commit("t", [RecordPut("project", A, {"n": 2})])
    ws.close()


def test_logging_in_just_after_the_account_was_made_gets_past_a_stale_not_found(crew, monkeypatch):
    """The account was made a moment ago on another computer, and this one's cache still says the entry isn't there. The hint says it is."""
    with crew.open("boss", writable=True) as ws:
        accounts.create_account(ws, "newcomer", "a starting password")
    with crew.open("boss") as ws:
        newest = ws._replica.head()
    calls = []
    real = SharedWorkspace._read_from_folder
    place = computer(crew.tmp_path, "newcomer")

    def stale_at_first(handle, seq):
        if seq == newest and len(calls) < 3 and handle._replica.path.is_relative_to(place):
            calls.append(seq)
            raise FileNotFoundError(seq)
        return real(handle, seq)
    monkeypatch.setattr(SharedWorkspace, "_read_from_folder", stale_at_first)
    made = accounts.login(crew.folder, "newcomer", "a starting password", state_dir=place)
    assert calls and made["session"].account == "newcomer"


def test_a_wrong_password_doesnt_wait_when_nothing_is_promised(crew, monkeypatch):
    monkeypatch.setattr(shared, "HINT_PATIENCE", 30.0)
    started = time.monotonic()
    with pytest.raises(accounts.LoginFailed):
        accounts.login(crew.folder, "u1", "not the password", state_dir=computer(crew.tmp_path, "u1"))
    assert time.monotonic() - started < 5.0


def test_an_io_error_before_publishing_is_an_ordinary_failure(crew, monkeypatch):
    def refuses(path, data):
        raise OSError(13, "permission denied")
    with crew.open("u1", writable=True) as ws:
        monkeypatch.setattr(atomicio, "publish_exclusive", refuses)
        with pytest.raises(OSError):
            ws.commit("t", [RecordPut("project", A, {"n": 1})])
        monkeypatch.undo()
        accounts.KDF = dict(accounts.KDF)
    with crew.open("u1") as ws:
        assert ws.get("project", A) is None


def test_a_lock_someone_else_holds_is_put_back_if_it_changed_while_clearing(crew):
    """Clearing goes by what was moved, not what was read earlier: the lock might have changed hands in between."""
    holder = crew.open("u1", writable=True)
    clearer = crew.open("u2")
    stale_view = dict(Workspace.read_writer_lock(crew.folder), token="an-older-token")
    clearer._clear_lock(stale_view, "test", automatic=True)
    assert Workspace.read_writer_lock(crew.folder)["token"] == holder._lock_token
    holder.commit("t", [RecordPut("project", A, {"n": 1})])
    holder.close()
    clearer.close()


def test_closing_after_the_lock_changed_hands_doesnt_take_the_new_holders_lock(crew, monkeypatch):
    monkeypatch.setattr(shared, "LEASE_SECONDS", 0.2)
    monkeypatch.setattr(shared, "BEAT_SECONDS", 3600.0)
    old = crew.open("u1", writable=True)
    new = crew.open("u2", writable=True, wait=10.0)
    old.close()
    assert Workspace.read_writer_lock(crew.folder)["token"] == new._lock_token
    new.commit("t", [RecordPut("project", A, {"n": 1})])
    new.close()


def test_an_unreadable_lock_file_clears_like_any_other(crew, monkeypatch):
    monkeypatch.setattr(shared, "LEASE_SECONDS", 0.2)
    (crew.folder / ".revisionworx" / "locks" / "writer.lock").write_bytes(b"garbage")
    with crew.open("u1", writable=True, wait=10.0) as ws:
        ws.commit("t", [RecordPut("project", A, {"n": 1})])
    crew.all_agree()


def test_a_silence_is_judged_by_this_computers_clock_alone(crew, monkeypatch):
    """The holder's claimed time means nothing: a computer's clock can be wrong by hours. It's only how long it's looked the same."""
    monkeypatch.setattr(shared, "BEAT_SECONDS", 3600.0)
    holder = crew.open("u1", writable=True)
    claim = Workspace.read_writer_lock(crew.folder)
    assert shared.lock_silence(holder.meta_dir, {**claim, "acquired": "1999-01-01T00:00:00+00:00"}) == 0.0
    assert 0.0 <= shared.lock_silence(holder.meta_dir, claim) < 5.0
    beat = shared._beat_path(holder.meta_dir, claim["token"])
    atomicio.atomic_write_json(beat, {"beat": 1})
    assert shared.lock_silence(holder.meta_dir, claim) == 0.0      # it's alive: the wait starts again
    holder.close()


@pytest.mark.parametrize("token", ["../../evil", "a/b", "C:\\x", "", None, 5, "?"])
def test_a_token_in_a_lock_file_cant_name_a_file_elsewhere(token, tmp_path):
    assert shared._beat_path(tmp_path, token) is None


# ------------------------------------------------------------------ dying at every step
MAX_POINTS = 60


@pytest.mark.parametrize("do", ["commit", "checkout-one"])
def test_dying_before_every_file_change_leaves_a_consistent_workspace(tmp_path, do):
    """The victim dies before its Nth change, for every N until it gets through. Afterwards, every computer agrees, the next writer gets in
    (after the lock clears), and the operation either happened or didn't. No claim about power loss, where unsynced data can vanish too."""
    crew = Crew(tmp_path, users=("victim", "survivor"))
    docs = _with_documents(crew, MAX_POINTS + 1)
    finished = False
    for point in range(MAX_POINTS):
        target = "prj_" + f"{point:032x}"
        doc = docs[point]
        proc = crew.spawn("victim", do, record=target, document=doc, die_at=point, lease=0.2, wait=30.0, txid=f"{point:032x}")
        _out, err = proc.communicate(timeout=120)
        if proc.returncode == 0:
            finished = True
            break
        assert proc.returncode == 137, err                         # it died where it was told to, and of nothing else
        # the survivor gets a turn, clearing a lock if one was left
        monkey = shared.LEASE_SECONDS
        shared.LEASE_SECONDS = 0.2
        try:
            with crew.open("survivor", writable=True, wait=30.0) as ws:
                ws.commit("t", [RecordPut("project", "prj_" + f"{point + 1000:032x}", {"by": "survivor"})])
                happened = ws.transaction_outcome(f"{point:032x}")["state"] == "committed" if do == "commit" else ws.get("checkout", doc) is not None
                if do == "commit":
                    assert (ws.get("project", target) is not None) == happened     # all of it or none of it
        finally:
            shared.LEASE_SECONDS = monkey
        crew.all_agree()
    assert finished, f"never got through in {MAX_POINTS} crash points"
    assert point >= 3                                              # or the crash points weren't being reached
