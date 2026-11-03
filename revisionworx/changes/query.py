# SPDX-License-Identifier: GPL-3.0-or-later
"""Searching, filtering and reporting over change records.

Scope boundary (issue #16): this module answers change-control questions only - which changes match,
which changes touch a given document ("where changed"), the change register. Project/document discovery
and the project dashboard belong to the search/dashboard subsystem; it may call :func:`counts_by_status`
rather than re-deriving change states. Queries read the authoritative records directly (no cache), so
results can never disagree with the journal.
"""
from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass, field

from . import workflow
from .impact import safe_cell
from .model import Change, ChangeOrder, ChangeRequest, Priority, ValidationError, actor_label
from .service import ChangeService

SORT_KEYS = ("number", "created", "modified", "priority", "status", "title")
REGISTER_COLUMNS = (("number", "Number"), ("kind", "Type"), ("title", "Title"), ("status_name", "Status"),
                    ("priority", "Priority"), ("category", "Category"), ("requester", "Requested by"),
                    ("created", "Created"), ("modified", "Modified"), ("affected_count", "Items"),
                    ("links", "Linked"))


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass
class ChangeFilter:
    kinds: tuple[str, ...] = ("ecr", "eco")
    statuses: tuple[str, ...] = ()          # empty = any
    open_only: bool = False
    text: str = ""                          # number, title, reason, description, item labels
    priorities: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    project_id: str | None = None
    item_id: str | None = None              # changes affecting this document/part id
    requested_by: str = ""                  # OS account name, case-insensitive
    created_from: str = ""                  # ISO date/time bounds, inclusive
    created_to: str = ""
    extra: dict = field(default_factory=dict)

    def matches(self, c: Change) -> bool:
        if c.kind.value not in self.kinds:
            return False
        if self.statuses and c.status.value not in self.statuses:
            return False
        if self.open_only and c.status in (workflow.ECR_TERMINAL | workflow.ECO_TERMINAL):
            return False
        if self.priorities and c.priority.value not in self.priorities:
            return False
        if self.categories and _fold(c.category) not in {_fold(x) for x in self.categories}:
            return False
        if self.project_id and self.project_id not in c.project_ids and \
                not any(i.item_type.value == "project" and i.item_id == self.project_id for i in c.affected):
            return False
        if self.item_id and not any(i.item_id == self.item_id for i in c.affected):
            return False
        if self.requested_by and _fold(c.created_by.get("user", "")) != _fold(self.requested_by):
            return False
        if self.created_from and c.created[:len(self.created_from)] < self.created_from:
            return False
        if self.created_to and c.created[:len(self.created_to)] > self.created_to:
            return False
        if self.text.strip():
            hay = " ".join([c.number, c.title, c.description, getattr(c, "reason", ""),
                            *(f"{i.label} {i.baseline_label}" for i in c.affected)])
            if not all(term in _fold(hay) for term in _fold(self.text).split()):
                return False
        return True


def row(c: Change, numbers: dict[str, str]) -> dict:
    if isinstance(c, ChangeRequest):
        links = numbers.get(c.eco_id, "") if c.eco_id else ""
    else:
        links = ", ".join(numbers.get(r, "?") for r in c.request_ids)
    return {"id": c.id, "kind": c.kind.value, "number": c.number, "title": c.title, "status": c.status.value,
            "status_name": workflow.STATUS_NAMES[c.status], "priority": c.priority.value, "category": c.category,
            "requester": actor_label(c.created_by), "created": c.created, "modified": c.modified,
            "affected_count": len(c.affected), "links": links, "rev": c.rev,
            "open": c.status not in (workflow.ECR_TERMINAL | workflow.ECO_TERMINAL)}


def _sort_key(name: str):
    prio = {p.value: i for i, p in enumerate(Priority)}
    if name == "priority":
        return lambda r: (-prio[r["priority"]], r["number"].casefold())
    if name == "number":
        return lambda r: (r["kind"], r["number"].casefold())
    return lambda r: (str(r[name]).casefold(), r["number"].casefold())


def search(svc: ChangeService, flt: ChangeFilter | None = None, *, sort: str = "number",
           descending: bool = False, limit: int = 200, offset: int = 0) -> dict:
    """Matching changes as table rows, stably sorted, one page at a time. Unreadable records are reported
    in ``problems`` (never silently skipped)."""
    if sort not in SORT_KEYS:
        raise ValidationError(f"sort must be one of {', '.join(SORT_KEYS)}")
    if limit < 1 or offset < 0:
        raise ValidationError("limit must be positive and offset non-negative")
    flt = flt or ChangeFilter()
    changes, problems = svc.scan(flt.kinds)
    numbers = {c.id: c.number for c in svc.scan()[0]} if flt.kinds != ("ecr", "eco") else \
        {c.id: c.number for c in changes}
    rows = [row(c, numbers) for c in changes if flt.matches(c)]
    rows.sort(key=_sort_key(sort), reverse=descending)
    return {"rows": rows[offset:offset + limit], "total": len(rows), "has_more": offset + limit < len(rows),
            "problems": problems}


def where_changed(svc: ChangeService, item_id: str, *, open_only: bool = False) -> list[dict]:
    """Every change that lists the item (document, part ...) as affected, with what it does to it."""
    changes, _ = svc.scan()
    numbers = {c.id: c.number for c in changes}
    out = []
    for c in changes:
        for it in c.affected:
            if it.item_id == item_id and (not open_only or row(c, numbers)["open"]):
                r = row(c, numbers)
                r.update(action=it.action.value, baseline=it.baseline_label)
                if isinstance(c, ChangeOrder):
                    imp = c.implementation(it.key)
                    r.update(implementation=imp.status.value, implemented_revision=imp.rev_label)
                out.append(r)
    out.sort(key=lambda r: (r["created"], r["number"]))
    return out


def counts_by_status(svc: ChangeService) -> dict:
    changes, problems = svc.scan()
    out: dict = {"ecr": {}, "eco": {}, "unreadable": len(problems)}
    for c in changes:
        out[c.kind.value][c.status.value] = out[c.kind.value].get(c.status.value, 0) + 1
    return out


def register_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([title for _, title in REGISTER_COLUMNS])
    for r in rows:
        w.writerow([safe_cell(r[key]) for key, _ in REGISTER_COLUMNS])
    return buf.getvalue()
