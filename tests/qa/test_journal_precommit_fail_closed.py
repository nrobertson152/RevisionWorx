# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent fail-closed precommit oracle for damaged authoritative journals.

Only a closed disposable workspace is corrupted. A rejected writer acquisition
must never publish an additional journal entry or alter the damaged evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from revisionworx.workspace import Workspace, WorkspaceCorruptError


@pytest.mark.parametrize("bad_value", [None, [], "not-an-entry", 42], ids=[
    "null", "array", "string", "number"
])
def test_corrupt_journal_rejects_writer_without_publishing(tmp_path: Path, bad_value):
    location = tmp_path / "damaged"
    with Workspace.create(location, "fail-closed-writer"):
        pass
    journal_dir = location / ".revisionworx" / "journal"
    first = journal_dir / "00000000000000000001.json"
    assert first.exists()
    first.write_text(json.dumps(bad_value), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in journal_dir.iterdir() if p.is_file()}
    assert len(before) == 1

    # Opening writable must fail before the caller can publish a commit.
    with pytest.raises(WorkspaceCorruptError):
        with Workspace.open(location, writable=True) as writer:
            writer.commit("qa.must-not-publish", [])

    after = {p.name: p.read_bytes() for p in journal_dir.iterdir() if p.is_file()}
    assert after == before, "failed writer acquisition altered authoritative journal"
    assert not (journal_dir / "00000000000000000002.json").exists()
