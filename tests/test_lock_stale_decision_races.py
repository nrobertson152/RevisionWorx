# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-019 rev. 4: no protocol process may stop a lock blocking before its actual token is revoked.

The three protocol-only P1s found on d2b7a1c by Codex, QA and Distributed Systems (PR #7), plus a
crash at every filesystem step of each. Writer B is always paused inside a real commit(), past its
lock and revocation checks, while another protocol process displaces B's lock on a stale decision:

  * stale revoked-lock cleanup: D reads revoked A, B replaces A, D's delayed move takes B;
  * stale administrative break: same shape, the mover is break_writer_lock;
  * stale release: A's release displaces B, a successor holds the name, and A dies at any point.

Oracle (journal-backed, not lock-file presence): B and the successor are never both effective.
"""
import os
import threading
from pathlib import Path

import pytest

from revisionworx import atomicio
from revisionworx.workspace import LockLostError, RecordPut, Workspace, WorkspaceError, WorkspaceLockedError

RID_B = "root_" + "b" * 32
RID_C = "root_" + "c" * 32
CLEANUP_STEPS, BREAK_STEPS, RELEASE_STEPS = 14, 4, 5  # filesystem changes after the stale move
MOVED_FROM_LOCK = (".releasing-", "writer.lock.revoked-", "writer.lock.broken-")


class Died(BaseException):
    """Process death: not an Exception, and sticky once injected (see Mover)."""


class PausedCommit:
    """B in a real commit(), held just past its lock and revocation checks."""

    def __init__(self, b):
        self.b, self.preflighted, self.go = b, threading.Event(), threading.Event()
        self.outcome = None
        real = b._current_rev

        def pause(kind, rid):
            self.preflighted.set()
            assert self.go.wait(20), "B was never resumed"
            return real(kind, rid)
        b._current_rev = pause
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.preflighted.wait(20), "B never passed its commit preflight"

    def _run(self):
        try:
            self.b.commit("t.b", [RecordPut("root", RID_B, {"name": "B"}, expect_rev=0)])
            self.outcome = "returned"
        except LockLostError:
            self.outcome = "fenced"

    def finish(self):
        self.go.set()
        self.thread.join(20)
        assert not self.thread.is_alive()
        return self.outcome


class Mover:
    """Runs one protocol step in this thread: calls ``first`` just before its first move of
    writer.lock (the stale instant) and dies before the ``die_at``-th later filesystem change."""

    def __init__(self, patch, lock: Path, first, die_at=None):
        self.me = threading.current_thread()
        self.lock, self.first, self.die_at = lock, first, die_at
        self.fired, self.count, self.died = False, 0, False
        self.real_replace = os.replace
        for name in ("replace", "rename", "link", "unlink"):
            patch.setattr(os, name, self._wrap(getattr(os, name)))

    def _wrap(self, fn):
        def wrapper(src, *args, **kwargs):
            if threading.current_thread() is not self.me:
                return fn(src, *args, **kwargs)
            if self.died:
                raise Died
            if not self.fired and fn.__name__ == "replace" and Path(src) == self.lock \
                    and Path(args[0]).name.startswith(MOVED_FROM_LOCK):
                self.fired = True
                self.me, saved = None, self.me    # our own nested calls run unobserved
                self.first()
                self.me = saved
            elif self.fired and self.die_at is not None:
                if self.count == self.die_at:
                    self.died = True
                    raise Died
                self.count += 1
            return fn(src, *args, **kwargs)
        return wrapper


def _workspace(tmp_path):
    root = tmp_path / "w"
    root.parent.mkdir(parents=True, exist_ok=True)
    with Workspace.create(root, "t"):
        pass
    return root, root / ".revisionworx" / "locks"


def _strand_revoked_lock(root, locks, patch_context):
    """A is revoked but its file stays at writer.lock (the archive step hit a sharing violation)."""
    a = Workspace.open(root, writable=True)
    token = a._lock_token
    real = os.replace

    def deny(src, dst, *args, **kwargs):
        if Path(src) == locks / "writer.lock":
            raise PermissionError("injected sharing violation")
        return real(src, dst, *args, **kwargs)
    with patch_context() as patch:
        patch.setattr(os, "replace", deny)
        patch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
        Workspace.break_writer_lock(root, token, "A confirmed stopped")
    a._lock_token = None
    a.close()
    assert Workspace.read_writer_lock(root)["token"] == token
    return token


def _successor_commits(root):
    """The next acquirer, then a commit. None if it was refused (a fail-closed outcome)."""
    try:
        with Workspace.open(root, writable=True) as c:
            c.commit("t.c", [RecordPut("root", RID_C, {"name": "C"}, expect_rev=0)])
            return c._lock_token
    except WorkspaceError:
        return None


def _assert_not_two_effective(root, paused, b_token):
    outcome = paused.finish()
    paused.b.close()
    with Workspace.open(root) as r:
        effective = list(r.entries_after(0))
        b_on = any(e.get("writer_token") == b_token and e["op"] == "t.b" for e in effective)
        c_entries = [e for e in effective if e["op"] == "t.c"]
        if c_entries:
            # C committed after this interleaving displaced B, so B's later commit must be fenced.
            assert not b_on, f"two effective writers: B ({outcome}) and C"
            assert r.get("root", RID_B) is None
            assert outcome == "fenced"
            assert b_token in Workspace._revoked_tokens(r.meta_dir)


def _no_live_token_in_evidence(root, locks):
    revoked = Workspace._revoked_tokens(root / ".revisionworx")
    for p in locks.iterdir():
        if p.name.startswith("writer.lock.") and not p.name.endswith(".tmp"):
            assert atomicio.read_json(p)["token"] in revoked, f"{p.name} holds an unrevoked token"


# --- stale revoked-lock cleanup ---------------------------------------------------------------
def _cleanup_race(tmp_path, monkeypatch, die_at):
    root, locks = _workspace(tmp_path)
    _strand_revoked_lock(root, locks, monkeypatch.context)
    state = {}

    def b_acquires_and_preflights():
        b = Workspace.open(root, writable=True)      # legitimately archives revoked A
        state["b_token"], state["paused"] = b._lock_token, PausedCommit(b)

    d_result = None
    with monkeypatch.context() as patch:
        mover = Mover(patch, locks / "writer.lock", b_acquires_and_preflights, die_at)
        try:
            with Workspace.open(root, writable=True) as d:      # D read A; its move now takes B
                d.commit("t.c", [RecordPut("root", RID_C, {"name": "C"}, expect_rev=0)])
                d_result = d._lock_token
        except Died:
            pass
    assert mover.fired
    if d_result is None:
        _successor_commits(root)
    _assert_not_two_effective(root, state["paused"], state["b_token"])
    _no_live_token_in_evidence(root, locks)
    return mover


def test_stale_revoked_cleanup_cannot_create_two_writers(tmp_path, monkeypatch):
    assert not _cleanup_race(tmp_path, monkeypatch, None).died


@pytest.mark.parametrize("die_at", range(CLEANUP_STEPS))
def test_stale_revoked_cleanup_crash_at_every_step(tmp_path, monkeypatch, die_at):
    _cleanup_race(tmp_path, monkeypatch, die_at)


# --- stale administrative break ---------------------------------------------------------------
def _break_race(tmp_path, monkeypatch, die_at):
    root, locks = _workspace(tmp_path)
    a = Workspace.open(root, writable=True)
    token = a._lock_token
    state = {}

    def b_acquires_and_preflights():
        b = Workspace.open(root, writable=True)      # A is already journal-revoked at this instant
        state["b_token"], state["paused"] = b._lock_token, PausedCommit(b)

    with monkeypatch.context() as patch:
        mover = Mover(patch, locks / "writer.lock", b_acquires_and_preflights, die_at)
        try:
            holder = Workspace.break_writer_lock(root, token, "A confirmed stopped")
            assert holder["token"] == token          # reports the writer the administrator named
        except Died:
            pass
    assert mover.fired
    a._lock_token = None
    a.close()
    _successor_commits(root)
    _assert_not_two_effective(root, state["paused"], state["b_token"])
    _no_live_token_in_evidence(root, locks)
    return mover


def test_stale_admin_break_cannot_create_two_writers(tmp_path, monkeypatch):
    assert not _break_race(tmp_path, monkeypatch, None).died


@pytest.mark.parametrize("die_at", range(BREAK_STEPS))
def test_stale_admin_break_crash_at_every_step(tmp_path, monkeypatch, die_at):
    _break_race(tmp_path, monkeypatch, die_at)


def test_break_keeps_planned_evidence_when_it_moved_the_named_writer(tmp_path):
    root, locks = _workspace(tmp_path)
    a = Workspace.open(root, writable=True)
    Workspace.break_writer_lock(root, a._lock_token, "A confirmed stopped")
    names = [p.name for p in locks.iterdir()]
    assert len(names) == 1 and names[0].startswith("writer.lock.broken-")
    with Workspace.open(root) as r:
        audits = [i["data"] for e in r.entries_after(0) if e["op"] == "workspace.lock.break"
                  for i in e["records"]]
    assert [x["evidence"] for x in audits] == names
    a.close()


# --- stale release, successor holds the name ----------------------------------------------------
def _release_race(tmp_path, monkeypatch, die_at):
    root, locks = _workspace(tmp_path)
    lock = locks / "writer.lock"
    a = Workspace.open(root, writable=True)
    a_token = a._lock_token
    state = {}
    c_token = "c" * 32
    info = atomicio.read_json(root / ".revisionworx" / "workspace.json")
    real_restore = a._restore_foreign_lock

    def admin_breaks_and_b_preflights():
        Workspace.break_writer_lock(root, a_token, "A confirmed stopped")
        b = Workspace.open(root, writable=True)
        state["b_token"], state["paused"] = b._lock_token, PausedCommit(b)

    def successor_takes_the_name(private, target, holder):
        # C's exclusive create wins here; C has not yet reached its orphan scan. That is C's work, not
        # A's, so it must not count as a step A can die on.
        mover.me, a_thread = None, mover.me
        atomicio.publish_exclusive(lock, atomicio.dumps_json({"token": c_token}))
        mover.me = a_thread
        return real_restore(private, target, holder)
    a._restore_foreign_lock = successor_takes_the_name

    with monkeypatch.context() as patch:
        # The revocation check runs before the move, so A's token still looks live to A. Run the
        # admin + B steps at the stale instant and keep A's own view of the journal stale.
        mover = Mover(patch, lock, admin_breaks_and_b_preflights, die_at)
        try:
            a._unlink_own_lock(lock)
        except Died:
            pass
    assert mover.fired
    a._lock_token = None
    a.close()

    # C resumes: the real scan, then the real open steps, then a real commit.
    Workspace._fence_orphaned_releases(root / ".revisionworx", c_token)
    c = Workspace(root / ".revisionworx", info, True, c_token)
    c.recover()
    c.commit("t.c", [RecordPut("root", RID_C, {"name": "C"}, expect_rev=0)])
    _assert_not_two_effective(root, state["paused"], state["b_token"])
    _no_live_token_in_evidence(root, locks)
    c.close()
    return mover


def test_stale_release_revokes_before_it_stops_blocking(tmp_path, monkeypatch):
    assert not _release_race(tmp_path, monkeypatch, None).died


@pytest.mark.parametrize("die_at", range(RELEASE_STEPS))
def test_stale_release_crash_at_every_step(tmp_path, monkeypatch, die_at):
    _release_race(tmp_path, monkeypatch, die_at)


def test_crash_matrices_reach_past_the_last_step(tmp_path, monkeypatch):
    """Guards the parametrized ranges above: a die_at past the last step must outlast the whole operation,
    otherwise a later crash point would go untested without anyone noticing."""
    assert not _cleanup_race(tmp_path / "cleanup", monkeypatch, CLEANUP_STEPS).died
    assert not _break_race(tmp_path / "break", monkeypatch, BREAK_STEPS).died
    assert not _release_race(tmp_path / "release", monkeypatch, RELEASE_STEPS).died


# --- acquisition stays bounded ------------------------------------------------------------------
def test_acquisition_never_recurses_without_bound(tmp_path, monkeypatch):
    """A lock that keeps coming back revoked must end in a refusal, not unbounded recursion."""
    root, locks = _workspace(tmp_path)
    token = _strand_revoked_lock(root, locks, monkeypatch.context)
    real = atomicio.publish_exclusive
    calls = []

    def always_taken(path, data):
        if Path(path).name == "writer.lock":
            calls.append(1)
            if not (locks / "writer.lock").exists():
                real(path, atomicio.dumps_json({"token": token}))  # the same revoked lock reappears
            raise atomicio.ExclusiveCreateError(str(path))
        return real(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", always_taken)
    with pytest.raises(WorkspaceLockedError):
        Workspace.open(root, writable=True)
    assert 1 < len(calls) < 100
