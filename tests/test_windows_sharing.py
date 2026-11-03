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
    root = tmp_path / "w"
    with Workspace.create(root, "t"):
        pass
    return root


def busy_reads(monkeypatch, name, times):
    """The next ``times`` reads of files called ``name`` fail with a sharing violation."""
    real = atomicio.read_json
    left = [times]

    def read(path):
        if Path(path).name.startswith(name) and left[0] > 0:
            left[0] -= 1
            raise PermissionError(13, "injected WinError 32")
        return real(path)
    monkeypatch.setattr(atomicio, "read_json", read)
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
    assert Workspace.read_writer_lock(root) is None
    assert token                                         # released by inspecting the moved file instead


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

    def poll():
        while not stop.is_set():
            try:
                with open(lock, "rb") as fh:
                    fh.read()
                    time.sleep(0.001)
            except OSError:
                time.sleep(0.0005)
    reader = threading.Thread(target=poll, daemon=True)
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
        reader.join(5)
    assert outcomes.count("won") == 1, outcomes
    assert outcomes.count("conflict") == n - 1, outcomes
    assert Workspace.read_writer_lock(root) is None
    with Workspace.open(root, writable=True) as ws:      # the next writer gets in
        assert ws.verify()["problems"] == []
        assert ws.get("checkout", doc_id) is not None
