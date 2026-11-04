# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent QA for engineering-CAD working-file provenance.

The on-disk content-store blob must reflect the working pathname captured
successfully by store_file(), or a concurrent save must fail as SourceChangedError.
These tests use real file operations on disposable files with no external lock
namespace mutation. They do not claim a general snapshot guarantee under
uncooperative concurrent in-place writers.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from revisionworx import atomicio
from revisionworx.content import ContentStore, SourceChangedError
from revisionworx.workspace import Workspace

OLD = b"OLD-CAD-DATA:" + b"\x41" * 128
NEW = b"NEW-CAD-DATA:" + b"\x42" * 128
assert len(OLD) == len(NEW)


def test_same_metadata_save_by_rename_must_not_capture_obsolete_file(tmp_path, monkeypatch):
    """Renaming the opened inode away must reject even when size and mtime match."""
    source = tmp_path / "assembly.ipt"
    source.write_bytes(OLD)
    before = source.stat()
    original_open = atomicio._open_for_read
    switched = []

    def opening_then_cad_save(path):
        handle = original_open(path)
        if Path(path) == source:
            replacement = tmp_path / "assembly.tmp"
            replacement.write_bytes(NEW)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(source, tmp_path / "assembly.previous")
            os.rename(replacement, source)
            switched.append(True)
        return handle

    with Workspace.create(tmp_path / "ws", "QA provenance") as ws:
        rejected, stored = False, None
        with monkeypatch.context() as patch:
            patch.setattr(atomicio, "_open_for_read", opening_then_cad_save)
            try:
                stored = ContentStore(ws).store_file(source)
            except SourceChangedError:
                rejected = True
        assert switched == [True], "test must actually trigger CAD rename"
        assert source.read_bytes() == NEW
        assert (source.stat().st_size, source.stat().st_mtime_ns) == (
            before.st_size, before.st_mtime_ns)
        assert (source.stat().st_dev, source.stat().st_ino) != (
            before.st_dev, before.st_ino), "new source must be a distinct filesystem object"
        if not rejected:
            assert stored.sha256 == hashlib.sha256(OLD).hexdigest(), (
                "source changed but snapshot unexpectedly captured other bytes")
            assert ContentStore(ws).read_verified(stored.sha256, len(OLD)) == OLD
        assert rejected, "UNSAFE: stored obsolete old CAD bytes after current pathname moved to NEW"


class _MutateAfterFirstRead:
    def __init__(self, underlying, source: Path, original_stat):
        self.underlying = underlying
        self.source = source
        self.original_stat = original_stat
        self.changed = False

    def __enter__(self):
        self.underlying.__enter__()
        return self

    def __exit__(self, *args):
        return self.underlying.__exit__(*args)

    def fileno(self):
        return self.underlying.fileno()

    def read(self, size=-1):
        chunk = self.underlying.read(size)
        if not self.changed and chunk:
            with open(self.source, "r+b") as fh:
                fh.seek(0)
                fh.write(NEW)
                fh.flush()
                os.fsync(fh.fileno())
            os.utime(self.source, ns=(
                self.original_stat.st_atime_ns, self.original_stat.st_mtime_ns))
            self.changed = True
        return chunk


def test_same_inode_in_place_save_with_preserved_metadata_must_not_silently_capture_old_bytes(
        tmp_path, monkeypatch):
    """A rename-only file-id fix does not detect this second, distinct provenance failure."""
    source = tmp_path / "hydraulic-assembly.ipt"
    source.write_bytes(OLD)
    before = source.stat()
    original_open = atomicio._open_for_read
    wrappers = []

    def open_then_mutate(path):
        handle = original_open(path)
        if Path(path) == source:
            wrapper = _MutateAfterFirstRead(handle, source, before)
            wrappers.append(wrapper)
            return wrapper
        return handle

    with Workspace.create(tmp_path / "ws", "QA same-file save") as ws:
        rejected, stored = False, None
        with monkeypatch.context() as patch:
            patch.setattr(atomicio, "_open_for_read", open_then_mutate)
            try:
                stored = ContentStore(ws).store_file(source)
            except SourceChangedError:
                rejected = True
        # Windows also routes the core's second, independent read through the patched opener.
        assert len(wrappers) >= 1 and wrappers[0].changed
        after = source.stat()
        assert source.read_bytes() == NEW
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert (after.st_size, after.st_mtime_ns) == (
            before.st_size, before.st_mtime_ns)
        if not rejected:
            assert stored.sha256 == hashlib.sha256(OLD).hexdigest(), (
                "in-place replacement with preserved mtime must not certify unrelated bytes")
            assert ContentStore(ws).read_verified(stored.sha256, len(OLD)) == OLD
        assert rejected, "UNSAFE: stored OLD bytes after same-inode CAD file became NEW"


def test_unchanged_working_source_is_saved_and_verified(tmp_path):
    source = tmp_path / "control.ipt"
    source.write_bytes(OLD)
    with Workspace.create(tmp_path / "ws", "QA unchanged control") as ws:
        stored = ContentStore(ws).store_file(source)
        assert stored.sha256 == hashlib.sha256(OLD).hexdigest()
        assert ContentStore(ws).read_verified(stored.sha256, len(OLD)) == OLD
        assert source.read_bytes() == OLD


def test_changed_mtime_after_save_by_rename_is_rejected(tmp_path, monkeypatch):
    """Positive control demonstrates existing size/mtime mismatch guard works."""
    source = tmp_path / "changed-mtime.ipt"
    source.write_bytes(OLD)
    before = source.stat()
    original_open = atomicio._open_for_read
    changed = []

    def open_then_save(path):
        handle = original_open(path)
        if Path(path) == source:
            candidate = tmp_path / "new.tmp"
            candidate.write_bytes(NEW)
            os.utime(candidate, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000_000))
            os.replace(source, tmp_path / "old.backup")
            os.rename(candidate, source)
            changed.append(True)
        return handle

    with Workspace.create(tmp_path / "ws", "QA changed mtime") as ws:
        with monkeypatch.context() as patch:
            patch.setattr(atomicio, "_open_for_read", open_then_save)
            with pytest.raises(SourceChangedError):
                ContentStore(ws).store_file(source)
    assert changed == [True]
