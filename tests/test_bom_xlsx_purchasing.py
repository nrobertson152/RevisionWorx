# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18 (REQUIREMENTS.md section 6): stdlib XLSX import/export and the derived purchasing list.

Foreign workbooks are hand-built here (shared strings, numbers, formulas, error cells, hostile archives)
so the tests need no third-party package. If openpyxl happens to be installed, interoperability with it
is checked as well; otherwise those tests skip (they are not counted as passed)."""
import io
import zipfile
from decimal import Decimal as D

import pytest

from bom_helpers import frame, line, part
from revisionworx.bom import (CsvImportError, MemoryCatalogStore, PartsCatalogService, Structure, ManufacturerRef,
                              VendorRef, csv_to_xlsx, parts_from_xlsx, parts_to_xlsx, purchasing_list,
                              purchasing_to_csv, rollup, structure_from_csv, structure_from_indented_xlsx,
                              structure_from_xlsx, structure_to_csv, structure_to_xlsx)
from revisionworx.bom import csvio, xlsx

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def workbook(sheet_xml: str, shared: list[str] | None = None, extra: dict[str, bytes] | None = None,
             sheet_name: str = "Sheet1") -> bytes:
    """A minimal foreign workbook, the way other tools write it (shared strings, numeric cells)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{M}" xmlns:r="http://schemas.openxmlformats.org/'
                                      f'officeDocument/2006/relationships"><sheets><sheet name="{sheet_name}" '
                                      'sheetId="1" r:id="rId7"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                                                 '2006/relationships"><Relationship Id="rId7" Target='
                                                 '"/xl/worksheets/data.xml" Type="x"/></Relationships>')
        z.writestr("xl/worksheets/data.xml", f'<worksheet xmlns="{M}"><sheetData>{sheet_xml}</sheetData></worksheet>')
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{M}">' + "".join(
                f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


SHARED = ["parent_number", "child_number", "quantity", "unit", "FRAME", "BOLT", "EA", "HOSE", "m"]
ROWS = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c>'
        '<c r="D1" t="s"><v>3</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" t="s"><v>5</v></c><c r="C2"><v>8</v></c>'
        '<c r="D2" t="s"><v>6</v></c></row>'
        '<row r="4"><c r="A4" t="s"><v>4</v></c><c r="B4" t="s"><v>7</v></c><c r="C4"><v>2.5</v></c>'
        '<c r="D4" t="s"><v>8</v></c></row>')


def test_export_roundtrips_and_is_deterministic():
    s = frame().with_lines(line("frame", "label", "1", note="=SUM(A1) & <tag> \"q\"  "))
    s = s.with_parts(part("label", "+LBL", description="Ünïcode ✓\nline two"))
    data = structure_to_xlsx(s)
    assert data == structure_to_xlsx(s) and data[:2] == b"PK"
    assert structure_from_xlsx(data) == s
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet and "=SUM(A1)" in sheet and "'=SUM" not in sheet  # inline string, no guard


def test_foreign_workbook_with_shared_strings_and_numbers():
    s = structure_from_xlsx(workbook(ROWS, SHARED))
    r = rollup(s, "pn:frame")
    assert r.total("pn:bolt") == D(8) and r.total("pn:hose") == D("2.5")


def test_row_numbers_in_errors_are_worksheet_rows():
    bad = ROWS.replace("<v>2.5</v>", "<v>1E-3</v>")
    with pytest.raises(CsvImportError) as exc:
        structure_from_xlsx(workbook(bad, SHARED))
    assert (exc.value.problems[0].row, exc.value.problems[0].column) == (4, "quantity")
    assert "exponent" in exc.value.problems[0].message


@pytest.mark.parametrize("cell,fragment", [
    ('<c r="C2"><f>2*4</f><v>8</v></c>', "formula cell"),
    ('<c r="C2" t="e"><v>#N/A</v></c>', "error value"),
    ('<c r="C2" t="s"><v>99</v></c>', "shared-string"),
])
def test_formulas_errors_and_broken_references_are_refused(cell, fragment):
    bad = ROWS.replace('<c r="C2"><v>8</v></c>', cell)
    with pytest.raises(CsvImportError, match=fragment) as exc:
        structure_from_xlsx(workbook(bad, SHARED))
    assert exc.value.problems[0].row == 2 and exc.value.problems[0].column == "C"


@pytest.mark.parametrize("data,fragment", [
    (b"not a zip", "not an .xlsx"),
    (workbook("", SHARED), "empty"),
    (workbook(ROWS, SHARED, sheet_name="Other"), None),
    (workbook(ROWS, None, extra={"xl/sharedStrings.xml": b'<!DOCTYPE x [<!ENTITY a "aaaa">]><sst/>'}),
     "DOCTYPE"),
])
def test_hostile_or_unusable_workbooks(data, fragment):
    if fragment is None:
        with pytest.raises(CsvImportError, match="no worksheet named"):
            structure_from_xlsx(data, sheet="BOM")
        assert structure_from_xlsx(data, sheet="Other")
        return
    with pytest.raises(CsvImportError, match=fragment):
        structure_from_xlsx(data)


