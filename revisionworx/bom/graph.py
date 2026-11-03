# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assembly graph algorithms: cycle detection, explosion, quantity rollup and where-used.

All traversals are iterative (no recursion limit), cycle-safe, deterministic and bounded by
:class:`~revisionworx.bom.units.Limits`. Rollup runs in O(items + lines) by propagating quantities in
topological order, so shared subassemblies never cause exponential work. Only the *indented* explosion,
which lists every path, can grow exponentially; it stops at ``max_explosion_rows``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .errors import CycleError, LimitExceeded, UnitError
from .model import NEEDS_VERIFICATION, BomLine, Structure
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, UnitRegistry, add, canonical_text, multiply

ONE = Decimal(1)


# ---------------------------------------------------------------------------------------------- cycles
def find_cycles(structure: Structure) -> list[tuple[str, ...]]:
    """Every strongly connected group of assemblies that contains a loop, as one representative cycle
    each (``(a, b, a)``), rotated to start at its smallest id and sorted. Empty when the graph is acyclic."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = 0
    nodes = structure.item_ids
    succ = {n: sorted({ln.child_id for ln in structure.children(n)}) for n in nodes}
    for start in nodes:
        if start in index:
            continue
        work = [(start, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            children = succ.get(node, [])
            while i < len(children):
                child = children[i]
                i += 1
                if child not in index:
                    work.append((node, i))
                    work.append((child, 0))
                    recurse = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if recurse:
                continue
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                sccs.append(comp)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    cycles = []
    for comp in sccs:
        members = set(comp)
        if len(comp) == 1 and comp[0] not in succ.get(comp[0], []):
            continue
        cycles.append(_cycle_in(min(members), members, succ))
    return sorted(cycles)


def _cycle_in(start: str, members: set[str], succ: dict[str, list[str]]) -> tuple[str, ...]:
    """A shortest cycle through ``start`` inside one strongly connected component (BFS)."""
    prev: dict[str, str] = {}
    frontier = [start]
    seen = {start}
    while frontier:
        nxt = []
        for n in frontier:
            for c in succ.get(n, []):
                if c not in members:
                    continue
                if c == start:
                    path = [n]
                    while path[-1] != start:
                        path.append(prev[path[-1]])
                    path.reverse()
                    return tuple(path) + (start,)
                if c not in seen:
                    seen.add(c)
                    prev[c] = n
                    nxt.append(c)
        frontier = nxt
    raise AssertionError("component has no cycle through its minimum")  # pragma: no cover


def reachable(structure: Structure, root_id: str) -> set[str]:
    seen, stack = {root_id}, [root_id]
    while stack:
        for ln in structure.children(stack.pop()):
            if ln.child_id not in seen:
                seen.add(ln.child_id)
                stack.append(ln.child_id)
    return seen


def topological_order(structure: Structure, root_id: str | None = None) -> list[str]:
    """Items ordered parents-before-children (deterministic). Raises :class:`CycleError`."""
    nodes = sorted(reachable(structure, root_id)) if root_id is not None else list(structure.item_ids)
    node_set = set(nodes)
    indeg = {n: 0 for n in nodes}
    for n in nodes:
        for c in {ln.child_id for ln in structure.children(n)}:
            if c in node_set:
                indeg[c] += 1
    ready = sorted(n for n in nodes if indeg[n] == 0)
    order: list[str] = []
    import heapq
    heapq.heapify(ready)
    while ready:
        n = heapq.heappop(ready)
        order.append(n)
        for c in sorted({ln.child_id for ln in structure.children(n)}):
            indeg[c] -= 1
            if indeg[c] == 0:
                heapq.heappush(ready, c)
    if len(order) != len(nodes):
        sub = structure.restricted_to(root_id) if root_id is not None else structure
        cycles = find_cycles(sub)
        raise CycleError(cycles[0] if cycles else ("?",))
    return order


def check_depth(structure: Structure, root_id: str, limits: Limits = DEFAULT_LIMITS) -> int:
    """Longest chain of levels below ``root_id``. Raises on a cycle or past ``limits.max_depth``."""
    order = topological_order(structure, root_id)
    depth = {root_id: 0}
    deepest = 0
    for n in order:
        d = depth.get(n)
        if d is None:
            continue
        for ln in structure.children(n):
            nd = d + 1
            if nd > limits.max_depth:
                raise LimitExceeded(f"assembly {structure.info(root_id).display_number} is more than "
                                    f"{limits.max_depth} levels deep")
            if nd > depth.get(ln.child_id, -1):
                depth[ln.child_id] = nd
                deepest = max(deepest, nd)
    return deepest


# ---------------------------------------------------------------------------------------- explosion
@dataclass(frozen=True)
class ExplosionRow:
    """One occurrence in an indented BOM. ``extended_quantity`` is per one of the root's unit."""

    level: int
    path: tuple[str, ...]          # item ids from the root to this item, inclusive
    line: BomLine
    extended_quantity: Decimal
    unit: str

    @property
    def item_id(self) -> str:
        return self.line.child_id


