# SPDX-License-Identifier: GPL-3.0-or-later
"""Change control service on the in-memory store: approvals, numbering, duplicates, dependencies,
implementations, optimistic concurrency, idempotent retries and the audit chain (issue #19)."""
import pytest

from revisionworx.changes import fixtures
from revisionworx.changes.audit import event_id
from revisionworx.changes.catalog import MemoryCatalog
from revisionworx.changes.model import (DependencyError, DuplicateNumber, ECOStatus, ECRStatus,
                                        ImplementationStatus, MalformedRecord, NotFound, ReplayRefused,
                                        SignoffError, StaleBaseline, TransitionError, ValidationError)
from revisionworx.changes.store import Commit, MemoryStore, Put
from revisionworx.workspace import ConflictError

DOC, DOC2 = "doc_" + "a" * 32, "doc_" + "b" * 32
R = {n: "rev_" + c * 32 for n, c in zip("ABCDE", "12345")}
TX = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def env():
    cat = MemoryCatalog()
    cat.add_document(DOC, "DWG-1 Bracket")
    cat.add_revision(DOC, R["A"], "A")
    cat.add_document(DOC2, "DWG-2 Frame")
    svc, actors = fixtures.deterministic(MemoryStore(), cat)
    return svc, actors, cat


def doc_item(doc=DOC, **kw):
    return {"item_type": "document", "item_id": doc, **kw}


def reviewed(svc, actors, **kw):
    ecr = svc.create_request("Thicker bracket", "Fatigue cracks", affected=[doc_item()], **kw)
    svc.submit(ecr.id)
    with actors.as_(fixtures.BOB):
        svc.begin_review(ecr.id)
    return ecr.id


# ---------------------------------------------------------------- creation and editing
def test_create_fills_document_baseline_from_latest_revision(env):
    svc, _, _ = env
    ecr = svc.create_request("t", "r", affected=[doc_item()])
    assert (ecr.affected[0].baseline_rev_id, ecr.affected[0].baseline_label) == (R["A"], "A")
    assert ecr.affected[0].label == "DWG-1 Bracket"
    assert ecr.status is ECRStatus.DRAFT and ecr.number == "ECR-0001" and ecr.rev == 1


def test_reason_is_required_and_bad_input_writes_nothing(env):
    svc, _, _ = env
    for kwargs, match in [(dict(title="t", reason=" "), "reason is required"),
                          (dict(title="", reason="r"), "title is required"),
                          (dict(title="t", reason="r", priority="asap"), "priority"),
                          (dict(title="t", reason="r", category="nope"), "unknown category"),
                          (dict(title="t", reason="r", affected=[doc_item("doc_" + "f" * 32)]), "does not exist"),
                          (dict(title="t", reason="r", affected=[doc_item(baseline_rev_id="rev_" + "9" * 32)]),
                           "is not a revision of"),
                          (dict(title="t", reason="r", affected=[doc_item(), doc_item()]), "listed twice")]:
        with pytest.raises(ValidationError, match=match):
            svc.create_request(**kwargs)
    assert svc.store.seq == 0 and svc.store.records == {}


def test_edit_records_old_and_new_values_and_only_in_draft(env):
    svc, actors, _ = env
    ecr = svc.create_request("Old title", "r")
    ecr = svc.edit_request(ecr.id, title="New title", priority="high", expected_rev=ecr.rev)
    ev = svc.history(ecr.id)[-1]
    assert ev["event"] == "edited"
    assert ev["details"]["changes"] == {"title": ["Old title", "New title"], "priority": ["normal", "high"]}
    with pytest.raises(ValidationError, match="nothing changed"):
        svc.edit_request(ecr.id, title="New title")
    with pytest.raises(ValidationError, match="cannot edit"):
        svc.edit_request(ecr.id, status="approved")
    svc.add_item(ecr.id, doc_item())
    svc.submit(ecr.id)
    with pytest.raises(TransitionError, match="only drafts can be edited"):
        svc.edit_request(ecr.id, title="sneaky")


