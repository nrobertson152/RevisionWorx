# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18 (base catalog): parts, numbering rules, single-level BOM editing, imports and store semantics
on the in-memory store. The workspace store is exercised in test_bom_catalog_workspace.py."""
from decimal import Decimal as D

import pytest

from revisionworx.bom import (CatalogError, ChangeControlWhereUsed, DocumentLink, EditRejected, ManufacturerRef,
                              MemoryCatalogStore, NumberingRule, Part, PartsCatalogService, StoreConflict, StoreError,
                              Structure, VendorRef, export_parts, import_parts, rollup, structure_from_csv, validate)
from revisionworx.bom.errors import CsvImportError, StructureError
from revisionworx.bom.store import Commit, Put


@pytest.fixture
def svc():
    return PartsCatalogService(MemoryCatalogStore(), clock=lambda: "2026-11-03T00:00:00+00:00",
                               actor=lambda: {"user": "eng", "host": "pc"})


def make(svc, *numbers, **kw):
    return [svc.create_part(n, **kw) for n in numbers]


# --------------------------------------------------------------------------------------- parts
def test_part_values_validate_and_normalise():
    p = Part("prt_" + "a" * 32, " Ab-1 ", manufacturers=(ManufacturerRef("Acme", "X1"), ManufacturerRef("Bolt Co", "B")),
             vendors=(VendorRef("Shop", "S1"),), documents=(DocumentLink("doc_1", "rev_1"),), attributes={"k": " v "})
    assert p.number == "Ab-1" and p.key == "ab-1" and p.attributes == (("k", "v"),)
    assert Part.from_record(p.to_record()) == p and p.info().resolved
    for bad in [dict(part_id="P1"), dict(number=""), dict(number="a\nb"), dict(kind="widget"), dict(status="gone")]:
        args = dict(part_id="prt_" + "b" * 32, number="N") | bad
        with pytest.raises(StructureError):
            Part(**args)
    with pytest.raises(StructureError, match="duplicate manufacturer"):
        Part("prt_" + "c" * 32, "N", manufacturers=(ManufacturerRef("A", "1"), ManufacturerRef("a", "1")))
    with pytest.raises(StructureError, match="malformed"):
        Part.from_record({"id": "prt_" + "d" * 32})


def test_create_find_update_and_audit_stamps(svc):
    p = svc.create_part("FRAME-100", description="Frame", kind="assembly")
    assert svc.find_by_number("frame-100") == p and svc.part(p.part_id) == p
    rec = svc.store.get("part", p.part_id)
    assert rec["created_by"] == {"user": "eng", "host": "pc"} and rec["_rev"] == 1
    p2 = svc.update_part(p.part_id, 1, description="Welded frame", revision="B")
    assert svc.part(p.part_id).description == "Welded frame" and p2.revision == "B"
    with pytest.raises(StoreConflict):
        svc.update_part(p.part_id, 1, description="stale")       # read before the last change
    with pytest.raises(StructureError, match="unknown kind"):
        svc.update_part(p.part_id, 2, kind="widget")
    assert svc.part_rev(p.part_id) == 2


def test_part_numbers_are_unique_case_insensitively(svc):
    a, b = make(svc, "A-1", "B-1")
    with pytest.raises(CatalogError, match="already used"):
        svc.create_part("a-1")
    with pytest.raises(CatalogError, match="already used"):
        svc.update_part(b.part_id, 1, number="A-1")
    svc.update_part(a.part_id, 1, number="a-1")      # case-only change keeps the claim
    assert svc.find_by_number("A-1").number == "a-1"
    svc.update_part(a.part_id, 2, number="A-2")       # renumber frees the old number
    assert svc.find_by_number("a-1") is None and svc.create_part("A-1").number == "A-1"


def test_concurrent_creates_of_one_number_cannot_both_commit():
    store = MemoryCatalogStore()
    s1, s2 = PartsCatalogService(store), PartsCatalogService(store)
    # s2 checked "free" before s1 committed: emulate by committing the claim between check and commit
    s1.create_part("X-1")
    with pytest.raises((CatalogError, StoreConflict)):
        s2.create_part("x-1")
    p = Part("prt_" + "f" * 32, "X-1")
    from revisionworx.bom.store import partnum_id
    with pytest.raises(StoreConflict):      # even bypassing the service check, the claim record refuses it
        store.commit(Commit("raw", [Put("part", p.part_id, p.to_record(), 0),
                                    Put("partnum", partnum_id("X-1"), {"id": partnum_id("X-1")}, 0)]))
    assert len(s1.parts()) == 1


def test_numbering_rule_skips_reserved_and_taken_numbers(svc):
    svc.save_rule(NumberingRule("std", prefix="P-", digits=4, suffix="-A", reserved=((2, 4),)))
    svc.create_part("P-0005-A")                         # a manual entry inside the sequence
    assert svc.preview_number("std") == "P-0001-A"
    got = [svc.create_part(rule_id="std").number for _ in range(3)]
    assert got == ["P-0001-A", "P-0006-A", "P-0007-A"]
    assert svc.create_part("LEGACY-9").manual_number is True
    assert svc.find_by_number("P-0005-A").manual_number is False
    with pytest.raises(CatalogError, match="either"):
        svc.create_part("N", rule_id="std")
    with pytest.raises(CatalogError, match="no numbering rule"):
        svc.create_part(rule_id="nope")
    tiny = NumberingRule("tiny", digits=1, start=9)
    svc.save_rule(tiny)
    svc.create_part(rule_id="tiny")
    with pytest.raises(StructureError, match="exhausted"):
        svc.create_part(rule_id="tiny")


def test_numbering_rule_values():
    r = NumberingRule("r", prefix="AB", digits=3)
    assert r.format(7) == "AB007" and r.matches("ab007") and r.sequence_of("AB012") == 12 and not r.matches("AB12")
    for bad in [dict(rule_id="Bad Id"), dict(digits=0), dict(start=-1), dict(reserved=((5, 1),))]:
        with pytest.raises(StructureError):
            NumberingRule(**(dict(rule_id="r") | bad))
    assert NumberingRule.from_record(r.to_record()) == r


def test_delete_and_obsolete_rules(svc):
    frame, bolt, spare = make(svc, "FRAME", "BOLT", "SPARE")
    svc.add_line(frame.part_id, bolt.part_id, 4)
    with pytest.raises(CatalogError, match="used in BOMs"):
        svc.delete_part(bolt.part_id, 1)
    svc.delete_part(spare.part_id, 1)
    assert svc.part(spare.part_id) is None and svc.find_by_number("SPARE") is None
    svc.set_status(bolt.part_id, "obsolete", 1)
    assert svc.part(bolt.part_id).obsolete
    with pytest.raises(CatalogError, match="obsolete"):
        svc.add_line(frame.part_id, bolt.part_id, 1, find_number="2")
    assert rollup(svc.structure(), frame.part_id).total(bolt.part_id) == D(4)  # existing usage still resolves


# ----------------------------------------------------------------------------------- BOM editing
def test_add_update_remove_lines(svc):
    frame, sub, bolt, hose = make(svc, "FRAME", "SUB", "BOLT", "HOSE")
    svc.update_part(hose.part_id, 1, unit="m")
    l1 = svc.add_line(frame.part_id, sub.part_id, 2, find_number="10")
    svc.add_line(sub.part_id, bolt.part_id, "3", find_number="10", reference_designators="B1 B2 B3")
    svc.add_line(frame.part_id, hose.part_id, "1500", unit="mm", find_number="20")
    assert l1.line_id.startswith("bln_") and svc.line_rev(l1.line_id) == 1
    assert rollup(svc.structure(), frame.part_id).as_dict()[hose.part_id] == (D("1.500"), "m")
    svc.update_line(l1.line_id, 1, quantity="4", note="doubled")
    assert rollup(svc.structure(), frame.part_id).total(bolt.part_id) == D(12)
    with pytest.raises(StoreConflict):
        svc.update_line(l1.line_id, 1, quantity="5")
    with pytest.raises(CatalogError, match="cannot change"):
        svc.update_line(l1.line_id, 2, child_id=bolt.part_id)
    svc.remove_line(l1.line_id, 2)
    assert [ln.child_id for ln in svc.bom(frame.part_id)] == [hose.part_id]
    with pytest.raises(CatalogError):
        svc.remove_line(l1.line_id, 2)


@pytest.mark.parametrize("edit,code", [
    (lambda s, p: s.add_line(p["B"], p["A"], 1), "cycle"),
    (lambda s, p: s.add_line(p["A"], p["A"], 1), "self-reference"),
    (lambda s, p: s.add_line(p["A"], p["C"], "1.5"), "invalid-quantity"),
    (lambda s, p: s.add_line(p["A"], p["C"], 1, find_number="10"), "duplicate-find-number"),
    (lambda s, p: s.add_line(p["A"], p["C"], 1, reference_designators="R1"), "duplicate-refdes"),
    (lambda s, p: s.add_line(p["A"], p["C"], 1, unit="kg"), "unit-mismatch"),
])
def test_edits_that_add_errors_are_refused_and_nothing_is_written(svc, edit, code):
    parts = {n: svc.create_part(n).part_id for n in "ABC"}
    svc.add_line(parts["A"], parts["B"], 1, find_number="10", reference_designators="R1")
    version = svc.structure_version()
    with pytest.raises(EditRejected) as exc:
        edit(svc, parts)
    assert code in [i.code for i in exc.value.issues]
    assert svc.structure_version() == version and len(svc.structure().lines) == 1


def test_unknown_parts_and_units_are_refused(svc):
    a, = make(svc, "A")
    with pytest.raises(CatalogError, match="no part"):
        svc.add_line(a.part_id, "prt_" + "0" * 32, 1)
    b, = make(svc, "B")
    with pytest.raises(CatalogError, match="unknown unit"):
        svc.add_line(a.part_id, b.part_id, 1, unit="parsec")
    from revisionworx.bom import QuantityError
    with pytest.raises(QuantityError):
        svc.add_line(a.part_id, b.part_id, "-1")


def test_unit_change_across_dimensions_is_refused_while_used(svc):
    a, w = make(svc, "A", "WIRE")
    svc.update_part(w.part_id, 1, unit="m")
    svc.add_line(a.part_id, w.part_id, "2", unit="m")
    svc.update_part(w.part_id, 2, unit="mm")            # same dimension: allowed
    with pytest.raises(CatalogError, match="cannot change"):
        svc.update_part(w.part_id, 3, unit="kg")


def test_two_edits_that_together_form_a_cycle_cannot_both_commit():
    store = MemoryCatalogStore()
    s1, s2 = PartsCatalogService(store), PartsCatalogService(store)
    a, b = s1.create_part("A"), s1.create_part("B")
    # both editors validated against the same structure version; the second commit must be refused
    version = s1.structure_version()
    s1.add_line(a.part_id, b.part_id, 1)
    from revisionworx.bom.catalog_service import line_to_record
    from revisionworx.bom.model import BomLine
    from revisionworx.bom.parts import new_line_id
    from revisionworx.bom.store import STRUCT_ID
    stale = BomLine(b.part_id, a.part_id, D(1), line_id=new_line_id())
    with pytest.raises(StoreConflict):
        store.commit(Commit("raced", [Put("bomline", stale.line_id, line_to_record(stale), 0),
                                      Put("bomstruct", STRUCT_ID, {"version": version + 1}, version)]))
    assert validate(s2.structure()).ok


def test_replace_bom_keeps_ids_and_is_atomic(svc):
    a, b, c, d = make(svc, "A", "B", "C", "D")
    old = svc.add_line(a.part_id, b.part_id, 1, find_number="1")
    svc.add_line(a.part_id, c.part_id, 1, find_number="2")
    from revisionworx.bom import BomLine
    new = svc.replace_bom(a.part_id, [old.with_changes(quantity=D(2)), BomLine(a.part_id, d.part_id, D(5))])
    assert {ln.child_id for ln in svc.bom(a.part_id)} == {b.part_id, d.part_id}
    assert old.line_id in {ln.line_id for ln in new} and svc.line_rev(old.line_id) == 2
    with pytest.raises(StoreConflict):
        svc.replace_bom(a.part_id, [], expect_version=0)
    with pytest.raises(EditRejected):
        svc.replace_bom(b.part_id, [BomLine(b.part_id, a.part_id, D(1))])
    assert len(svc.bom(a.part_id)) == 2


def test_store_failure_mid_commit_writes_nothing(svc):
    a, b = make(svc, "A", "B")
    before = svc.store.list("bomline"), svc.structure_version()
    svc.store.fail_next = OSError("disk full")
    with pytest.raises(OSError):
        svc.add_line(a.part_id, b.part_id, 1)
    assert (svc.store.list("bomline"), svc.structure_version()) == before


def test_txid_retry_returns_original_result_without_duplicates(svc):
    a, b = make(svc, "A", "B")
    txid = "1" * 32
    store = svc.store
    commit = Commit("x", [Put("partrule", "r", {"id": "r", "prefix": "", "digits": 3, "suffix": "", "start": 1,
                                                 "reserved": []}, 0)])
    seq = store.commit(commit, txid)
    assert store.commit(commit, txid) == seq                 # retry after an "ambiguous" failure
    with pytest.raises(StoreError, match="different change"):
        store.commit(Commit("y", []), txid)
    with pytest.raises(StoreError, match="unknown record kind"):
        store.commit(Commit("z", [Put("document", "x", {})]))


# -------------------------------------------------------------------------------------- imports
CSV = (b"parent_number,child_number,quantity,unit,find_number\n"
       b"FRAME,SUB,2,EA,10\nSUB,BOLT,3,EA,10\nFRAME,BOLT,4,EA,20\n")


def test_import_structure_creates_missing_parts_in_one_commit(svc):
    existing = svc.create_part("BOLT", description="Hex bolt")
    s = structure_from_csv(CSV)
    mapping = svc.import_structure(s)
    assert mapping["pn:bolt"] == existing.part_id and len(svc.parts()) == 3
    frame = svc.find_by_number("FRAME")
    assert rollup(svc.structure(), frame.part_id).total(existing.part_id) == D(10)
    assert all(ln.provenance == "imported" for ln in svc.structure().lines)
    with pytest.raises(CatalogError, match="already have a BOM"):
        svc.import_structure(s)
    svc.import_structure(s, replace_existing=True)
    assert len(svc.structure().lines) == 3


def test_import_structure_refuses_cycles_and_unknown_parts(svc):
    a = svc.create_part("A")
    b = svc.create_part("B")
    svc.add_line(a.part_id, b.part_id, 1)
    version = svc.structure_version()
    with pytest.raises(EditRejected):
        svc.import_structure(structure_from_csv(b"parent_number,child_number,quantity\nB,A,1\n"))
    with pytest.raises(CatalogError, match="not in the catalog"):
        svc.import_structure(structure_from_csv(b"parent_number,child_number,quantity\nA,NEW,1\n"),
                             create_missing=False)
    assert svc.structure_version() == version and len(svc.parts()) == 2


def test_parts_csv_roundtrip_update_and_all_or_nothing(svc):
    svc.create_part("A-1", description="=cmd", manufacturers=[ManufacturerRef("Acme", "X1"), ManufacturerRef("B", "2")],
                    vendors=[VendorRef("Shop", "")], documents=[DocumentLink("doc_1", "", "datasheet")],
                    attributes={"Material": "Steel"}, unit="kg")
    svc.create_part("Ünï-2")
    data = export_parts(svc)
    other = PartsCatalogService(MemoryCatalogStore())
    assert import_parts(other, data) == {"created": 2, "updated": 0, "unchanged": 0}
    assert other.parts() == svc.parts()
    assert import_parts(other, data) == {"created": 0, "updated": 0, "unchanged": 2}
    edited = data.replace(b"=cmd", b"new text")
    with pytest.raises(CatalogError, match="allow updates"):
        import_parts(other, edited)
    assert import_parts(other, edited, update_existing=True)["updated"] == 1
    with pytest.raises(CsvImportError, match="repeats row"):
        import_parts(other, b"number\nX\nx\n")
    with pytest.raises(CsvImportError, match="required column"):
        import_parts(other, b"description\nX\n")
    with pytest.raises(CatalogError, match="already used"):   # new id, number taken by another part
        import_parts(other, b"part_id,number\nprt_" + b"9" * 32 + b",A-1\n")
    assert len(other.parts()) == 2


# -------------------------------------------------------------------------- change-control seam
class _Item:
    def __init__(self, item_type, item_id=None, label=""):
        self.item_type, self.item_id, self.label = item_type, item_id, label


def test_where_used_provider_for_change_control(svc):
    frame, sub, bolt = make(svc, "FRAME", "SUB", "BOLT")
    svc.add_line(frame.part_id, sub.part_id, 2)
    svc.add_line(sub.part_id, bolt.part_id, 3, child_revision="B")
    provider = ChangeControlWhereUsed(svc.structure)
    out = provider.where_used(_Item("part", bolt.part_id))
    assert [(e["label"], e["relationship"], e["quantity"], e["referenced_revision"]) for e in out["edges"]] == [
        ("SUB", "direct", "3 EA", "B"), ("FRAME", "indirect", "6 EA", "")]
    assert "NOT searched" in out["coverage"]
    assert provider.where_used(_Item("part", None, "bolt"))["edges"]          # by label
    assert provider.where_used(_Item("document", "doc_1"))["edges"] == []
    unknown = provider.where_used(_Item("part", None, "GHOST"))
    assert unknown["edges"] == [] and "not in the parts catalog" in unknown["coverage"]


def test_snapshot_and_catalog_reader(svc):
    frame, bolt = make(svc, "FRAME", "BOLT")
    svc.update_part(frame.part_id, 1, revision="C")
    svc.add_line(frame.part_id, bolt.part_id, 4)
    snap = svc.snapshot(frame.part_id)
    assert snap.label == "C" and snap.source.kind == "catalog" and snap.structure.part(bolt.part_id).number == "BOLT"
    lone = svc.snapshot(bolt.part_id)
    assert lone.structure.lines == ()
    cat = svc.catalog()
    assert cat.get(bolt.part_id).number == "BOLT" and cat.find_by_number("frame")[0].part_id == frame.part_id
    assert isinstance(svc.structure(), Structure)
