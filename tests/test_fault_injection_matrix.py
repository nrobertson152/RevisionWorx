# SPDX-License-Identifier: GPL-3.0-or-later
"""The filesystem refuses, the process survives: failure at every filesystem step of every core operation.

The crash matrix (test_process_crash_matrix.py) asks what a dead process leaves behind. This asks the other question: when
a write fails (disk full, a folder gone read-only, an I/O error), the code keeps running through its error paths, which
crashes never exercise. For each operation the Nth filesystem change and, in the persistent modes, every one after it
fails. Then the disk comes back, and:

* the caller got an ``OSError`` or a ``WorkspaceError``, never a stray ``KeyError`` or ``UnboundLocalError``;
* the operation happened completely or not at all, by the journal, and doing it again ends in the right state;
* the same handle can be closed, so nobody needs an administrator because a disk filled up for a moment;
* reads equal the journal, verify finds nothing, nothing is left blocking writers, and a new writer gets in.

Failures are injected at the Python call. No claim about how a particular filesystem or disk behaves when actually full.
"""
from __future__ import annotations

import builtins
import errno
import hashlib
import io
import os
from pathlib import Path

import pytest

from revisionworx import atomicio, documents as D, projects
from revisionworx.projects import NewProject
from revisionworx.workspace import CommittedNotApplied, RecordPut, Workspace, WorkspaceError, WorkspaceLockedError
from test_process_crash_matrix import World, assert_consistent, build

TXID = "ef" * 16
MODES = {"disk-full": (errno.ENOSPC, True), "read-only": (errno.EROFS, True), "access-denied": (errno.EACCES, True),
         "one-io-error": (errno.EIO, False)}
MAX_POINTS = 300


