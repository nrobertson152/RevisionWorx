# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #74: clearing an orphaned lock file on Windows, where a file that is open can't always be moved.

Main CI run 42510465131 (windows-latest, Python 3.13) raced four administrators clearing one orphan. They read it with
Python's open(), which on Windows doesn't share delete access. While a competitor was reading the orphan, the winner's
move into custody failed with WinError 32, and the winner's command died with a raw PermissionError: exit codes
[1, 3, 3, 3] instead of [0, 3, 3, 3].

These tests emulate Windows' share modes, or inject the sharing violation, so they run everywhere. Every one of them
fails on e5ded07. tests/test_orphan_clear_custody.py races real processes, which produces the violation for real on
Windows CI.
"""
import collections
import os
from pathlib import Path

import pytest

from revisionworx import atomicio
from revisionworx.workspace import (LockReleaseBusyError, OrphanClearConflict, Workspace, WorkspaceCorruptError,
                                    WorkspaceError)
from test_orphan_clear_custody import NAME, ORIGINAL, _auths, _die_on, _evidence_matches_audit, _orphan, _results

REASON = "cleared after checking the writer is stopped"


def quick(monkeypatch):
    """Windows' retry policy (ADR-016), without the waits."""
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)


def windows_share_modes(monkeypatch, locks, while_open):
    """Emulate Windows for the files in ``locks``.

    A file opened with Python's open() (here, Path.read_bytes) doesn't share delete access, so renaming or deleting it
    fails with a sharing violation while it's open. RevisionWorx's own reader, atomicio._open_for_read, opens with every
    share mode on Windows and blocks nothing. ``while_open(path)`` runs while a file is open: what a competing process
    does meanwhile. Linux keeps an open file readable after it has been moved, as a Windows file opened with
    FILE_SHARE_DELETE stays."""
    quick(monkeypatch)
    held = collections.Counter()
    real_read, real_open, real_replace, real_unlink = Path.read_bytes, atomicio._open_for_read, os.replace, Path.unlink

    def plain_read(self):
        if self.parent != locks:
            return real_read(self)
        with open(self, "rb") as fh:
            held[self.name] += 1
            try:
                while_open(self)
            finally:
                held[self.name] -= 1
            return fh.read()

    def shared_open(path):
        fh = real_open(path)
        if Path(path).parent == locks:
            try:
                while_open(Path(path))
            except BaseException:
                fh.close()
                raise
        return fh

    def busy(path):
        p = Path(path)
        return p.parent == locks and held[p.name] > 0

    def replace(src, dst, *a, **kw):
        if busy(src):
            raise PermissionError(13, "emulated WinError 32: open without FILE_SHARE_DELETE", str(src))
        return real_replace(src, dst, *a, **kw)

    def unlink(self, missing_ok=False):
        if busy(self):
            raise PermissionError(13, "emulated WinError 32: open without FILE_SHARE_DELETE", str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "read_bytes", plain_read)
    monkeypatch.setattr(atomicio, "_open_for_read", shared_open)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)


def deny_moves(monkeypatch, name, times):
    """The next ``times`` moves of the file called ``name`` fail as a sharing violation does on Windows, as while a
    virus scanner or backup tool has it open."""
    quick(monkeypatch)
    real, left = os.replace, [times]

    def replace(src, dst, *a, **kw):
        if Path(src).name == name and left[0] > 0:
            left[0] -= 1
            raise PermissionError(13, "injected WinError 32", str(src))
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(os, "replace", replace)
    return left


def test_reading_an_orphan_never_stops_a_competitor_taking_it_into_custody(share, monkeypatch):
    """The CI failure in one process: while administrator A reads the orphan, B authorises and clears it. Was: B's move
    into custody failed with WinError 32 and B's command died with a raw PermissionError."""
    locks = _orphan(share)
    seen = {}

    def b_clears_meanwhile(path):
        if path.name == NAME and "b" not in seen:
            seen["b"] = None
            seen["b"] = Workspace.clear_orphaned_release(share, NAME, reason="admin B")
    windows_share_modes(monkeypatch, locks, b_clears_meanwhile)
    with pytest.raises(OrphanClearConflict) as a:
        Workspace.clear_orphaned_release(share, NAME, reason="admin A")
    monkeypatch.undo()
    assert seen["b"]["outcome"] == "completed" and not seen["b"]["adopted"]
    assert a.value.outcome == "completed"                   # A is told B's clear stands; nothing is recorded for A
    assert len(_auths(share)) == 1 and len(_results(share)) == 1
    left = sorted(p.name for p in locks.iterdir() if p.name.startswith(("writer.lock.", ".releasing-", ".clearing-")))
    assert left == [seen["b"]["evidence"]]
    assert _evidence_matches_audit(locks, share)
    with Workspace.open(share, writable=True):
        pass


