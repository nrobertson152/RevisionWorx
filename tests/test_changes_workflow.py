# SPDX-License-Identifier: GPL-3.0-or-later
"""Change control: every workflow step from every state - allowed steps succeed, all others are refused
with TransitionError and write nothing (issue #19)."""
import pytest

from revisionworx.changes import fixtures
from revisionworx.changes.catalog import MemoryCatalog
from revisionworx.changes.model import ECOStatus, ECRStatus, TransitionError
from revisionworx.changes.store import MemoryStore
from revisionworx.changes.workflow import (ECO_STEPS, ECR_STEPS, ECR_SYSTEM_STEPS, allowed_ecr_steps,
                                           allowed_eco_steps)

DOC = "doc_" + "a" * 32
REV = "rev_" + "a" * 32


def make():
    cat = MemoryCatalog()
    cat.add_document(DOC, "DWG-1 Bracket")
    cat.add_revision(DOC, REV, "A")
    svc, actors = fixtures.deterministic(MemoryStore(), cat)
    return svc, actors


def ecr_in(state: ECRStatus):
    svc, actors = make()
    # review-only items need no implementation, so orders can complete without documents changing
    ecr = svc.create_request("t", "r", affected=[{"item_type": "document", "item_id": DOC, "action": "review"}])
    path = {
        ECRStatus.DRAFT: [],
        ECRStatus.SUBMITTED: ["submit"],
        ECRStatus.IN_REVIEW: ["submit", "begin_review"],
        ECRStatus.APPROVED: ["submit", "begin_review", "approve"],
        ECRStatus.CONVERTED: ["submit", "begin_review", "approve", "order"],
        ECRStatus.CLOSED: ["submit", "begin_review", "approve", "order", "finish"],
        ECRStatus.REJECTED: ["submit", "reject"],
        ECRStatus.CANCELLED: ["cancel"],
    }[state]
    for step in path:
        if step == "order":
            svc.create_order([ecr.id])
        elif step == "finish":
            eco = svc.get(svc.request(ecr.id).eco_id)
            svc.start_work(eco.id)
            svc.mark_implemented(eco.id)
            svc.close_order(eco.id)
        else:
            call_ecr(svc, step, ecr.id)
    assert svc.request(ecr.id).status is state
    return svc, ecr.id


def call_ecr(svc, step, ecr_id):
    return {"submit": lambda: svc.submit(ecr_id),
            "begin_review": lambda: svc.begin_review(ecr_id),
            "return_for_rework": lambda: svc.return_for_rework(ecr_id, "needs work"),
            "approve": lambda: svc.approve(ecr_id),
            "reject": lambda: svc.reject(ecr_id, "no"),
            "cancel": lambda: svc.cancel_request(ecr_id, "not needed")}[step]()


USER_ECR_STEPS = [s for s in ECR_STEPS if s not in ECR_SYSTEM_STEPS]


@pytest.mark.parametrize("state", list(ECRStatus))
@pytest.mark.parametrize("step", USER_ECR_STEPS)
def test_every_ecr_step_from_every_state(state, step):
    svc, ecr_id = ecr_in(state)
    allowed, target = ECR_STEPS[step]
    if state is ECRStatus.CONVERTED and step == "cancel":
        allowed = frozenset()           # must cancel the change order instead
    before = svc.store.seq
    if state in allowed:
        assert call_ecr(svc, step, ecr_id).status is target
        assert svc.store.seq == before + 1                     # exactly one commit
    else:
        with pytest.raises(TransitionError):
            call_ecr(svc, step, ecr_id)
        assert svc.store.seq == before                         # nothing written
        assert svc.request(ecr_id).status is state


def eco_in(state: ECOStatus):
    svc, ecr_id = ecr_in(ECRStatus.APPROVED)
    eco = svc.create_order([ecr_id])
    path = {ECOStatus.PLANNING: [], ECOStatus.IN_PROGRESS: ["start_work"],
            ECOStatus.IMPLEMENTED: ["start_work", "mark_implemented"],
            ECOStatus.CLOSED: ["start_work", "mark_implemented", "close"],
            ECOStatus.CANCELLED: ["cancel"]}[state]
    for step in path:
        call_eco(svc, step, eco.id)
    assert svc.order(eco.id).status is state
    return svc, eco.id


def call_eco(svc, step, eco_id):
    return {"start_work": lambda: svc.start_work(eco_id),
            "mark_implemented": lambda: svc.mark_implemented(eco_id),
            "reopen": lambda: svc.reopen(eco_id, "verification failed"),
            "close": lambda: svc.close_order(eco_id),
            "cancel": lambda: svc.cancel_order(eco_id, "superseded")}[step]()


@pytest.mark.parametrize("state", list(ECOStatus))
@pytest.mark.parametrize("step", list(ECO_STEPS))
def test_every_eco_step_from_every_state(state, step):
    svc, eco_id = eco_in(state)
    allowed, target = ECO_STEPS[step]
    before = svc.store.seq
    if state in allowed:
        assert call_eco(svc, step, eco_id).status is target
        assert svc.store.seq == before + 1
    else:
        with pytest.raises(TransitionError):
            call_eco(svc, step, eco_id)
        assert svc.store.seq == before
        assert svc.order(eco_id).status is state


def test_terminal_states_allow_nothing():
    for s in (ECRStatus.CLOSED, ECRStatus.REJECTED, ECRStatus.CANCELLED):
        assert allowed_ecr_steps(s) == []
    for s in (ECOStatus.CLOSED, ECOStatus.CANCELLED):
        assert allowed_eco_steps(s) == []


def test_refusal_names_the_state_and_the_allowed_steps():
    svc, ecr_id = ecr_in(ECRStatus.DRAFT)
    with pytest.raises(TransitionError, match=r"ECR-0001 is Draft; allowed now: submit, cancel"):
        svc.approve(ecr_id)


def test_closing_an_order_closes_its_requests_and_cancelling_returns_them():
    svc, eco_id = eco_in(ECOStatus.CLOSED)
    eco = svc.order(eco_id)
    assert all(svc.request(r).status is ECRStatus.CLOSED for r in eco.request_ids)
    svc2, eco2 = eco_in(ECOStatus.CANCELLED)
    for r in svc2.order(eco2).request_ids:
        ecr = svc2.request(r)
        assert ecr.status is ECRStatus.APPROVED and ecr.eco_id is None
        assert [e["event"] for e in svc2.history(r)][-2:] == ["converted", "order-cancelled"]
    # and the request can go onto a new order
    again = svc2.create_order(list(svc2.order(eco2).request_ids))
    assert again.number == "ECO-0002"
