# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: revision-aware BOM comparison, change impact and Before You Revise reports."""
from decimal import Decimal as D

import pytest

from bom_helpers import frame, line, part
from revisionworx.bom import (BomError, BomSnapshot, SourceRef, Structure, change_impact, compare, compare_rollups,
                              revision_impact)
from revisionworx.bom import csvio
from revisionworx.bom.impact import DIRECT, INDIRECT, RELEASED, REVIEW, STALE, VERIFY


def test_identical_structures_have_no_differences():
    assert compare(frame(), frame()).empty
    assert compare_rollups(frame(), "frame", frame(), "frame") == ()


def test_line_changes_are_classified_field_by_field():
    a = frame()
    b = (a.replacing_line(a.children("sub")[0], a.children("sub")[0].with_changes(quantity=D(4), child_revision="C"))
          .without_line(a.children("sub")[1])
          .with_lines(line("sub", "nut", "4", find_number="3")))
    d = compare(a, b)
    assert d.summary() == {"added": 1, "removed": 1, "changed": 1, "parts_changed": 0}
    changed = d.of_kind("changed")[0]
    assert changed.child_id == "bolt" and changed.field_names == ("quantity", "child_revision")
    assert [(f.before, f.after) for f in changed.fields] == [("3", "4"), ("", "C")]
    assert d.of_kind("removed")[0].child_id == "cable" and d.of_kind("added")[0].child_id == "nut"


def test_renumbered_find_number_is_a_change_not_remove_plus_add():
    a = frame()
    b = a.replacing_line(a.children("frame")[1], a.children("frame")[1].with_changes(find_number="20"))
    d = compare(a, b)
    assert [(c.kind, c.field_names) for c in d.changes] == [("changed", ("find_number",))]


def test_line_ids_win_matching_and_reparenting_shows_as_move():
    a = Structure.build([line("x", "p", line_id="L1"), line("y", "q", line_id="L2")])
    b = Structure.build([line("z", "p", line_id="L1"), line("y", "q", "2", line_id="L2")])
    d = compare(a, b)
    assert sorted((c.kind, c.parent_id) for c in d.changes) == [("added", "z"), ("changed", "y"), ("removed", "x")]


def test_compare_is_antisymmetric():
    a = frame()
    b = a.with_lines(line("frame", "bolt", "1", find_number="9")).without_line(a.children("sub")[1])
    ab, ba = compare(a, b), compare(b, a)
    flip = {"added": "removed", "removed": "added", "changed": "changed"}
    assert sorted((flip[c.kind], c.parent_id, c.child_id) for c in ab.changes) == \
        sorted((c.kind, c.parent_id, c.child_id) for c in ba.changes)


def test_part_record_changes_are_reported():
    a = frame()
    b = a.with_parts(part("bolt", "BOLT-M8", description="Hex bolt M8x25"))
    d = compare(a, b)
    assert d.changes == () and [(p.kind, p.fields[0].field) for p in d.part_changes] == [("changed", "description")]


def test_summarized_quantity_differences():
    a = frame()
    b = a.replacing_line(a.children("frame")[0], a.children("frame")[0].with_changes(quantity=D(3)))
    deltas = {x.item_id: (x.before, x.after, x.delta, x.kind) for x in compare_rollups(a, "frame", b, "frame")}
    assert deltas == {"bolt": (D(10), D(13), D(3), "changed"), "cable": (D(1), D("1.5"), D("0.5"), "changed"),
                      "sub": (D(2), D(3), D(1), "changed")}


def test_unit_change_in_rollup_converts_or_splits():
    a = Structure.build([line("a", "w", "500", unit="mm")], [part("w", unit="mm")])
    b = Structure.build([line("a", "w", "0.5", unit="m")], [part("w", unit="m")])
    assert compare_rollups(a, "a", b, "a") == ()  # same physical quantity
    c = Structure.build([line("a", "w", "1", unit="kg")], [part("w", unit="kg")])
    kinds = [(d.unit, d.kind) for d in compare_rollups(a, "a", c, "a")]
    assert kinds == [("mm", "removed"), ("kg", "added")]


# ------------------------------------------------------------------------------- change impact
def products():
    """Two products share SUB; a third does not use it."""
    s = frame()
    pump = Structure.build([line("pump", "sub", "1"), line("pump", "motor", "1")] + list(s.restricted_to("sub").lines),
                           s.parts + (part("pump", "PUMP-1"), part("motor", "MOTOR")))
    other = Structure.build([line("cart", "wheel", "4")], [part("cart", "CART"), part("wheel", "WHEEL")])
    return (BomSnapshot(s, "frame", "C", SourceRef("workspace-revision", "doc_f", "rev_f", "C", 3, "aa" * 32,
                                                    state="released")),
            BomSnapshot(pump, "pump", "A"), BomSnapshot(other, "cart", "A"))