def test_unicode_round_trips(env):
    svc, _, _ = env
    ecr = svc.create_request("Ändere Halter – Ø 12 mm 🛠", "理由: 疲労亀裂", description="Zeile 1\nZeile 2",
                             affected=[{"item_type": "part", "label": "Teil-Ä1"}])
    again = svc.get(ecr.id)
    assert again.title == "Ändere Halter – Ø 12 mm 🛠" and again.reason == "理由: 疲労亀裂"
    assert svc.verify_history(ecr.id) == []


# ---------------------------------------------------------------- numbers
def test_numbers_are_sequential_manual_numbers_are_skipped_and_duplicates_refused(env):
    svc, _, _ = env
    assert svc.create_request("a", "r").number == "ECR-0001"
    assert svc.create_request("b", "r", number="ECR-0002").number == "ECR-0002"
    assert svc.create_request("c", "r").number == "ECR-0003"    # skips the manual one
    before = svc.store.seq
    for dup in ("ECR-0001", "ecr-0002", "ＥＣＲ-0003", " ECR-0003 "):
        with pytest.raises(DuplicateNumber):
            svc.create_request("dup", "r", number=dup)
    assert svc.store.seq == before
    assert svc.find_by_number("ecr-0002").title == "b"


def test_number_taken_between_check_and_commit_is_refused_atomically(env):
    svc, _, _ = env
    real_get = svc.store.get

    def get_hiding_reservations(kind, rid):
        return None if kind == "number" else real_get(kind, rid)
    svc.create_request("first", "r", number="X-1")
    svc.store.get = get_hiding_reservations           # simulate a stale pre-check
    with pytest.raises(DuplicateNumber, match="taken by another change"):
        svc.create_request("second", "r", number="x-1")
    svc.store.get = real_get
    assert len(svc.store.list("ecr")) == 1


def test_bulk_create_is_all_or_nothing(env):
    svc, _, _ = env
    made = svc.create_requests([{"title": "a", "reason": "r"}, {"title": "b", "reason": "r", "number": "M-1"}])
    assert [c.number for c in made] == ["ECR-0001", "M-1"] and svc.store.seq == 1
    with pytest.raises(ValidationError, match="item 2: reason is required"):
        svc.create_requests([{"title": "c", "reason": "r"}, {"title": "d", "reason": ""}])
    with pytest.raises(DuplicateNumber, match="twice"):
        svc.create_requests([{"title": "c", "reason": "r", "number": "Q"}, {"title": "d", "reason": "r",
                                                                              "number": "q"}])
    assert svc.store.seq == 1 and len(svc.store.list("ecr")) == 2


# ---------------------------------------------------------------- approvals
def test_quorum_counts_distinct_approvers_in_the_current_round(env):
    svc, actors, _ = env
    svc.save_policy({"ecr_approvals_required": 2})
    ecr_id = reviewed(svc, actors)
    with actors.as_(fixtures.BOB):
        assert svc.approve(ecr_id).status is ECRStatus.IN_REVIEW
        with pytest.raises(SignoffError, match="already signed off"):
            svc.approve(ecr_id)
    with actors.as_({"user": "BOB", "host": "other-pc"}):       # same account name, another machine
        with pytest.raises(SignoffError, match="already signed off"):
            svc.approve(ecr_id)
    with actors.as_(fixtures.CAROL):
        ecr = svc.approve(ecr_id, "fine", role="Quality")
    assert ecr.status is ECRStatus.APPROVED
    assert [s.actor["user"] for s in ecr.signoffs] == ["bob", "carol"]
    assert svc.history(ecr_id)[-1]["details"]["approvals"] == 2


def test_rework_starts_a_new_round_so_old_approvals_do_not_count(env):
    svc, actors, _ = env
    svc.save_policy({"ecr_approvals_required": 2})
    ecr_id = reviewed(svc, actors)
    with actors.as_(fixtures.BOB):
        svc.approve(ecr_id)
        svc.return_for_rework(ecr_id, "add the frame drawing")
    svc.edit_request(ecr_id, description="also frame")
    svc.submit(ecr_id)
    with actors.as_(fixtures.CAROL):
        svc.begin_review(ecr_id)
        assert svc.approve(ecr_id).status is ECRStatus.IN_REVIEW      # bob's round-1 approval is stale
    with actors.as_(fixtures.BOB):
        assert svc.approve(ecr_id).status is ECRStatus.APPROVED