class Disk:
    """Makes filesystem changes fail from the Nth one on, or just the Nth."""

    def __init__(self, patch, code: int, from_n: int, persistent: bool):
        self.code, self.from_n, self.persistent = code, from_n, persistent
        self.armed, self.count, self.fired = False, 0, None
        real_open, real_os_open = builtins.open, os.open

        def check(what, path=None):
            if not self.armed:
                return
            n, self.count = self.count, self.count + 1
            if n == from_n or (persistent and n > from_n):
                self.fired = self.fired or what
                raise OSError(code, os.strerror(code), str(path) if path is not None else None)

        def wrap(name):
            real = getattr(os, name)

            def call(*args, **kwargs):
                check(name, args[0] if name not in ("write", "fsync") else None)
                return real(*args, **kwargs)
            return call
        for name in ("replace", "rename", "link", "unlink", "mkdir", "write", "fsync"):
            patch.setattr(os, name, wrap(name))

        def os_open(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                check("create", path)
            return real_os_open(path, flags, *args, **kwargs)

        def any_open(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wxa+"):
                check("create", file)
            return real_open(file, mode, *args, **kwargs)
        patch.setattr(os, "open", os_open)
        patch.setattr(builtins, "open", any_open)
        patch.setattr(io, "open", any_open)


def run_matrix(w: World, monkeypatch, mode: str, operation, check, min_points=2, open_first=True) -> int:
    """``operation(ws)`` under failure from step 0, 1, 2... ``check(context, raised)`` runs after the disk comes back."""
    code, persistent = MODES[mode]
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    k = 0
    while True:
        w.reset()
        with monkeypatch.context() as patch:
            disk = Disk(patch, code, k, persistent)
            ws = Workspace.open(w.root, writable=True) if open_first else None
            raised, close_raised = None, None
            disk.armed = True
            try:
                result = operation(ws)
                ws = result if open_first is False and isinstance(result, Workspace) else ws
            except (OSError, WorkspaceError) as exc:
                raised = exc
            try:
                if ws is not None:
                    ws.close()
            except (OSError, WorkspaceError) as exc:
                close_raised = exc
            disk.armed = False
            fired = disk.fired
        if fired is None:
            assert raised is None and close_raised is None, (raised, close_raised)
            assert k >= min_points, f"only {k} failure points"
            return k
        context = f"{mode}: step {k} ({fired}), raised {raised!r}, close raised {close_raised!r}"
        if ws is not None:
            ws.close()                             # the disk is back: this must finish the release, with no admin
        check(context, raised)
        k += 1
        assert k < MAX_POINTS, "the matrix does not end"


def finish_and_verify(w: World, context: str) -> None:
    """A new writer gets in with no administrator, recovers, and everything then agrees with the journal. In this same
    process: a lock this process could not release is retried by its next open."""
    try:
        with Workspace.open(w.root, writable=True):
            pass
    except WorkspaceLockedError as exc:
        raise AssertionError(f"{context}: the lock is stuck") from exc
    assert_consistent(w, context)


@pytest.fixture(params=list(MODES))
def mode(request):
    return request.param


def test_commit(tmp_path, monkeypatch, mode):
    w = build(tmp_path)
    rid = "root_" + "c" * 32
    request = [RecordPut("root", rid, {"name": "x"}, expect_rev=0)]

    def check(context, raised):
        with Workspace.open(w.root) as ws:
            state = ws.transaction_outcome(TXID)["state"]
            assert state in ("committed", "not-found"), context
            assert (state == "committed") == (ws.get("root", rid) is not None), context
            if state == "committed":
                assert isinstance(raised, CommittedNotApplied) or raised is None, f"{context}: saved, but told it failed"
            else:
                assert not isinstance(raised, CommittedNotApplied), f"{context}: told it was saved, but it was not"
        with Workspace.open(w.root, writable=True) as ws:
            ws.commit("t", request, txid=TXID)
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: ws.commit("t", request, txid=TXID), check)


def test_a_handle_keeps_working_after_a_failed_commit(tmp_path, monkeypatch, mode):
    """The disk filled for a moment. The session must not need reopening or an administrator."""
    w = build(tmp_path)
    code, persistent = MODES[mode]
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    w.reset()
    ws = Workspace.open(w.root, writable=True)
    with monkeypatch.context() as patch:
        disk = Disk(patch, code, 0, persistent)
        disk.armed = True
        with pytest.raises(OSError):
            ws.commit("t", [RecordPut("root", "root_" + "c" * 32, {"name": "x"}, expect_rev=0)])
    assert ws.writable
    ws.commit("t", [RecordPut("root", "root_" + "c" * 32, {"name": "x"}, expect_rev=0)])
    ws.close()
    finish_and_verify(w, mode)


def test_checkin(tmp_path, monkeypatch, mode):
    edited = b"IPT v2 edited"
    w = build(tmp_path, checked_out=True, edited=edited)

    def check(context, raised):
        assert w.path.read_bytes() == edited, f"{context}: the working file changed"
        with Workspace.open(w.root) as ws:
            state = ws.transaction_outcome(TXID)["state"]
            doc, co = ws.get("document", w.doc), ws.get("checkout", w.doc)
            if state == "committed":
                assert doc["rev_count"] == 2 and co is None, context
                assert isinstance(raised, CommittedNotApplied) or raised is None, f"{context}: saved, but told it failed"
            else:
                assert state == "not-found" and doc["rev_count"] == 1 and co["token"] == w.token, f"{context}: half a check-in"
        with Workspace.open(w.root, writable=True) as ws:
            rev = D.checkin(ws, w.doc, w.token, "edited", txid=TXID)
            assert rev["sha256"] == hashlib.sha256(edited).hexdigest(), context
            assert len(D.history(ws, w.doc)) == 2, f"{context}: duplicated"
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.checkin(ws, w.doc, w.token, "edited", txid=TXID), check, min_points=6)


def test_register(tmp_path, monkeypatch, mode):
    def prepare(w, ws):
        w.new_file = w.path.with_name("new part.ipt")
        w.new_file.write_bytes(b"NEW")
    w = build(tmp_path, prepare=prepare)

    def check(context, raised):
        with Workspace.open(w.root) as ws:
            had = [d for d in ws.list("document").values() if d["rel_path"].endswith("new part.ipt")]
        assert w.new_file.read_bytes() == b"NEW", context
        if not had:
            with Workspace.open(w.root, writable=True) as ws:
                D.register_document(ws, w.project, w.new_file)
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.register_document(ws, w.project, w.new_file), check, min_points=6)


def test_cancel_with_revert(tmp_path, monkeypatch, mode):
    edits = b"unsaved engineering work"
    w = build(tmp_path, checked_out=True, edited=edits)

    def check(context, raised):
        body = w.path.read_bytes()
        assert body in (edits, b"IPT v1"), f"{context}: working file is {body!r}"
        assert [p.name for p in w.path.parent.iterdir() if ".rwx-" in p.name] == [] or atomicio.IS_WINDOWS or raised, context
        with Workspace.open(w.root) as ws:
            held = ws.get("checkout", w.doc) is not None
        assert held or body == b"IPT v1", f"{context}: released, but the file was not reverted"
        if held:
            with Workspace.open(w.root, writable=True) as ws:
                D.cancel_checkout(ws, w.doc, w.token, revert=True)
        assert w.path.read_bytes() == b"IPT v1", context
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.cancel_checkout(ws, w.doc, w.token, revert=True), check)


