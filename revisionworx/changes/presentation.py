# SPDX-License-Identifier: GPL-3.0-or-later
"""Toolkit-free helpers for the desktop GUI (Claude B, issue #13): which buttons are enabled and why,
what an error means for the user, and a ready-made detail view. Nothing here imports Qt, so it is tested
headless in the same way as ``gui/viewmodels.py``. The full contract is in docs/CHANGE_CONTROL.md.
"""
from __future__ import annotations

from ..workspace import (CommittedNotApplied, ConflictError, LockLostError, ReadOnlyError, TxidReuseError,
                         WorkspaceCorruptError, WorkspaceLockedError)
from . import workflow
from .impact import impact_summary
from .model import (ActionStatus, Change, ChangeError, ChangeOrder, ChangeRequest, DependencyError,
                    DuplicateNumber, ECOStatus, ECRStatus, MalformedRecord, NotFound, ReplayRefused,
                    SignoffError, SignoffStage, StaleBaseline, TransitionError, ValidationError, actor_key)
from .service import ChangeService

# action -> (label, inputs the dialog must collect)
ACTIONS = {
    "edit": ("Edit…", ()), "submit": ("Submit for review", ("comment?",)),
    "begin_review": ("Start review", ("comment?",)), "approve": ("Approve", ("comment?", "role?")),
    "reject": ("Reject…", ("reason",)), "return_for_rework": ("Return for rework…", ("reason",)),
    "cancel": ("Cancel request…", ("reason",)), "create_order": ("Create change order", ()),
    "rebaseline": ("Re-baseline out-of-date items", ()),
    "sign_off_order": ("Sign off…", ("decision", "comment?", "role?")), "start_work": ("Start work", ()),
    "add_action": ("Add action…", ("title",)), "record_implementation": ("Record implementing revision…",
                                                                         ("item", "revision")),
    "mark_implemented": ("Mark implemented", ()), "reopen": ("Reopen…", ("reason",)),
    "close_order": ("Close change order", ()), "cancel_order": ("Cancel change order…", ("reason",)),
}


def _act(name: str, enabled: bool = True, reason: str = "") -> dict:
    label, needs = ACTIONS[name]
    return {"action": name, "label": label, "enabled": enabled, "reason": reason, "needs": list(needs)}


def available_actions(svc: ChangeService, change: Change, read_only: bool = False) -> list[dict]:
    """Every action relevant in the current state, with ``enabled`` and a human ``reason`` when disabled.
    The service re-validates everything on commit; this only drives the UI."""
    policy = svc.policy()
    out: list[dict] = []
    me = actor_key(svc.actor())
    if isinstance(change, ChangeRequest):
        st = change.status
        if st is ECRStatus.DRAFT:
            out.append(_act("edit"))
            stale = svc.stale_items(change)
            if stale:
                out.append(_act("rebaseline"))
            if policy.require_affected_items and not change.affected:
                out.append(_act("submit", False, "add at least one affected item"))
            elif stale and policy.block_stale_baselines:
                out.append(_act("submit", False, "some affected documents were revised; re-baseline first"))
            else:
                out.append(_act("submit"))
        if st is ECRStatus.SUBMITTED:
            out.append(_act("begin_review"))
        if st is ECRStatus.IN_REVIEW:
            if policy.require_distinct_approver and me == actor_key(change.created_by):
                out.append(_act("approve", False, "policy: the requester cannot approve their own request"))
            elif any(actor_key(s.actor) == me and s.round == change.round and s.stage is SignoffStage.ECR_REVIEW
                     and s.content_digest == change.content_digest() for s in change.signoffs):
                out.append(_act("approve", False, "you have already signed off this review"))
            elif svc.stale_items(change) and policy.block_stale_baselines:
                out.append(_act("approve", False, "some affected documents were revised; return for rework"))
            else:
                out.append(_act("approve"))
        if st in (ECRStatus.SUBMITTED, ECRStatus.IN_REVIEW):
            out += [_act("return_for_rework"), _act("reject")]
        if st is ECRStatus.APPROVED:
            out.append(_act("create_order"))
        if st in workflow.ECR_STEPS["cancel"][0]:
            out.append(_act("cancel"))
    else:
        st = change.status
        if st is ECOStatus.PLANNING:
            g = svc.gate(change, SignoffStage.ECO_RELEASE)
            need = policy.eco_release_approvals_required
            out.append(_act("sign_off_order"))
            ok = g["approvals"] >= need and not g["rejections"]
            out.append(_act("start_work", ok, "" if ok else f"{g['approvals']} of {need} release approval(s); "
                            f"{len(g['rejections'])} rejection(s)"))
        if st in workflow.ECO_WORK_OPEN:
            out.append(_act("add_action"))
        if st is ECOStatus.IN_PROGRESS:
            out.append(_act("record_implementation"))
            blockers = svc.blockers(change)
            out.append(_act("mark_implemented", not blockers, "; ".join(blockers)))
        if st is ECOStatus.IMPLEMENTED:
            g = svc.gate(change, SignoffStage.ECO_CLOSURE)
            need = policy.eco_closure_approvals_required
            out.append(_act("sign_off_order"))
            ok = g["approvals"] >= need and not g["rejections"]
            out.append(_act("close_order", ok, "" if ok else f"{g['approvals']} of {need} closure approval(s); "
                            f"{len(g['rejections'])} rejection(s)"))
            out.append(_act("reopen"))
        if st in workflow.ECO_STEPS["cancel"][0]:
            out.append(_act("cancel_order"))
    if read_only:
        for a in out:
            a.update(enabled=False, reason="the workspace is open read-only")
    return out


