# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structure validation: collects every problem instead of stopping at the first.

Severity:
* ``error`` - the structure is wrong; explosion or rollup would fail or give a wrong number.
* ``warning`` - legal but suspicious; an engineer should look (e.g. the same part on two lines).
* ``info`` - worth knowing (unresolved identities, lines that need verification).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .catalog import CatalogReader
from .errors import CycleError, LimitExceeded, QuantityError, UnitError
from .graph import check_depth, find_cycles, reachable
from .model import NEEDS_VERIFICATION, Structure, natural_key
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, UnitRegistry, check_line_quantity

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    parent_id: str = ""
    child_id: str = ""
    line_id: str = ""

    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER[self.severity], self.code, self.parent_id, self.child_id, self.line_id,
                self.message)


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    def codes(self) -> list[str]:
        return [i.code for i in self.issues]


def validate(structure: Structure, *, catalog: CatalogReader | None = None, roots: Iterable[str] = (),
             units: UnitRegistry = DEFAULT_UNITS, limits: Limits = DEFAULT_LIMITS) -> ValidationReport:
    """Check a structure. ``catalog`` enables dangling-reference checks against the parts catalog;
    ``roots`` names the top-level products, enabling orphan and depth checks."""
    issues: list[Issue] = []
    name = lambda pid: structure.info(pid).display_number  # noqa: E731

    if len(structure.lines) > limits.max_lines:
        issues.append(Issue("error", "too-many-lines",
                            f"{len(structure.lines)} lines exceed the limit of {limits.max_lines}"))

    for ln in structure.lines:
        loc = dict(parent_id=ln.parent_id, child_id=ln.child_id, line_id=ln.line_id)
        where = f"{name(ln.child_id)} in {name(ln.parent_id)}"
        if ln.parent_id == ln.child_id:
            issues.append(Issue("error", "self-reference", f"{name(ln.parent_id)} lists itself", **loc))
        unit = None
        if not units.known(ln.unit):
            issues.append(Issue("error", "unknown-unit", f"{where}: unknown unit {ln.unit!r}", **loc))
        else:
            unit = units.get(ln.unit)
        try:
            check_line_quantity(ln.quantity, unit, limits, what=f"quantity of {where}")
        except QuantityError as exc:
            issues.append(Issue("error", "invalid-quantity", str(exc), **loc))
        part = structure.part(ln.child_id)
        if unit is not None and part is not None and units.known(part.unit):
            if units.get(part.unit).dimension != unit.dimension:
                issues.append(Issue("error", "unit-mismatch",
                                    f"{where}: unit {ln.unit} ({unit.dimension}) cannot be converted to the "
                                    f"part's unit {part.unit} ({units.get(part.unit).dimension})", **loc))
        elif part is not None and not units.known(part.unit):
            issues.append(Issue("error", "unknown-unit", f"part {name(ln.child_id)} has unknown unit "
                                f"{part.unit!r}", child_id=ln.child_id))
        if ln.reference_designators and unit is not None and unit.integral \
                and ln.quantity == ln.quantity.to_integral_value() \
                and len(ln.reference_designators) != int(ln.quantity) \
                and not any("-" in r for r in ln.reference_designators):
            issues.append(Issue("warning", "refdes-count-mismatch",
                                f"{where}: quantity {ln.quantity_text} but {len(ln.reference_designators)} "
                                "reference designator(s)", **loc))
        if ln.provenance in NEEDS_VERIFICATION:
            issues.append(Issue("info", "needs-verification",
                                f"{where}: provenance '{ln.provenance}' must be verified before release", **loc))

    # per single-level BOM
    for pid in structure.assembly_ids:
        lines = structure.children(pid)
        finds = Counter(ln.find_number for ln in lines if ln.find_number)
        for fn, n in sorted(finds.items(), key=lambda kv: natural_key(kv[0])):
            if n > 1:
                issues.append(Issue("error", "duplicate-find-number",
                                    f"{name(pid)}: find number {fn} is used on {n} lines", parent_id=pid))
        refs: Counter[str] = Counter()
        for ln in lines:
            refs.update(r.casefold() for r in ln.reference_designators)
        for r, n in sorted(refs.items()):
            if n > 1:
                issues.append(Issue("error", "duplicate-refdes",
                                    f"{name(pid)}: reference designator {r.upper()} appears {n} times",
                                    parent_id=pid))
        per_child = Counter(ln.child_id for ln in lines)
        for cid, n in sorted(per_child.items()):
            if n > 1:
                issues.append(Issue("warning", "duplicate-child",
                                    f"{name(pid)}: {name(cid)} appears on {n} lines; rollup adds them",
                                    parent_id=pid, child_id=cid))

    # identities
    for pid in structure.item_ids:
        part = structure.part(pid)
        if part is None:
            issues.append(Issue("warning", "missing-part-info",
                                f"{pid}: no part record in this structure", child_id=pid))
        elif not part.resolved:
            issues.append(Issue("info", "unresolved-identity",
                                f"{part.display_number}: part number not matched to a catalog part",
                                child_id=pid))
        if catalog is not None and (part is None or part.resolved) and catalog.get(pid) is None:
            issues.append(Issue("error", "dangling-reference",
                                f"{name(pid)} ({pid}) is not in the parts catalog", child_id=pid))
    numbers: dict[str, list[str]] = {}
    for p in structure.parts:
        if p.number:
            numbers.setdefault(p.number.casefold(), []).append(p.part_id)
    for key, ids in sorted(numbers.items()):
        if len(ids) > 1:
            issues.append(Issue("error", "duplicate-part-number",
                                f"part number {structure.part(ids[0]).number} is used by {len(ids)} items: "
                                f"{', '.join(sorted(ids))}"))

    # graph
    for cycle in find_cycles(structure):
        issues.append(Issue("error", "cycle", "assembly cycle: " + " -> ".join(name(c) for c in cycle),
                            parent_id=cycle[0], child_id=cycle[1]))
    roots = tuple(roots)
    if roots:
        covered: set[str] = set()
        for r in roots:
            if r not in structure.item_ids:
                issues.append(Issue("error", "unknown-root", f"top-level item {r} is not in the structure",
                                    parent_id=r))
                continue
            covered |= reachable(structure, r)
            try:
                check_depth(structure, r, limits)
            except LimitExceeded as exc:
                issues.append(Issue("error", "too-deep", str(exc), parent_id=r))
            except CycleError:
                pass  # already reported
        for pid in structure.assembly_ids:
            if pid not in covered:
                issues.append(Issue("warning", "orphan-assembly",
                                    f"{name(pid)} has a BOM but is not used by any listed top-level item",
                                    parent_id=pid))
    else:
        for top in structure.top_level_ids:
            try:
                check_depth(structure, top, limits)
            except LimitExceeded as exc:
                issues.append(Issue("error", "too-deep", str(exc), parent_id=top))
            except (CycleError, UnitError):
                pass
    return ValidationReport(tuple(sorted(issues, key=Issue.sort_key)))