def test_create_project(tmp_path, monkeypatch, mode):
    w = build(tmp_path)

    def op(ws):
        return projects.create_project(ws, NewProject(name="New job", number="J-2", customer="c"))

    def check(context, raised):
        with Workspace.open(w.root) as ws:
            made = [p for p in ws.list("project").values() if p["name"] == "New job"]
            if made:
                assert (projects.project_folder(ws, made[0]) / "02 CAD").is_dir(), f"{context}: registered, no folder"
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, op, check)


def test_gc(tmp_path, monkeypatch, mode):
    def prepare(w, ws):
        from revisionworx.content import ContentStore
        orphan = w.path.with_name("orphan.bin")
        orphan.write_bytes(b"orphan")
        ContentStore(ws).store_file(orphan)
        orphan.unlink()
    w = build(tmp_path, prepare=prepare)

    def check(context, raised):
        with Workspace.open(w.root, writable=True) as ws:
            D.gc_content(ws, apply=True, min_age_seconds=0.0)
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.gc_content(ws, apply=True, min_age_seconds=0.0), check)


def test_repair(tmp_path, monkeypatch, mode):
    rogue = "root_" + "f" * 32

    def prepare(w, ws):
        path = ws.meta_dir / "records" / "document" / f"{w.doc}.json"
        damaged = atomicio.read_json(path)
        damaged["title"] = "tampered"
        atomicio.atomic_write_json(path, damaged)
        atomicio.atomic_write_json(ws.meta_dir / "records" / "root" / f"{rogue}.json", {"name": "rogue", "_rev": 1})
    w = build(tmp_path, prepare=prepare)

    def check(context, raised):
        with Workspace.open(w.root, writable=True) as ws:
            assert ws.get("root", rogue) is None, context
            ws.verify(repair=True)
            assert ws.get("document", w.doc)["title"] == "bracket", context
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: ws.verify(repair=True), check)


def test_close(tmp_path, monkeypatch, mode):
    """Releasing can fail too. The session must be able to try again rather than leave the lock for an administrator."""
    w = build(tmp_path)

    def check(context, raised):
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: None, check, min_points=1)


def test_opening_over_an_orphan(tmp_path, monkeypatch, mode):
    token = "b" * 32
    w = build(tmp_path)
    atomicio.atomic_write_json(w.meta / "locks" / ".releasing-feed", {"token": token, "user": "u", "host": "h", "pid": 1})
    import shutil
    shutil.rmtree(w.base / "snapshot")
    w.snapshot()

    def check(context, raised):
        finish_and_verify(w, context)
        assert token in Workspace._revoked_tokens(w.meta), context
    run_matrix(w, monkeypatch, mode, lambda ws: Workspace.open(w.root, writable=True), check, open_first=False)


def test_breaking_a_lock(tmp_path, monkeypatch, mode):
    w = build(tmp_path)
    holder = Workspace.open(w.root, writable=True)
    token = holder._lock_token
    holder._lock_token = None
    holder.close()
    import shutil
    shutil.rmtree(w.base / "snapshot")
    w.snapshot()

    def check(context, raised):
        current = Workspace.read_writer_lock(w.root)
        if current:                                # the break did not get as far as moving it: an admin repeats it
            Workspace.break_writer_lock(w.root, token, "again")
        with Workspace.open(w.root, writable=True):
            pass
        assert token in Workspace._revoked_tokens(w.meta), context
        assert_consistent(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: Workspace.break_writer_lock(w.root, token, "crash test"), check,
               open_first=False)


def test_creating_a_workspace(tmp_path, monkeypatch, mode):
    w = World(base=tmp_path, root=tmp_path / "fresh")

    def check(context, raised):
        if (w.meta / "workspace.json").exists():
            with Workspace.open(w.root, writable=True):
                pass
        else:
            Workspace.create(w.root, "again").close()
        with Workspace.open(w.root) as ws:
            assert ws.verify()["problems"] == [], context
    run_matrix(w, monkeypatch, mode, lambda ws: Workspace.create(w.root, "fresh"), check, open_first=False)