def describe_error(exc: BaseException) -> dict:
    """User-facing message plus what the UI should do:

    ``fix-input`` keep the dialog open; ``reload`` refresh and let the user retry; ``saved`` the change IS
    saved (show success with a warning, never retry); ``read-only`` switch to read-only; ``new-attempt``
    retry as a new action with a new txid; ``reopen`` close and reopen the workspace handle."""
    if isinstance(exc, CommittedNotApplied):
        return {"ui": "saved", "message": f"Saved. {exc}"}
    if isinstance(exc, ConflictError):
        return {"ui": "reload", "message": "Someone changed this record since you opened it. Nothing was saved; "
                                           "reload and try again."}
    if isinstance(exc, LockLostError):
        return {"ui": "reopen", "message": f"This session lost write access; nothing was saved. {exc}"}
    if isinstance(exc, (WorkspaceCorruptError,)):
        return {"ui": "read-only", "message": f"Writes are blocked for safety: {exc}"}
    if isinstance(exc, (ReadOnlyError, WorkspaceLockedError)):
        return {"ui": "read-only", "message": str(exc)}
    if isinstance(exc, (ReplayRefused, TxidReuseError)):
        return {"ui": "new-attempt", "message": str(exc)}
    if isinstance(exc, StaleBaseline):
        return {"ui": "reload", "message": str(exc), "items": exc.items}
    if isinstance(exc, (ValidationError, DuplicateNumber, SignoffError, DependencyError, TransitionError)):
        return {"ui": "fix-input", "message": str(exc)}
    if isinstance(exc, (NotFound, MalformedRecord)):
        return {"ui": "reload", "message": str(exc)}
    if isinstance(exc, ChangeError):
        return {"ui": "fix-input", "message": str(exc)}
    if isinstance(exc, OSError):
        return {"ui": "fix-input", "message": f"{getattr(exc, 'filename', '') or ''} {exc.strerror or exc}".strip()}
    return {"ui": "reload", "message": f"Unexpected error: {type(exc).__name__}: {exc}"}


def change_detail(svc: ChangeService, change_id: str, read_only: bool = False) -> dict:
    """Everything a detail pane shows, in one call (one read-only handle)."""
    change = svc.get(change_id)
    detail = {"change": change.to_dict() | {"rev": change.rev},
              "status_name": workflow.STATUS_NAMES[change.status],
              "history": svc.history(change_id), "history_problems": svc.verify_history(change_id),
              "actions": available_actions(svc, change, read_only), "impact": impact_summary(svc, change_id)}
    if isinstance(change, ChangeOrder):
        detail["gates"] = {s.value: {"approvals": g["approvals"], "rejections": len(g["rejections"])}
                           for s in (SignoffStage.ECO_RELEASE, SignoffStage.ECO_CLOSURE)
                           for g in [svc.gate(change, s)]}
        detail["open_actions"] = [a.to_dict() for a in change.actions if a.status is ActionStatus.OPEN]
    else:
        g = svc.gate(change, SignoffStage.ECR_REVIEW)
        detail["gates"] = {"ecr-review": {"approvals": g["approvals"], "rejections": len(g["rejections"]),
                                          "required": svc.policy().ecr_approvals_required}}
    return detail
