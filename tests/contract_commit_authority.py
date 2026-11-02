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

    def test_fencing_after_revocation(self, authority, revoke):
        revoke(authority)
        with pytest.raises(LockLostError):
            authority.commit("t", [RecordPut("project", A, {"v": "stale"})], txid=T2)
        assert authority.transaction_outcome(T2)["state"] in ("not-found", "fenced")
        assert authority.get("project", A) is None
