# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Engineering change impact ("Before You Revise") reports over BOM snapshots.

Two reports:

* :func:`revision_impact` - "I am about to revise item X": every searched snapshot that uses X, directly
  or through subassemblies, the revision of X each usage references, quantity per top-level unit, and a
  classification from docs/REQUIREMENTS.md section 9.
* :func:`change_impact` - "this assembly's BOM changes from snapshot A to snapshot B": which top-level
  products that use the assembly are affected and how their summarized quantities change.

Coverage is explicit. A report only knows the snapshots it was given; it lists them and never says an
item is unused anywhere. An affected relationship is a review signal, not proof of incompatibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from .diff import BomDiff, QuantityDelta, compare, compare_rollups
from .errors import BomError, CycleError, LimitExceeded, UnitError
from .graph import where_used
from .snapshot import BomSnapshot
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, UnitRegistry

# Classifications (REQUIREMENTS.md section 9, item 5)
DIRECT = "directly-referenced"
INDIRECT = "indirectly-referenced"
RELEASED = "released-configuration-to-evaluate"
REVIEW = "drawing-bom-review-suggested"
VERIFY = "verification-required"
STALE = "references-older-revision"

COVERAGE_NOTE = ("Only the BOM snapshots listed under coverage were searched. Unloaded, unmanaged or "
                 "unindexed BOMs were not searched, so absence of a usage here is not proof that the item "
                 "is unused. An affected relationship is a review signal, not proof of incompatibility.")


@dataclass(frozen=True)
class CoverageEntry:
    snapshot: str                  # display name
    digest: str
    source_kind: str
    document_id: str
    revision_id: str
    revision_label: str
    released: bool
    searched: bool
    problem: str = ""


@dataclass(frozen=True)
class ImpactEntry:
    snapshot: str
    snapshot_digest: str
    assembly_id: str
    assembly_number: str
    top_level_id: str
    relation: str                  # "direct" | "indirect"
    distance: int
    referenced_revisions: tuple[str, ...]   # revisions of the item named on the using lines ("" = unspecified)
    quantity_per_top_level: Decimal | None
    unit: str
    classifications: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class RevisionImpactReport:
    item_id: str
    item_number: str
    current_revision: str
    entries: tuple[ImpactEntry, ...]
    coverage: tuple[CoverageEntry, ...]
    coverage_note: str = COVERAGE_NOTE

    @property
    def affected_top_level(self) -> tuple[str, ...]:
        return tuple(sorted({e.top_level_id for e in self.entries}))

    @property
    def needs_verification(self) -> bool:
        return any(VERIFY in e.classifications for e in self.entries)


def _coverage(s: BomSnapshot, searched: bool, problem: str = "") -> CoverageEntry:
    return CoverageEntry(s.display_name, s.digest, s.source.kind, s.source.document_id, s.source.revision_id,
                         s.label or s.source.revision_label, s.released, searched, problem)


def revision_impact(snapshots: Sequence[BomSnapshot], item_id: str, *, current_revision: str = "",
                    limits: Limits = DEFAULT_LIMITS, units: UnitRegistry = DEFAULT_UNITS) -> RevisionImpactReport:
    """Where ``item_id`` is used across ``snapshots`` before it is revised.

    ``current_revision`` is the revision being superseded. A usage that pins a different revision is
    classified ``references-older-revision``; an unpinned usage (empty revision) floats to the latest."""
    entries: list[ImpactEntry] = []
    coverage: list[CoverageEntry] = []
    number = item_id
    for snap in sorted(snapshots, key=lambda s: (s.display_name, s.digest)):
        st = snap.structure
        if item_id in st.item_ids:
            number = st.info(item_id).display_number
        try:
            wu = where_used(st, item_id, limits=limits, units=units)
        except BomError as exc:  # pragma: no cover - where_used reports problems in-band
            coverage.append(_coverage(snap, False, str(exc)))
            continue
        coverage.append(_coverage(snap, True, "cycle in structure" if wu.cycles else ""))
        tops = {u.assembly_id for u in wu.usages if u.is_top_level}
        for u in wu.usages:
            if not u.is_top_level and not u.direct:
                continue  # intermediate assemblies are reported through their direct usage / top levels
            revs = tuple(sorted({ln.child_revision for ln in u.via_lines if ln.parent_id == u.assembly_id}
                                if u.direct else set()))
            cls = [DIRECT if u.direct else INDIRECT]
            notes = []
            if snap.released:
                cls.append(RELEASED)
            if u.direct:
                cls.append(REVIEW)
            if u.needs_verification:
                cls.append(VERIFY)
                notes.append("reached through a line whose provenance is inferred or unknown")
            if current_revision and any(r and r != current_revision for r in revs):
                cls.append(STALE)
                notes.append(f"pins revision {', '.join(r for r in revs if r)}, not {current_revision}")
            if u.direct and "" in revs:
                notes.append("revision not specified on the line; it follows the latest revision")
            if u.quantity_per_assembly is None:
                notes.append("quantity unknown (cycle or unit problem in this structure)")
            top_ids = sorted(tops & _above(st, u.assembly_id)) or [u.assembly_id]
            for top in top_ids:
                qty, unit = u.quantity_per_assembly, u.unit
                if top != u.assembly_id:
                    top_usage = next((x for x in wu.usages if x.assembly_id == top), None)
                    qty = top_usage.quantity_per_assembly if top_usage else None
                    unit = top_usage.unit if top_usage else ""
                entries.append(ImpactEntry(snap.display_name, snap.digest, u.assembly_id,
                                           st.info(u.assembly_id).display_number, top,
                                           "direct" if u.direct else "indirect", u.distance, revs, qty, unit,
                                           tuple(cls), tuple(notes)))
    entries.sort(key=lambda e: (e.snapshot, e.relation != "direct", e.distance, e.assembly_number,
                                e.assembly_id, e.top_level_id))
    return RevisionImpactReport(item_id, number, current_revision, tuple(entries), tuple(coverage))