def test_a_clear_rides_out_a_program_holding_the_orphan_for_a_moment(share, monkeypatch):
    """Was: the first sharing violation on the move into custody ended the command with a raw PermissionError."""
    locks = _orphan(share)
    left = deny_moves(monkeypatch, NAME, 3)
    r = Workspace.clear_orphaned_release(share, NAME, reason=REASON)
    assert left == [0] and r["outcome"] == "completed"
    monkeypatch.undo()
    assert len(_auths(share)) == 1 and len(_results(share)) == 1
    assert not (locks / NAME).exists() and _evidence_matches_audit(locks, share)
    with Workspace.open(share, writable=True):
        pass


def test_a_clear_that_cannot_move_the_orphan_says_so_and_the_same_command_finishes_it(share, monkeypatch):
    """Something outside RevisionWorx holds the orphan past the bound. Was: a raw PermissionError, which said nothing
    about the clear. Now the administrator is told it's authorised, still blocks writers, and how to finish it."""
    locks = _orphan(share)
    deny_moves(monkeypatch, NAME, 10**9)
    with pytest.raises(WorkspaceError, match="same command again, with the same reason") as exc:
        Workspace.clear_orphaned_release(share, NAME, reason=REASON)
    assert not isinstance(exc.value, OrphanClearConflict)
    assert (locks / NAME).read_bytes() == ORIGINAL           # untouched, so it still blocks writers
    assert len(_auths(share)) == 1 and not _results(share)
    monkeypatch.undo()
    r = Workspace.clear_orphaned_release(share, NAME, reason=REASON)
    assert r["adopted"] and r["outcome"] == "completed"
    assert len(_auths(share)) == 1 and len(_results(share)) == 1


def test_opening_while_a_program_holds_a_pending_clear_asks_to_retry(share, monkeypatch):
    """The next open finishes an authorised clear. Was: a file held past the bound failed the open with a raw
    PermissionError, which the window showed as 'check your access rights'. Now the open is refused as busy (retry,
    nothing written), and a later open finishes the clear."""
    locks = _orphan(share)
    _die_on(monkeypatch, lambda s, d: s.endswith(NAME))      # authorised, then the administrator's process died
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason=REASON)
    monkeypatch.undo()
    deny_moves(monkeypatch, NAME, 10**9)
    with pytest.raises(LockReleaseBusyError, match=f"{NAME} could not be moved or read"):
        Workspace.open(share, writable=True)
    assert Workspace.read_writer_lock(share) is None          # it gave its lock back: fail closed
    assert (locks / NAME).read_bytes() == ORIGINAL and not _results(share)
    monkeypatch.undo()
    with Workspace.open(share, writable=True):
        pass
    assert [r["outcome"] for r in _results(share)] == ["completed"]
    assert _evidence_matches_audit(locks, share)


def test_a_swapped_orphan_keeps_blocking_while_a_program_holds_it(share, monkeypatch):
    """Fencing moves a token-bearing orphan aside, then reads what it moved. If that has no token (it was swapped
    meanwhile), it goes back to a blocking name. Was: that one move wasn't retried, so a program holding the file for a
    moment turned 'unreadable: an administrator must clear it' into a raw PermissionError."""
    with Workspace.create(share, "W"):
        pass
    locks = share / ".revisionworx" / "locks"
    holder = {"user": "a", "host": "h", "pid": 1, "token": "f" * 32, "acquired": "2026-11-08T00:00:00Z"}
    (locks / ".releasing-0123").write_bytes(atomicio.dumps_json(holder))
    quick(monkeypatch)
    real, denied = os.replace, []

    def replace(src, dst, *a, **kw):
        s, d = Path(src).name, Path(dst).name
        if s.startswith(".fencing-") and len(denied) < 3:
            denied.append(s)
            raise PermissionError(13, "injected WinError 32", str(src))
        real(src, dst, *a, **kw)
        if s == ".releasing-0123" and d.startswith(".fencing-"):
            Path(dst).write_bytes(b"{ swapped")             # an outside swap between the scan and the move
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(WorkspaceCorruptError, match="unreadable"):
        Workspace.open(share, writable=True)
    monkeypatch.undo()
    assert len(denied) == 3
    kept = [p for p in locks.iterdir() if p.name.startswith(".releasing-")]
    assert len(kept) == 1 and kept[0].read_bytes() == b"{ swapped"
    assert not list(locks.glob(".fencing-*")) and Workspace.read_writer_lock(share) is None
