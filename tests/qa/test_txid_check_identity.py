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
def test_changed_recordcheck_replay_is_rejected_even_after_restart(tmp_path, modification, request):
    """Same writes and txid cannot launder altered checks into an earlier success.

    Legacy journals without a persisted 'checks' identity retain a strict
    expected failure. Corrected journals must raise TxidReuseError and PASS
    normally; unrelated exceptions and incorrect outcomes fail acceptance.
    """
    location, a, b, target, txid, op, operations, seq, target_revision = _setup_committed_request(tmp_path)
    # Inspect the durable journal itself. Old ADR-017 omitted RecordCheck
    # request identity and should be strict xfail; the corrected format has a
    # 'checks' field and must PASS as a normal safety acceptance test.
    with Workspace.open(location) as reader:
        if "checks" not in reader.read_entry(seq):
            request.node.add_marker(pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="legacy ADR-017 journal omits RecordCheck txid identity",
            ))
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
            assert writer.get("root", target)["_rev"] == target_revision
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


def test_identical_txid_reordered_check_across_write_must_be_rejected(tmp_path, request):
    """Same checks and writes, but different *interleaved* operation order.

    Legacy ADR-017 journals lose the check/write interleaving and are strict
    expected failures; ADR-021 v2 journals include the complete ordered list
    digest and MUST pass normally.
    """
    location, a, b, target, txid, op, operations, seq, target_revision = _setup_committed_request(tmp_path)
    with Workspace.open(location) as reader:
        recorded = reader.read_entry(seq)
        if (recorded.get("request") or {}).get("version") != 2:
            request.node.add_marker(pytest.mark.xfail(
                strict=True, raises=AssertionError,
                reason="legacy ADR-017 lost interleaved full-operation ordering",
            ))
    ordered = operations()
    changed = [ordered[0], ordered[2], ordered[1]]
    with Workspace.open(location, writable=True) as writer:
        assert writer.head_seq() == seq
        try:
            writer.commit(op, changed, txid=txid)
        except TxidReuseError:
            assert writer.head_seq() == seq
            assert writer.transaction_outcome(txid) == {"state": "committed", "seq": seq}
            assert writer.get("root", target)["_rev"] == target_revision
            return
        except Exception as exc:
            pytest.fail(
                f"reordered full operation list must raise TxidReuseError; "
                f"got {type(exc).__name__}: {exc}"
            )
        assert False, (
            "same txid accepted reordered RecordCheck across RecordPut: "
            "request identity discarded the original operation interleaving"
        )

def test_legacy_transaction_without_request_identity_refuses_ambiguous_retry(tmp_path):
    """ADR-021 cannot infer historical RecordCheck preconditions retroactively."""
    if not hasattr(workspace_mod, "IdentityUnverifiable"):
        pytest.skip("ADR-021 explicit legacy identity refusal not present on prior Phase 2")
    root = tmp_path / "legacy-journal"
    rid = "root_" + "e" * 32
    txid = "9" * 32
    put = RecordPut("root", rid, {"id": rid, "name": "historical record"}, expect_rev=0)
    with Workspace.create(root, "QA legacy identity") as writer:
        seq = writer.head_seq() + 1
        historical = {
            "seq": seq,
            "txid": txid,
            "time": workspace_mod.now_iso(),
            "actor": workspace_mod.current_actor(),
            "op": "qa.legacy.entry",
            "records": [put.as_json()],
            "blobs": [],
            "writer_token": writer._lock_token,
            # Deliberately no v2 "request" field: an actual pre-ADR021 format.
        }
        historical["digest"] = workspace_mod.entry_digest(historical)
        from revisionworx import atomicio
        atomicio.publish_exclusive(
            root / ".revisionworx" / "journal" / f"{seq:020d}.json",
            atomicio.dumps_json(historical),
        )
        writer.recover()
        assert writer.get("root", rid)["name"] == "historical record"
        assert writer.transaction_outcome(txid) == {"state": "committed", "seq": seq}
    with Workspace.open(root, writable=True) as writer:
        before = writer.head_seq()
        with pytest.raises(workspace_mod.IdentityUnverifiable) as error:
            writer.commit("qa.legacy.entry", [put], txid=txid)
        assert error.value.seq == seq
        assert writer.head_seq() == before
        assert writer.transaction_outcome(txid) == {"state": "committed", "seq": seq}
        assert writer.get("root", rid)["_rev"] == seq
        assert writer.verify()["problems"] == []


def test_adr021_rejects_nonfinite_payload_before_journal_publication(tmp_path):
    """Non-canonical NaN must not acquire a durable txid identity."""
    if not hasattr(workspace_mod, "REQUEST_IDENTITY_VERSION"):
        pytest.skip("canonical request identity not present on earlier Phase 2")
    root = tmp_path / "reject-nonfinite"
    with Workspace.create(root, "QA nonfinite payload") as writer:
        rid = "root_" + "8" * 32
        seq = writer.head_seq()
        txid = "8" * 32
        with pytest.raises(ValueError):
            writer.commit("qa.nonfinite", [
                RecordPut("root", rid, {"id": rid, "value": float("nan")}, expect_rev=0)
            ], txid=txid)
        assert writer.head_seq() == seq
        assert writer.transaction_outcome(txid) == {"state": "not-found"}
        assert writer.get("root", rid) is None
