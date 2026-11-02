"""Direct ADR-017 CommitAuthorityContract conformance against a LOCAL batch adapter.

This reuses the EXACT production contract from a separately checked-out Phase 2
branch. The adapter is TEST-ONLY: it is NOT a wire protocol, service, approved
storage backend, or safe replacement for Workspace. Its resource-CAS pre-read
can conservatively reject a concurrent unconditional writer, unlike the
application's optional per-record expect_rev. Document this divergence.

Run: REVISIONWORX_PHASE2_CHECKOUT=/path/to/phase2-documents
     python -m pytest -q prototype/test_phase2_contract_adapter.py
"""
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

SOURCE = Path(os.environ["REVISIONWORX_PHASE2_CHECKOUT"]).resolve()
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "tests"))

from contract_commit_authority import (CommitAuthorityContract, PreconditionIdentityContract,
                                       RequestIdentityV2Contract)  # noqa: E402
from revisionworx.workspace import (ConflictError, LockLostError, RecordCheck,  # noqa: E402
                                    RecordDelete, RecordPut, TxidReuseError)
from prototype.record_batch_research import (BatchAuthorityResearch,  # noqa: E402
                                             BatchLeaseFenced,
                                             BatchRecordConflict,
                                             BatchResourceConflict,
                                             BatchTxidConflict)
from prototype.witnessed_coordinator import TestIdentityProvider as FixtureIdentityProvider  # noqa: E402

assert Path(sys.modules["revisionworx.workspace"].__file__).resolve() == SOURCE / "revisionworx" / "workspace.py"

A = "prj_" + "a" * 32
_KEY = b"phase2-contract-research-test-key-never-prod"


class ResearchCommitAuthorityAdapter:
    """Owns one test lease and delegates record CAS to witnessed SQLite batches."""

    def __init__(self, root):
        self.idp = FixtureIdentityProvider({"test": ("secret", "engineer"),
                                         "admin": ("admin-secret", "administrator")}, _KEY)
        self.principal = self.idp.authenticate("test", "secret")
        self.admin = self.idp.authenticate("admin", "admin-secret")
        self.model = BatchAuthorityResearch(
            root / "authority.db", root / "epoch", root / "witness",
            root / "objects", self.idp)
        self.credential = self.model.acquire("workspace", self.principal)

    def commit(self, op: str, ops: list, blob_refs: list[str] | None = None,
               txid: str | None = None) -> int:
        if blob_refs:
            raise NotImplementedError("test adapter does not map RevisionWorx blob refs into SMB manifests")
        txid = txid or uuid.uuid4().hex
        operations = []
        for item in ops:
            if isinstance(item, RecordPut):
                operations.append({"type": "put", "kind": item.kind, "id": item.id,
                                   "data": {k: v for k, v in item.data.items() if k != "_rev"},
                                   "expect_rev": item.expect_rev})
            elif isinstance(item, RecordDelete):
                operations.append({"type": "delete", "kind": item.kind,
                                   "id": item.id, "expect_rev": item.expect_rev})
            elif isinstance(item, RecordCheck):
                operations.append({"type": "check", "kind": item.kind,
                                   "id": item.id, "expect_rev": item.expect_rev})
            else:
                raise ValueError("unknown record operation")
        # The current research batch schema requires a resource-level CAS
        # in addition to optional record CAS. Recover its original expected
        # resource version for idempotent same-txid retry.
        with self.model.core.authority._transaction() as db:
            self.model._verify(db)
            row = db.execute("SELECT expected_revision FROM commits WHERE txid=?",
                             (txid,)).fetchone()
            if row is None:
                row = db.execute("SELECT record_revision FROM authority WHERE resource=?",
                                 ("workspace",)).fetchone()
            expected_resource_rev = row[0] if row is not None else 0
        try:
            result = self.model.publish_batch(
                self.credential, self.principal, txid, operations,
                expected_revision=expected_resource_rev, op=op)
        except BatchRecordConflict as exc:
            raise ConflictError(exc.kind, exc.rid, exc.expected, exc.actual) from exc
        except BatchTxidConflict as exc:
            raise TxidReuseError(str(exc)) from exc
        except BatchLeaseFenced as exc:
            raise LockLostError(str(exc)) from exc
        except BatchResourceConflict as exc:
            raise ConflictError("workspace", "workspace",
                                expected_resource_rev, self.model.snapshot("workspace")[2]) from exc
        return result["sequence"]

    def transaction_outcome(self, txid: str) -> dict:
        result = self.model.outcome(txid, self.principal)
        if result["state"] == "not-found":
            return {"state": "not-found"}
        return {"state": result["state"], "seq": result["sequence"]}

    def head_seq(self) -> int:
        return self.model.snapshot_batch()["high_watermark"]

    def get(self, kind: str, rid: str) -> dict | None:
        row = self.model.snapshot_batch()["records"].get((kind, rid))
        if row is None or row["data"] is None:
            return None
        return {**row["data"], "_rev": row["revision"]}

    def list(self, kind: str) -> dict[str, dict]:
        records = self.model.snapshot_batch()["records"]
        return {rid: {**record["data"], "_rev": record["revision"]}
                for (k, rid), record in records.items()
                if k == kind and record["data"] is not None}

    def entries_after(self, seq: int):
        with self.model.core.authority._transaction() as db:
            self.model._verify(db)
            rows = db.execute("""SELECT sequence,txid,payload FROM commits
                                 WHERE sequence>? ORDER BY sequence""", (seq,)).fetchall()
        for number, txid, payload in rows:
            command = json.loads(payload)
            records = []
            for item in command["operations"]:
                if item["type"] == "check":
                    continue
                record = {"kind": item["kind"], "id": item["id"]}
                if item["type"] == "delete":
                    record["delete"] = True
                else:
                    record["data"] = item["data"]
                if item["expect_rev"] is not None:
                    record["expect_rev"] = item["expect_rev"]
                records.append(record)
            yield {"seq": number, "txid": txid, "op": command["op"],
                   "records": records, "blobs": []}


