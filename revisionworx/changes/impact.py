# SPDX-License-Identifier: GPL-3.0-or-later
"""Change-impact summaries.

For a change, report each affected item's verified state (current revision, stale baseline, checkout,
implementation), other open changes touching the same items, the dependency chain in both directions and
an explicit coverage statement. Following REQUIREMENTS section 9, an affected relationship is a review
signal, not proof of incompatibility, and coverage warnings are never suppressed - including in exports.

Where-used (which assemblies, BOMs and projects use an item) is not implemented in the core yet. A
subsystem that provides it (issues #15/#18, Phase 4) can pass a :class:`WhereUsedProvider`; without one
the report says that downstream usage was NOT searched rather than implying there is none.
"""
from __future__ import annotations

import csv
import io
from typing import Protocol

from . import workflow
from .model import (AffectedItem, Change, ChangeOrder, ImplementationStatus, ItemType, MalformedRecord,
                    NotFound, actor_label)
from .service import NEEDS_IMPLEMENTATION, ChangeService

NO_WHERE_USED = ("Where-used for parts, assemblies and BOMs is not available in this version of RevisionWorx: "
                 "downstream usage of the affected items was NOT searched. Review assemblies, drawings and BOMs "
                 "that may use them manually.")


class WhereUsedProvider(Protocol):
    def where_used(self, item: AffectedItem) -> dict:
        """{"edges": [{"label", "relationship", "referenced_revision", "verification"}], "coverage": str}"""


def _open(c: Change) -> bool:
    return c.status not in (workflow.ECR_TERMINAL | workflow.ECO_TERMINAL)


def impact_summary(svc: ChangeService, change_id: str, where_used: WhereUsedProvider | None = None) -> dict:
    change = svc.get(change_id)
    all_changes, problems = svc.scan()
    by_id = {c.id: c for c in all_changes}
    items = []
    for item in change.affected:
        state = svc.describe(item)
        row = {"key": item.key, "type": item.item_type.value, "label": item.label, "item_id": item.item_id,
               "action": item.action.value, "baseline": item.baseline_label or item.baseline_rev_id or "",
               "verification": "declared" if state is None else ("verified" if state.exists else "missing"),
               "current": "", "stale": False, "checked_out_by": "", "classes": ["directly-affected"]}
        if state is not None and state.exists:
            row["current"] = state.latest_label or ""
            if item.item_type is ItemType.DOCUMENT and item.baseline_rev_id and \
                    state.latest_rev_id != item.baseline_rev_id:
                row["stale"] = True
            if state.checked_out_by:
                row["checked_out_by"] = actor_label(state.checked_out_by)
        if isinstance(change, ChangeOrder):
            imp = change.implementation(item.key)
            row["implementation"] = imp.status.value
            row["implemented_revision"] = imp.rev_label
            if imp.status is ImplementationStatus.IMPLEMENTED:
                row["stale"] = False     # moving past the baseline is the point of an implemented order
        if row["verification"] == "declared":
            row["classes"].append("verification-required")
        if row["verification"] == "missing":
            row["classes"].append("missing")
        if row["stale"]:
            row["classes"].append("stale-baseline")
        if row["checked_out_by"]:
            row["classes"].append("checked-out")
        others = sorted(c.number for c in all_changes
                        if c.id != change.id and _open(c) and item.key in {i.key for i in c.affected}
                        and not (isinstance(change, ChangeOrder) and c.id in change.request_ids)
                        and getattr(c, "eco_id", None) != change.id)
        if others:
            row["classes"].append("concurrent-change")
        row["other_changes"] = others
        if where_used is not None:
            wu = where_used.where_used(item)
            row["where_used"] = wu.get("edges", [])
            row["where_used_coverage"] = wu.get("coverage", "")
        items.append(row)

    deps, seen, stack = [], set(), [(d, 1) for d in change.requires()]
    while stack:
        did, depth = stack.pop(0)
        if did in seen:
            continue
        seen.add(did)
        other = by_id.get(did)
        if other is None:
            deps.append({"id": did, "number": "?", "status": "unreadable", "depth": depth, "complete": False})
            continue
        deps.append({"id": did, "number": other.number, "title": other.title, "status": other.status.value,
                     "depth": depth, "complete": other.status.value == "closed"})
        stack.extend((d, depth + 1) for d in other.requires())
    dependents = sorted(c.number for c in all_changes if change.id in c.requires())

    coverage = []
    if where_used is None:
        coverage.append(NO_WHERE_USED)
    declared = [r["label"] for r in items if r["verification"] == "declared"]
    if declared:
        coverage.append(f"{len(declared)} item(s) are declared by the requester and could not be verified: "
                        + ", ".join(declared))
    if svc.catalog is None:
        coverage.append("No workspace catalog was available: no document state was verified.")
    if problems:
        coverage.append(f"{len(problems)} change record(s) could not be read; related-change results may be "
                        "incomplete.")
    blockers = []
    if isinstance(change, ChangeOrder):
        try:
            blockers = svc.blockers(change)
        except (NotFound, MalformedRecord) as exc:
            blockers = [str(exc)]
    return {"change": {"id": change.id, "number": change.number, "kind": change.kind.value, "title": change.title,
                       "status": change.status.value, "status_name": workflow.STATUS_NAMES[change.status]},
            "items": items, "dependencies": deps, "dependents": dependents, "blockers": blockers,
            "coverage": coverage,
            "counts": {"items": len(items), "stale": sum(r["stale"] for r in items),
                       "declared": len(declared), "missing": sum(r["verification"] == "missing" for r in items),
                       "concurrent": sum(bool(r["other_changes"]) for r in items),
                       "open_dependencies": sum(not d["complete"] for d in deps),
                       "pending_implementation": sum(
                           1 for r, it in zip(items, change.affected)
                           if r.get("implementation") == "pending" and it.action in NEEDS_IMPLEMENTATION)}}


