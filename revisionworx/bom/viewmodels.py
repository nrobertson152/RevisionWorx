# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Toolkit-free view models for a future BOM screen (Claude B, issue #13 owns the GUI wiring).

Every function returns frozen rows of display strings plus the raw values a table needs for sorting
and selection. Nothing here imports Qt, touches the workspace or writes files; all functions are pure and
can run in a background job. :class:`BomSession` keeps the documents a screen has loaded, in memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

from . import csvio
from .catalog import CatalogReader
from .diff import BomDiff, compare, compare_rollups
from .graph import describe_quantity, explode, rollup, where_used
from .impact import ChangeImpactReport, RevisionImpactReport, change_impact, revision_impact
from .model import Structure
from .snapshot import BomSnapshot, load_any, snapshot_to_json
from .units import canonical_text
from .validate import ValidationReport, validate
from .workspace_adapter import single_root, sniff_layout

VIEWMODEL_CONTRACT = 1


@dataclass(frozen=True)
class IndentedRow:
    level: int
    indent_label: str               # part number indented by level, for a single-column tree view
    find_number: str
    part_number: str
    part_id: str
    description: str
    revision: str
    quantity: str
    unit: str
    extended_quantity: str
    is_assembly: bool
    flags: tuple[str, ...]          # "unresolved", "needs-verification"
    path: tuple[str, ...]


@dataclass(frozen=True)
class SummaryRow:
    part_number: str
    part_id: str
    description: str
    total: str
    unit: str
    occurrences: int
    is_assembly: bool
    flags: tuple[str, ...]
    total_value: Decimal


@dataclass(frozen=True)
class ChangeRow:
    change: str
    parent_number: str
    part_number: str
    find_number: str
    field: str
    before: str
    after: str


@dataclass(frozen=True)
class IssueRow:
    severity: str
    code: str
    message: str
    part_id: str


@dataclass(frozen=True)
class WhereUsedRow:
    assembly_number: str
    assembly_id: str
    relation: str
    level: int
    quantity: str
    top_level: bool
    flags: tuple[str, ...]


def _flags(structure: Structure, item_id: str, provenance: str = "", unverified: bool = False) -> tuple[str, ...]:
    out = []
    if not structure.info(item_id).resolved:
        out.append("unresolved")
    if unverified or provenance in ("inferred", "unknown"):
        out.append("needs-verification")
    return tuple(out)


def indented_rows(structure: Structure, root_id: str, *, max_level: int | None = None,
                  cancelled: Callable[[], bool] | None = None) -> list[IndentedRow]:
    ex = explode(structure, root_id, max_level=max_level, cancelled=cancelled)
    out = []
    for r in ex.rows:
        info = structure.info(r.item_id)
        out.append(IndentedRow(r.level, "    " * (r.level - 1) + info.display_number, r.line.find_number,
                               info.number, r.item_id, info.description, r.line.child_revision,
                               r.line.quantity_text, r.line.unit, canonical_text(r.extended_quantity),
                               structure.is_assembly(r.item_id),
                               _flags(structure, r.item_id, r.line.provenance), r.path))
    return out


def summary_rows(structure: Structure, root_id: str, *, leaves_only: bool = False) -> list[SummaryRow]:
    ro = rollup(structure, root_id, leaves_only=leaves_only)
    return [SummaryRow(structure.info(r.item_id).number, r.item_id, structure.info(r.item_id).description,
                       canonical_text(r.total), r.unit, r.occurrences, r.is_assembly,
                       _flags(structure, r.item_id, unverified=r.needs_verification), r.total) for r in ro.rows]


def change_rows(diff: BomDiff, before: Structure, after: Structure) -> list[ChangeRow]:
    num = lambda pid: (after.part(pid) or before.part(pid) or after.info(pid)).display_number  # noqa: E731
    out = []
    for c in diff.changes:
        ln = c.after or c.before
        if c.kind == "changed":
            out += [ChangeRow("changed", num(c.parent_id), num(c.child_id), ln.find_number, f.field, f.before,
                              f.after) for f in c.fields]
        else:
            q = f"{ln.quantity_text} {ln.unit}"
            out.append(ChangeRow(c.kind, num(c.parent_id), num(c.child_id), ln.find_number, "quantity",
                                 q if c.kind == "removed" else "", q if c.kind == "added" else ""))
    for p in diff.part_changes:
        if p.fields:
            out += [ChangeRow(f"part-{p.kind}", "", num(p.part_id), "", f.field, f.before, f.after)
                    for f in p.fields]
        else:
            out.append(ChangeRow(f"part-{p.kind}", "", num(p.part_id), "", "", "", ""))
    return out


