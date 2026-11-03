# SPDX-License-Identifier: GPL-3.0-or-later
"""Change-control lifecycle: the fixed state machines and the small configurable policy.

The state machines are deliberately fixed (so every workspace means the same thing by "approved"); what a
team configures is how much ceremony each step needs: how many approvals, whether the requester may
approve their own request, whether stale baselines block approval. The defaults suit a small team.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import ECOStatus, ECRStatus, TransitionError, ValidationError, clean_text

# step name -> (allowed from-states, to-state). Steps are the only way a status changes.
ECR_STEPS: dict[str, tuple[frozenset, ECRStatus]] = {
    "submit": (frozenset({ECRStatus.DRAFT}), ECRStatus.SUBMITTED),
    "begin_review": (frozenset({ECRStatus.SUBMITTED}), ECRStatus.IN_REVIEW),
    "return_for_rework": (frozenset({ECRStatus.SUBMITTED, ECRStatus.IN_REVIEW}), ECRStatus.DRAFT),
    "approve": (frozenset({ECRStatus.IN_REVIEW}), ECRStatus.APPROVED),
    "reject": (frozenset({ECRStatus.SUBMITTED, ECRStatus.IN_REVIEW}), ECRStatus.REJECTED),
    "cancel": (frozenset({ECRStatus.DRAFT, ECRStatus.SUBMITTED, ECRStatus.IN_REVIEW, ECRStatus.APPROVED}),
               ECRStatus.CANCELLED),
    # system steps, driven by change orders only
    "convert": (frozenset({ECRStatus.APPROVED}), ECRStatus.CONVERTED),
    "unconvert": (frozenset({ECRStatus.CONVERTED}), ECRStatus.APPROVED),
    "close": (frozenset({ECRStatus.CONVERTED}), ECRStatus.CLOSED),
}
ECR_SYSTEM_STEPS = frozenset({"convert", "unconvert", "close"})
ECR_EDITABLE = frozenset({ECRStatus.DRAFT})
ECR_TERMINAL = frozenset({ECRStatus.CLOSED, ECRStatus.REJECTED, ECRStatus.CANCELLED})

ECO_STEPS: dict[str, tuple[frozenset, ECOStatus]] = {
    "start_work": (frozenset({ECOStatus.PLANNING}), ECOStatus.IN_PROGRESS),
    "mark_implemented": (frozenset({ECOStatus.IN_PROGRESS}), ECOStatus.IMPLEMENTED),
    "reopen": (frozenset({ECOStatus.IMPLEMENTED}), ECOStatus.IN_PROGRESS),
    "close": (frozenset({ECOStatus.IMPLEMENTED}), ECOStatus.CLOSED),
    "cancel": (frozenset({ECOStatus.PLANNING, ECOStatus.IN_PROGRESS}), ECOStatus.CANCELLED),
}
ECO_PLAN_EDITABLE = frozenset({ECOStatus.PLANNING})
ECO_WORK_OPEN = frozenset({ECOStatus.PLANNING, ECOStatus.IN_PROGRESS})
ECO_TERMINAL = frozenset({ECOStatus.CLOSED, ECOStatus.CANCELLED})

STATUS_NAMES = {
    ECRStatus.DRAFT: "Draft", ECRStatus.SUBMITTED: "Submitted", ECRStatus.IN_REVIEW: "In review",
    ECRStatus.APPROVED: "Approved", ECRStatus.CONVERTED: "On change order", ECRStatus.CLOSED: "Closed",
    ECRStatus.REJECTED: "Rejected", ECRStatus.CANCELLED: "Cancelled",
    ECOStatus.PLANNING: "Planning", ECOStatus.IN_PROGRESS: "In progress",
    ECOStatus.IMPLEMENTED: "Implemented", ECOStatus.CLOSED: "Closed", ECOStatus.CANCELLED: "Cancelled",
}


def ecr_target(step: str, current: ECRStatus, number: str = "") -> ECRStatus:
    allowed, target = ECR_STEPS[step]
    if current not in allowed:
        raise TransitionError(_refusal(number, step, current, allowed_ecr_steps(current)))
    return target


def eco_target(step: str, current: ECOStatus, number: str = "") -> ECOStatus:
    allowed, target = ECO_STEPS[step]
    if current not in allowed:
        raise TransitionError(_refusal(number, step, current, allowed_eco_steps(current)))
    return target


def allowed_ecr_steps(status: ECRStatus) -> list[str]:
    return [s for s, (frm, _) in ECR_STEPS.items() if status in frm and s not in ECR_SYSTEM_STEPS]


def allowed_eco_steps(status: ECOStatus) -> list[str]:
    return [s for s, (frm, _) in ECO_STEPS.items() if status in frm]


def _refusal(number: str, step: str, current, allowed: list[str]) -> str:
    what = f"{number} is" if number else "the change is"
    return (f"cannot {step.replace('_', ' ')}: {what} {STATUS_NAMES.get(current, current)}; "
            f"allowed now: {', '.join(s.replace('_', ' ') for s in allowed) or 'nothing (final state)'}")


DEFAULT_CATEGORIES = ("design", "documentation", "manufacturing", "correction", "obsolescence", "other")


@dataclass(frozen=True)
class Policy:
    ecr_prefix: str = "ECR-"
    eco_prefix: str = "ECO-"
    number_width: int = 4
    ecr_approvals_required: int = 1
    eco_release_approvals_required: int = 0
    eco_closure_approvals_required: int = 0
    require_distinct_approver: bool = False
    require_affected_items: bool = True
    block_stale_baselines: bool = True
    close_requires_revision_state: str | None = None   # a core lifecycle role: "approved" or "released"
    categories: tuple[str, ...] = field(default=DEFAULT_CATEGORIES)

    def to_dict(self) -> dict:
        return {"ecr_prefix": self.ecr_prefix, "eco_prefix": self.eco_prefix, "number_width": self.number_width,
                "ecr_approvals_required": self.ecr_approvals_required,
                "eco_release_approvals_required": self.eco_release_approvals_required,
                "eco_closure_approvals_required": self.eco_closure_approvals_required,
                "require_distinct_approver": self.require_distinct_approver,
                "require_affected_items": self.require_affected_items,
                "block_stale_baselines": self.block_stale_baselines,
                "close_requires_revision_state": self.close_requires_revision_state,
                "categories": list(self.categories)}

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        """Validate a stored or user-supplied policy. Unknown keys are refused, not ignored."""
        if not isinstance(d, dict):
            raise ValidationError("policy must be an object")
        d = {k: v for k, v in d.items() if k != "_rev"}
        known = set(cls().to_dict())
        unknown = set(d) - known
        if unknown:
            raise ValidationError(f"unknown policy settings: {sorted(unknown)}")
        base = {**cls().to_dict(), **d}

        def count(key: str, lo: int, hi: int) -> int:
            v = base[key]
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                raise ValidationError(f"{key} must be a whole number from {lo} to {hi}")
            return v

        for key in ("require_distinct_approver", "require_affected_items", "block_stale_baselines"):
            if not isinstance(base[key], bool):
                raise ValidationError(f"{key} must be true or false")
        prefixes = []
        for key in ("ecr_prefix", "eco_prefix"):
            p = clean_text(base[key], key, limit="number")
            if len(p) > 16:
                raise ValidationError(f"{key} is longer than 16 characters")
            prefixes.append(p)
        if prefixes[0].casefold() == prefixes[1].casefold():
            raise ValidationError("change request and change order prefixes must differ")
        role = base["close_requires_revision_state"]
        if role not in (None, "approved", "released"):
            raise ValidationError("close_requires_revision_state must be empty, 'approved' or 'released'")
        cats = base["categories"]
        if not isinstance(cats, (list, tuple)) or not cats:
            raise ValidationError("categories must be a non-empty list")
        cleaned = tuple(clean_text(c, "category", required=True) for c in cats)
        if len({c.casefold() for c in cleaned}) != len(cleaned):
            raise ValidationError("categories must be unique")
        return cls(prefixes[0], prefixes[1], count("number_width", 1, 9),
                   count("ecr_approvals_required", 0, 10), count("eco_release_approvals_required", 0, 10),
                   count("eco_closure_approvals_required", 0, 10), base["require_distinct_approver"],
                   base["require_affected_items"], base["block_stale_baselines"], role, cleaned)

    def format_number(self, kind: str, n: int) -> str:
        prefix = self.ecr_prefix if kind == "ecr" else self.eco_prefix
        return f"{prefix}{n:0{self.number_width}d}"

    def category(self, value: str) -> str:
        text = clean_text(value or "design", "category", required=True)
        for c in self.categories:
            if c.casefold() == text.casefold():
                return c
        raise ValidationError(f"unknown category {text!r}; use one of {', '.join(self.categories)}")
