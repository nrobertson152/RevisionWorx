# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent Phase 2 recovery gates, deliberately separate from production tests.

No production code is modified. This module skips on the Phase 1 QA base.
The manual orphan-clear test is a strict *acceptance failure* on the current
Phase 2 implementation, not a successful recovery certification.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import time
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip(
        "Phase 2 document/content subsystem is absent on Phase 1; execute against pinned Phase 2",
        allow_module_level=True,
    )

from revisionworx import atomicio, documents as D
from revisionworx.content import ContentStore, ContentTooLarge
from .test_phase2_integrity import managed_document  # shared synthetic CAD fixture
from revisionworx.workspace import (
    CommittedNotApplied, RecordPut, Workspace, WorkspaceCorruptError,
    WorkspaceError,
)


def _journal_entries(ws):
    return [ws.read_entry(seq) for seq in ws.journal_seqs()]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Issue #10: manual orphan-clear moves evidence before its administrative journal audit",
)
def test_manual_orphan_clear_crash_must_not_silently_authorize_a_new_writer(tmp_path, monkeypatch):
    """P1 failpoint: crash after clear's evidence rename, before its audit.

    A pre-existing corrupt release file blocks all new writers. An attempted
    administrative clear is interrupted before the audit can be durably written.
    Without that audit, a successor must still fail closed; simply moving the
    only blocking evidence and then dying is not an audited recovery.
    """
    if not hasattr(Workspace, "clear_orphaned_release"):
        pytest.skip("Phase 2 ADR-019 manual orphan clearance unavailable")
    root = tmp_path / "workspace"
    with Workspace.create(root, "QA orphan clear interruption"):
        pass
    meta = root / ".revisionworx"
    locks = meta / "locks"
    orphan_name = ".releasing-" + "a" * 16
    orphan = locks / orphan_name
    evidence = b"{unreadable-owner-token"
    orphan.write_bytes(evidence)
    with pytest.raises(WorkspaceCorruptError, match="orphaned|unreadable"):
        Workspace.open(root, writable=True)
    assert Workspace.read_writer_lock(root) is None
    with Workspace.open(root) as reader:
        pre_head = reader.head_seq()
        assert not any(e.get("op") == "workspace.orphan.clear" for e in _journal_entries(reader))

    def interrupted_admin_audit(*args, **kwargs):
        raise SystemExit("QA process death before administrative audit")

    with monkeypatch.context() as m:
        m.setattr(Workspace, "_publish_admin_entry", interrupted_admin_audit)
        with pytest.raises(SystemExit, match="QA process death"):
            Workspace.clear_orphaned_release(root, orphan_name, "operator verified stop")

    with Workspace.open(root) as reader:
        audit = [e for e in _journal_entries(reader) if e.get("op") == "workspace.orphan.clear"]
        assert reader.head_seq() == pre_head, "injected interruption must precede audit publication"

    evidence_files = list(locks.glob("writer.lock.orphaned-*"))
    if evidence_files:
        assert any(p.read_bytes() == evidence for p in evidence_files)
    assert (orphan.exists() or audit), (
        "administrative clear removed its only blocking orphan evidence "
        "without publishing the required durable audit"
    )
    # Even if recovery stores its evidence differently, no unaudited writer can
    # commit while clearance has not been made authoritative.
    if not audit:
        try:
            with Workspace.open(root, writable=True) as successor:
                successor.commit("qa.unaudited.orphan.clear", [
                    RecordPut("root", "root_" + "f" * 32, {"name": "unapproved"})
                ])
        except (WorkspaceError, OSError):
            pass
        else:
            raise AssertionError("a new writer committed after an unaudited orphan clear")


def test_transaction_outcome_differentiates_pre_and_post_publication_faults(ws, monkeypatch):
    """Prepublication failures are not-found; postpublication failures are durable."""
    rid = "root_" + "a" * 32
    tx_before, tx_after = "1" * 32, "2" * 32
    initial_head = ws.head_seq()
    real_publish = atomicio.publish_exclusive

    def fail_before_publish(path, data):
        if Path(path).parent.name == "journal":
            raise OSError("QA injected failure before durable publication")
        return real_publish(path, data)

    with monkeypatch.context() as m:
        m.setattr(atomicio, "publish_exclusive", fail_before_publish)
        with pytest.raises(OSError, match="before durable publication"):
            ws.commit("qa.outcome.before", [
                RecordPut("root", rid, {"id": rid, "name": "never saved"}, expect_rev=0)
            ], txid=tx_before)
    assert ws.head_seq() == initial_head
    assert ws.transaction_outcome(tx_before) == {"state": "not-found"}
    assert ws.get("root", rid) is None

    with monkeypatch.context() as m:
        def failed_materialization(entry):
            raise OSError("QA injected postpublication materialization loss")
        m.setattr(ws, "_apply", failed_materialization)
        with pytest.raises(CommittedNotApplied) as error:
            ws.commit("qa.outcome.after", [
                RecordPut("root", rid, {"id": rid, "name": "durably saved"}, expect_rev=0)
            ], txid=tx_after)
    seq = error.value.seq
    assert ws.head_seq() == initial_head + 1
    assert ws.transaction_outcome(tx_after) == {"state": "committed", "seq": seq}
    ws.recover()
    assert ws.get("root", rid)["name"] == "durably saved"
    assert ws.commit("qa.outcome.after", [
        RecordPut("root", rid, {"id": rid, "name": "durably saved"}, expect_rev=0)
    ], txid=tx_after) == seq
    assert ws.head_seq() == seq
    assert ws.verify()["problems"] == []


