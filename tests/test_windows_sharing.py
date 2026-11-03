# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #10: transient Windows sharing violations must cost a retry, never authority or availability.

On Windows, CPython's open() does not share delete access, so a process inspecting writer.lock can make
a concurrent rename fail, and a rename in progress can make an open fail, with WinError 32. The tests
inject that PermissionError so they run everywhere; the process tests produce it for real on Windows
(see .github/workflows/core-ci.yml).
"""
import multiprocessing as mp
import os
import threading
import time
from pathlib import Path

import pytest

import proc_helpers
from revisionworx import atomicio
from revisionworx.workspace import RecordPut, Workspace, WorkspaceError, WorkspaceLockedError

RID = "root_" + "a" * 32


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    root = tmp_path / "w"
    with Workspace.create(root, "t"):
        pass
    return root


def busy_reads(monkeypatch, name, times):
    """The next ``times`` opens of files called ``name`` fail as sharing violations do on Windows."""
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    real = atomicio._open_for_read
    left = [times]

    def opener(path):
        if Path(path).name.startswith(name) and left[0] > 0:
            left[0] -= 1
            raise PermissionError(13, "injected WinError 32")
        return real(path)
    monkeypatch.setattr(atomicio, "_open_for_read", opener)
    return left


def test_close_releases_despite_a_transient_read_failure(root, monkeypatch):
    """Was: close() took any read failure to mean 'not mine' and leaked the lock until an admin broke it."""
    ws = Workspace.open(root, writable=True)
    busy_reads(monkeypatch, "writer.lock", 1)
    ws.close()
    monkeypatch.undo()
    assert Workspace.read_writer_lock(root) is None


def test_close_releases_when_reads_stay_busy(root, monkeypatch):
    ws = Workspace.open(root, writable=True)
    token = ws._lock_token
    busy_reads(monkeypatch, "writer.lock", 10_000)      # release must not depend on the courtesy read
    ws.close()
    monkeypatch.undo()
    assert Workspace.read_writer_lock(root) is None      # released by inspecting the moved file instead
    assert token not in Workspace._revoked_tokens(root / ".revisionworx")


def test_close_never_takes_a_successors_lock_after_a_failed_read(root, monkeypatch):
    a = Workspace.open(root, writable=True)
    Workspace.break_writer_lock(root, a._lock_token, "A unresponsive")
    b = Workspace.open(root, writable=True)
    busy_reads(monkeypatch, "writer.lock", 1)
    a.close()
    monkeypatch.undo()
    assert Workspace.read_writer_lock(root)["token"] == b._lock_token
    b.commit("t.b", [RecordPut("root", RID, {"name": "B"}, expect_rev=0)])
    b.close()


def test_commit_survives_a_transient_read_of_its_own_lock(root, monkeypatch):
    """Was: a busy read looked like a lost lock, and the handle went read-only for good."""
    with Workspace.open(root, writable=True) as ws:
        busy_reads(monkeypatch, "writer.lock", 3)
        ws.commit("t", [RecordPut("root", RID, {"name": "x"}, expect_rev=0)])
        assert ws.writable
        monkeypatch.undo()


def test_scan_does_not_call_a_busy_orphan_corrupt(root, monkeypatch):
    """Was: one busy read of a healthy orphan refused every writer with an 'unreadable, call an admin'."""
    a = Workspace.open(root, writable=True)
    token = a._lock_token
    locks = root / ".revisionworx" / "locks"
    os.replace(locks / "writer.lock", locks / ".releasing-abc")
    busy_reads(monkeypatch, ".releasing-", 2)
    with Workspace.open(root, writable=True):
        pass
    monkeypatch.undo()
    assert token in Workspace._revoked_tokens(root / ".revisionworx")
    a._lock_token = None
    a.close()


def test_acquisition_retries_when_the_holder_just_left(root, monkeypatch):
    """Was: exclusive create failed, the holder released, the read found nothing, and a lock that was
    free got reported as held by '?'."""
    holder = Workspace.open(root, writable=True)
    real = atomicio.read_json
    calls = []

    def read(path):
        if Path(path).name == "writer.lock" and not calls:
            calls.append(1)
            monkeypatch.setattr(atomicio, "read_json", real)
            holder.close()
            raise FileNotFoundError(path)
        return real(path)
    monkeypatch.setattr(atomicio, "read_json", read)
    with Workspace.open(root, writable=True) as ws:
        assert ws.writable


def test_held_lock_is_still_refused(root, monkeypatch):
    with Workspace.open(root, writable=True):
        busy_reads(monkeypatch, "writer.lock", 2)
        with pytest.raises(WorkspaceLockedError) as exc:
            Workspace.open(root, writable=True)
        assert exc.value.holder.get("token") not in (None, "?")


def test_lock_stays_held_when_release_cannot_finish(root, monkeypatch):
    """Retries are bounded, and giving up must never pretend the lock was released."""
    ws = Workspace.open(root, writable=True)
    token = ws._lock_token

    def deny(src, dst):
        raise PermissionError(13, "injected WinError 32")
    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(WorkspaceError):
        ws.close()
    monkeypatch.undo()
    assert Workspace.read_writer_lock(root)["token"] == token


def test_retry_sharing_rides_out_a_transient_violation_on_windows(monkeypatch, tmp_path):
    """Was: a reader of state.json made the writer's replace fail, so a saved change looked unapplied."""
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    target = tmp_path / "state.json"
    real, denied = os.replace, []

    def replace(src, dst):
        if len(denied) < 3:
            denied.append(1)
            raise PermissionError(13, "injected WinError 32")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    atomicio.atomic_write_json(target, {"applied_seq": 7})
    assert len(denied) == 3
    monkeypatch.undo()
    assert atomicio.read_json(target) == {"applied_seq": 7}


