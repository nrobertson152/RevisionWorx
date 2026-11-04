# SPDX-License-Identifier: GPL-3.0-or-later
"""Randomised schedule exploration of the writer-lock protocol (ADR-019 rev. 4).

Point fixes keep losing to the next interleaving, so this test checks the property itself. Writers
and an administrator run as real threads over the real Workspace code on a real directory, but a
seeded scheduler lets exactly one run at a time and may switch (or kill) an actor before every
filesystem operation. A kill is "sticky": all later operations in that actor also fail, so cleanup
handlers cannot do work a dead process could not do.

Oracles, all read back from the journal and the lock directory after the run:
  * authority never interleaves: effective entries never go token A, token B, token A;
  * no lost update: each effective write's expect_rev is the previous effective write of the record;
  * no unfenced evidence: a lock file may leave the scanned namespace only if its token is revoked.
  * reads match the journal: get() and list() equal a replay of the effective entries, whatever a
    stale writer later did to the record files (ADR-022).

No claim is made about SMB/NFS, power loss, or outside actors: only protocol processes on a local
filesystem, in-process threads standing in for processes. Set REVISIONWORX_SCHEDULE_SEEDS to widen.
"""
import os
import pathlib
import random
import threading

import pytest

from revisionworx import atomicio
from revisionworx.workspace import REV_FIELD, RecordPut, Workspace, WorkspaceError

RID = "root_" + "a" * 32
SEEDS = int(os.environ.get("REVISIONWORX_SCHEDULE_SEEDS", "300"))
EVIDENCE = ("writer.lock.revoked-", "writer.lock.broken-", "writer.lock.displaced-", "writer.lock.orphaned-")
HANDOFF_TIMEOUT = 30


class Killed(BaseException):
    """The actor's process died here. Not an Exception, so only `except BaseException` sees it."""


class Scheduler:
    def __init__(self, seed: int, kill_rate: float):
        self.rng = random.Random(seed)
        self.kill_rate = kill_rate
        self.baton: dict[threading.Thread, threading.Semaphore] = {}
        self.dead: set[threading.Thread] = set()
        self.live: list[threading.Thread] = []
        self.stuck = False

    def add(self, thread: threading.Thread) -> None:
        self.baton[thread] = threading.Semaphore(0)
        self.live.append(thread)

    def _wait(self, me: threading.Thread) -> None:
        if not self.baton[me].acquire(timeout=HANDOFF_TIMEOUT):
            self.stuck = True
            raise Killed("scheduler handoff timed out")

    def point(self) -> None:
        """Called before every filesystem operation an actor performs."""
        me = threading.current_thread()
        if me not in self.baton:
            return                                   # the test's main thread runs unscheduled
        if me in self.dead:
            raise Killed
        if self.rng.random() < self.kill_rate:
            self.dead.add(me)
            raise Killed
        nxt = self.rng.choice(self.live)
        if nxt is not me:
            self.baton[nxt].release()
            self._wait(me)
            if me in self.dead:
                raise Killed

    def finished(self) -> None:
        me = threading.current_thread()
        self.live.remove(me)
        if self.live:
            self.baton[self.rng.choice(self.live)].release()


def _scheduled(sched: Scheduler, fn):
    def wrapper(*args, **kwargs):
        sched.point()
        return fn(*args, **kwargs)
    return wrapper


def _writer(root: pathlib.Path, rng: random.Random, failures: list) -> None:
    for _ in range(3):
        try:
            ws = Workspace.open(root, writable=True)
        except WorkspaceError:
            continue
        try:
            for _ in range(rng.randint(1, 2)):
                current = ws.get("root", RID)
                rev = int(current[REV_FIELD]) if current else 0
                ws.commit("sched.put", [RecordPut("root", RID, {"name": "x"}, expect_rev=rev)])
        except WorkspaceError:
            pass
        finally:
            try:
                ws.close()
            except WorkspaceError:
                pass


