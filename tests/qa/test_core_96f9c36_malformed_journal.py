# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent QA: a structurally damaged but parseable journal is a controlled corruption.

Source under test: core 96f9c368a11fd43c0440d404c7258c7dfeacabf6.
The core promises malformed journal entries raise WorkspaceCorruptError rather
than raw Python exceptions, and health() reports damaged journals without
raising. These are fault-injection recovery tests, NOT adversarial manipulation
of an active writer's lock namespace or a claim about power-loss safety.
"""
from __future__ import annotations

import json

import pytest

from revisionworx.workspace import Workspace, WorkspaceCorruptError

# Valid JSON with the wrong outer shape: the most common partial/incorrect restore
# formats still parse, so JSONDecodeError alone does not protect these code paths.
BAD_ENTRIES = (
    ("null", None),
    ("array", []),
    ("string", "not a journal entry"),
    ("number", 9),
    ("bool", True),
    ("missing-records", {"seq": 1}),
    ("wrong-records", {"seq": 1, "records": "not-an-array"}),
    ("invalid-record-item", {"seq": 1, "records": [None]}),
)


def _damaged_world(tmp_path, payload):
    root = tmp_path / "share"
    with Workspace.create(root, "QA malformed journal"):
        pass
    journal = root / ".revisionworx" / "journal"
    records = sorted(journal.glob("*.json"))
    assert records, "fixture must have a real product journal entry"
    records[0].write_text(json.dumps(payload), encoding="utf-8")
    return root, int(records[0].stem)


@pytest.mark.parametrize("name,payload", BAD_ENTRIES, ids=[x[0] for x in BAD_ENTRIES])
def test_read_entry_maps_valid_json_wrong_structure_to_corruption(tmp_path, name, payload):
    root, seq = _damaged_world(tmp_path, payload)
    with Workspace.open(root) as reader:
        with pytest.raises(WorkspaceCorruptError, match=r"journal entry"):
            reader.read_entry(seq)


@pytest.mark.parametrize("name,payload", BAD_ENTRIES, ids=[x[0] for x in BAD_ENTRIES])
def test_health_never_crashes_for_structurally_invalid_journal(tmp_path, name, payload):
    root, _ = _damaged_world(tmp_path, payload)
    with Workspace.open(root) as reader:
        report = reader.health()
    assert report["ok"] is False
    assert isinstance(report["reason"], str) and "journal" in report["reason"]
    assert report["head_seq"] >= 1


def test_valid_journal_health_control_remains_good(tmp_path):
    root = tmp_path / "good"
    with Workspace.create(root, "QA healthy"):
        pass
    with Workspace.open(root) as reader:
        assert reader.health()["ok"] is True
        assert reader.read_entry(1)["records"]