def test_retry_sharing_gives_up_and_reports(monkeypatch):
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    calls = []

    def always_busy():
        calls.append(1)
        raise PermissionError(13, "injected WinError 32")
    with pytest.raises(PermissionError):
        atomicio.retry_sharing(always_busy)
    assert len(calls) == atomicio.SHARING_RETRIES


def test_retry_sharing_does_not_retry_elsewhere(monkeypatch):
    monkeypatch.setattr(atomicio, "RETRY_SHARING", False)
    calls = []

    def denied():
        calls.append(1)
        raise PermissionError(13, "a real permissions problem")
    with pytest.raises(PermissionError):
        atomicio.retry_sharing(denied)
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="real Windows share modes")
def test_our_reader_does_not_block_rename_or_delete_on_windows(tmp_path):
    """The actual fix: with the file open through atomicio, renaming and deleting it still work."""
    path = tmp_path / "writer.lock"
    path.write_bytes(b'{"token": "t"}')
    with atomicio._open_for_read(path) as fh:
        moved = tmp_path / ".releasing-x"
        os.replace(path, moved)
        assert fh.read() == b'{"token": "t"}'
        moved.unlink()
    assert not moved.exists()
    with pytest.raises(FileNotFoundError):
        atomicio.read_json(path)


@pytest.mark.skipif(os.name != "nt", reason="real Windows share modes")
def test_control_python_open_does_block_rename_on_windows(tmp_path):
    """Why the fix was needed, and proof the sharing tests would notice if this stopped being true."""
    path = tmp_path / "writer.lock"
    path.write_bytes(b"x")
    with open(path, "rb"):
        with pytest.raises(PermissionError):
            os.replace(path, tmp_path / ".releasing-x")


# --- real processes; on Windows these produce genuine sharing violations -----------------------------
@pytest.mark.parametrize("n", [6, 10])
def test_process_contention_with_a_reader_polling_the_lock(tmp_path, n):
    """Issue #10 acceptance: one winner, the rest conflict, nothing leaked, while a reader holds the lock
    file open as often as it can."""
    root = tmp_path / "share"
    doc_id = proc_helpers.make_checkout_fixture(root)
    ctx = mp.get_context("spawn")
    barrier, results = ctx.Barrier(n), ctx.Queue()
    stop = threading.Event()
    lock = root / ".revisionworx" / "locks" / "writer.lock"

    def status_poller():
        """RevisionWorx itself asking who holds the lock, as fast as it can. Must never block a release."""
        while not stop.is_set():
            Workspace.read_writer_lock(root)

    def outside_program():
        """Something like a virus scanner: Python's open() does not share delete, so while this has the file
        open, a rename really does fail with WinError 32 on Windows. It lets go, and release has to wait."""
        while not stop.is_set():
            try:
                with open(lock, "rb") as fh:
                    fh.read()
                    time.sleep(0.005)
            except OSError:
                pass
            time.sleep(0.02)
    readers = [threading.Thread(target=fn, daemon=True) for fn in (status_poller, outside_program)]
    for reader in readers:
        reader.start()
    procs = [ctx.Process(target=proc_helpers.race_checkout, args=(str(root), doc_id, barrier, results))
             for _ in range(n)]
    try:
        for p in procs:
            p.start()
        outcomes = [results.get(timeout=150) for _ in range(n)]
        for p in procs:
            p.join(60)
    finally:
        stop.set()
        for reader in readers:
            reader.join(5)
    assert outcomes.count("won") == 1, outcomes
    assert outcomes.count("conflict") == n - 1, outcomes
    assert Workspace.read_writer_lock(root) is None
    with Workspace.open(root, writable=True) as ws:      # the next writer gets in
        assert ws.verify()["problems"] == []
        assert ws.get("checkout", doc_id) is not None


