# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent adversarial acceptance for Claude D's Engineering Change Control.

Pin product source separately in CI. A red strict test demonstrates a
contract failure, not green acceptance. All test stores/workspaces disposable;
never touch production authority internals or another agent's branch.
"""
from __future__ import annotations

import pytest

from revisionworx import changes
from revisionworx.changes import exchange, fixtures
from revisionworx.changes.catalog import MemoryCatalog
from revisionworx.changes.model import (
    ECOStatus, ECRStatus, DependencyError, SignoffError, StaleBaseline, ValidationError,
    SignoffStage,
)
from revisionworx.changes.store import MemoryStore

DOC = "doc_" + "a" * 32
A = "rev_" + "1" * 32
B = "rev_" + "2" * 32
PART = {"item_type": "part", "label": "P-220", "action": "review"}
TXID = "12" * 16


def service(with_document=False):
    store = MemoryStore()
    cat = MemoryCatalog() if with_document else None
    if with_document:
        cat.add_document(DOC, "DWG-100 production drawing")
        cat.add_revision(DOC, A, "A")
    svc, actors = fixtures.deterministic(store, cat)
    return svc, actors, cat, store


def approve_request(svc, actors, *, title="Change", affected=None):
    ecr = svc.create_request(title, "Functional requirement", affected=affected or [PART])
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    with actors.as_(fixtures.BOB):
        approved = svc.approve(ecr.id)
    assert approved.status is ECRStatus.APPROVED
    assert svc.verify_history(ecr.id) == []
    return approved


def test_control_approved_ecr_is_blocked_after_document_revision_changes():
    svc, actors, cat, _ = service(with_document=True)
    approved = approve_request(svc, actors, affected=[
        {"item_type": "document", "item_id": DOC, "action": "review"}])
    cat.add_revision(DOC, B, "B")
    with pytest.raises(StaleBaseline):
        svc.create_order([approved.id])
    assert svc.request(approved.id).status is ECRStatus.APPROVED


def test_stale_approved_ecr_can_rework_and_rebaseline_without_reusing_old_approval():
    """Review P2: an Approved request becomes stale; it must have a safe rework path."""
    svc, actors, cat, _ = service(with_document=True)
    ecr = approve_request(svc, actors, affected=[
        {"item_type": "document", "item_id": DOC, "action": "review"}])
    assert svc.gate(svc.request(ecr.id), SignoffStage.ECR_REVIEW)["approvals"] == 1
    cat.add_revision(DOC, B, "B")
    with pytest.raises(StaleBaseline):
        svc.create_order([ecr.id])
    reworked = svc.return_for_rework(ecr.id, "B is latest; previous review no longer applies")
    assert reworked.status is ECRStatus.DRAFT
    rebased = svc.rebaseline(ecr.id)
    assert rebased.affected[0].baseline_rev_id == B
    assert svc.gate(svc.request(ecr.id), SignoffStage.ECR_REVIEW)["approvals"] == 0
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    assert svc.gate(svc.request(ecr.id), SignoffStage.ECR_REVIEW)["approvals"] == 0
    with pytest.raises(Exception):
        svc.create_order([ecr.id])
    with actors.as_(fixtures.CAROL):
        svc.approve(ecr.id)
    order = svc.create_order([ecr.id])
    assert order.status is ECOStatus.PLANNING
    assert svc.verify_history(ecr.id) == []


def test_control_signed_eco_requires_release_approval_before_start():
    svc, actors, _, _ = service()
    svc.save_policy({"eco_release_approvals_required": 1})
    ecr = approve_request(svc, actors)
    eco = svc.create_order([ecr.id])
    with pytest.raises(SignoffError, match="0 of 1"):
        svc.start_work(eco.id)
    with actors.as_(fixtures.BOB):
        svc.sign_off_order(eco.id, "approve")
    assert svc.gate(svc.order(eco.id), SignoffStage.ECO_RELEASE)["approvals"] == 1
    assert svc.start_work(eco.id).status is ECOStatus.IN_PROGRESS


def test_order_dependency_edit_invalidates_signed_release_content_and_restart(tmp_path):
    """Review P2: dropping a required prerequisite must invalidate release signoff."""
    source = tmp_path / "change-order-dependency"
    from revisionworx.workspace import Workspace
    with Workspace.create(source, "QA approved dependency") as ws:
        svc = changes.service_for(ws)
        svc.save_policy({"eco_release_approvals_required": 1})
        prerequisite = svc.create_request("First prerequisite", "Needed beforehand", affected=[PART])
        approved = svc.create_request("Engineering change", "Improve component", affected=[PART])
        svc.submit(approved.id)
        svc.begin_review(approved.id)
        svc.approve(approved.id)
        eco = svc.create_order([approved.id])
        svc.set_dependencies(eco.id, [prerequisite.id])
        svc.sign_off_order(eco.id, "approve")
        before = svc.order(eco.id)
        assert svc.gate(before, SignoffStage.ECO_RELEASE)["approvals"] == 1
        old_digest = before.content_digest()
        svc.set_dependencies(eco.id, [])
        after = svc.order(eco.id)
        assert after.dependencies == ()
        assert after.content_digest() != old_digest, (
            "UNSAFE: dependency removal does not change ECO reviewed-content digest")
        assert svc.gate(after, SignoffStage.ECO_RELEASE)["approvals"] == 0
        with pytest.raises(SignoffError):
            svc.start_work(eco.id)
        assert svc.verify_history(eco.id) == []
        sha = after.event_head
    with Workspace.open(source, writable=True) as reopened:
        check = changes.service_for(reopened)
        restored = check.order(eco.id)
        assert restored.event_head == sha
        assert restored.content_digest() != old_digest
        assert check.gate(restored, SignoffStage.ECO_RELEASE)["approvals"] == 0
        with pytest.raises(SignoffError):
            check.start_work(eco.id)
        assert check.verify_history(eco.id) == []


def test_control_multi_request_merge_preserves_required_action_and_rejects_conflict():
    svc, actors, _, _ = service(with_document=True)
    ref = approve_request(svc, actors, title="Review only", affected=[
        {"item_type": "document", "item_id": DOC, "action": "reference"}])
    rev = approve_request(svc, actors, title="Actual revision", affected=[
        {"item_type": "document", "item_id": DOC, "action": "revise"}])
    merged = svc.create_order([ref.id, rev.id])
    assert merged.affected[0].action.value == "revise"
    with pytest.raises(DependencyError, match="implementation"):
        svc.mark_implemented(merged.id)
    obsolete = approve_request(svc, actors, title="Retire drawing", affected=[
        {"item_type": "document", "item_id": DOC, "action": "obsolete"}])
    with pytest.raises(ValidationError, match="conflicting actions"):
        svc.create_order([rev.id, obsolete.id])


def test_control_identical_csv_import_retry_is_idempotent():
    svc, _, _, store = service()
    text = "title,reason,number\nFirst,Needed,CR-220\nSecond,Required,\n"
    first = exchange.import_requests_csv(svc, text, txid=TXID)
    seq = store.seq
    again = exchange.import_requests_csv(svc, text, txid=TXID)
    assert [c.id for c in first] == [c.id for c in again]
    assert store.seq == seq
    assert len(svc.scan()[0]) == 2


def test_changed_csv_payload_reusing_txid_must_not_be_reported_as_identical_retry():
    """Independent txid identity: same identifier must not silently accept a different import."""
    svc, _, _, store = service()
    text = "title,reason,number\nFirst,Needed,CR-220\nSecond,Required,\n"
    first = exchange.import_requests_csv(svc, text, txid=TXID)
    seq = store.seq
    altered = "title,reason,number\nA different title,Needed,CR-220\nSecond,Required,\n"
    with pytest.raises(changes.ReplayRefused):
        exchange.import_requests_csv(svc, altered, txid=TXID)
    assert store.seq == seq
    assert len(svc.scan()[0]) == 2
    assert svc.request(first[0].id).title == "First"