@pytest.fixture
def authority(tmp_path):
    return ResearchCommitAuthorityAdapter(tmp_path)


@pytest.fixture
def revoke():
    def _revoke(authority):
        authority.model.override("workspace", authority.credential["generation"],
                                 "ADR-017 conformance revocation", authority.admin)
    return _revoke


class TestWitnessedBatchAdapter(CommitAuthorityContract):
    """EXACT source contract is inherited, not copied or weakened."""
    pass


class TestFullPreconditionIdentityAdapter(PreconditionIdentityContract):
    """Unmodified new ADR-017 upstream precondition identity conformance."""
    pass


class TestVersionedFullRequestAdapter(RequestIdentityV2Contract):
    """Unchanged Claude ADR-021 full-request identity contract."""
    pass

def test_reuse_after_lost_ack_and_restart(tmp_path):
    first = ResearchCommitAuthorityAdapter(tmp_path)
    ops = [RecordPut("project", A, {"v": 1}, expect_rev=0)]
    seq = first.commit("cad.checkin", ops, txid="e" * 32)
    fresh = ResearchCommitAuthorityAdapter.__new__(ResearchCommitAuthorityAdapter)
    fresh.idp = first.idp
    fresh.principal = first.principal
    fresh.admin = first.admin
    fresh.model = BatchAuthorityResearch(tmp_path / "authority.db",
                                          tmp_path / "epoch", tmp_path / "witness",
                                          tmp_path / "objects", first.idp)
    fresh.credential = first.credential
    assert fresh.commit("cad.checkin", ops, txid="e" * 32) == seq
    assert fresh.head_seq() == seq
    assert fresh.transaction_outcome("e" * 32) == {"state": "committed", "seq": seq}


def test_admission_after_fencing_fails_closed(authority, revoke):
    revoke(authority)
    with pytest.raises(LockLostError):
        authority.commit("cad.checkin",
                         [RecordPut("project", A, {"v": "revoked"})],
                         txid="f" * 32)
    assert authority.transaction_outcome("f" * 32) == {"state": "not-found"}


def test_altered_recordcheck_is_txid_reuse_in_research_adapter(authority):
    """Extra oracle missing from upstream contract: check inputs affect request identity."""
    rid = "prj_" + "d" * 32
    ops = [RecordCheck("settings", "workspace", 0),
           RecordPut("project", rid, {"name": "A"}, expect_rev=0)]
    # Our research adapter requires settings to be absent, which it is initially.
    first = authority.commit("create", ops, txid="c" * 32)
    with pytest.raises(TxidReuseError):
        authority.commit("create", [
            RecordCheck("settings", "workspace", 999),
            RecordPut("project", rid, {"name": "A"}, expect_rev=0)],
            txid="c" * 32)
    assert authority.transaction_outcome("c" * 32) == {"state": "committed", "seq": first}


def test_reordered_checks_reuse_is_rejected_by_local_model(authority):
    rid = "prj_" + "7" * 32
    checks = [RecordCheck("project", "prj_" + "8" * 32, 0),
              RecordCheck("project", "prj_" + "9" * 32, 0)]
    write = RecordPut("project", rid, {"name": "A"}, expect_rev=0)
    seq = authority.commit("two-checks", checks + [write], txid="7" * 32)
    with pytest.raises(TxidReuseError):
        authority.commit("two-checks", list(reversed(checks)) + [write],
                         txid="7" * 32)
    assert authority.transaction_outcome("7" * 32) == {"state": "committed", "seq": seq}


def test_dropped_check_reuse_is_rejected_by_local_model(authority):
    rid = "prj_" + "6" * 32
    check = RecordCheck("project", "prj_" + "5" * 32, 0)
    write = RecordPut("project", rid, {"name": "A"}, expect_rev=0)
    seq = authority.commit("with-check", [check, write], txid="8" * 32)
    with pytest.raises(TxidReuseError):
        authority.commit("with-check", [write], txid="8" * 32)
    assert authority.transaction_outcome("8" * 32) == {"state": "committed", "seq": seq}


def test_identical_txid_replay_even_after_lease_revoked_is_outcome_not_new_write(authority, revoke):
    ops = [RecordPut("project", "prj_" + "4" * 32, {"name": "first"}, expect_rev=0)]
    seq = authority.commit("first", ops, txid="9" * 32)
    revoke(authority)
    assert authority.transaction_outcome("9" * 32) == {"state": "committed", "seq": seq}
    assert authority.commit("first", ops, txid="9" * 32) == seq
    assert authority.head_seq() == seq
    with pytest.raises(LockLostError):
        authority.commit("second", [RecordPut("project", "prj_" + "3" * 32,
                                             {"name": "new"})], txid="a" * 32)
    assert authority.get("project", "prj_" + "3" * 32) is None


def test_reordered_writes_change_canonical_request_identity(authority):
    a = RecordPut("project", "prj_" + "1" * 32, {"k": 1}, expect_rev=0)
    b = RecordPut("project", "prj_" + "2" * 32, {"k": 2}, expect_rev=0)
    seq = authority.commit("two", [a, b], txid="d" * 32)
    with pytest.raises(TxidReuseError):
        authority.commit("two", [b, a], txid="d" * 32)
    assert authority.head_seq() == seq
