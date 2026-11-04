# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ADR-017/021 ordered RecordCheck identity/restart acceptance.

Test-only. Pinned historical baseline f01b37d345c5208ab0fa520588a2abe8c66c894a.
Do not treat this suite as acceptance without execution on an exact source SHA.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from revisionworx.workspace import (
    RecordCheck, RecordPut, TxidReuseError, Workspace,
)


def _txid():
    return uuid4().hex


def _request(checks):
    return [*checks, RecordPut("settings", "adr017_result", {"value": "ok"}, expect_rev=0)]


def _checks():
    return [RecordCheck("settings", "workspace", 1),
            RecordCheck("settings", "missing_adr017", 0)]


def test_ordered_checks_are_part_of_idempotency_identity_across_restart(tmp_path):
    location = tmp_path / "project"
    txid = _txid()
    with Workspace.create(location, "ordered-checks") as ws:
        ops = _request(_checks())
        committed = ws.commit("adr017.accept", ops, txid=txid)
        assert ws.commit("adr017.accept", _request(_checks()), txid=txid) == committed
        assert ws.transaction_outcome(txid) == {"state": "committed", "seq": committed}
        assert ws.head_seq() == committed

    with Workspace.open(location, writable=True) as ws:
        assert ws.commit("adr017.accept", _request(_checks()), txid=txid) == committed
        with pytest.raises(TxidReuseError):
            ws.commit("adr017.accept", _request(list(reversed(_checks()))), txid=txid)
        assert ws.head_seq() == committed
        assert ws.get("settings", "adr017_result")["value"] == "ok"
        assert ws.transaction_outcome(txid) == {"state": "committed", "seq": committed}


def test_changed_check_precondition_refuses_reused_txid_after_restart(tmp_path):
    location = tmp_path / "project"
    txid = _txid()
    with Workspace.create(location, "changed-precondition") as ws:
        committed = ws.commit("adr017.accept", _request(_checks()), txid=txid)
    with Workspace.open(location, writable=True) as ws:
        modified = [RecordCheck("settings", "workspace", 0), _checks()[1]]
        with pytest.raises(TxidReuseError):
            ws.commit("adr017.accept", _request(modified), txid=txid)
        assert ws.head_seq() == committed
        assert ws.transaction_outcome(txid) == {"state": "committed", "seq": committed}


def test_identical_retry_does_not_recheck_stale_preconditions(tmp_path):
    location = tmp_path / "project"
    txid = _txid()
    with Workspace.create(location, "stale-check-retry") as ws:
        committed = ws.commit("adr017.accept", _request(_checks()), txid=txid)
        ws.commit("adr017.advance", [RecordPut(
            "settings", "workspace", {"changed": True}, expect_rev=1)])
        advanced = ws.head_seq()
        assert advanced > committed
    with Workspace.open(location, writable=True) as ws:
        # A previously committed identical request must return its original
        # outcome even though the checked record has since advanced.
        assert ws.commit("adr017.accept", _request(_checks()), txid=txid) == committed
        assert ws.head_seq() == advanced
        assert ws.transaction_outcome(txid) == {"state": "committed", "seq": committed}
