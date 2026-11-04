# SPDX-License-Identifier: GPL-3.0-or-later
"""A handle that may not write must leave the working file alone.

Restoring or reverting overwrites the user's unsaved CAD work, and a journal entry cannot fence or undo that, so the check
has to come first. Before this, a read-only or revoked handle overwrote the file, then failed at the commit."""
from __future__ import annotations

import os
import stat
import time

import pytest

from revisionworx import cli
from revisionworx import documents as D
from revisionworx import projects as P
from revisionworx.content import ContentStore
from revisionworx.workspace import LockLostError, ReadOnlyError, Workspace, WorkspaceError

EDITS = b"unsaved work"


@pytest.fixture
def setup(tmp_path):
    location = tmp_path / "w"
    ws = Workspace.create(location, "w")
    root = P.add_root(ws, "r", location)
    pid = P.create_project(ws, P.NewProject(name="p", number="1", root_id=root, mode="managed", rel_path="proj"))["id"]
    working = P.project_folder(ws, ws.get("project", pid)) / "a.ipt"
    working.write_bytes(b"revision one")
    doc, rev = D.register_document(ws, pid, working)
    co = D.checkout(ws, doc["id"])
    working.write_bytes(EDITS)
    ws.close()
    return location, working, doc, rev, co


OVERWRITERS = {
    "restore": lambda ws, doc, rev, co: D.restore_to_working(ws, doc["id"], co["token"], rev["id"]),
    "revert": lambda ws, doc, rev, co: D.cancel_checkout(ws, doc["id"], co["token"], revert=True),
}


@pytest.mark.parametrize("overwrite", OVERWRITERS.values(), ids=OVERWRITERS)
def test_read_only_handle_does_not_touch_the_working_file(setup, overwrite):
    location, working, doc, rev, co = setup
    with Workspace.open(location) as ro:
        with pytest.raises(ReadOnlyError):
            overwrite(ro, doc, rev, co)
    assert working.read_bytes() == EDITS


@pytest.mark.parametrize("overwrite", OVERWRITERS.values(), ids=OVERWRITERS)
def test_closed_handle_does_not_touch_the_working_file(setup, overwrite):
    location, working, doc, rev, co = setup
    ws = Workspace.open(location, writable=True)
    ws.close()
    with pytest.raises(WorkspaceError):
        overwrite(ws, doc, rev, co)
    assert working.read_bytes() == EDITS


@pytest.mark.parametrize("overwrite", OVERWRITERS.values(), ids=OVERWRITERS)
@pytest.mark.parametrize("lock_left_behind", [False, True], ids=["lock-moved", "lock-still-in-place"])
def test_revoked_handle_does_not_touch_the_working_file(setup, overwrite, lock_left_behind, monkeypatch):
    location, working, doc, rev, co = setup
    stale = Workspace.open(location, writable=True)
    token = Workspace.read_writer_lock(location)["token"]
    if lock_left_behind:                         # an administrator stopped after the revocation, before moving the lock
        monkeypatch.setattr(Workspace, "_displace_writer_lock", staticmethod(lambda *a, **k: None))
    Workspace.break_writer_lock(location, token, "owner gone")
    with pytest.raises(LockLostError):
        overwrite(stale, doc, rev, co)
    assert working.read_bytes() == EDITS
    assert stale.writable is False