@dataclass(frozen=True)
class Explosion:
    root_id: str
    root_quantity: Decimal
    rows: tuple[ExplosionRow, ...]


def _item_unit(structure: Structure, item_id: str, units: UnitRegistry, fallback: str) -> str:
    p = structure.part(item_id)
    unit = p.unit if p is not None else fallback
    return units.canonical(unit) if units.known(unit) else unit


def _line_qty_in_child_unit(structure: Structure, ln: BomLine, units: UnitRegistry) -> tuple[Decimal, str]:
    """The line quantity expressed in the child's own unit (so totals for one item add up)."""
    target = _item_unit(structure, ln.child_id, units, ln.unit)
    if units.known(ln.unit) and units.known(target):
        return units.convert(ln.quantity, ln.unit, target), units.canonical(target)
    if ln.unit.casefold() == target.casefold():
        return ln.quantity, target
    raise UnitError(f"line {ln.parent_id} -> {ln.child_id}: unit {ln.unit!r} does not match the part's unit "
                    f"{target!r}")


def explode(structure: Structure, root_id: str, quantity: Decimal = ONE, *, limits: Limits = DEFAULT_LIMITS,
            units: UnitRegistry = DEFAULT_UNITS, max_level: int | None = None,
            cancelled: Callable[[], bool] | None = None) -> Explosion:
    """Indented (multi-level) BOM of ``root_id``: depth-first, children in stable line order.

    Each row's ``extended_quantity`` = parent's extended quantity x line quantity (in the child's unit).
    Raises :class:`CycleError` if the root contains itself, :class:`LimitExceeded` past the depth or row
    limits, and :class:`InterruptedError` if ``cancelled()`` returns true."""
    check_depth(structure, root_id, limits)
    rows: list[ExplosionRow] = []
    # Stack of (line, level, path, parent_extended)
    stack: list[tuple[BomLine, int, tuple[str, ...], Decimal]] = [
        (ln, 1, (root_id,), quantity) for ln in reversed(structure.children(root_id))]
    while stack:
        ln, level, path, parent_qty = stack.pop()
        if ln.child_id in path:  # defensive: check_depth already refused cycles
            raise CycleError(path[path.index(ln.child_id):] + (ln.child_id,))
        q, unit = _line_qty_in_child_unit(structure, ln, units)
        ext = multiply(parent_qty, q, limits, what=f"extended quantity of {structure.info(ln.child_id).display_number}")
        child_path = path + (ln.child_id,)
        rows.append(ExplosionRow(level, child_path, ln, ext, unit))
        if len(rows) > limits.max_explosion_rows:
            raise LimitExceeded(f"indented BOM of {structure.info(root_id).display_number} has more than "
                                f"{limits.max_explosion_rows} rows; use the summarized rollup instead")
        if cancelled is not None and len(rows) % 1000 == 0 and cancelled():
            raise InterruptedError("explosion cancelled")
        if max_level is None or level < max_level:
            for child_line in reversed(structure.children(ln.child_id)):
                stack.append((child_line, level + 1, child_path, ext))
    return Explosion(root_id, quantity, tuple(rows))


# ------------------------------------------------------------------------------------------- rollup
@dataclass(frozen=True)
class RollupRow:
    """Total quantity of one item needed for ``root_quantity`` of the root."""

    item_id: str
    total: Decimal
    unit: str
    occurrences: int               # number of BOM lines (in the reachable structure) that use the item
    is_assembly: bool
    min_level: int
    needs_verification: bool       # reached through a line whose provenance is inferred/unknown


@dataclass(frozen=True)
class Rollup:
    root_id: str
    root_quantity: Decimal
    rows: tuple[RollupRow, ...]

    def total(self, item_id: str) -> Decimal:
        for r in self.rows:
            if r.item_id == item_id:
                return r.total
        return Decimal(0)

    def as_dict(self) -> dict[str, tuple[Decimal, str]]:
        return {r.item_id: (r.total, r.unit) for r in self.rows}


