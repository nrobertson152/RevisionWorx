# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: cycles, explosion, rollup, where-used and safety limits."""
from decimal import Decimal as D

import pytest

from bom_helpers import frame, line, part, paths_total
from revisionworx.bom import (CycleError, LimitExceeded, Structure, UnitError, explode, find_cycles, rollup,
                              topological_order, where_used)
from revisionworx.bom.graph import check_depth
from revisionworx.bom.units import Limits


def test_explosion_is_depth_first_in_find_number_order_with_extended_quantities():
    ex = explode(frame(), "frame", D(3))
    got = [(r.level, r.item_id, r.extended_quantity, r.unit) for r in ex.rows]
    assert got == [(1, "sub", D(6), "EA"), (2, "bolt", D(18), "EA"), (2, "cable", D(3), "m"),
                   (1, "bolt", D(12), "EA")]
    assert ex.rows[1].path == ("frame", "sub", "bolt")


def test_explosion_max_level_and_cancellation():
    s = frame()
    assert [r.item_id for r in explode(s, "frame", max_level=1).rows] == ["sub", "bolt"]
    big = Structure.build([line("r", f"c{i}") for i in range(2500)])
    with pytest.raises(InterruptedError):
        explode(big, "r", cancelled=lambda: True)


def test_rollup_sums_shared_parts_across_levels_and_converts_units():
    r = rollup(frame(), "frame", D(5))
    assert r.as_dict() == {"bolt": (D(50), "EA"), "cable": (D("5.000"), "m"), "sub": (D(10), "EA")}
    assert {x.item_id: x.occurrences for x in r.rows} == {"bolt": 2, "cable": 1, "sub": 1}
    leaves = rollup(frame(), "frame", leaves_only=True)
    assert [x.item_id for x in leaves.rows] == ["bolt", "cable"]
    assert r.total("nothing") == 0


def test_rollup_matches_brute_force_paths_on_a_diamond():
    s = Structure.build([line("top", "l", "2"), line("top", "r", "3"), line("l", "base", "5"), line("r", "base", "7"),
                         line("base", "screw", "4")])
    r = rollup(s, "top").as_dict()
    assert r["base"][0] == D(2 * 5 + 3 * 7) == paths_total(s, "top", "base")
    assert r["screw"][0] == D(31 * 4) == paths_total(s, "top", "screw")


def test_rollup_scales_on_exponential_path_counts():
    # 30 diamonds (60 levels): 2**30 paths. Rollup is linear; explosion stops at its row limit.
    lines = []
    for k in range(30):
        lines += [line(f"n{k}", f"a{k}"), line(f"n{k}", f"b{k}"), line(f"a{k}", f"n{k + 1}"), line(f"b{k}", f"n{k + 1}")]
    s = Structure.build(lines)
    assert rollup(s, "n0").total("n30") == D(2) ** 30
    with pytest.raises(LimitExceeded, match="rows"):
        explode(s, "n0", limits=Limits(max_explosion_rows=10_000))


def test_cycles_are_detected_with_the_loop_named():
    s = frame().with_lines(line("bolt", "frame"))
    cycles = find_cycles(s)
    assert cycles == [("bolt", "frame", "bolt")] or cycles == [("bolt", "frame", "sub", "bolt")]
    assert cycles[0][0] == cycles[0][-1] == "bolt"
    for fn in (lambda: explode(s, "frame"), lambda: rollup(s, "frame"), lambda: topological_order(s)):
        with pytest.raises(CycleError) as exc:
            fn()
        assert exc.value.cycle[0] == exc.value.cycle[-1]


def test_self_reference_and_multiple_independent_cycles():
    s = Structure.build([line("a", "a"), line("x", "y"), line("y", "x"), line("p", "q")])
    assert find_cycles(s) == [("a", "a"), ("x", "y", "x")]
    assert find_cycles(frame()) == []


def test_cycle_outside_the_root_does_not_block_it():
    s = frame().with_lines(line("x", "y"), line("y", "x"))
    assert rollup(s, "frame").total("bolt") == D(10)


def test_depth_limit():
    s = Structure.build([line(f"n{k}", f"n{k + 1}") for k in range(70)])
    with pytest.raises(LimitExceeded, match="levels deep"):
        check_depth(s, "n0")
    assert check_depth(s, "n0", Limits(max_depth=100)) == 70
    with pytest.raises(LimitExceeded):
        rollup(s, "n0")


def test_total_quantity_limit_raises_instead_of_overflowing():
    s = Structure.build([line(f"n{k}", f"n{k + 1}", "1000") for k in range(6)])
    with pytest.raises(LimitExceeded, match="total-quantity limit"):
        rollup(s, "n0")
    assert rollup(s, "n0", limits=Limits(max_total_quantity=D(10) ** 30)).total("n6") == D(10) ** 18


def test_unit_mismatch_between_line_and_part_is_refused():
    s = Structure.build([line("a", "b", "2", unit="kg")], [part("b", unit="m")])
    with pytest.raises(UnitError):
        rollup(s, "a")


def test_where_used_direct_and_transitive_with_quantities():
    s = frame().with_lines(line("pump", "sub", "1"), line("skid", "pump", "2"))
    wu = where_used(s, "bolt")
    got = {(u.assembly_id, u.direct, u.distance, u.quantity_per_assembly, u.is_top_level) for u in wu.usages}
    assert got == {("frame", True, 1, D(10), True), ("sub", True, 1, D(3), False),
                   ("pump", False, 2, D(3), False), ("skid", False, 3, D(6), True)}
    assert {u.assembly_id for u in wu.top_level} == {"frame", "skid"}
    assert where_used(s, "skid").usages == ()


def test_where_used_survives_cycles_and_reports_them():
    s = frame().with_lines(line("bolt", "frame"))
    wu = where_used(s, "bolt")
    assert wu.cycles and all(u.quantity_per_assembly is None for u in wu.usages)


def test_needs_verification_propagates_from_inferred_lines():
    s = Structure.build([line("a", "b", provenance="inferred"), line("b", "c"), line("a", "d")])
    flags = {r.item_id: r.needs_verification for r in rollup(s, "a").rows}
    assert flags == {"b": True, "c": True, "d": False}
    assert where_used(s, "c").usages[0].needs_verification is False  # b->c itself is declared
    assert [u.needs_verification for u in where_used(s, "c").usages if u.assembly_id == "a"] == [True]