def test_distinct_approver_policy(env):
    svc, actors, _ = env
    svc.save_policy({"require_distinct_approver": True})
    ecr_id = reviewed(svc, actors)
    with pytest.raises(SignoffError, match="someone other than the requester"):
        svc.approve(ecr_id)                                   # alice requested it
    with actors.as_(fixtures.BOB):
        assert svc.approve(ecr_id).status is ECRStatus.APPROVED


def test_reject_needs_a_reason_and_is_final(env):
    svc, actors, _ = env
    ecr_id = reviewed(svc, actors)
    with pytest.raises(ValidationError):
        svc.reject(ecr_id, "  ")
    ecr = svc.reject(ecr_id, "duplicate of ECR-7")
    assert ecr.status is ECRStatus.REJECTED and ecr.disposition == "duplicate of ECR-7"
    with pytest.raises(TransitionError):
        svc.submit(ecr_id)


def test_approval_is_blocked_while_a_baseline_is_stale(env):
    svc, actors, cat = env
    ecr_id = reviewed(svc, actors)
    cat.add_revision(DOC, R["B"], "B")                        # someone revised the drawing meanwhile
    before = svc.store.seq
    with pytest.raises(StaleBaseline) as info:
        svc.approve(ecr_id)
    assert info.value.items == [f"document:{DOC}"] and svc.store.seq == before
    svc.return_for_rework(ecr_id, "re-baseline")
    ecr = svc.rebaseline(ecr_id)
    assert ecr.affected[0].baseline_label == "B"
    svc.submit(ecr_id)
    svc.begin_review(ecr_id)
    assert svc.approve(ecr_id).status is ECRStatus.APPROVED


def test_stale_baselines_can_be_allowed_by_policy(env):
    svc, actors, cat = env
    svc.save_policy({"block_stale_baselines": False})
    ecr_id = reviewed(svc, actors)
    cat.add_revision(DOC, R["B"], "B")
    assert svc.approve(ecr_id).status is ECRStatus.APPROVED


def test_submit_requires_affected_items_unless_policy_says_otherwise(env):
    svc, _, _ = env
    ecr = svc.create_request("t", "r")
    with pytest.raises(ValidationError, match="no affected items"):
        svc.submit(ecr.id)
    svc.save_policy({"require_affected_items": False})
    assert svc.submit(ecr.id).status is ECRStatus.SUBMITTED


# ---------------------------------------------------------------- change orders
def approved(svc, actors, **kw):
    ecr_id = reviewed(svc, actors, **kw)
    with actors.as_(fixtures.BOB):
        svc.approve(ecr_id)
    return ecr_id


def test_order_from_several_requests_merges_items_and_links_both_ways(env):
    svc, actors, _ = env
    a = approved(svc, actors)
    b = svc.create_request("Frame", "r", priority="urgent", affected=[doc_item(), doc_item(DOC2, label="DWG-2")])
    svc.submit(b.id)
    svc.begin_review(b.id)
    svc.approve(b.id)
    eco = svc.create_order([a, b.id], actions=["Update drawings"])
    assert eco.request_ids == (a, b.id) and len(eco.affected) == 2 and eco.priority.value == "urgent"
    assert eco.title == "Implements ECR-0001, ECR-0002"
    assert {svc.request(x).eco_id for x in (a, b.id)} == {eco.id}
    with pytest.raises(TransitionError, match="On change order"):
        svc.create_order([a])


def test_order_requires_approved_requests(env):
    svc, actors, _ = env
    draft = svc.create_request("t", "r", affected=[doc_item()])
    with pytest.raises(TransitionError, match="only approved"):
        svc.create_order([draft.id])
    with pytest.raises(ValidationError):
        svc.create_order([])
    with pytest.raises(NotFound):
        svc.create_order(["ecr_" + "9" * 32])
    assert svc.store.seq == 1


