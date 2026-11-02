# SPDX-License-Identifier: GPL-3.0-or-later
"""Configurable document lifecycle (ADR-012)."""
from __future__ import annotations

import re

DEFAULT_LIFECYCLE = {
    "id": "default",
    "states": [
        {"id": "wip", "name": "Work in Progress"},
        {"id": "in_review", "name": "In Review"},
        {"id": "approved", "name": "Approved"},
        {"id": "released", "name": "Released"},
        {"id": "superseded", "name": "Superseded"},
        {"id": "obsolete", "name": "Obsolete"},
    ],
    "initial": "wip",
    "approved": "approved",
    "released": "released",
    "superseded": "superseded",
    "transitions": [
        ["wip", "in_review"], ["wip", "obsolete"],
        ["in_review", "wip"], ["in_review", "approved"],
        ["approved", "released"], ["approved", "wip"],
        ["released", "obsolete"], ["superseded", "obsolete"],
    ],
    "system_transitions": [["released", "superseded"]],
    "require_distinct_approver": False,
}

ROLE_KEYS = ("initial", "approved", "released", "superseded")


class LifecycleError(ValueError):
    pass


def validate_lifecycle(lc: dict) -> dict:
    states = [s["id"] for s in lc.get("states", [])]
    if not states or len(states) != len(set(states)):
        raise LifecycleError("lifecycle states must be a non-empty list of unique ids")
    for sid in states:
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", sid):
            raise LifecycleError(f"invalid state id {sid!r}")
    for key in ROLE_KEYS:
        if lc.get(key) not in states:
            raise LifecycleError(f"lifecycle role {key!r} must name one of its states")
    for group in ("transitions", "system_transitions"):
        for pair in lc.get(group, []):
            if len(pair) != 2 or pair[0] not in states or pair[1] not in states or pair[0] == pair[1]:
                raise LifecycleError(f"invalid transition {pair!r}")
    if [lc["released"], lc["superseded"]] not in lc.get("system_transitions", []):
        raise LifecycleError("released -> superseded must be a system transition")
    if [lc["released"], lc["superseded"]] in lc.get("transitions", []):
        raise LifecycleError("released -> superseded is performed automatically on release; "
                             "it cannot also be a user transition")
    return {"id": lc.get("id", "default"), "states": [{"id": s["id"], "name": str(s.get("name", s["id"]))}
                                                       for s in lc["states"]],
            **{k: lc[k] for k in ROLE_KEYS},
            "transitions": [list(p) for p in lc.get("transitions", [])],
            "system_transitions": [list(p) for p in lc.get("system_transitions", [])],
            "require_distinct_approver": bool(lc.get("require_distinct_approver", False))}


def allowed_targets(lc: dict, state: str) -> list[str]:
    return [b for a, b in lc["transitions"] if a == state]


def state_name(lc: dict, sid: str) -> str:
    for s in lc["states"]:
        if s["id"] == sid:
            return s["name"]
    return sid
