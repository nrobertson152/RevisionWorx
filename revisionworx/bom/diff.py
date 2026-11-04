# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""BOM comparison: line-level structural differences and summarized quantity differences.

Lines of the two structures are matched in deterministic passes, so a renumbered find number reads as
"find number changed" rather than "removed + added":

1. same non-empty ``line_id`` on both sides;
2. same (parent, child, find number);
3. same (parent, child), pairing the leftovers in stable order.

Whatever is still unmatched is added or removed. ``compare(a, a)`` is always empty, and
``compare(b, a)`` is the exact inverse of ``compare(a, b)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .graph import rollup
from .model import BomLine, Structure, natural_key
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, UnitRegistry, canonical_text, subtract

_COMPARED = ("quantity", "unit", "find_number", "reference_designators", "child_revision", "provenance",
             "note", "attributes")


@dataclass(frozen=True)
class FieldChange:
    field: str
    before: str
    after: str


@dataclass(frozen=True)
class LineChange:
    kind: str                      # "added" | "removed" | "changed"
    parent_id: str
    child_id: str
    before: BomLine | None
    after: BomLine | None
    fields: tuple[FieldChange, ...] = ()

    def sort_key(self) -> tuple:
        ln = self.after or self.before
        assert ln is not None
        return (self.parent_id, natural_key(ln.find_number), self.child_id,
                {"removed": 0, "changed": 1, "added": 2}[self.kind], ln.sort_key())

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.field for f in self.fields)


@dataclass(frozen=True)
class PartChange:
    part_id: str
    kind: str                      # "added" | "removed" | "changed"
    fields: tuple[FieldChange, ...] = ()


@dataclass(frozen=True)
class BomDiff:
    changes: tuple[LineChange, ...]
    part_changes: tuple[PartChange, ...]

    @property
    def empty(self) -> bool:
        return not self.changes and not self.part_changes

    def of_kind(self, kind: str) -> tuple[LineChange, ...]:
        return tuple(c for c in self.changes if c.kind == kind)

    def summary(self) -> dict[str, int]:
        return {k: len(self.of_kind(k)) for k in ("added", "removed", "changed")} | {
            "parts_changed": len(self.part_changes)}


def _text(value) -> str:
    if isinstance(value, Decimal):
        return canonical_text(value)
    if isinstance(value, tuple):
        if value and isinstance(value[0], tuple):
            return "; ".join(f"{k}={v}" for k, v in value)
        return " ".join(value)
    return str(value)


def _field_changes(a: BomLine, b: BomLine) -> tuple[FieldChange, ...]:
    out = []
    for f in _COMPARED:
        va, vb = getattr(a, f), getattr(b, f)
        if f == "quantity" and va == vb:
            continue
        if va != vb:
            out.append(FieldChange(f, _text(va), _text(vb)))
    if a.line_id != b.line_id:
        out.append(FieldChange("line_id", a.line_id, b.line_id))
    return tuple(out)


def _pair(left: list[BomLine], right: list[BomLine], key) -> list[tuple[BomLine, BomLine]]:
    """Pair lines with equal ``key`` in stable order; removes paired lines from both lists."""
    buckets: dict = {}
    for ln in right:
        k = key(ln)
        if k is not None:
            buckets.setdefault(k, []).append(ln)
    pairs = []
    for ln in list(left):
        k = key(ln)
        if k is None:
            continue
        cands = buckets.get(k)
        if cands:
            other = cands.pop(0)
            pairs.append((ln, other))
            left.remove(ln)
            right.remove(other)
    return pairs


def compare(a: Structure, b: Structure) -> BomDiff:
    """Differences that turn structure ``a`` into structure ``b``."""
    left, right = list(a.lines), list(b.lines)
    pairs = _pair(left, right, lambda ln: ("id", ln.line_id) if ln.line_id else None)
    pairs += _pair(left, right, lambda ln: (ln.parent_id, ln.child_id, ln.find_number))
    pairs += _pair(left, right, lambda ln: (ln.parent_id, ln.child_id))
    changes = []
    for x, y in pairs:
        fields = _field_changes(x, y)
        if x.parent_id != y.parent_id or x.child_id != y.child_id:  # only possible through line ids
            changes.append(LineChange("removed", x.parent_id, x.child_id, x, None))
            changes.append(LineChange("added", y.parent_id, y.child_id, None, y))
        elif fields:
            changes.append(LineChange("changed", x.parent_id, x.child_id, x, y, fields))
    changes += [LineChange("removed", ln.parent_id, ln.child_id, ln, None) for ln in left]
    changes += [LineChange("added", ln.parent_id, ln.child_id, None, ln) for ln in right]
    part_changes = []
    pa, pb = {p.part_id: p for p in a.parts}, {p.part_id: p for p in b.parts}
    for pid in sorted(set(pa) | set(pb)):
        x, y = pa.get(pid), pb.get(pid)
        if x is None:
            part_changes.append(PartChange(pid, "added"))
        elif y is None:
            part_changes.append(PartChange(pid, "removed"))
        elif x != y:
            fields = tuple(FieldChange(f, _text(getattr(x, f)), _text(getattr(y, f)))
                           for f in ("number", "description", "unit", "revision", "kind", "resolved")
                           if getattr(x, f) != getattr(y, f))
            part_changes.append(PartChange(pid, "changed", fields))
    return BomDiff(tuple(sorted(changes, key=LineChange.sort_key)), tuple(part_changes))


@dataclass(frozen=True)
class QuantityDelta:
    item_id: str
    before: Decimal
    after: Decimal
    unit: str

    @property
    def delta(self) -> Decimal:
        return subtract(self.after, self.before)

    @property
    def kind(self) -> str:
        if self.before == 0:
            return "added"
        if self.after == 0:
            return "removed"
        return "changed"


def compare_rollups(a: Structure, root_a: str, b: Structure, root_b: str | None = None, *,
                    leaves_only: bool = False, limits: Limits = DEFAULT_LIMITS,
                    units: UnitRegistry = DEFAULT_UNITS) -> tuple[QuantityDelta, ...]:
    """Summarized quantity differences per item for one unit of each root. Unchanged items are omitted.

    If an item's unit differs between the two sides, the "after" unit is used when the quantities
    convert exactly; otherwise both rows are reported (removed in the old unit, added in the new)."""
    ra = rollup(a, root_a, leaves_only=leaves_only, limits=limits, units=units).as_dict()
    rb = rollup(b, root_b or root_a, leaves_only=leaves_only, limits=limits, units=units).as_dict()
    out: list[QuantityDelta] = []
    for item in sorted(set(ra) | set(rb), key=lambda i: (natural_key(_number(a, b, i)), i)):
        (qa, ua), (qb, ub) = ra.get(item, (Decimal(0), "")), rb.get(item, (Decimal(0), ""))
        ua, ub = ua or ub, ub or ua
        if ua != ub:
            try:
                qa = units.convert(qa, ua, ub)
                ua = ub
            except Exception:
                out.append(QuantityDelta(item, qa, Decimal(0), ua))
                out.append(QuantityDelta(item, Decimal(0), qb, ub))
                continue
        if qa != qb:
            out.append(QuantityDelta(item, qa, qb, ub))
    return tuple(out)


def _number(a: Structure, b: Structure, item: str) -> str:
    p = a.part(item) or b.part(item)
    return p.display_number if p else item


def lines_by_parent(changes: Iterable[LineChange]) -> dict[str, list[LineChange]]:
    out: dict[str, list[LineChange]] = {}
    for c in changes:
        out.setdefault(c.parent_id, []).append(c)
    return out