def test_implementation_must_descend_from_the_baseline(env):
    svc, actors, cat = env
    eco = svc.create_order([approved(svc, actors)])
    svc.start_work(eco.id)
    key = eco.affected[0].key
    with pytest.raises(ValidationError, match="baseline revision itself"):
        svc.record_implementation(eco.id, key, rev_id=R["A"])
    with pytest.raises(ValidationError, match="not a revision of"):
        svc.record_implementation(eco.id, key, rev_id=R["E"])
    cat.add_revision(DOC, R["B"], "B")
    cat.add_revision(DOC, R["C"], "C")
    with pytest.raises(StaleBaseline, match=r"1 other revision\(s\).*\(B\)"):
        svc.record_implementation(eco.id, key, rev_id=R["C"])
    eco = svc.record_implementation(eco.id, key, rev_id=R["C"], acknowledge_intervening=True, note="B was a typo fix")
    imp = eco.implementation(key)
    assert (imp.status, imp.rev_label, imp.intervening) == (ImplementationStatus.IMPLEMENTED, "C", (R["B"],))
    # a revision on another branch of history (not after the baseline) is refused
    cat.add_document(DOC2, "x")
    cat.revisions[R["D"]] = {"id": R["D"], "document_id": DOC, "label": "D", "number": 9, "state": "wip",
                             "previous_rev_id": None}
    with pytest.raises(StaleBaseline, match="does not descend"):
        svc.record_implementation(eco.id, key, rev_id=R["D"])


def test_declared_items_take_a_label_not_an_id(env):
    svc, actors, _ = env
    ecr = svc.create_request("t", "r", affected=[{"item_type": "part", "label": "P-1", "baseline_label": "B"}])
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    svc.approve(ecr.id)
    eco = svc.create_order([ecr.id])
    svc.start_work(eco.id)
    key = eco.affected[0].key
    with pytest.raises(ValidationError, match="rev_label, not rev_id"):
        svc.record_implementation(eco.id, key, rev_id=R["B"])
    eco = svc.record_implementation(eco.id, key, rev_label="C")
    assert svc.history(eco.id)[-1]["details"]["verified"] is False


def test_mark_implemented_lists_every_blocker(env):
    svc, actors, cat = env
    eco = svc.create_order([approved(svc, actors)], actions=["Update drawing", {"title": "FYI", "required": False}])
    svc.start_work(eco.id)
    with pytest.raises(DependencyError) as info:
        svc.mark_implemented(eco.id)
    assert "required action open: Update drawing" in str(info.value)
    assert "no implementation recorded for DWG-1 Bracket" in str(info.value)
    assert "FYI" not in str(info.value)
    act = eco.actions[0].id
    with pytest.raises(ValidationError):
        svc.waive_action(eco.id, act, "")
    svc.waive_action(eco.id, act, "drawing already correct")
    with pytest.raises(TransitionError, match="already waived"):
        svc.complete_action(eco.id, act)
    svc.mark_not_required(eco.id, eco.affected[0].key, "dimension already 4 mm")
    assert svc.mark_implemented(eco.id).status is ECOStatus.IMPLEMENTED


def test_release_and_closure_signoff_gates(env):
    svc, actors, _ = env
    svc.save_policy({"eco_release_approvals_required": 1, "eco_closure_approvals_required": 1})
    eco = svc.create_order([approved(svc, actors)])
    with pytest.raises(SignoffError, match="0 of 1"):
        svc.start_work(eco.id)
    with actors.as_(fixtures.CAROL):
        svc.sign_off_order(eco.id, "reject", "plan misses the supplier")
    with actors.as_(fixtures.BOB):
        svc.sign_off_order(eco.id, "approve")
    with pytest.raises(SignoffError, match="carol@ws-03 rejected it"):
        svc.start_work(eco.id)
    svc.add_action(eco.id, "Notify supplier")                  # plan content changes -> fresh gate
    with actors.as_(fixtures.BOB):
        svc.sign_off_order(eco.id, "approve")
    svc.start_work(eco.id)
    eco = svc.order(eco.id)
    svc.complete_action(eco.id, eco.actions[0].id)
    svc.mark_not_required(eco.id, eco.affected[0].key, "no change after all")
    svc.mark_implemented(eco.id)
    with pytest.raises(SignoffError, match="0 of 1 required"):
        svc.close_order(eco.id)
    with pytest.raises(ValidationError, match="decision"):
        svc.sign_off_order(eco.id, "maybe")
    with actors.as_(fixtures.CAROL):
        svc.sign_off_order(eco.id, "approve", role="Verification")
    assert svc.close_order(eco.id).status is ECOStatus.CLOSED


