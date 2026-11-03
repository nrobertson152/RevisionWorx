# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: seeded property tests over random structures (deterministic; no extra dependencies).

Each property is checked on many random acyclic structures with shared subassemblies and fractional
quantities. Two independent algorithms must agree, and serialization must be lossless."""
import random
from decimal import Decimal as D

import pytest

from bom_helpers import paths_total, random_dag
from revisionworx.bom import (BomLine, CycleError, Structure, compare, explode, find_cycles, rollup, snapshot_from_json,
                              snapshot_to_json, structure_digest, structure_from_csv, structure_from_json,
                              structure_to_csv, structure_to_json, validate, where_used, BomSnapshot)
from revisionworx.bom.units import DEFAULT_UNITS

SEEDS = range(40)


@pytest.mark.parametrize("seed", SEEDS)
def test_rollup_equals_sum_over_all_paths(seed):
    s, root = random_dag(seed, n_items=14, max_children=3)
    r = rollup(s, root)
    for row in r.rows:
        assert row.total == paths_total(s, root, row.item_id), row.item_id


@pytest.mark.parametrize("seed", SEEDS)
def test_explosion_rows_sum_to_rollup(seed):
    s, root = random_dag(seed, n_items=14, max_children=3)
    sums: dict[str, D] = {}
    for row in explode(s, root).rows:
        sums[row.item_id] = sums.get(row.item_id, D(0)) + row.extended_quantity
    assert sums == {r.item_id: r.total for r in rollup(s, root).rows}


@pytest.mark.parametrize("seed", SEEDS)
def test_scaling_the_root_scales_every_total(seed):
    s, root = random_dag(seed)
    one, many = rollup(s, root), rollup(s, root, D("7.5"))
    assert [r.total * D("7.5") for r in one.rows] == [r.total for r in many.rows]


@pytest.mark.parametrize("seed", SEEDS)
def test_serializations_roundtrip_and_ignore_input_order(seed):
    s, root = random_dag(seed)
    rnd = random.Random(seed)
    lines, parts = list(s.lines), list(s.parts)
    rnd.shuffle(lines)
    rnd.shuffle(parts)
    shuffled = Structure.build(lines, parts)
    assert shuffled == s and structure_digest(shuffled) == structure_digest(s)
    assert structure_from_json(structure_to_json(s)) == s
    assert structure_from_csv(structure_to_csv(s)) == s
    snap = BomSnapshot(s, root, f"r{seed}")
    assert snapshot_from_json(snapshot_to_json(snap)) == snap


@pytest.mark.parametrize("seed", SEEDS)
def test_random_dags_validate_and_back_edges_are_always_caught(seed):
    s, root = random_dag(seed)
    assert find_cycles(s) == [] and "cycle" not in validate(s, roots=[root]).codes()
    rnd = random.Random(seed)
    deep = rnd.choice([r.item_id for r in rollup(s, root).rows])
    looped = s.with_lines(BomLine(deep, root, D(1)))
    cycles = find_cycles(looped)
    assert len(cycles) == 1 and cycles[0][0] == cycles[0][-1]
    assert {root, deep} <= set(cycles[0])
    with pytest.raises(CycleError):
        rollup(looped, root)


@pytest.mark.parametrize("seed", SEEDS)
def test_diff_properties(seed):
    a, root = random_dag(seed)
    b, _ = random_dag(seed + 1000)
    assert compare(a, a).empty
    ab, ba = compare(a, b), compare(b, a)
    assert ab.summary()["added"] == ba.summary()["removed"] and ab.summary()["changed"] == ba.summary()["changed"]
    # every removed line exists in a, every added line in b
    assert all(c.before in a.lines for c in ab.of_kind("removed"))
    assert all(c.after in b.lines for c in ab.of_kind("added"))


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_where_used_agrees_with_rollup(seed):
    s, root = random_dag(seed)
    totals = rollup(s, root).as_dict()
    for item in list(totals)[:5]:
        usage = {u.assembly_id: u for u in where_used(s, item).usages}
        assert usage[root].quantity_per_assembly == totals[item][0]


def test_unit_roundtrip_conversions_are_exact():
    rnd = random.Random(7)
    for _ in range(300):
        q = D(rnd.randrange(1, 10**6)) / D(1000)
        for a, b in [("m", "mm"), ("kg", "g"), ("L", "mL"), ("ft", "in"), ("lb", "oz")]:
            assert DEFAULT_UNITS.convert(DEFAULT_UNITS.convert(q, a, b), b, a) == q


def test_large_structure_stays_linear():
    """20k items, ~40k lines with all quantities 1: rollup and validation complete (no timing claim)."""
    s, root = random_dag(3, n_items=20_000, max_children=2, decimals=False)
    assert len(rollup(s, root).rows) == 19_999
    assert "cycle" not in validate(s, roots=[root]).codes()