def test_content_gc_audit_is_published_before_delete_and_precommit_failure_preserves_data(
        ws, tmp_path, monkeypatch):
    """Audit-first GC: unreferenced old blob survives if journal publish fails."""
    store = ContentStore(ws)
    orphan_source = tmp_path / "gc-orphan.bin"
    orphan_source.write_bytes(b"orphan object created before an aborted check-in")
    orphan = store.store_file(orphan_source)
    old_obj = store.path_for(orphan.sha256)
    os.utime(old_obj, (time.time() - 7200, time.time() - 7200))

    young_source = tmp_path / "gc-young.bin"
    young_source.write_bytes(b"recent orphan is still protected")
    young = store.store_file(young_source)
    young_obj = store.path_for(young.sha256)

    before = ws.head_seq()
    dry = D.gc_content(ws)
    assert [x["sha256"] for x in dry["orphans"]] == [orphan.sha256]
    assert dry["kept_young"] >= 1
    assert old_obj.is_file() and young_obj.is_file()
    assert ws.head_seq() == before

    real_publish = atomicio.publish_exclusive
    def no_audit(path, data):
        if Path(path).parent.name == "journal":
            raise OSError("QA GC journal fault")
        return real_publish(path, data)
    with monkeypatch.context() as m:
        m.setattr(atomicio, "publish_exclusive", no_audit)
        with pytest.raises(OSError, match="QA GC journal fault"):
            D.gc_content(ws, apply=True)
    assert old_obj.is_file()
    assert young_obj.is_file()
    assert ws.head_seq() == before

    original_unlink = Path.unlink
    observed = []
    def deletion_requires_durable_audit(path, *args, **kwargs):
        if path == old_obj:
            audits = [e for e in _journal_entries(ws) if e["op"] == "content.gc"]
            assert len(audits) == 1
            assert audits[0]["seq"] <= ws.head_seq()
            assert any(item["sha256"] == orphan.sha256 for
                       record in audits[0]["records"] for item in record["data"]["removed"])
            observed.append(audits[0]["seq"])
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", deletion_requires_durable_audit)
        done = D.gc_content(ws, apply=True)
    assert observed and done["removed"]
    assert not old_obj.exists() and young_obj.exists()
    assert ws.head_seq() == before + 1
    assert ws.verify()["problems"] == []


def test_revision_chain_cycle_is_rejected_before_history_or_new_revision(ws, managed_document):
    """Semantically corrupt revision links must never be shown as valid history."""
    folder, source, assembly, before, doc, rev_a = managed_document
    current = ws.get("revision", rev_a["id"])
    assert current["previous_rev_id"] is None
    ws.commit("qa.fault.inject.cyclic.revision.link", [
        RecordPut("revision", rev_a["id"], {
            **current, "previous_rev_id": rev_a["id"]
        }, expect_rev=current["_rev"])
    ])
    with pytest.raises(WorkspaceError, match="loops"):
        D.history(ws, doc["id"])
    co = D.checkout(ws, doc["id"])
    source.write_bytes(b"second revision which must not be accepted after corrupt history")
    baseline = ws.head_seq()
    with pytest.raises(WorkspaceError, match="loops"):
        D.checkin(ws, doc["id"], co["token"], "must reject cycle")
    assert ws.head_seq() == baseline
    assert ws.get("document", doc["id"])["rev_count"] == 1
    assert assembly.read_bytes() == b"REF pump-bracket.ipt\n"


def test_verified_revision_read_rejects_boundary_overlimit_and_same_length_corruption(
        ws, managed_document):
    """ADR-018 returns verified immutable bytes, never altered working bytes."""
    folder, source, assembly, before, doc, rev_a = managed_document
    content = ContentStore(ws)
    stored = content.path_for(rev_a["sha256"])
    original = stored.read_bytes()
    size = len(original)
    source.write_bytes(b"mutable working file is not an immutable revision")
    assert D.read_revision_bytes(ws, rev_a["id"], max_bytes=size)[1] == original
    with pytest.raises(ContentTooLarge):
        D.read_revision_bytes(ws, rev_a["id"], max_bytes=size - 1)
    with pytest.raises(ValueError, match="positive"):
        D.read_revision_bytes(ws, rev_a["id"], max_bytes=0)
    assert content.read_verified(rev_a["sha256"], max_bytes=size, expected_size=size) == original
    with pytest.raises(WorkspaceCorruptError, match="expected"):
        content.read_verified(rev_a["sha256"], max_bytes=size + 1, expected_size=size + 1)
    modified = bytes([original[0] ^ 1]) + original[1:]
    stored.write_bytes(modified)
    with pytest.raises(WorkspaceCorruptError, match="SHA-256"):
        D.read_revision_bytes(ws, rev_a["id"], max_bytes=size)
    assert source.read_bytes() == b"mutable working file is not an immutable revision"
    assert ws.get("revision", rev_a["id"])["sha256"] == hashlib.sha256(original).hexdigest()