@pytest.mark.parametrize("overwrite", OVERWRITERS.values(), ids=OVERWRITERS)
def test_a_handle_that_may_write_still_can(setup, overwrite):
    location, working, doc, rev, co = setup
    with Workspace.open(location, writable=True) as ws:
        overwrite(ws, doc, rev, co)
    assert working.read_bytes() == b"revision one"
    assert not [p.name for p in working.parent.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("overwrite", OVERWRITERS.values(), ids=OVERWRITERS)
def test_overwriting_keeps_the_files_permissions(setup, overwrite):
    location, working, doc, rev, co = setup
    os.chmod(working, 0o640)
    with Workspace.open(location, writable=True) as ws:
        overwrite(ws, doc, rev, co)
    assert stat.S_IMODE(os.stat(working).st_mode) == 0o640


@pytest.mark.parametrize("overwrite", OVERWRITERS.values(), ids=OVERWRITERS)
def test_missing_folder_is_reported_as_missing_and_not_recreated(setup, overwrite):
    location, working, doc, rev, co = setup
    moved = working.parent.with_name("moved")
    working.parent.rename(moved)
    with Workspace.open(location, writable=True) as ws:
        with pytest.raises(D.MissingFileError):
            overwrite(ws, doc, rev, co)
    assert not working.parent.exists() and (moved / "a.ipt").read_bytes() == EDITS


def test_gc_leaves_what_it_does_not_recognise_and_says_so(tmp_path):
    with Workspace.create(tmp_path / "w", "w") as ws:
        root = ContentStore(ws).root
        sub = root / "ab"
        sub.mkdir(parents=True)
        foreign = sub / "notes.txt"
        foreign.write_text("someone's file")
        folder = sub / ("c" * 64)
        folder.mkdir()
        misfiled = sub / ("e" * 64)
        misfiled.write_bytes(b"not where its name says")
        orphan = root / "dd" / ("d" * 64)
        orphan.parent.mkdir()
        orphan.write_bytes(b"orphan")
        for path in (foreign, folder, misfiled, orphan):
            os.utime(path, (time.time() - 90000, time.time() - 90000))
        dry = D.gc_content(ws)
        assert [o["sha256"] for o in dry["orphans"]] == ["d" * 64]
        assert dry["unrecognised"] == [f"ab/{'c' * 64}", f"ab/{'e' * 64}", "ab/notes.txt"]
        applied = D.gc_content(ws, apply=True)
        assert applied["removed"] is True
    assert foreign.exists() and folder.is_dir() and misfiled.exists() and not orphan.exists()


def test_gc_stops_when_the_lock_is_revoked_even_if_the_lock_file_is_still_there(tmp_path, monkeypatch):
    location = tmp_path / "w"
    ws = Workspace.create(location, "w")
    orphan = ContentStore(ws).path_for("d" * 64)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    os.utime(orphan, (time.time() - 90000, time.time() - 90000))
    real_commit = ws.commit

    def commit_then_revoked(*args, **kwargs):
        seq = real_commit(*args, **kwargs)
        monkeypatch.setattr(Workspace, "_displace_writer_lock", staticmethod(lambda *a, **k: None))
        Workspace.break_writer_lock(location, Workspace.read_writer_lock(location)["token"], "owner gone")
        return seq
    monkeypatch.setattr(ws, "commit", commit_then_revoked)
    with pytest.raises(LockLostError):
        D.gc_content(ws, apply=True)
    assert orphan.exists()


def test_repair_is_refused_to_a_revoked_handle_before_it_moves_anything(setup, monkeypatch):
    location, working, doc, rev, co = setup
    stale = Workspace.open(location, writable=True)
    record = stale.meta_dir / "records" / "document" / f"{doc['id']}.json"
    record.write_text("{")
    monkeypatch.setattr(Workspace, "_displace_writer_lock", staticmethod(lambda *a, **k: None))
    Workspace.break_writer_lock(location, Workspace.read_writer_lock(location)["token"], "owner gone")
    with pytest.raises(LockLostError):
        stale.verify(repair=True)
    assert record.read_text() == "{"
    assert not (stale.meta_dir / "quarantine").exists()


def test_gc_command_names_what_it_left_alone(tmp_path, capsys):
    location = tmp_path / "w"
    with Workspace.create(location, "w") as ws:
        sub = ContentStore(ws).root / "ab"
        sub.mkdir(parents=True)
        (sub / "notes.txt").write_text("someone's file")
    cli.main(["--workspace", str(location), "doc", "gc"])
    out = capsys.readouterr().out
    assert "left alone" in out and "ab/notes.txt" in out
    assert (sub / "notes.txt").exists()
