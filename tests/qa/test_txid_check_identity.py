# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ADR-017 transaction-ID replay identity acceptance.

A transaction's identity includes *all submitted operations*, including
RecordCheck preconditions. Even though checks are not written as journal
record mutations, changing, removing or reordering one on retry is not an
identical request and must raise TxidReuseError rather than replaying an old
success. This module intentionally skips on the earlier Phase 1 QA base.
"""
from __future__ import annotations

import pytest

from revisionworx import workspace as workspace_mod
from revisionworx.workspace import RecordPut, Workspace

if not hasattr(workspace_mod, "RecordCheck"):
    pytest.skip(
        "RecordCheck is not available on the original Phase 1 QA branch; "
        "execute this module in the disposable Phase 2 overlay",
        allow_module_level=True,
    )

RecordCheck = workspace_mod.RecordCheck
TxidReuseError = workspace_mod.TxidReuseError


def _setup_committed_request(tmp_path):
    location = tmp_path / "workspace"
    guard_a, guard_b = "root_" + "a" * 32, "root_" + "b" * 32
    target = "root_" + "c" * 32
    txid = "d" * 32
    op_name = "qa.txid.check.identity"
    guard_revs = {}

    def operations():
        return [
            RecordCheck("root", guard_a, expect_rev=guard_revs[guard_a]),
            RecordCheck("root", guard_b, expect_rev=guard_revs[guard_b]),
            RecordPut("root", target, {"id": target, "name": "one durable change"},
                      expect_rev=0),
        ]

    with Workspace.create(location, "QA txid check identity") as writer:
        writer.commit(
            "qa.guards.create",
            [
                RecordPut("root", guard_a, {"id": guard_a, "name": "guard A"}, expect_rev=0),
                RecordPut("root", guard_b, {"id": guard_b, "name": "guard B"}, expect_rev=0),
            ],
        )
        guard_revs[guard_a] = writer.get("root", guard_a)["_rev"]
        guard_revs[guard_b] = writer.get("root", guard_b)["_rev"]
        seq = writer.commit(op_name, operations(), txid=txid)
        assert writer.transaction_outcome(txid) == {"state": "committed", "seq": seq}
        assert writer.get("root", target)["name"] == "one durable change"
        assert writer.verify()["problems"] == []
        target_revision = writer.get("root", target)["_rev"]
    return location, guard_a, guard_b, target, txid, op_name, operations, seq, target_revision


def test_identical_txid_with_identical_checks_replays_after_restart(tmp_path):
    """Positive control: genuine lost-ACK retry retains prior result exactly."""
    location, _, _, target, txid, op, operations, seq, target_revision = _setup_committed_request(tmp_path)

    with Workspace.open(location, writable=True) as writer:
        before = writer.head_seq()
        assert before == seq
        assert writer.commit(op, operations(), txid=txid) == seq
        assert writer.head_seq() == before
        assert writer.transaction_outcome(txid) == {"state": "committed", "seq": seq}
        assert writer.get("root", target)["_rev"] == target_revision
        assert writer.verify()["problems"] == []


@pytest.mark.parametrize("modification", [
    "alter_checked_revision",
    "remove_check",
    "reorder_checks",
])
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P1 ADR-017 gap: committed txid identity omits RecordCheck inputs",
)
def test_changed_recordcheck_replay_is_rejected_even_after_restart(tmp_path, modification):
    """Same writes and txid cannot launder altered checks into an earlier success.

    Strict XFAIL is only for the current *wrongly accepted* replay. Unexpected
    exceptions remain failures; a real TxidReuseError is XPASS until the marker
    is removed after independent acceptance of Claude's corrected production.
    """
    location, a, b, target, txid, op, operations, seq, target_revision = _setup_committed_request(tmp_path)
    changed = operations()
    if modification == "alter_checked_revision":
        changed[0] = RecordCheck("root", a, expect_rev=0)
    elif modification == "remove_check":
        changed.pop(1)
    elif modification == "reorder_checks":
        changed[0], changed[1] = changed[1], changed[0]
    else:
        raise RuntimeError(f"unsupported QA test variation: {modification}")

    with Workspace.open(location, writable=True) as writer:
        assert writer.head_seq() == seq
        try:
            writer.commit(op, changed, txid=txid)
        except TxidReuseError:
            assert writer.head_seq() == seq
            assert writer.transaction_outcome(txid) == {"state": "committed", "seq": seq}
            assert writer.get("root", target)["_rev"] == 1
            assert writer.verify()["problems"] == []
            return
        except Exception as exc:
            pytest.fail(
                f"changed check must produce TxidReuseError, not "
                f"{type(exc).__name__}: {exc}"
            )

        # Old code compares only journalled RecordPut/Delete, so replay is
        # mistakenly accepted. Do not count that as an idempotency success.
        assert False, (
            f"txid {txid} accepted materially changed RecordCheck "
            f"preconditions ({modification}) for already committed seq {seq}"
        )