def test_change_impact_reports_every_affected_product_with_deltas():
    f, pump, cart = products()
    sub_old = BomSnapshot(f.structure.restricted_to("sub"), "sub", "B")
    new_lines = [line("sub", "bolt", "5", find_number="1"), line("sub", "cable", "500", unit="mm", find_number="2"),
                 line("sub", "washer", "5", find_number="3")]
    sub_new = BomSnapshot(Structure.build(new_lines, f.structure.parts + (part("washer", "W-8"),)), "sub", "C")
    rep = change_impact(sub_old, sub_new, [f, pump, cart])
    assert rep.diff.summary()["added"] == 1 and rep.diff.summary()["changed"] == 1
    assert {(d.item_id, d.before, d.after) for d in rep.own_deltas} == {("bolt", D(3), D(5)), ("washer", D(0), D(5))}
    got = {a.top_level_id: {(d.item_id, d.delta) for d in a.deltas} for a in rep.affected}
    assert got == {"frame": {("bolt", D(4)), ("washer", D(10))}, "pump": {("bolt", D(2)), ("washer", D(5))}}
    assert [a.released for a in rep.affected if a.top_level_id == "frame"] == [True]
    assert {c.snapshot for c in rep.coverage} == {"FRAME-100 C", "PUMP-1 A", "CART A"}
    assert "not proof" in rep.coverage_note
    text = csvio.change_impact_to_csv(rep, excel=False).decode()
    assert "COVERAGE" in text and "NOTE" in text


def test_change_impact_requires_the_same_assembly():
    f, pump, _ = products()
    with pytest.raises(BomError):
        change_impact(f, pump)


# ------------------------------------------------------------------------- Before You Revise
def test_revision_impact_classifies_usages():
    f, pump, cart = products()
    pinned = f.structure.replacing_line(f.structure.children("frame")[1],
                                        f.structure.children("frame")[1].with_changes(child_revision="A"))
    inferred = pump.structure.replacing_line(pump.structure.children("sub")[0],
                                             pump.structure.children("sub")[0].with_changes(provenance="inferred"))
    snaps = [BomSnapshot(pinned, "frame", "C", f.source), BomSnapshot(inferred, "pump", "A"), cart]
    rep = revision_impact(snaps, "bolt", current_revision="B")
    assert rep.item_number == "BOLT-M8" and rep.affected_top_level == ("frame", "pump")
    by = {(e.snapshot, e.assembly_id, e.top_level_id): e for e in rep.entries}
    direct_frame = by[("FRAME-100 C", "frame", "frame")]
    assert set(direct_frame.classifications) == {DIRECT, RELEASED, REVIEW, STALE}
    assert direct_frame.referenced_revisions == ("A",) and direct_frame.quantity_per_top_level == D(10)
    sub_in_pump = by[("PUMP-1 A", "sub", "pump")]
    assert VERIFY in sub_in_pump.classifications and sub_in_pump.quantity_per_top_level == D(3)
    top_pump = by[("PUMP-1 A", "pump", "pump")]
    assert top_pump.relation == "indirect" and INDIRECT in top_pump.classifications
    assert rep.needs_verification
    assert [c.searched for c in rep.coverage] == [True, True, True]


def test_revision_impact_never_claims_absence():
    _, _, cart = products()
    rep = revision_impact([cart], "bolt")
    assert rep.entries == () and len(rep.coverage) == 1 and "not proof" in rep.coverage_note
    text = csvio.revision_impact_to_csv(rep, excel=False).decode()
    assert "COVERAGE,CART A" in text and "absence of a usage here is not proof" in text


def test_revision_impact_with_a_cycle_still_reports():
    f, _, _ = products()
    cyc = BomSnapshot(f.structure.with_lines(line("bolt", "frame")), "frame", "X")
    rep = revision_impact([cyc], "bolt")
    assert [(e.assembly_id, e.relation) for e in rep.entries] == [("frame", "direct"), ("sub", "direct")]
    assert all(e.quantity_per_top_level is None and any("quantity unknown" in n for n in e.notes)
               for e in rep.entries)
    assert rep.coverage[0].problem == "cycle in structure"