def impact_text(report: dict) -> str:
    c = report["change"]
    lines = [f"Impact summary: {c['number']} {c['title']} ({c['status_name']})", ""]
    lines.append("Coverage:")
    lines += [f"  ! {n}" for n in report["coverage"]] or ["  complete"]
    lines.append("")
    lines.append("Affected items:")
    for r in report["items"]:
        flags = ", ".join(x for x in r["classes"] if x != "directly-affected") or "ok"
        cur = f" now {r['current']}" if r["current"] else ""
        lines.append(f"  - [{r['type']}] {r['label']}: {r['action']}, baseline {r['baseline'] or '-'}{cur} "
                     f"({r['verification']}; {flags})")
        if r["other_changes"]:
            lines.append(f"      also changed by: {', '.join(r['other_changes'])}")
    if report["dependencies"]:
        lines += ["", "Requires:"]
        lines += [f"  {'  ' * (d['depth'] - 1)}- {d['number']} ({d['status']})" for d in report["dependencies"]]
    if report["dependents"]:
        lines += ["", f"Required by: {', '.join(report['dependents'])}"]
    if report["blockers"]:
        lines += ["", "Blocking completion:"] + [f"  - {b}" for b in report["blockers"]]
    lines += ["", "An affected relationship is an engineering review signal, not proof of incompatibility."]
    return "\n".join(lines) + "\n"


def impact_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["change", "type", "label", "action", "baseline", "current", "verification", "flags",
                "other_changes"])
    for r in report["items"]:
        w.writerow([safe_cell(x) for x in (report["change"]["number"], r["type"], r["label"], r["action"],
                                          r["baseline"], r["current"], r["verification"],
                                          ";".join(r["classes"]), ";".join(r["other_changes"]))])
    for note in report["coverage"]:  # never dropped from an export
        w.writerow([safe_cell(report["change"]["number"]), "coverage", safe_cell(note), "", "", "", "", "", ""])
    return buf.getvalue()


def safe_cell(value) -> str:
    """Neutralise spreadsheet formula injection in exported text."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
