# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable engineering BOM structure: lines and the multi-level structure they form.

* A :class:`BomLine` says "one ``parent`` contains ``quantity`` ``unit`` of ``child``", per one of the
  parent's own unit.
* A single-level BOM is the lines that share a ``parent_id``.
* A :class:`Structure` is a set of lines plus the :class:`PartInfo` of the items they mention. Shared
  subassemblies are defined once and used many times, so the structure is a graph, not a tree.

Structures are values: every "edit" returns a new structure, and two structures with the same content
compare equal and serialize to the same bytes, whatever order their lines were given in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from functools import cached_property
from typing import Iterable, Mapping

from .catalog import PartInfo, check_id, clean_text
from .errors import QuantityError, StructureError
from .units import canonical_text, to_quantity


class Provenance(str, Enum):
    """Where a BOM line came from (docs/ARCHITECTURE.md, CAD compatibility)."""

    DECLARED = "declared"            # entered or confirmed by an engineer
    IMPORTED = "imported"            # imported from a BOM file (CSV/JSON)
    CAD_EXTRACTED = "cad-extracted"  # read from a CAD file by a verified adapter
    SCHEMATIC = "schematic"          # from a schematic adapter (OpenSchematix)
    INFERRED = "inferred"            # guessed (e.g. filename match): never proof
    UNKNOWN = "unknown"


#: Provenances that need an engineer's verification before a report may treat them as fact.
NEEDS_VERIFICATION = frozenset({Provenance.INFERRED.value, Provenance.UNKNOWN.value})

_REFDES_SPLIT = re.compile(r"[\s,;]+")


def natural_key(text: str) -> tuple:
    """Sort key that orders ``"2"`` before ``"10"`` and ignores case."""
    parts = re.split(r"(\d+)", text.casefold())
    return tuple((0, int(p), p) if p.isdigit() else (1, 0, p) for p in parts if p != "")


def parse_refdes(value: str | Iterable[str]) -> tuple[str, ...]:
    """Reference designators from text (``"R1, R2 R3"``) or an iterable, in natural order.

    Ranges such as ``R1-R4`` are kept as written; they are not expanded."""
    if isinstance(value, str):
        items = [p for p in _REFDES_SPLIT.split(value) if p]
    else:
        items = list(value)
    out = []
    for item in items:
        r = clean_text(item, what="reference designator", allow_empty=False, max_len=64)
        if any(c.isspace() or c in ",;" for c in r):
            raise StructureError(f"reference designator {r!r} contains a separator")
        out.append(r)
    return tuple(sorted(out, key=lambda r: (natural_key(r), r)))


@dataclass(frozen=True)
class BomLine:
    """One parent-child usage. Quantity is per one of the parent's own unit (its ``PartInfo.unit``)."""

    parent_id: str
    child_id: str
    quantity: Decimal
    unit: str = "EA"
    find_number: str = ""
    reference_designators: tuple[str, ...] = ()
    child_revision: str = ""
    line_id: str = ""
    provenance: str = Provenance.DECLARED.value
    note: str = ""
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        s = object.__setattr__
        s(self, "parent_id", check_id(self.parent_id, what="parent id"))
        s(self, "child_id", check_id(self.child_id, what="child id"))
        q = to_quantity(self.quantity, what=f"quantity of {self.child_id} in {self.parent_id}")
        if q <= 0:
            raise QuantityError(f"quantity of {self.child_id} in {self.parent_id} must be greater than "
                                f"zero, got {canonical_text(q)}")
        s(self, "quantity", q)
        s(self, "unit", clean_text(self.unit, what="unit", allow_empty=False, max_len=32))
        s(self, "find_number", clean_text(self.find_number, what="find number", max_len=32))
        s(self, "reference_designators", parse_refdes(self.reference_designators))
        s(self, "child_revision", clean_text(self.child_revision, what="child revision", max_len=64))
        s(self, "line_id", clean_text(self.line_id, what="line id", max_len=200))
        prov = self.provenance.value if isinstance(self.provenance, Provenance) else self.provenance
        try:
            prov = Provenance(prov).value
        except ValueError:
            raise StructureError(f"unknown provenance {self.provenance!r}; use one of "
                                 f"{', '.join(p.value for p in Provenance)}") from None
        s(self, "provenance", prov)
        s(self, "note", clean_text(self.note, what="note"))
        attrs = dict(self.attributes.items() if isinstance(self.attributes, Mapping) else self.attributes)
        clean = {}
        for k, v in attrs.items():
            key = clean_text(k, what="attribute name", allow_empty=False, max_len=64)
            if key in clean:
                raise StructureError(f"duplicate attribute {key!r}")
            clean[key] = clean_text(v, what=f"attribute {key!r}")
        s(self, "attributes", tuple(sorted(clean.items())))

    @property
    def quantity_text(self) -> str:
        return canonical_text(self.quantity)

    def sort_key(self) -> tuple:
        """Total, deterministic order: parent, find number (natural), child, then every other field."""
        return (self.parent_id, natural_key(self.find_number), self.find_number, self.child_id,
                self.line_id, self.quantity_text, self.unit, self.reference_designators, self.child_revision,
                self.provenance, self.note, self.attributes)

    def with_changes(self, **changes) -> "BomLine":
        return replace(self, **changes)