def test_reopen_starts_a_new_closure_round(env):
    svc, actors, _ = env
    svc.save_policy({"eco_closure_approvals_required": 1})
    ecr = svc.create_request("t", "r", affected=[doc_item(action="review")])
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    svc.approve(ecr.id)
    eco = svc.create_order([ecr.id])
    svc.start_work(eco.id)
    svc.mark_implemented(eco.id)
    with actors.as_(fixtures.BOB):
        svc.sign_off_order(eco.id, "approve")
    svc.reopen(eco.id, "found a missing note")
    svc.mark_implemented(eco.id)
    with pytest.raises(SignoffError):
        svc.close_order(eco.id)                               # bob's approval was for round 1


# ---------------------------------------------------------------- dependencies
def test_dependencies_validate_targets_and_cycles(env):
    svc, _, _ = env
    a = svc.create_request("a", "r")
    b = svc.create_request("b", "r", dependencies=[a.id])
    c = svc.create_request("c", "r", dependencies=[b.id])
    with pytest.raises(DependencyError, match="cycle"):
        svc.set_dependencies(a.id, [c.id])
    with pytest.raises(DependencyError, match="itself"):
        svc.set_dependencies(a.id, [a.id])
    with pytest.raises(DependencyError, match="unknown"):
        svc.set_dependencies(a.id, ["eco_" + "4" * 32])
    with pytest.raises(DependencyError, match="twice"):
        svc.set_dependencies(c.id, [b.id, b.id])
    # related links do not form enforcement cycles
    assert svc.set_dependencies(a.id, [{"change_id": c.id, "type": "related"}]).requires() == []


def test_requires_dependency_blocks_implementation_until_closed(env):
    svc, actors, _ = env
    first = approved(svc, actors)
    second = svc.create_request("Second", "needs first", affected=[doc_item(action="review")],
                                dependencies=[first])
    svc.submit(second.id)
    svc.begin_review(second.id)
    svc.approve(second.id)
    eco2 = svc.create_order([second.id])
    assert [d.change_id for d in eco2.dependencies] == [first]
    svc.start_work(eco2.id)
    with pytest.raises(DependencyError, match="depends on ECR-0001, which is Approved"):
        svc.mark_implemented(eco2.id)
    eco1 = svc.create_order([first])
    svc.start_work(eco1.id)
    svc.mark_not_required(eco1.id, eco1.affected[0].key, "n/a")
    svc.mark_implemented(eco1.id)
    svc.close_order(eco1.id)
    assert svc.mark_implemented(eco2.id).status is ECOStatus.IMPLEMENTED


# ---------------------------------------------------------------- concurrency, retries, failures
def test_stale_expected_rev_is_a_conflict_and_writes_nothing(env):
    svc, _, _ = env
    ecr = svc.create_request("t", "r", affected=[doc_item()])
    shown = ecr.rev
    svc.edit_request(ecr.id, title="someone else's edit")
    before = svc.store.seq
    with pytest.raises(ConflictError):
        svc.edit_request(ecr.id, title="mine", expected_rev=shown)
    with pytest.raises(ConflictError):
        svc.submit(ecr.id, expected_rev=shown)
    assert svc.store.seq == before and svc.get(ecr.id).title == "someone else's edit"


def test_failure_before_commit_writes_nothing_and_retry_succeeds(env):
    svc, _, _ = env
    svc.store.fail_next_commit = OSError("disk full")
    with pytest.raises(OSError):
        svc.create_request("t", "r", txid=TX)
    assert svc.store.records == {}
    assert svc.create_request("t", "r", txid=TX).number == "ECR-0001"


def test_txid_retry_returns_the_original_change_without_duplicating(env):
    svc, _, _ = env
    first = svc.create_request("t", "r", txid=TX)
    again = svc.create_request("t", "r", txid=TX)
    assert again.id == first.id and svc.last_replayed and len(svc.store.list("ecr")) == 1
    assert svc.get(first.id).event_count == 1
    other = "f" * 32
    svc.edit_request(first.id, title="x", txid=other)
    assert svc.edit_request(first.id, title="x", txid=other).title == "x" and svc.last_replayed
    assert svc.get(first.id).event_count == 2
    second = svc.create_request("u", "r")
    with pytest.raises(ReplayRefused, match="different change"):
        svc.edit_request(second.id, title="y", txid=other)
    with pytest.raises(ValidationError, match="txid"):
        svc.create_request("t", "r", txid="NOT-HEX")


