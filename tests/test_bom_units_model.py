# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: exact quantities, units and immutable BOM values."""
from decimal import Decimal as D

import pytest

from bom_helpers import frame, line, part
from revisionworx.bom import (DEFAULT_UNITS, BomLine, PartInfo, QuantityError, Structure, StructureError, Unit,
                              UnitError, UnitRegistry, canonical_text, parse_quantity)
from revisionworx.bom.units import Limits, add, check_line_quantity, multiply, to_quantity


@pytest.mark.parametrize("text,value", [("2", D(2)), ("0.25", D("0.25")), (" 7 ", D(7)), ("10.50", D("10.50")),
                                        ("０７", D(7))])
def test_parse_quantity_accepts_plain_decimals(text, value):
    assert parse_quantity(text) == value


@pytest.mark.parametrize("text,fragment", [("-1", "negative"), ("+1", "without a sign"), ("1,5", "decimal separator"),
                                           ("1e3", "exponent"), ("NaN", "not a finite"), (".5", "both sides"),
                                           ("5.", "both sides"), ("", "empty"), ("two", "not a decimal"),
                                           ("1 000", "not a decimal")])
def test_parse_quantity_refuses_ambiguous_text_with_reason(text, fragment):
    with pytest.raises(QuantityError, match=fragment):
        parse_quantity(text)


def test_floats_are_refused_because_they_are_inexact():
    with pytest.raises(QuantityError, match="float"):
        to_quantity(0.1)
    with pytest.raises(QuantityError, match="boolean"):
        to_quantity(True)
    assert to_quantity(3) == D(3) and to_quantity("0.1") + to_quantity("0.2") == D("0.3")


def test_canonical_text_is_stable():
    assert [canonical_text(D(x)) for x in ("2.50", "100", "1E+2", "0.000", "007")] == ["2.5", "100", "100", "0", "7"]
    with pytest.raises(QuantityError):
        canonical_text(D("NaN"))


def test_unit_conversion_is_exact_or_refused():
    assert DEFAULT_UNITS.convert(D(1), "in", "mm") == D("25.4")
    assert DEFAULT_UNITS.convert(D(2), "lb", "kg") == D("0.90718474")
    assert DEFAULT_UNITS.convert(D(500), "mm", "m") == D("0.5")
    with pytest.raises(QuantityError, match="no exact value"):
        DEFAULT_UNITS.convert(D(1), "mm", "in")  # 1/25.4 does not terminate
    with pytest.raises(UnitError, match="cannot convert"):
        DEFAULT_UNITS.convert(D(1), "kg", "m")


def test_unit_lookup_is_case_insensitive_with_aliases():
    assert DEFAULT_UNITS.canonical("ea") == "EA" and DEFAULT_UNITS.canonical("PCS") == "EA"
    assert DEFAULT_UNITS.canonical("Meter") == "m" and DEFAULT_UNITS.canonical("l") == "L"
    with pytest.raises(UnitError, match="unknown unit 'furlong'"):
        DEFAULT_UNITS.get("furlong")


def test_custom_registry_refuses_duplicates_and_bad_factors():
    reg = UnitRegistry([Unit("EA", "each", "count", D(1), integral=True)])
    reg.register(Unit("PR", "pair", "count", D(2), integral=True))
    assert reg.convert(D(3), "PR", "EA") == D(6)
    with pytest.raises(UnitError):
        reg.register(Unit("ea", "dup", "count", D(1)))
    with pytest.raises(UnitError):
        Unit("X", "bad", "count", D(0))


def test_line_quantity_limits():
    ea = DEFAULT_UNITS.get("EA")
    check_line_quantity(D(5), ea)
    for q, msg in [(D("1.5"), "whole number"), (D(0), "greater than zero"), (D("0.0000001"), "decimal places"),
                   (D(10) ** 10, "per-line limit")]:
        with pytest.raises(QuantityError, match=msg):
            check_line_quantity(q, ea)


