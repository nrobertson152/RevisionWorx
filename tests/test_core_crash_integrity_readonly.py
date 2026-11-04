# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive crash-corruption fixtures for the Phase 2 journal (#29).

Only a CLOSED disposable workspace is damaged. These tests never modify
production code, a live writer lock, or another agent's repository branch.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from revisionworx.workspace import Workspace, WorkspaceCorruptError


def _snapshot(root: Path) -> dict[str, str]:
    """Capture all authoritative and derived file bytes, not mtimes."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*") if p.is_file()
    }


@pytest.fixture
def closed_workspace(tmp_path: Path):
    root = tmp_path / "disposable"
    with Workspace.create(root, "crash-integrity-fixture"):
        pass
    meta = root / ".revisionworx"
    return root, meta


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b'{"seq": 1, "records": [', id="truncated-json"),
        pytest.param(b'null', id="valid-json-nonobject"),
        pytest.param(b'[]', id="valid-json-array"),
    ],
)
def test_damaged_closed_journal_fails_typed_and_read_only_does_not_mutate(
    closed_workspace, payload
):
    root, meta = closed_workspace
    entry = meta / "journal" / "00000000000000000001.json"
    entry.write_bytes(payload)
    before = _snapshot(root)
    with Workspace.open(root, writable=False) as ws:
        with pytest.raises(WorkspaceCorruptError):
            ws.read_entry(1)
        report = ws.health()
        assert report["ok"] is False
        assert isinstance(report["reason"], str) and report["reason"]
    assert _snapshot(root) == before


def test_missing_first_journal_entry_refuses_open_without_repair(closed_workspace):
    root, meta = closed_workspace
    # A later sequence without seq 1 is a structural gap, not a recoverable
    # pending materialisation. Use a byte-for-byte copy, then remove seq 1.
    first = meta / "journal" / "00000000000000000001.json"
    second = meta / "journal" / "00000000000000000002.json"
    second.write_bytes(first.read_bytes())
    first.unlink()
    before = _snapshot(root)
    with pytest.raises(WorkspaceCorruptError, match="gaps"):
        Workspace.open(root, writable=False)
    assert _snapshot(root) == before


def test_intact_closed_workspace_read_only_health_preserves_every_file(closed_workspace):
    root, _meta = closed_workspace
    before = _snapshot(root)
    with Workspace.open(root, writable=False) as ws:
        assert ws.read_entry(1)["seq"] == 1
        assert ws.health()["ok"] is True
    assert _snapshot(root) == before