def rollup(structure: Structure, root_id: str, quantity: Decimal = ONE, *, leaves_only: bool = False,
           limits: Limits = DEFAULT_LIMITS, units: UnitRegistry = DEFAULT_UNITS) -> Rollup:
    """Summarized (flattened) quantities of every item under ``root_id``.

    Runs in topological order: total(child) += total(parent) x line quantity. Exact; refuses cycles,
    unit mismatches and totals over ``limits.max_total_quantity``."""
    check_depth(structure, root_id, limits)
    order = topological_order(structure, root_id)
    totals: dict[str, Decimal] = {root_id: quantity}
    unit_of: dict[str, str] = {}
    occurrences: dict[str, int] = {}
    level: dict[str, int] = {root_id: 0}
    unverified: dict[str, bool] = {root_id: False}
    for n in order:
        t = totals.get(n)
        if t is None:
            continue
        for ln in structure.children(n):
            q, unit = _line_qty_in_child_unit(structure, ln, units)
            prev_unit = unit_of.setdefault(ln.child_id, unit)
            if prev_unit != unit:  # pragma: no cover - _line_qty_in_child_unit normalises to one unit
                raise UnitError(f"{ln.child_id}: units {prev_unit} and {unit} cannot be summed")
            name = structure.info(ln.child_id).display_number
            contribution = multiply(t, q, limits, what=f"total quantity of {name}")
            totals[ln.child_id] = add(totals.get(ln.child_id, Decimal(0)), contribution, limits,
                                      what=f"total quantity of {name}")
            occurrences[ln.child_id] = occurrences.get(ln.child_id, 0) + 1
            level[ln.child_id] = min(level.get(ln.child_id, 10**9), level[n] + 1)
            flag = unverified[n] or ln.provenance in NEEDS_VERIFICATION
            unverified[ln.child_id] = unverified.get(ln.child_id, False) or flag
    rows = []
    for item, total in totals.items():
        if item == root_id:
            continue
        is_asm = structure.is_assembly(item)
        if leaves_only and is_asm:
            continue
        rows.append(RollupRow(item, total, unit_of[item], occurrences[item], is_asm, level[item],
                              unverified[item]))
    rows.sort(key=lambda r: (natural_key_for(structure, r.item_id), r.item_id))
    return Rollup(root_id, quantity, tuple(rows))


def natural_key_for(structure: Structure, item_id: str) -> tuple:
    from .model import natural_key
    return natural_key(structure.info(item_id).display_number)


# ---------------------------------------------------------------------------------------- where-used
@dataclass(frozen=True)
class Usage:
    """``item`` is used (directly or through subassemblies) by ``assembly_id``."""

    assembly_id: str
    direct: bool
    distance: int                  # 1 = direct parent
    via_lines: tuple[BomLine, ...]  # the direct lines when direct, otherwise the lines into the item
    quantity_per_assembly: Decimal | None   # None when it cannot be computed (cycle, unit mismatch)
    unit: str
    needs_verification: bool
    is_top_level: bool


@dataclass(frozen=True)
class WhereUsed:
    item_id: str
    usages: tuple[Usage, ...]
    cycles: tuple[tuple[str, ...], ...]   # cycles met on the way up; quantities there are None

    @property
    def direct(self) -> tuple[Usage, ...]:
        return tuple(u for u in self.usages if u.direct)

    @property
    def top_level(self) -> tuple[Usage, ...]:
        return tuple(u for u in self.usages if u.is_top_level)


def where_used(structure: Structure, item_id: str, *, limits: Limits = DEFAULT_LIMITS,
               units: UnitRegistry = DEFAULT_UNITS) -> WhereUsed:
    """Every assembly that uses ``item_id`` directly or transitively, with the quantity per assembly.

    Never raises on cycles: assemblies on a cycle are reported with ``quantity_per_assembly=None``."""
    distance: dict[str, int] = {}
    frontier = [item_id]
    d = 0
    while frontier:
        d += 1
        if d > limits.max_depth + 1:
            break
        nxt = []
        for n in frontier:
            for ln in structure.usages(n):
                if ln.parent_id not in distance and ln.parent_id != item_id:
                    distance[ln.parent_id] = d
                    nxt.append(ln.parent_id)
        frontier = sorted(set(nxt))
    cycles = tuple(c for c in find_cycles(structure) if item_id in c or any(a in c for a in distance))
    direct_lines = structure.usages(item_id)
    usages = []
    for asm in sorted(distance, key=lambda a: (distance[a], natural_key_for(structure, a), a)):
        lines = tuple(ln for ln in direct_lines if ln.parent_id == asm)
        qty: Decimal | None
        unit = ""
        unverified = any(ln.provenance in NEEDS_VERIFICATION for ln in lines)
        try:
            r = rollup(structure, asm, limits=limits, units=units)
            row = next((x for x in r.rows if x.item_id == item_id), None)
            qty = row.total if row else None
            unit = row.unit if row else ""
            unverified = unverified or bool(row and row.needs_verification)
        except (CycleError, UnitError, LimitExceeded):
            qty = None
        usages.append(Usage(asm, bool(lines), distance[asm], lines or tuple(
            ln for ln in direct_lines if asm in reachable_up(structure, ln.parent_id)), qty, unit, unverified,
            not structure.usages(asm)))
    return WhereUsed(item_id, tuple(usages), cycles)


def reachable_up(structure: Structure, item_id: str) -> set[str]:
    """``item_id`` and every assembly above it."""
    seen, stack = {item_id}, [item_id]
    while stack:
        for ln in structure.usages(stack.pop()):
            if ln.parent_id not in seen:
                seen.add(ln.parent_id)
                stack.append(ln.parent_id)
    return seen


def describe_quantity(q: Decimal | None, unit: str) -> str:
    return "unknown" if q is None else f"{canonical_text(q)} {unit}".strip()
