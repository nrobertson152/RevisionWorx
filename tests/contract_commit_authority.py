# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable conformance tests for any CommitAuthority (see revisionworx/authority.py).

A new implementation (e.g. the LAN coordinator) gets these tests by providing an ``authority``
fixture plus a ``revoke(authority)`` helper fixture."""
import pytest

from revisionworx.authority import CommitAuthority
from revisionworx.workspace import (ConflictError, LockLostError, RecordCheck, RecordDelete, RecordPut,
                                    TxidReuseError)

A, B = "prj_" + "a" * 32, "prj_" + "b" * 32
T1, T2 = "1" * 32, "2" * 32


class CommitAuthorityContract:
    def test_conforms_to_protocol(self, authority):
        assert isinstance(authority, CommitAuthority)

    def test_create_then_conflict_on_create(self, authority):
        s = authority.commit("t", [RecordPut("project", A, {"v": 1}, expect_rev=0)])
        assert authority.get("project", A)["_rev"] == s
        with pytest.raises(ConflictError):
            authority.commit("t", [RecordPut("project", A, {"v": 2}, expect_rev=0)])

    def test_atomic_rejection(self, authority):
        authority.commit("t", [RecordPut("project", A, {"v": 1})])
        head = authority.head_seq()
        with pytest.raises(ConflictError):
            authority.commit("t", [RecordPut("project", B, {"v": 1}), RecordCheck("project", A, 999)])
        assert authority.head_seq() == head and authority.get("project", B) is None

    def test_ordering_and_rev(self, authority):
        s1 = authority.commit("t", [RecordPut("project", A, {"v": 1})])
        s2 = authority.commit("t", [RecordPut("project", A, {"v": 2}, expect_rev=s1)])
        assert s2 > s1 and authority.get("project", A) == {"v": 2, "_rev": s2}
        assert [e["seq"] for e in authority.entries_after(s1)] == [s2]

    def test_delete_with_expectation(self, authority):
        s = authority.commit("t", [RecordPut("project", A, {"v": 1})])
        with pytest.raises(ConflictError):
            authority.commit("t", [RecordDelete("project", A, expect_rev=s + 7)])
        authority.commit("t", [RecordDelete("project", A, expect_rev=s)])
        assert authority.get("project", A) is None and A not in authority.list("project")

    def test_outcome_and_idempotent_retry(self, authority):
        ops = [RecordPut("project", A, {"v": 1}, expect_rev=0)]
        s = authority.commit("t", ops, txid=T1)
        head = authority.head_seq()
        assert authority.commit("t", ops, txid=T1) == s  # retry: same result, nothing new
        assert authority.head_seq() == head
        assert authority.transaction_outcome(T1) == {"state": "committed", "seq": s}
        assert authority.transaction_outcome(T2) == {"state": "not-found"}
        with pytest.raises(TxidReuseError):
            authority.commit("t", [RecordPut("project", B, {"v": 9})], txid=T1)
        assert authority.get("project", B) is None

    def test_outcomes_are_classified(self, authority, revoke):
        from revisionworx.authority import classify
        authority.commit("t", [RecordPut("project", A, {"v": 1})], txid=T1)
        for ops, txid, code in (([RecordPut("project", A, {"v": 2}, expect_rev=0)], None, "conflict"),
                                ([RecordPut("project", B, {"v": 1})], T1, "txid-reuse")):
            with pytest.raises(Exception) as info:
                authority.commit("t", ops, txid=txid)
            assert classify(info.value) == code
        revoke(authority)
        with pytest.raises(Exception) as info:
            authority.commit("t", [RecordPut("project", B, {"v": 1})])
        assert classify(info.value) == "fenced"
        assert classify(RuntimeError("x")) == "error"

    def test_fencing_after_revocation(self, authority, revoke):
        revoke(authority)
        with pytest.raises(LockLostError):
            authority.commit("t", [RecordPut("project", A, {"v": "stale"})], txid=T2)
        assert authority.transaction_outcome(T2)["state"] in ("not-found", "fenced")
        assert authority.get("project", A) is None


class PreconditionIdentityContract:
    """DS finding (PR #7): a txid identifies the WHOLE request, including RecordCheck preconditions."""

    def test_same_txid_different_precondition_is_reuse(self, authority):
        s = authority.commit("t", [RecordPut("project", A, {"v": 1})])
        authority.commit("t", [RecordPut("project", B, {"v": 1}), RecordCheck("project", A, s)], txid=T1)
        with pytest.raises(TxidReuseError):  # same writes, different precondition
            authority.commit("t", [RecordPut("project", B, {"v": 1}), RecordCheck("project", A, s + 99)],
                             txid=T1)
        assert authority.commit("t", [RecordPut("project", B, {"v": 1}), RecordCheck("project", A, s)],
                                txid=T1)  # identical request still replays


class RequestIdentityV2Contract:
    """ADR-021: a txid names the complete submitted request, in order."""

    def test_reordered_operations_are_a_different_request(self, authority):
        s = authority.commit("t", [RecordPut("project", A, {"v": 1})])
        ops = [RecordCheck("project", A, s), RecordPut("project", B, {"v": 1})]
        authority.commit("t", ops, txid=T2)
        with pytest.raises(TxidReuseError):
            authority.commit("t", list(reversed(ops)), txid=T2)
        assert authority.commit("t", ops, txid=T2)  # identical request replays

    def test_check_only_request_identity(self, authority):
        s = authority.commit("t", [RecordPut("project", A, {"v": 1})])
        authority.commit("t", [RecordCheck("project", A, s)], txid=T2)
        with pytest.raises(TxidReuseError):
            authority.commit("t", [RecordCheck("project", A, s + 1)], txid=T2)

    def test_key_order_does_not_matter_but_values_do(self, authority):
        authority.commit("t", [RecordPut("project", A, {"x": 1, "y": 2})], txid=T2)
        assert authority.commit("t", [RecordPut("project", A, {"y": 2, "x": 1})], txid=T2)
        with pytest.raises(TxidReuseError):
            authority.commit("t", [RecordPut("project", A, {"y": 2, "x": 1.5})], txid=T2)

    def test_non_finite_numbers_are_rejected_before_writing(self, authority):
        head = authority.head_seq()
        with pytest.raises(ValueError):
            authority.commit("t", [RecordPut("project", A, {"v": float("nan")})], txid=T2)
        assert authority.head_seq() == head