def test_totals_are_bounded_and_never_rounded():
    limits = Limits(max_total_quantity=D(1000))
    assert multiply(D(10), D(100), limits) == D(1000)
    from revisionworx.bom import LimitExceeded
    with pytest.raises(LimitExceeded):
        multiply(D(10), D("100.1"), limits)
    with pytest.raises(LimitExceeded):
        add(D(999), D(2), limits)
    with pytest.raises(QuantityError, match="exactly"):
        multiply(D("1." + "1" * 60), D("1." + "1" * 60))  # 120 significant digits > context precision


def test_bom_line_normalises_and_validates():
    ln = BomLine("a", "b", "2.50", unit=" EA ", reference_designators="R10, R2;R1", attributes={"Material": " Steel "})
    assert ln.quantity == D("2.50") and ln.quantity_text == "2.5" and ln.unit == "EA"
    assert ln.reference_designators == ("R1", "R2", "R10")
    assert ln.attributes == (("Material", "Steel"),)
    with pytest.raises(QuantityError):
        BomLine("a", "b", D(-1))
    with pytest.raises(QuantityError):
        BomLine("a", "b", D(0))
    with pytest.raises(StructureError, match="must not be empty"):
        BomLine("", "b", D(1))
    with pytest.raises(StructureError, match="control characters"):
        BomLine("a\x00", "b", D(1))
    with pytest.raises(StructureError, match="provenance"):
        BomLine("a", "b", D(1), provenance="rumour")


def test_part_info_validation():
    p = PartInfo("p1", number=" Ab-1 ")
    assert p.number == "Ab-1" and p.display_number == "Ab-1"
    assert PartInfo("p2").display_number == "p2"
    with pytest.raises(StructureError):
        PartInfo("p3", unit="")


def test_structure_is_order_independent_and_immutable():
    s = frame()
    shuffled = Structure.build(list(reversed(s.lines)), list(reversed(s.parts)))
    assert shuffled == s and shuffled.lines == s.lines
    with pytest.raises(Exception):
        s.lines = ()  # type: ignore[misc]
    s2 = s.with_lines(line("frame", "washer", "8", find_number="3"))
    assert len(s2) == len(s) + 1 and len(s) == 4  # original unchanged


def test_structure_lookups():
    s = frame()
    assert [ln.child_id for ln in s.children("frame")] == ["sub", "bolt"]
    assert {ln.parent_id for ln in s.usages("bolt")} == {"frame", "sub"}
    assert s.top_level_ids == ("frame",) and s.assembly_ids == ("frame", "sub")
    assert s.info("ghost").resolved is False


def test_duplicate_line_ids_and_conflicting_parts_are_refused():
    with pytest.raises(StructureError, match="duplicate line id"):
        Structure.build([line("a", "b", line_id="L1"), line("a", "c", line_id="L1")])
    with pytest.raises(StructureError, match="two different records"):
        Structure.build([], [part("a", "A"), part("a", "B")])


def test_functional_edits():
    s = frame()
    old = s.children("sub")[0]
    s2 = s.replacing_line(old, old.with_changes(quantity=D(5)))
    assert s2.children("sub")[0].quantity == D(5) and s.children("sub")[0].quantity == D(3)
    with pytest.raises(KeyError):
        s.without_line(line("x", "y"))
    s3 = s.with_single_level("sub", [line("sub", "nut", "2")])
    assert [ln.child_id for ln in s3.children("sub")] == ["nut"]
    with pytest.raises(StructureError):
        s.with_single_level("sub", [line("frame", "nut")])
    r = s.restricted_to("sub")
    assert {ln.parent_id for ln in r.lines} == {"sub"} and r.part("frame") is None


def test_merge_requires_consistent_definitions():
    s = frame()
    other = Structure.build([line("pump", "sub", "1")] + list(s.restricted_to("sub").lines), s.parts)
    merged = s.merged_with(other)
    assert set(merged.top_level_ids) == {"frame", "pump"}
    clash = Structure.build([line("sub", "bolt", "9")], [])
    with pytest.raises(StructureError, match="defined differently"):
        s.merged_with(clash)