def test_checkout(tmp_path, monkeypatch, mode):
    w = build(tmp_path)

    def check(context, raised):
        assert w.path.read_bytes() == b"IPT v1", context
        with Workspace.open(w.root) as ws:
            held = ws.get("checkout", w.doc)
        if isinstance(raised, CommittedNotApplied):
            assert held and raised.result["token"] == held["token"], f"{context}: saved, but the token was lost"
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.checkout(ws, w.doc), check)


def test_restore(tmp_path, monkeypatch, mode):
    edits = b"later edits"

    def prepare(w, ws):
        w.first = w.rev
        token = D.checkout(ws, w.doc)["token"]
        w.path.write_bytes(b"IPT v2")
        D.checkin(ws, w.doc, token, "second")
        w.token = D.checkout(ws, w.doc)["token"]
        w.path.write_bytes(edits)
    w = build(tmp_path, prepare=prepare)

    def check(context, raised):
        body = w.path.read_bytes()
        assert body in (edits, b"IPT v1"), f"{context}: working file is {body!r}"
        with Workspace.open(w.root) as ws:
            co = ws.get("checkout", w.doc)
        assert co["token"] == w.token, context
        assert co["restored_from"] is None or body == b"IPT v1", f"{context}: claims a restore that did not happen"
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.restore_to_working(ws, w.doc, w.token, w.first), check)


def test_release_and_supersede(tmp_path, monkeypatch, mode):
    def prepare(w, ws):
        for state in ("in_review", "approved", "released"):
            D.transition(ws, w.rev, state)
        token = D.checkout(ws, w.doc)["token"]
        w.path.write_bytes(b"IPT v2")
        w.second = D.checkin(ws, w.doc, token, "second")["id"]
        D.transition(ws, w.second, "in_review")
        D.transition(ws, w.second, "approved")
    w = build(tmp_path, prepare=prepare)

    def check(context, raised):
        with Workspace.open(w.root) as ws:
            doc, first, second = ws.get("document", w.doc), ws.get("revision", w.rev), ws.get("revision", w.second)
        pair = (first["state"], second["state"], doc["released_rev_id"])
        assert pair in (("released", "approved", w.rev), ("superseded", "released", w.second)), f"{context}: {pair}"
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: D.transition(ws, w.second, "released"), check)


def test_adopt(tmp_path, monkeypatch, mode):
    def prepare(w, ws):
        w.old_job = w.root / "Engineering" / "Old job"
        w.old_job.mkdir()
        (w.old_job / "a.txt").write_bytes(b"a")
    w = build(tmp_path, prepare=prepare)

    def check(context, raised):
        assert [p.name for p in w.old_job.iterdir()] == ["a.txt"] and (w.old_job / "a.txt").read_bytes() == b"a", context
        with Workspace.open(w.root) as ws:
            for project in ws.list("project").values():
                if project["name"] == "Old job":
                    assert ws.read_blob(project["baseline"]["blob"])["files"], f"{context}: registered without its baseline"
        finish_and_verify(w, context)
    run_matrix(w, monkeypatch, mode, lambda ws: projects.adopt_project(ws, w.old_job, name="Old job"), check)


def test_a_release_that_fails_is_not_forgotten_when_the_process_keeps_running(tmp_path, monkeypatch):
    """A GUI drops its handle when close() raises. Its next open in this process must clean up rather than find itself locked out."""
    w = build(tmp_path)
    w.reset()
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
    ws = Workspace.open(w.root, writable=True)
    with monkeypatch.context() as patch:
        disk = Disk(patch, errno.EIO, 0, True)
        disk.armed = True
        with pytest.raises(WorkspaceError, match="still held"):
            ws.close()
    assert Workspace.read_writer_lock(w.root)["token"] == ws._lock_token    # never pretends it was released
    del ws
    with Workspace.open(w.root, writable=True) as again:
        assert again.writable
    assert not Workspace._unreleased


def test_a_failed_close_keeps_the_handle_closed(tmp_path, monkeypatch):
    """Retrying the release must not bring the handle back to life."""
    from revisionworx.workspace import WorkspaceError as Refused
    w = build(tmp_path)
    w.reset()
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
    ws = Workspace.open(w.root, writable=True)
    with monkeypatch.context() as patch:
        disk = Disk(patch, errno.EIO, 0, True)
        disk.armed = True
        with pytest.raises(Refused):
            ws.close()
    with pytest.raises(Refused, match="closed"):
        ws.commit("t", [RecordPut("root", "root_" + "c" * 32, {"name": "x"})])
    ws.close()
    assert Workspace.read_writer_lock(w.root) is None
