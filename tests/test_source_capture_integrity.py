# SPDX-License-Identifier: GPL-3.0-or-later
"""Source-content capture detects uncooperative writers (P0, CORE_INVARIANTS C1-C4).

A writer is injected at an exact point of ContentStore.store_file by wrapping the read handle. Every
"detected" case must raise SourceChangedError and leave no content object, no journal entry and the
checkout in place. On f01b37d (size+mtime path stat only) the same-size/same-mtime cases are stored.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from revisionworx import atomicio
from revisionworx import content as content_mod
from revisionworx import documents as D
from revisionworx.content import ContentStore, SourceChangedError

from test_documents import proj, reg  # noqa: F401  (fixture reuse)

ORIGINAL = b"IPT revision one " * 64        # 1088 bytes
SAME_SIZE = b"IPT revision TWO " * 64       # same length, different bytes


@pytest.fixture
def checked_out(ws, proj):  # noqa: F811
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    path = ws.folder / "02 CAD/Parts/bracket.ipt"
    path.write_bytes(ORIGINAL)
    co = D.checkout(ws, doc["id"])
    return ws, doc, co, path


def _objects(ws) -> set[str]:
    root = ws.meta_dir / "content" / "sha256"
    return {p.name for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")}


def _inject(monkeypatch, path: Path, action, *, on_open: int = 1, after_reads: int = 1):
    """Run ``action`` once, during open number ``on_open`` of ``path``, after ``after_reads`` reads."""
    monkeypatch.setattr(content_mod, "CHUNK", 64)
    real_open = atomicio._open_for_read
    opens = []

    class Hooked:
        def __init__(self, fh):
            self.fh, self.reads = fh, 0

        def read(self, n=-1):
            self.reads += 1
            if len(opens) == on_open and self.reads == after_reads + 1 and not getattr(self, "fired", False):
                self.fired = True
                action()
            return self.fh.read(n)

        def fileno(self):
            return self.fh.fileno()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()

    def hooked(p):
        fh = real_open(p)
        if Path(p) == path:
            opens.append(1)
            return Hooked(fh)
        return fh
    monkeypatch.setattr(content_mod.atomicio, "_open_for_read", hooked)


def _same_mtime(path: Path, st: os.stat_result) -> None:
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _rename_replace_same_size_same_mtime(path: Path):
    st = os.stat(path)
    side = path.with_name("~bracket.tmp")
    side.write_bytes(SAME_SIZE)
    _same_mtime(side, st)
    os.replace(side, path)                    # save-by-rename, as Inventor/SolidWorks do


def _in_place_same_size_mtime_restored(path: Path):
    st = os.stat(path)
    with open(path, "r+b") as fh:            # same inode
        fh.write(SAME_SIZE[:32])
    _same_mtime(path, st)


def _assert_nothing_captured(ws, doc, co, before_objects, head):
    assert _objects(ws) == before_objects
    assert ws.head_seq() == head
    assert ws.get("checkout", doc["id"])["token"] == co["token"]
    assert ws.get("document", doc["id"])["rev_count"] == doc["rev_count"]


@pytest.mark.parametrize("writer", [
    pytest.param(_rename_replace_same_size_same_mtime, id="save-by-rename-same-size-same-mtime"),
    pytest.param(_in_place_same_size_mtime_restored, id="same-inode-same-size-mtime-restored"),
])
@pytest.mark.parametrize("when", [
    pytest.param(dict(on_open=1, after_reads=1), id="during-first-read"),
    pytest.param(dict(on_open=2, after_reads=1), id="during-verification-read"),
])
def test_concurrent_writer_is_detected_and_nothing_is_committed(checked_out, monkeypatch, writer, when):
    ws, doc, co, path = checked_out
    before, head = _objects(ws), ws.head_seq()
    _inject(monkeypatch, path, lambda: writer(path), **when)
    with pytest.raises(SourceChangedError):
        D.checkin(ws, doc["id"], co["token"], "v2")
    monkeypatch.undo()
    _assert_nothing_captured(ws, doc, co, before, head)
    # The retry captures the file as it now stands, consistently.
    rev = D.checkin(ws, doc["id"], co["token"], "v2")
    assert ContentStore(ws).read_verified(rev["sha256"], 1 << 20) == path.read_bytes()


def test_replaced_between_open_and_identity_check_is_detected(checked_out, monkeypatch):
    """The path is swapped for a link right after we open it: we would hash one file while the path
    names another."""
    ws, doc, co, path = checked_out
    if sys.platform == "win32":
        pytest.skip("creating symbolic links needs privileges on Windows")
    before, head = _objects(ws), ws.head_seq()
    elsewhere = path.with_name("elsewhere.ipt")
    elsewhere.write_bytes(SAME_SIZE)

    def swap():
        os.replace(path, path.with_name("old.ipt"))
        os.symlink(elsewhere, path)
    _inject(monkeypatch, path, swap, on_open=1, after_reads=0)
    with pytest.raises(SourceChangedError):
        D.checkin(ws, doc["id"], co["token"], "v2")
    monkeypatch.undo()
    _assert_nothing_captured(ws, doc, co, before, head)


def test_stored_bytes_are_exactly_the_stable_source(checked_out):
    """Positive acceptance: no writer, the revision holds the file's bytes and mtime."""
    ws, doc, co, path = checked_out
    path.write_bytes(SAME_SIZE)
    rev = D.checkin(ws, doc["id"], co["token"], "v2")
    assert ContentStore(ws).read_verified(rev["sha256"], 1 << 20) == SAME_SIZE
    assert rev["source_mtime_ns"] == os.stat(path).st_mtime_ns


def test_change_after_capture_is_not_part_of_the_revision(checked_out, monkeypatch):
    """Documented limit: capture is a snapshot. A save after the capture finished is not in the
    revision; the working file then reads as modified, never as unchanged."""
    ws, doc, co, path = checked_out
    path.write_bytes(SAME_SIZE)
    real_commit = ws.commit

    def save_then_commit(*a, **kw):
        path.write_bytes(b"later save " * 10)
        return real_commit(*a, **kw)
    monkeypatch.setattr(ws, "commit", save_then_commit)
    rev = D.checkin(ws, doc["id"], co["token"], "v2")
    monkeypatch.undo()
    assert ContentStore(ws).read_verified(rev["sha256"], 1 << 20) == SAME_SIZE
    assert D.document_status(ws, doc["id"], verify_hash=True)["state"] == "modified-externally"