def _admin(root: pathlib.Path, rng: random.Random, failures: list) -> None:
    for _ in range(2):
        holder = Workspace.read_writer_lock(root)
        if holder and holder.get("token"):
            try:
                Workspace.break_writer_lock(root, holder["token"], "schedule exploration")
            except WorkspaceError:
                pass


def check_invariants(root: pathlib.Path) -> None:
    with Workspace.open(root) as reader:
        previous_rev = 0
        tokens: list[str] = []
        for entry in reader.entries_after(0):
            tok = entry.get("writer_token")
            if tok and (not tokens or tokens[-1] != tok):
                assert tok not in tokens, f"two effective writers interleaved at seq {entry['seq']}: {tok}"
                tokens.append(tok)
            for rec in entry["records"]:
                if rec["id"] == RID:
                    assert rec["expect_rev"] == previous_rev, f"lost update at seq {entry['seq']}"
                    previous_rev = entry["seq"]
        revoked = Workspace._revoked_tokens(reader.meta_dir)
        expected = {"name": "x", REV_FIELD: previous_rev} if previous_rev else None
        assert reader.get("root", RID) == expected, f"get() disagrees with the journal: {expected}"
        assert reader.list("root").get(RID) == expected, f"list() disagrees with the journal: {expected}"
    locks = root / ".revisionworx" / "locks"
    for path in locks.iterdir():
        if path.name.startswith(EVIDENCE):
            token = atomicio.read_json(path).get("token")
            assert token in revoked, f"{path.name} left the scanned namespace with live token {token}"


def run_schedule(root: pathlib.Path, seed: int, monkeypatch) -> None:
    with Workspace.create(root, "schedules"):
        pass
    rng = random.Random(seed)
    sched = Scheduler(seed, kill_rate=rng.choice([0.0, 0.005, 0.02]))
    failures: list = []
    actors = [(_writer, random.Random(seed + 1)), (_writer, random.Random(seed + 2)),
              (_writer, random.Random(seed + 3)), (_admin, random.Random(seed + 4))]

    def body(fn, actor_rng):
        me = threading.current_thread()
        try:
            sched._wait(me)
            fn(root, actor_rng, failures)
        except Killed:
            pass
        except BaseException as exc:             # noqa: BLE001 - anything else is a finding
            failures.append(repr(exc))
        finally:
            sched.finished()

    threads = [threading.Thread(target=body, args=a, daemon=True) for a in actors]
    for t in threads:
        sched.add(t)
    with monkeypatch.context() as patch:
        for name in ("replace", "rename", "link", "unlink", "scandir"):
            patch.setattr(os, name, _scheduled(sched, getattr(os, name)))
        patch.setattr(atomicio, "read_json", _scheduled(sched, atomicio.read_json))
        patch.setattr(os, "fsync", lambda fd: None)  # speed only; power loss is not modelled here
        for t in threads:
            t.start()
        sched.baton[sched.rng.choice(threads)].release()
        for t in threads:
            t.join(HANDOFF_TIMEOUT * 2)
    assert not any(t.is_alive() for t in threads) and not sched.stuck, f"seed {seed}: scheduler stuck"
    assert not failures, f"seed {seed}: unexpected exceptions: {failures}"
    try:
        check_invariants(root)
    except AssertionError as exc:
        raise AssertionError(f"seed {seed}: {exc}") from exc


def test_no_schedule_breaks_writer_authority(tmp_path, monkeypatch):
    for seed in range(SEEDS):
        run_schedule(tmp_path / f"s{seed}", seed, monkeypatch)


@pytest.mark.parametrize("seed", [0, 1])
def test_scheduler_is_deterministic_enough_to_replay(tmp_path, monkeypatch, seed):
    """Same seed, same final journal length: a failing seed in a report can be rerun."""
    lengths = []
    for attempt in range(2):
        root = tmp_path / str(attempt)
        run_schedule(root, seed, monkeypatch)
        with Workspace.open(root) as reader:
            lengths.append(reader.head_seq())
    assert lengths[0] == lengths[1]