def issue_rows(report: ValidationReport) -> list[IssueRow]:
    return [IssueRow(i.severity, i.code, i.message, i.child_id or i.parent_id) for i in report.issues]


def where_used_rows(structure: Structure, item_id: str) -> list[WhereUsedRow]:
    wu = where_used(structure, item_id)
    return [WhereUsedRow(structure.info(u.assembly_id).display_number, u.assembly_id,
                         "direct" if u.direct else "indirect", u.distance,
                         describe_quantity(u.quantity_per_assembly, u.unit), u.is_top_level,
                         ("needs-verification",) if u.needs_verification else ()) for u in wu.usages]


@dataclass
class BomSession:
    """In-memory working set for a BOM screen: loaded snapshots by name. Nothing is persisted here; the
    screen saves through explicit exports, and authoritative storage stays with the core."""

    catalog: CatalogReader | None = None
    snapshots: dict[str, BomSnapshot] = field(default_factory=dict)

    def load_file(self, path: Path | str, *, name: str = "", layout: str = "auto", root_number: str = "") -> str:
        p = Path(path)
        data = p.read_bytes() if p.suffix.casefold() == ".json" else None
        if data is not None:
            loaded = load_any(data)
            snap = loaded if isinstance(loaded, BomSnapshot) else BomSnapshot(
                loaded, _root(loaded, root_number), p.stem)
        elif layout == "indented" or (layout == "auto" and _looks_indented(p)):
            st, root = csvio.structure_from_indented_csv(p, root_number=root_number, catalog=self.catalog)
            snap = BomSnapshot(st, root, p.stem)
        else:
            st = csvio.structure_from_csv(p, catalog=self.catalog)
            snap = BomSnapshot(st, _root(st, root_number), p.stem)
        key = name or p.stem
        self.snapshots[key] = snap
        return key

    def add(self, name: str, snapshot: BomSnapshot) -> None:
        self.snapshots[name] = snapshot

    def validate(self, name: str) -> ValidationReport:
        s = self.snapshots[name]
        return validate(s.structure, catalog=self.catalog, roots=(s.root_id,))

    def compare(self, before: str, after: str) -> tuple[BomDiff, list[ChangeRow]]:
        a, b = self.snapshots[before], self.snapshots[after]
        d = compare(a.structure, b.structure)
        return d, change_rows(d, a.structure, b.structure)

    def summary_change(self, before: str, after: str):
        a, b = self.snapshots[before], self.snapshots[after]
        return compare_rollups(a.structure, a.root_id, b.structure, b.root_id)

    def revision_impact(self, item_id: str, current_revision: str = "") -> RevisionImpactReport:
        return revision_impact(list(self.snapshots.values()), item_id, current_revision=current_revision)

    def change_impact(self, before: str, after: str) -> ChangeImpactReport:
        others = [s for k, s in self.snapshots.items() if k not in (before, after)]
        return change_impact(self.snapshots[before], self.snapshots[after], others)

    def save_snapshot(self, name: str, path: Path | str, *, overwrite: bool = False) -> Path:
        return csvio.save(snapshot_to_json(self.snapshots[name]), path, overwrite=overwrite)

    def export_structure_csv(self, name: str, path: Path | str, *, overwrite: bool = False) -> Path:
        return csvio.save(csvio.structure_to_csv(self.snapshots[name].structure), path, overwrite=overwrite)


def _looks_indented(p: Path) -> bool:
    with open(p, "rb") as fh:
        return sniff_layout(fh.read(4096)) == "indented"


def _root(structure: Structure, root_number: str) -> str:
    return single_root(structure, root_number)
