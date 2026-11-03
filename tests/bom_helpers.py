# SPDX-License-Identifier: GPL-3.0-or-later
"""Builders and seeded random generators for the BOM tests (issue #18). Deterministic: every random
structure comes from ``random.Random(seed)``."""
from __future__ import annotations

import random
from decimal import Decimal

from revisionworx.bom import BomLine, PartInfo, Structure

D = Decimal


def part(pid: str, number: str | None = None, **kw) -> PartInfo:
    return PartInfo(pid, number=number if number is not None else pid.upper(), **kw)


def line(parent: str, child: str, qty="1", **kw) -> BomLine:
    return BomLine(parent, child, Decimal(qty) if isinstance(qty, str) else qty, **kw)


def frame() -> Structure:
    """FRAME (top) -> 2 x SUB, 4 x BOLT; SUB -> 3 x BOLT, 500 mm CABLE (cable is stocked in metres)."""
    parts = [part("frame", "FRAME-100", description="Welded frame"), part("sub", "SUB-200", description="Pump sub"),
             part("bolt", "BOLT-M8", description="Hex bolt M8x20"), part("cable", "CABLE-3C", unit="m")]
    lines = [line("frame", "sub", "2", find_number="1"),
             line("frame", "bolt", "4", find_number="2", reference_designators="B1 B2 B3 B4"),
             line("sub", "bolt", "3", find_number="1"),
             line("sub", "cable", "500", unit="mm", find_number="2")]
    return Structure.build(lines, parts)


def random_dag(seed: int, n_items: int = 24, max_children: int = 3, decimals: bool = True) -> tuple[Structure, str]:
    """A random acyclic structure with shared subassemblies and a single root ``i0``.

    Items are numbered i0..in; an edge only goes from a lower to a higher index, so it is acyclic. Every
    item i>0 gets at least one parent so the whole graph is reachable from i0."""
    rnd = random.Random(seed)
    ids = [f"i{k}" for k in range(n_items)]
    lines: dict[tuple[str, str], BomLine] = {}
    for k in range(1, n_items):
        p = rnd.randrange(0, k)
        lines[(ids[p], ids[k])] = None  # type: ignore
    for k in range(n_items):
        for _ in range(rnd.randrange(0, max_children)):
            c = rnd.randrange(k + 1, n_items) if k + 1 < n_items else None
            if c is not None:
                lines[(ids[k], ids[c])] = None  # type: ignore
    out = []
    for n, (p, c) in enumerate(sorted(lines)):
        if decimals and rnd.random() < 0.3:
            q = Decimal(rnd.randrange(1, 9)) / Decimal(4)   # quarters: exact
        else:
            q = Decimal(rnd.randrange(1, 4)) if decimals else Decimal(1)
        out.append(BomLine(p, c, q, unit="EA" if q == q.to_integral_value() else "kg",
                           find_number=str(10 * (n % 7 + 1))))
    units_of: dict[str, set[str]] = {}
    for ln in out:
        units_of.setdefault(ln.child_id, set()).add(ln.unit)
    parts = {i: PartInfo(i, number=f"PN-{i[1:]:0>4}", unit="kg" if units_of.get(i) == {"kg"} else "EA") for i in ids}
    # a child with both EA and kg lines: make all its lines integral EA
    fixed = []
    for ln in out:
        p = parts[ln.child_id]
        if p.unit == "EA" and ln.unit != "EA":
            ln = ln.with_changes(quantity=ln.quantity.to_integral_value() or Decimal(1), unit="EA")
        fixed.append(ln)
    return Structure.build(fixed, parts.values()), "i0"


def paths_total(structure: Structure, root: str, item: str, qty: Decimal = Decimal(1)) -> Decimal:
    """Reference rollup by brute-force path enumeration (independent of the topological algorithm)."""
    total = Decimal(0)
    stack = [(root, qty)]
    while stack:
        node, q = stack.pop()
        for ln in structure.children(node):
            ext = q * ln.quantity
            if ln.child_id == item:
                total += ext
            stack.append((ln.child_id, ext))
    return total