@dataclass(frozen=True)
class Structure:
    """An immutable set of BOM lines and the items they mention. Build with :meth:`build`."""

    lines: tuple[BomLine, ...] = ()
    parts: tuple[PartInfo, ...] = ()
    _parts_by_id: Mapping[str, PartInfo] = field(default=None, compare=False, repr=False)  # type: ignore

    def __post_init__(self) -> None:
        if not all(isinstance(ln, BomLine) for ln in self.lines):
            raise StructureError("Structure.lines must contain BomLine values")
        if not all(isinstance(p, PartInfo) for p in self.parts):
            raise StructureError("Structure.parts must contain PartInfo values")
        object.__setattr__(self, "lines", tuple(sorted(self.lines, key=BomLine.sort_key)))
        by_id: dict[str, PartInfo] = {}
        for p in self.parts:
            if p.part_id in by_id and by_id[p.part_id] != p:
                raise StructureError(f"two different records for part id {p.part_id!r}")
            by_id[p.part_id] = p
        object.__setattr__(self, "parts", tuple(by_id[k] for k in sorted(by_id)))
        object.__setattr__(self, "_parts_by_id", by_id)
        ids = [ln.line_id for ln in self.lines if ln.line_id]
        if len(ids) != len(set(ids)):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise StructureError(f"duplicate line id(s): {', '.join(dup)}")

    @classmethod
    def build(cls, lines: Iterable[BomLine] = (), parts: Iterable[PartInfo] = ()) -> "Structure":
        return cls(tuple(lines), tuple(parts))

    # ----------------------------------------------------------------- lookups
    @cached_property
    def _children(self) -> dict[str, tuple[BomLine, ...]]:
        out: dict[str, list[BomLine]] = {}
        for ln in self.lines:
            out.setdefault(ln.parent_id, []).append(ln)
        return {k: tuple(v) for k, v in out.items()}

    @cached_property
    def _parents(self) -> dict[str, tuple[BomLine, ...]]:
        out: dict[str, list[BomLine]] = {}
        for ln in self.lines:
            out.setdefault(ln.child_id, []).append(ln)
        return {k: tuple(sorted(v, key=BomLine.sort_key)) for k, v in out.items()}

    def part(self, part_id: str) -> PartInfo | None:
        return self._parts_by_id.get(part_id)

    def info(self, part_id: str) -> PartInfo:
        """Known part info, or a minimal placeholder (unresolved) for an id with no record."""
        p = self._parts_by_id.get(part_id)
        return p if p is not None else PartInfo(part_id=part_id, resolved=False)

    def children(self, parent_id: str) -> tuple[BomLine, ...]:
        """The single-level BOM of ``parent_id``, in stable order."""
        return self._children.get(parent_id, ())

    def usages(self, child_id: str) -> tuple[BomLine, ...]:
        """Every line that uses ``child_id`` directly."""
        return self._parents.get(child_id, ())

    def is_assembly(self, part_id: str) -> bool:
        return part_id in self._children

    @cached_property
    def item_ids(self) -> tuple[str, ...]:
        ids = set(self._parts_by_id)
        for ln in self.lines:
            ids.add(ln.parent_id)
            ids.add(ln.child_id)
        return tuple(sorted(ids))

    @cached_property
    def assembly_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._children))

    @cached_property
    def top_level_ids(self) -> tuple[str, ...]:
        """Assemblies that no line uses as a child: the products at the top of the structure."""
        return tuple(sorted(p for p in self._children if p not in self._parents))

    def line(self, line_id: str) -> BomLine:
        for ln in self.lines:
            if ln.line_id == line_id:
                return ln
        raise KeyError(line_id)

    # ----------------------------------------------------------------- functional edits
    def with_lines(self, *lines: BomLine) -> "Structure":
        return Structure(self.lines + tuple(lines), self.parts)

    def without_line(self, line: BomLine) -> "Structure":
        if line not in self.lines:
            raise KeyError("line is not in this structure")
        rest = list(self.lines)
        rest.remove(line)
        return Structure(tuple(rest), self.parts)

    def replacing_line(self, old: BomLine, new: BomLine) -> "Structure":
        return self.without_line(old).with_lines(new)

    def with_parts(self, *parts: PartInfo) -> "Structure":
        merged = {p.part_id: p for p in self.parts}
        merged.update({p.part_id: p for p in parts})
        return Structure(self.lines, tuple(merged.values()))

    def with_single_level(self, parent_id: str, lines: Iterable[BomLine]) -> "Structure":
        """Replace the whole single-level BOM of ``parent_id`` (used for what-if change impact)."""
        new = tuple(lines)
        if any(ln.parent_id != parent_id for ln in new):
            raise StructureError(f"every replacement line must have parent {parent_id!r}")
        kept = tuple(ln for ln in self.lines if ln.parent_id != parent_id)
        return Structure(kept + new, self.parts)

    def restricted_to(self, root_id: str) -> "Structure":
        """Only the lines reachable from ``root_id`` (cycle-safe), with the parts they mention."""
        seen, stack, lines = {root_id}, [root_id], []
        while stack:
            pid = stack.pop()
            for ln in self.children(pid):
                lines.append(ln)
                if ln.child_id not in seen:
                    seen.add(ln.child_id)
                    stack.append(ln.child_id)
        return Structure(tuple(lines), tuple(p for p in self.parts if p.part_id in seen))

    def merged_with(self, other: "Structure") -> "Structure":
        """Union of two structures. An assembly defined in both must have identical lines."""
        for pid in set(self._children) & set(other._children):
            if self.children(pid) != other.children(pid):
                raise StructureError(f"assembly {self.info(pid).display_number} is defined differently "
                                     "in the two structures")
        lines = self.lines + tuple(ln for ln in other.lines if ln.parent_id not in self._children)
        return Structure(lines, self.parts + other.parts)

    def __len__(self) -> int:
        return len(self.lines)
