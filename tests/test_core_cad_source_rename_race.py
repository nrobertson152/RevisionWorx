# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic source-path replacement regression for issue #29.

A CAD editor may save by atomically replacing a file with a different inode,
while preserving its size and mtime. Inject the replacement at the boundary
between the first copy and the independent second source read. No sleeps,
threads, external CAD application, or mutation of a live shared workspace.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import revisionworx.content as content_module
from revisionworx.content import ContentStore, SourceChangedError
from revisionworx.workspace import Workspace


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*") if p.is_file()
    }


def test_equal_size_mtime_rename_during_capture_refuses_publication(tmp_path, monkeypatch):
    source = tmp_path / "assembly.iam"
    original = b"native-CAD-reference-A"
    replacement = b"native-CAD-reference-B"
    assert len(original) == len(replacement)
    source.write_bytes(original)
    original_stat = source.stat()
    workspace = tmp_path / "workspace"
    with Workspace.create(workspace, "rename-race") as ws:
        store = ContentStore(ws)
        before = _files(store.root)
        real_hash_pinned = content_module._hash_pinned
        calls = []

        def replace_at_second_read(path, expected):
            assert Path(path) == source
            calls.append(1)
            other = tmp_path / "editor-save.tmp"
            other.write_bytes(replacement)
            os.utime(other, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            os.replace(other, source)
            assert source.stat().st_size == original_stat.st_size
            assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
            return real_hash_pinned(path, expected)

        monkeypatch.setattr(content_module, "_hash_pinned", replace_at_second_read)
        with pytest.raises(SourceChangedError):
            store.store_file(source)
        assert calls == [1], "injection must actually execute"
        assert source.read_bytes() == replacement
        assert _files(store.root) == before, "rejected capture must not publish content or leave staging"


def test_unchanged_source_capture_publishes_verified_bytes(tmp_path):
    source = tmp_path / "assembly.iam"
    payload = b"native-CAD-reference-A"
    source.write_bytes(payload)
    workspace = tmp_path / "workspace"
    with Workspace.create(workspace, "rename-control") as ws:
        store = ContentStore(ws)
        saved = store.store_file(source)
        assert saved.sha256 == hashlib.sha256(payload).hexdigest()
        assert saved.size == len(payload)
        assert store.read_verified(saved.sha256, max_bytes=len(payload)) == payload
    assert source.read_bytes() == payload