def _above(structure, item_id: str) -> set[str]:
    from .graph import reachable_up
    return reachable_up(structure, item_id)


@dataclass(frozen=True)
class AffectedProduct:
    snapshot: str
    top_level_id: str
    top_level_number: str
    released: bool
    deltas: tuple[QuantityDelta, ...]
    problem: str = ""


@dataclass(frozen=True)
class ChangeImpactReport:
    assembly_id: str
    assembly_number: str
    before: str
    after: str
    diff: BomDiff
    own_deltas: tuple[QuantityDelta, ...]
    affected: tuple[AffectedProduct, ...]
    coverage: tuple[CoverageEntry, ...]
    coverage_note: str = COVERAGE_NOTE


def change_impact(before: BomSnapshot, after: BomSnapshot, context: Sequence[BomSnapshot] = (), *,
                  leaves_only: bool = False, limits: Limits = DEFAULT_LIMITS,
                  units: UnitRegistry = DEFAULT_UNITS) -> ChangeImpactReport:
    """Impact of changing assembly ``before.root_id`` from ``before`` to ``after``.

    For every context snapshot that uses the assembly, the assembly's whole sub-structure is replaced by
    the ``after`` version (what-if, nothing is written) and the summarized quantities of each top-level
    product are compared."""
    if before.root_id != after.root_id:
        raise BomError("before and after must be snapshots of the same assembly")
    asm = before.root_id
    diff = compare(before.structure.restricted_to(asm), after.structure.restricted_to(asm))
    own = compare_rollups(before.structure, asm, after.structure, asm, leaves_only=leaves_only, limits=limits,
                          units=units)
    affected: list[AffectedProduct] = []
    coverage: list[CoverageEntry] = []
    old_sub, new_sub = before.structure.restricted_to(asm), after.structure.restricted_to(asm)
    for snap in sorted(context, key=lambda s: (s.display_name, s.digest)):
        st = snap.structure
        if asm not in st.item_ids or not st.usages(asm):
            coverage.append(_coverage(snap, True))
            continue
        tops = sorted(t for t in _above(st, asm) if not st.usages(t))
        problem = ""
        try:
            base = _replace_subtree(st, asm, old_sub)
            changed = _replace_subtree(st, asm, new_sub)
        except BomError as exc:
            base = changed = None
            problem = f"cannot apply the change in this structure: {exc}"
        coverage.append(_coverage(snap, True, problem))
        for top in tops:
            if base is None or changed is None:
                affected.append(AffectedProduct(snap.display_name, top, st.info(top).display_number,
                                                snap.released, (), problem))
                continue
            try:
                deltas = compare_rollups(base, top, changed, top, leaves_only=leaves_only, limits=limits,
                                         units=units)
                affected.append(AffectedProduct(snap.display_name, top, st.info(top).display_number,
                                                snap.released, deltas))
            except (CycleError, UnitError, LimitExceeded) as exc:
                affected.append(AffectedProduct(snap.display_name, top, st.info(top).display_number,
                                                snap.released, (), str(exc)))
    return ChangeImpactReport(asm, before.structure.info(asm).display_number, before.display_name,
                              after.display_name, diff, own, tuple(affected), tuple(coverage))


def _replace_subtree(structure, asm: str, sub):
    """``structure`` with the BOM of every assembly defined in ``sub`` taken from ``sub``.

    Assemblies only the old context defines below ``asm`` are left alone if something else uses them."""
    from .model import Structure
    keep = [ln for ln in structure.lines if ln.parent_id not in sub.assembly_ids and ln.parent_id != asm]
    merged = Structure.build(keep + list(sub.lines), list(structure.parts) + [
        p for p in sub.parts if structure.part(p.part_id) is None or structure.part(p.part_id) == p])
    return merged


def item_quantity(report: ChangeImpactReport, top_level_id: str, item_id: str) -> QuantityDelta | None:
    for a in report.affected:
        if a.top_level_id == top_level_id:
            for d in a.deltas:
                if d.item_id == item_id:
                    return d
    return None


ZERO = Decimal(0)
