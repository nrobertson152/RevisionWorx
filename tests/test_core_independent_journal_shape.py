# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent fail-closed journal-shape acceptance gates (issue #29).

Pinned negative control: phase2-documents f01b37d345c5208ab0fa520588a2abe8c66c894a
(runtime 96f9c368). These are STRICT desired-behavior tests: do not mark
xfail or interpret a red result as successful product acceptance.

Only disposable, CLOSED workspaces are modified. Never edit a live lock.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from revisionworx.workspace import Workspace, WorkspaceCorruptError


@pytest.fixture
def damaged_workspace(tmp_path: Path):
    location = tmp_path / "workspace"
    with Workspace.create(location, "journal-shape-gate"):
        pass
    journal = location / ".revisionworx" / "journal" / "00000000000000000001.json"
    original = json.loads(journal.read_text(encoding="utf-8"))
    assert original["seq"] == 1
    assert original["records"]
    return location, journal, original


def _replace_entry(journal: Path, entry: dict) -> None:
    # Digestless journal entries are supported for legacy compatibility.
    # Remove digest to exercise structural validation, not digest mismatch.
    entry.pop("digest", None)
    journal.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")


def _boolean_sequence(entry):
    entry["seq"] = True  # True == 1 in Python; still not a valid journal sequence


def _string_delete(entry):
    entry["records"][0]["delete"] = "false"  # truthy, not a boolean


def _list_put_data(entry):
    entry["records"][0]["data"] = []


def _nonstring_blob(entry):
    entry["blobs"] = [7]


def _unhashable_txid(entry):
    entry["txid"] = ["bad"]


def _unhashable_writer_token(entry):
    entry["writer_token"] = ["bad"]


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(_boolean_sequence, id="boolean-sequence"),
        pytest.param(_string_delete, id="nonboolean-delete"),
        pytest.param(_list_put_data, id="nonmapping-put-data"),
        pytest.param(_nonstring_blob, id="nonstring-blob"),
        pytest.param(_unhashable_txid, id="unhashable-txid"),
        pytest.param(_unhashable_writer_token, id="unhashable-writer-token"),
    ],
)
def test_malformed_journal_is_typed_and_never_healthy(damaged_workspace, corrupt):
    location, journal, original = damaged_workspace
    corrupt(original)
    _replace_entry(journal, original)

    # Public read_entry must reject invalid structure with a typed error.
    with Workspace.open(location, writable=False) as ws:
        with pytest.raises(WorkspaceCorruptError):
            ws.read_entry(1)

        # health() must never crash or silently claim a damaged journal is OK.
        report = ws.health()
        assert report["ok"] is False
        assert isinstance(report["reason"], str) and report["reason"]


def test_unchanged_disposable_workspace_is_healthy(damaged_workspace):
    location, _journal, _entry = damaged_workspace
    with Workspace.open(location, writable=False) as ws:
        assert ws.read_entry(1)["seq"] == 1
        assert ws.health()["ok"] is True
