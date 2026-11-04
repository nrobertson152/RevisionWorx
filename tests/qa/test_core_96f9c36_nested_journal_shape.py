# SPDX-License-Identifier: GPL-3.0-or-later
"""QA damage-recovery contract: nested journal fields must also fail closed.

Legacy journal entries may lack 'digest', but still have a well-formed shape.
A structurally invalid record or transaction attribution must never be
reported healthy or leak KeyError/TypeError while diagnosing a damaged file.
These are offline fault-injection tests on CLOSED disposable workspaces.
"""
import json

import pytest

from revisionworx.workspace import Workspace, WorkspaceCorruptError

RID = "root_" + "a" * 32

CORRUPT = (
    ("missing-put-data", {"seq": 1, "records": [{"kind": "settings", "id": "workspace"}]}),
    ("wrong-put-data", {"seq": 1, "records": [{"kind": "settings", "id": "workspace", "data": []}]}),
    ("wrong-kind-type", {"seq": 1, "records": [{"kind": ["settings"], "id": "workspace", "data": {}}]}),
    ("wrong-id-type", {"seq": 1, "records": [{"kind": "settings", "id": ["workspace"], "data": {}}]}),
    ("wrong-txid-type", {"seq": 1, "records": [], "txid": ["not hashable"]}),
    ("wrong-writer-token-type", {"seq": 1, "records": [], "writer_token": ["not hashable"]}),
    ("wrong-blobs-item", {"seq": 1, "records": [], "blobs": [42]}),
)


def damage_closed_workspace(tmp_path, payload):
    root = tmp_path / "damage"
    with Workspace.create(root, "QA nested journal shape"):
        pass
    journal = root / ".revisionworx" / "journal"
    target, = journal.glob("*.json")
    target.write_text(json.dumps(payload), encoding="utf-8")
    return root


@pytest.mark.parametrize("case,payload", CORRUPT, ids=[row[0] for row in CORRUPT])
def test_nested_journal_corruption_is_never_accepted(tmp_path, case, payload):
    root = damage_closed_workspace(tmp_path, payload)
    with Workspace.open(root) as ws:
        with pytest.raises(WorkspaceCorruptError, match="journal entry"):
            ws.read_entry(1)


@pytest.mark.parametrize("case,payload", CORRUPT, ids=[row[0] for row in CORRUPT])
def test_health_reports_nested_journal_corruption_without_exception(tmp_path, case, payload):
    root = damage_closed_workspace(tmp_path, payload)
    with Workspace.open(root) as ws:
        state = ws.health()
    assert state["ok"] is False
    assert "journal" in (state["reason"] or "")
