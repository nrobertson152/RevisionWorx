# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions for the Codex review of PR #21 at 59c0f7b. Each test failed on that commit."""
import pytest

from revisionworx.bom import (CatalogError, EditRejected, MemoryCatalogStore, PartsCatalogService, StoreConflict,
                              WorkspaceCatalogStore, export_parts, import_parts, structure_from_csv, validate)
from revisionworx.bom.errors import CsvImportError


def interleave(svc, other_action):
    """Run ``other_action`` right after ``svc`` validated its edit and before it commits."""
    real = svc._new_errors

    def hook(before, after):
        errors = real(before, after)
        svc._new_errors = real
        other_action()
        return errors
    svc._new_errors = hook


# P1: commits must pin the revisions of the state that was validated -----------------------------
def test_interleaved_edits_cannot_both_commit_a_cycle():
    store = MemoryCatalogStore()
    s1, s2 = PartsCatalogService(store), PartsCatalogService(store)
    a, b = s1.create_part("A"), s1.create_part("B")
    interleave(s1, lambda: s2.add_line(b.part_id, a.part_id, 1))
    with pytest.raises(StoreConflict):
        s1.add_line(a.part_id, b.part_id, 1)
    assert validate(s1.structure()).ok and len(s1.structure().lines) == 1


def test_interleaved_obsolete_marking_is_detected():
    store = MemoryCatalogStore()
    s1, s2 = PartsCatalogService(store), PartsCatalogService(store)
    a, b = s1.create_part("A"), s1.create_part("B")
    interleave(s1, lambda: s2.set_status(b.part_id, "obsolete", s2.part_rev(b.part_id)))
    with pytest.raises(StoreConflict):
        s1.add_line(a.part_id, b.part_id, 1)          # validated while B was still active


def test_delete_cannot_race_a_new_usage():
    store = MemoryCatalogStore()
    s1, s2 = PartsCatalogService(store), PartsCatalogService(store)
    a, b = s1.create_part("A"), s1.create_part("B")
    real_lines = s1.line_records
    calls = []

    def lines_then_add():
        recs = real_lines()                         # delete reads "B is unused" ...
        if not calls:
            calls.append(1)
            s2.add_line(a.part_id, b.part_id, 1)      # ... and a new usage lands before it commits
        return recs
    s1.line_records = lines_then_add
    with pytest.raises(StoreConflict):
        s1.delete_part(b.part_id, s1.part_rev(b.part_id))
    assert s2.part(b.part_id) is not None


def test_interleaving_on_the_real_workspace_store(ws):
    s1 = PartsCatalogService(WorkspaceCatalogStore(ws))
    s2 = PartsCatalogService(WorkspaceCatalogStore(ws))   # same writable handle: models two UI actions
    a, b = s1.create_part("A"), s1.create_part("B")
    interleave(s1, lambda: s2.add_line(b.part_id, a.part_id, 1))
    with pytest.raises(StoreConflict):
        s1.add_line(a.part_id, b.part_id, 1)
    assert validate(s1.structure()).ok and ws.verify()["problems"] == []


# P1: repeated part ids in one import ---------------------------------------------------------------
def test_import_refuses_repeated_part_ids(ws):
    svc = PartsCatalogService(WorkspaceCatalogStore(ws))
    pid = "prt_" + "7" * 32
    data = f"part_id,number\n{pid},A\n{pid},B\n".encode()
    with pytest.raises(CatalogError, match="repeat"):
        import_parts(svc, data)
    assert svc.parts() == [] and svc.find_by_number("A") is None


# P2: imported units are validated and canonicalised --------------------------------------------------
def test_import_validates_and_canonicalises_units():
    svc = PartsCatalogService(MemoryCatalogStore())
    with pytest.raises(CatalogError, match="unknown unit"):
        import_parts(svc, b"number,unit\nX,parsec\n")
    import_parts(svc, b"number,unit\nY,MM\nZ,pcs\n")
    assert {p.number: p.unit for p in svc.parts()} == {"Y": "mm", "Z": "EA"}


# P2: parts CSV headers are case-insensitive ------------------------------------------------------------
def test_parts_headers_are_case_insensitive_and_lose_nothing():
    from revisionworx.bom import ManufacturerRef
    svc = PartsCatalogService(MemoryCatalogStore())
    p = svc.create_part("A-1", manufacturers=[ManufacturerRef("Acme", "X1")], attributes={"Mixed Case": "v"})
    svc.set_status(p.part_id, "obsolete", svc.part_rev(p.part_id))
    data = export_parts(svc).decode("utf-8-sig")
    header, rest = data.split("\r\n", 1)
    upper = (header.upper().replace("ATTR:MIXED CASE", "attr:Mixed Case") + "\r\n" + rest).encode()
    other = PartsCatalogService(MemoryCatalogStore())
    import_parts(other, upper)
    assert other.parts() == svc.parts()
    with pytest.raises(CsvImportError, match="unknown column"):
        import_parts(other, b"number,MANUFACTURER_X\nB,Acme\n")


# P2: structure import must not add new usages of obsolete parts ----------------------------------------
def test_import_structure_refuses_new_usage_of_obsolete_parts():
    svc = PartsCatalogService(MemoryCatalogStore())
    a, b, c = svc.create_part("A"), svc.create_part("B"), svc.create_part("C")
    svc.add_line(a.part_id, c.part_id, 1)
    svc.set_status(b.part_id, "obsolete", svc.part_rev(b.part_id))
    svc.set_status(c.part_id, "obsolete", svc.part_rev(c.part_id))
    with pytest.raises(CatalogError, match="obsolete"):
        svc.import_structure(structure_from_csv(b"parent_number,child_number,quantity\nA,B,1\nA,C,1\n"),
                             replace_existing=True)
    # an existing usage of an obsolete part may stay (re-import of the same BOM)
    svc.import_structure(structure_from_csv(b"parent_number,child_number,quantity\nA,C,2\n"), replace_existing=True)
    assert [ln.quantity for ln in svc.bom(a.part_id)] == [2]
    with pytest.raises(EditRejected):
        svc.import_structure(structure_from_csv(b"parent_number,child_number,quantity\nC,A,1\n"))
