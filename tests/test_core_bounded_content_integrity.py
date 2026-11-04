# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only bounded content integrity gates; issue #29.

Only content objects in a disposable workspace are damaged. These tests
never alter authoritative journal entries or another agent's source tree.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from revisionworx.content import ContentStore, ContentTooLarge
from revisionworx.workspace import Workspace, WorkspaceCorruptError


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture
def content_fixture(tmp_path: Path):
    root = tmp_path / "content-gate"
    source = tmp_path / "part.ipt"
    payload = b"synthetic-native-cad-revision-001"
    source.write_bytes(payload)
    with Workspace.create(root, "content-gate") as ws:
        stored = ContentStore(ws).store_file(source)
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    return root, stored.sha256, payload


def test_verified_read_returns_exact_bytes_and_preserves_closed_workspace(content_fixture):
    root, digest, payload = content_fixture
    before = _snapshot(root)
    with Workspace.open(root, writable=False) as ws:
        assert ContentStore(ws).read_verified(digest, max_bytes=len(payload), expected_size=len(payload)) == payload
    assert _snapshot(root) == before


def test_same_length_object_corruption_is_typed_and_never_exported(content_fixture, tmp_path):
    root, digest, payload = content_fixture
    with Workspace.open(root, writable=False) as ws:
        object_path = ContentStore(ws).path_for(digest)
    object_path.write_bytes(b"X" + payload[1:])
    before = _snapshot(root)
    destination = tmp_path / "must-not-exist.ipt"
    with Workspace.open(root, writable=False) as ws:
        store = ContentStore(ws)
        with pytest.raises(WorkspaceCorruptError):
            store.read_verified(digest, max_bytes=len(payload), expected_size=len(payload))
        with pytest.raises(WorkspaceCorruptError):
            store.export(digest, destination)
    assert not destination.exists()
    assert _snapshot(root) == before


def test_bounded_read_rejects_oversize_without_mutation(content_fixture):
    root, digest, payload = content_fixture
    before = _snapshot(root)
    with Workspace.open(root, writable=False) as ws:
        with pytest.raises(ContentTooLarge):
            ContentStore(ws).read_verified(digest, max_bytes=len(payload) - 1)
    assert _snapshot(root) == before


def test_missing_content_object_fails_closed_without_creating_files(content_fixture):
    root, digest, payload = content_fixture
    with Workspace.open(root, writable=False) as ws:
        ContentStore(ws).path_for(digest).unlink()
    before = _snapshot(root)
    with Workspace.open(root, writable=False) as ws:
        with pytest.raises(WorkspaceCorruptError):
            ContentStore(ws).read_verified(digest, max_bytes=len(payload))
    assert _snapshot(root) == before
