# SPDX-License-Identifier: GPL-3.0-or-later
"""The file journal (standalone mode) against the CommitAuthority contract."""
import pytest

from revisionworx.workspace import RecordPut

from contract_commit_authority import CommitAuthorityContract, PreconditionIdentityContract, RequestIdentityV2Contract
from revisionworx.workspace import Workspace


@pytest.fixture
def authority(share):
    ws = Workspace.create(share, "Contract")
    yield ws
    ws._lock_token = ws._lock_token if ws.writable else None
    ws.close()


@pytest.fixture
def revoke(share):
    def _revoke(ws):
        Workspace.break_writer_lock(share, ws._lock_token, reason="contract test")
    return _revoke


class TestFileJournal(CommitAuthorityContract, PreconditionIdentityContract, RequestIdentityV2Contract):
    pass


def _publish_legacy_entry(ws, txid):
    """An entry exactly as written before ADR-021 (no request identity), properly digested."""
    from revisionworx import atomicio
    from revisionworx.workspace import entry_digest
    seq = ws.head_seq() + 1
    entry = {"seq": seq, "txid": txid, "time": "2026-11-01T00:00:00+00:00", "actor": {"user": "legacy"},
             "op": "legacy", "records": [{"kind": "project", "id": "prj_" + "c" * 32, "data": {"v": 1}}],
             "blobs": [], "checks": [], "writer_token": ws._lock_token}
    entry["digest"] = entry_digest(entry)
    atomicio.publish_exclusive(ws._journal_dir() / f"{seq:020d}.json", atomicio.dumps_json(entry))
    ws.recover()
    return seq


def test_legacy_txid_retry_is_identity_unverifiable_not_guessed(authority):
    from revisionworx.authority import classify
    from revisionworx.workspace import IdentityUnverifiable
    seq = _publish_legacy_entry(authority, "c" * 32)
    head = authority.head_seq()
    with pytest.raises(IdentityUnverifiable) as info:  # even an apparently identical retry
        authority.commit("legacy", [RecordPut("project", "prj_" + "c" * 32, {"v": 1})], txid="c" * 32)
    assert classify(info.value) == "identity-unverifiable" and authority.head_seq() == head
    assert authority.transaction_outcome("c" * 32) == {"state": "committed", "seq": seq}  # lost-ACK path
    assert authority.verify()["problems"] == []  # history untouched, nothing rewritten


def test_new_entries_record_versioned_identity(authority):
    from revisionworx.workspace import REQUEST_IDENTITY_VERSION, request_identity
    ops = [RecordPut("project", "prj_" + "d" * 32, {"v": 1})]
    seq = authority.commit("t", ops, txid="e" * 32)
    req = authority.read_entry(seq)["request"]
    assert req == {"version": REQUEST_IDENTITY_VERSION, "digest": request_identity("t", ops, None)}