# ---------------------------------------------------------------- a live release vs. a genuine orphan
# Coordinator handoff on PR #7 (2026-11-03): CI 41678241178 on Windows saw a live peer's in-flight
# locks/.releasing-* refuse reads between its os.replace and its unlink (delete pending). Being
# unable to *read* a release file is not evidence that it is damaged: when it has gone, its releaser
# finished; while it is still there, nobody may write yet, but the answer is "retry", not "corrupt,
# call an administrator" (who could not read it either).

def _orphan_of(root):
    """A holds the lock; its release is caught mid-flight: writer.lock moved to .releasing-*."""
    a = Workspace.open(root, writable=True)
    locks = root / ".revisionworx" / "locks"
    os.replace(locks / "writer.lock", locks / ".releasing-abc")
    return a, locks, locks / ".releasing-abc"


def test_release_that_finishes_after_the_retry_budget_is_not_called_corrupt(root, monkeypatch):
    """Was: the file was busy for the whole retry budget and then vanished (its releaser unlinked it),
    yet acquisition refused with 'unreadable ... run lock clear-orphan' for a file that no longer existed."""
    a, locks, orphan = _orphan_of(root)
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_RETRIES", 3)
    real, seen = atomicio._open_for_read, [0]

    def opener(path):
        if Path(path).name == orphan.name:
            seen[0] += 1
            if seen[0] == 3:
                orphan.unlink()                       # the live releaser finishes on our last attempt
            raise PermissionError(13, "injected: delete pending")
        return real(path)
    monkeypatch.setattr(atomicio, "_open_for_read", opener)
    with Workspace.open(root, writable=True) as ws:
        assert ws.writable
    monkeypatch.undo()
    assert seen[0] == 3
    assert a._lock_token not in Workspace._revoked_tokens(root / ".revisionworx")  # nothing to fence
    assert not [p for p in locks.iterdir() if p.name.startswith((".releasing-", ".fencing-"))]
    a._lock_token = None
    a.close()


def test_unreadable_release_file_still_blocks_but_as_busy_not_corrupt(root, monkeypatch):
    from revisionworx.workspace import LockReleaseBusyError, WorkspaceCorruptError
    a, locks, orphan = _orphan_of(root)
    before = orphan.read_bytes()
    busy_reads(monkeypatch, orphan.name, 10_000)                    # only the peer's file is denied
    with pytest.raises(LockReleaseBusyError) as exc:
        Workspace.open(root, writable=True)
    assert not isinstance(exc.value, WorkspaceCorruptError)
    assert exc.value.name == orphan.name and "clear-orphan" not in str(exc.value)
    monkeypatch.undo()
    assert Workspace.read_writer_lock(root) is None                 # fail closed: lock given back
    assert orphan.read_bytes() == before                             # untouched, still blocking
    assert a._lock_token not in Workspace._revoked_tokens(root / ".revisionworx")
    with Workspace.open(root, writable=True):                        # readable again: fenced as usual
        pass
    assert a._lock_token in Workspace._revoked_tokens(root / ".revisionworx")
    a._lock_token = None
    a.close()


def test_unreadable_custody_file_is_put_back_as_busy_not_quarantined_as_corrupt(root, monkeypatch):
    """The fencer moved the orphan into custody (.fencing-*) and then could not read it. It must stay a
    blocking orphan with its bytes, and the error must not send an administrator to clear it."""
    from revisionworx.workspace import LockReleaseBusyError, WorkspaceCorruptError
    a, locks, orphan = _orphan_of(root)
    before = orphan.read_bytes()
    busy_reads(monkeypatch, ".fencing-", 10_000)
    with pytest.raises(LockReleaseBusyError) as exc:
        Workspace.open(root, writable=True)
    assert not isinstance(exc.value, WorkspaceCorruptError)
    monkeypatch.undo()
    left = [p for p in locks.iterdir() if p.name.startswith((".releasing-", ".fencing-"))]
    assert len(left) == 1 and left[0].read_bytes() == before
    assert Workspace.read_writer_lock(root) is None
    assert a._lock_token not in Workspace._revoked_tokens(root / ".revisionworx")
    with Workspace.open(root, writable=True):
        pass
    assert a._lock_token in Workspace._revoked_tokens(root / ".revisionworx")
    a._lock_token = None
    a.close()