def test_audit_events_are_write_once(env):
    svc, _, _ = env
    ecr = svc.create_request("t", "r")
    ev_id = event_id(ecr.id, 1)
    with pytest.raises(ConflictError):
        svc.store.commit(Commit("forge", [Put("event", ev_id, {"forged": True}, expect_rev=0)]))
    assert svc.verify_history(ecr.id) == []


@pytest.mark.parametrize("damage, problem", [
    (lambda ev: ev.update(comment="rewritten"), "altered-event"),
    (lambda ev: ev.update(n=7), "misplaced-event"),
    (lambda ev: None, "missing-event"),
])
def test_audit_chain_detects_tampering(env, damage, problem):
    svc, actors, _ = env
    ecr_id = reviewed(svc, actors)
    table = svc.store.records["event"]
    ev = table[event_id(ecr_id, 2)]
    if damage(ev) is None and problem == "missing-event":
        del table[event_id(ecr_id, 2)]
    assert problem in {p["problem"] for p in svc.verify_history(ecr_id)}


def test_audit_chain_detects_truncation_and_unrecorded_events(env):
    svc, actors, _ = env
    ecr_id = reviewed(svc, actors)
    rec = svc.store.records["ecr"][ecr_id]
    rec["event_count"] -= 1
    assert {p["problem"] for p in svc.verify_history(ecr_id)} >= {"head-mismatch", "unrecorded-event"}


def test_malformed_record_is_reported_not_hidden(env):
    svc, _, _ = env
    good = svc.create_request("t", "r")
    svc.store.records["ecr"]["ecr_" + "e" * 32] = {"kind": "ecr", "schema": 1, "id": "ecr_" + "e" * 32, "_rev": 9}
    changes, problems = svc.scan()
    assert [c.id for c in changes] == [good.id]
    assert problems[0]["id"] == "ecr_" + "e" * 32
    with pytest.raises(MalformedRecord):
        svc.get("ecr_" + "e" * 32)


def test_policy_changes_are_audited_and_conflict_checked(env):
    svc, _, _ = env
    svc.save_policy({"ecr_approvals_required": 2}, comment="CCB decision 12")
    rec = svc.store.get("config", "policy")
    with pytest.raises(ConflictError):
        svc.save_policy({"ecr_approvals_required": 3}, expected_rev=rec["_rev"] - 1)
    hist = svc.policy_history()
    assert hist[0]["details"]["old"]["ecr_approvals_required"] == 1
    assert hist[0]["details"]["new"]["ecr_approvals_required"] == 2 and hist[0]["comment"] == "CCB decision 12"
    svc.store.records["config"]["policy"]["ecr_approvals_required"] = "lots"
    with pytest.raises(MalformedRecord, match="policy"):
        svc.policy()


def _approve_new(svc, title, items):
    ecr = svc.create_request(title, "r", affected=items)
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    return svc.approve(ecr.id).id


def test_merging_requests_keeps_the_strongest_action_on_a_shared_item(env):
    """Codex P1 on PR #20: a 'reference' request merged before a 'revise' request lost the revision."""
    svc, _, _ = env
    ref = _approve_new(svc, "context", [doc_item(action="reference")])
    rev = _approve_new(svc, "change", [doc_item(action="revise")])
    eco = svc.create_order([ref, rev])
    assert eco.affected[0].action.value == "revise"
    svc.start_work(eco.id)
    with pytest.raises(DependencyError, match="no implementation recorded"):
        svc.mark_implemented(eco.id)


def test_merging_requests_refuses_conflicting_actions_or_baselines(env):
    svc, _, _ = env
    a = _approve_new(svc, "a", [doc_item(action="revise"), {"item_type": "part", "label": "P-1",
                                                             "baseline_label": "B"}])
    b = _approve_new(svc, "b", [doc_item(action="obsolete")])
    c = _approve_new(svc, "c", [{"item_type": "part", "label": "p-1", "baseline_label": "C"}])
    with pytest.raises(ValidationError, match="conflicting actions"):
        svc.create_order([a, b])
    with pytest.raises(ValidationError, match="different baselines"):
        svc.create_order([a, c])