def test_zip_bomb_member_is_refused_before_full_expansion(monkeypatch):
    monkeypatch.setattr(xlsx, "MAX_MEMBER_BYTES", 10_000)
    big = '<row r="1"><c r="A1" t="inlineStr"><is><t>' + "x" * 50_000 + "</t></is></c></row>"
    with pytest.raises(CsvImportError, match="over the limit|beyond the size limit"):
        structure_from_xlsx(workbook(big, SHARED))


def test_indented_and_parts_and_reports_as_xlsx():
    ind = ('<row r="1"><c r="A1" t="inlineStr"><is><t>Level</t></is></c><c r="B1" t="inlineStr"><is><t>Part Number'
           '</t></is></c><c r="C1" t="inlineStr"><is><t>Qty</t></is></c></row>'
           '<row r="2"><c r="A2"><v>0</v></c><c r="B2" t="inlineStr"><is><t>TOP</t></is></c><c r="C2"><v>1</v></c></row>'
           '<row r="3"><c r="A3"><v>1</v></c><c r="B3" t="inlineStr"><is><t>LEAF</t></is></c><c r="C3"><v>4</v></c></row>')
    st, root = structure_from_indented_xlsx(workbook(ind))
    assert root == "pn:top" and rollup(st, root).total("pn:leaf") == D(4)
    svc = PartsCatalogService(MemoryCatalogStore())
    svc.create_part("-A1", manufacturers=[ManufacturerRef("Acme", "=X")], description="x" * 300)
    rows = parts_from_xlsx(parts_to_xlsx(svc.parts()))
    assert [p for _, p, _ in rows] == svc.parts()
    report = csvio.rollup_to_csv(rollup(frame(), "frame"), frame())
    with zipfile.ZipFile(io.BytesIO(csv_to_xlsx(report, sheet="Rollup"))) as z:
        assert b'name="Rollup"' in z.read("xl/workbook.xml")
    with pytest.raises(ValueError):
        csv_to_xlsx(report, sheet="bad/name")


def test_openpyxl_interop_when_available(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    data = structure_to_xlsx(frame())
    p = tmp_path / "ours.xlsx"
    p.write_bytes(data)
    ws = openpyxl.load_workbook(p).active
    assert ws["A1"].value == "parent_number" and ws.max_row == 1 + len(frame().parts) + len(frame().lines)
    wb = openpyxl.Workbook()
    sh = wb.active
    sh.append(["Parent", "Child", "Qty", "UOM"])
    sh.append(["FRAME", "BOLT", 8, "EA"])
    sh.append(["FRAME", "HOSE", 2.5, "m"])
    theirs = tmp_path / "theirs.xlsx"
    wb.save(theirs)
    assert rollup(structure_from_xlsx(theirs), "pn:frame").total("pn:hose") == D("2.5")


# ------------------------------------------------------------------------------- purchasing list
def test_purchasing_list_is_derived_and_distinct_from_the_engineering_bom():
    svc = PartsCatalogService(MemoryCatalogStore())
    p = {n: svc.create_part(n, **kw) for n, kw in {
        "FRAME": dict(kind="assembly"), "SUB": dict(kind="assembly"),
        "BOLT": dict(manufacturers=[ManufacturerRef("Würth", "0057")], vendors=[VendorRef("Shop", "S1")]),
        "HOSE": dict(unit="m", kind="material", vendors=[VendorRef("Hydra")]),
        "SPEC": dict(kind="document"), "GLUE": dict(unit="EA")}.items()}
    ids = {n: x.part_id for n, x in p.items()}
    svc.add_line(ids["FRAME"], ids["SUB"], 2)
    svc.add_line(ids["SUB"], ids["BOLT"], 3)
    svc.add_line(ids["SUB"], ids["HOSE"], "0.75", unit="m")
    svc.add_line(ids["FRAME"], ids["SPEC"], 1)
    svc.add_line(ids["FRAME"], ids["GLUE"], 1)
    svc.set_status(ids["GLUE"], "obsolete", svc.part_rev(ids["GLUE"]))
    st = svc.structure()
    # a fractional count requirement: half a frame's worth of glue tubes per kit
    st = st.with_single_level(ids["FRAME"], [ln.with_changes(quantity=D("0.5")) if ln.child_id == ids["GLUE"] else ln
                                             for ln in st.children(ids["FRAME"])])
    before = structure_to_csv(st)
    pl = purchasing_list(st, ids["FRAME"], D(3), parts={x.part_id: x for x in svc.parts()})
    got = {b.number: (b.required, b.order_quantity, b.unit, b.flags) for b in pl.lines}
    assert got == {"BOLT": (D(18), D(18), "EA", ()),
                   "GLUE": (D("1.5"), D(2), "EA", ("rounded-up", "no-source", "obsolete")),
                   "HOSE": (D("4.50"), D("4.50"), "m", ())}
    assert pl.excluded == (ids["SPEC"],) and [b.number for b in pl.unsourced] == ["GLUE"]
    assert structure_to_csv(st) == before                       # the engineering BOM is unchanged
    text = purchasing_to_csv(pl, excel=False).decode()
    assert "EXCLUDED" in text and "not included" in text and "Würth 0057" in text


def test_purchasing_list_without_catalog_flags_everything_unsourced():
    s = structure_from_csv(b"parent_number,child_number,quantity,provenance\nA,B,2,inferred\n")
    pl = purchasing_list(s, "pn:a")
    assert pl.lines[0].flags == ("no-source", "unresolved", "needs-verification")
