# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: CSV import/export - lossless round trip, Unicode, spreadsheet-formula safety, indented
CAD-style BOMs and meaningful all-or-nothing errors."""
from decimal import Decimal as D

import pytest

from bom_helpers import frame, line, part
from revisionworx.bom import (CsvImportError, InMemoryCatalog, PartInfo, Structure, explode, rollup,
                              structure_from_csv, structure_from_indented_csv, structure_to_csv, validate)
from revisionworx.bom import csvio
from revisionworx.bom.csvio import guard, unguard


def problems(exc):
    return [(p.row, p.column, p.message) for p in exc.value.problems]


def test_parent_child_roundtrip_is_lossless():
    s = frame().with_lines(line("frame", "plate", "1", note="=SUM(A1)", attributes={"Material": "-Steel"},
                                child_revision="B", line_id="L1", provenance="cad-extracted"))
    s = s.with_parts(PartInfo("plate", number="+PL-1", description="@top plate, \"quoted\"\nsecond line"))
    data = structure_to_csv(s)
    assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data
    assert structure_from_csv(data) == s
    assert structure_to_csv(structure_from_csv(data)) == data  # byte-stable
    plain = structure_to_csv(s, excel=False, formula_guard=False)
    assert structure_from_csv(plain, formula_guard=False) == s


def test_export_escapes_formulas_and_import_reverses_exactly():
    data = structure_to_csv(Structure.build([line("a", "b", note="=1+1")], [part("b", "@cmd", description="-5 V")]))
    text = data.decode("utf-8-sig")
    assert "'=1+1" in text and "'@cmd" in text and "'-5 V" in text
    for v in ["=x", "'=x", "''+x", "plain", "'quoted", "", "'", "-", "\tTab", "a=b"]:
        assert unguard(guard(v)) == v
    assert guard("a=b") == "a=b" and guard("'plain") == "'plain"


def test_unicode_survives_and_bom_or_no_bom_both_read():
    s = Structure.build([line("ä", "日本", "2", note="Ω µ ±")], [part("ä", "Gehäuse-Ä"), part("日本", "部品-1")])
    for excel in (True, False):
        assert structure_from_csv(structure_to_csv(s, excel=excel)) == s


def test_cp1252_files_need_the_encoding_named():
    data = "parent_number,child_number,quantity\nGehäuse,Schraube,2\n".encode("cp1252")
    with pytest.raises(CsvImportError, match="not valid utf-8-sig text"):
        structure_from_csv(data)
    s = structure_from_csv(data, encoding="cp1252")
    assert s.part("pn:gehäuse").number == "Gehäuse"


def test_part_numbers_without_catalog_stay_unresolved():
    s = structure_from_csv(b"Parent,Child,Qty,UOM\nFRAME-1,bolt-m8,4,pcs\nframe-1,NUT,4,EA\n")
    assert {p.part_id: p.resolved for p in s.parts} == {"pn:frame-1": False, "pn:bolt-m8": False, "pn:nut": False}
    assert s.children("pn:frame-1")[0].unit == "pcs"
    assert rollup(s, "pn:frame-1").total("pn:bolt-m8") == D(4)
    assert "unresolved-identity" in validate(s).codes()
    assert all(ln.provenance == "imported" for ln in s.lines)


def test_catalog_resolves_numbers_and_refuses_ambiguity_and_unknown_ids():
    cat = InMemoryCatalog([PartInfo("prt_1", number="FRAME"), PartInfo("prt_2", number="BOLT"),
                           PartInfo("prt_3", number="DUP"), PartInfo("prt_4", number="dup")])
    s = structure_from_csv(b"parent_number,child_number,quantity\nframe,bolt,4\n", catalog=cat)
    assert s.children("prt_1")[0].child_id == "prt_2" and s.part("prt_2").resolved
    with pytest.raises(CsvImportError) as exc:
        structure_from_csv(b"parent_number,child_number,child_id,quantity\nFRAME,DUP,,1\nFRAME,,prt_9,1\n",
                           catalog=cat)
    msgs = [m for _, _, m in problems(exc)]
    assert any("matches 2 catalog parts" in m for m in msgs) and any("prt_9" in m for m in msgs)


def test_errors_name_row_and_column_and_nothing_is_imported():
    data = (b"parent_number,child_number,quantity,unit\n"
            b"A,B,2,EA\n"
            b"A,C,-1,EA\n"
            b"A,,1,EA\n"
            b"A,D,1,5,EA\n"
            b"A,E,1e3,EA\n")
    with pytest.raises(CsvImportError) as exc:
        structure_from_csv(data)
    rows = problems(exc)
    assert (3, "quantity") == rows[0][:2] and "negative" in rows[0][2]
    assert (4, "child_number") == rows[1][:2]
    assert rows[2][0] == 5 and "values but only" in rows[2][2]
    assert rows[3][:2] == (6, "quantity") and "exponent" in rows[3][2]
    assert "4 problem(s)" in str(exc.value)


def test_row_numbers_count_physical_lines_inside_quoted_newlines():
    data = b'parent_number,child_number,quantity,note\nA,B,1,"two\nlines"\nA,C,x,ok\n'
    with pytest.raises(CsvImportError) as exc:
        structure_from_csv(data)
    assert problems(exc)[0][:2] == (4, "quantity")


@pytest.mark.parametrize("data,fragment", [
    (b"", "empty"),
    (b"\n\n,,\n", "empty"),
    (b"parent_number,child_number\nA,B\n", "required columns"),
    (b"level,number,qty\n0,A,1\n", "indented"),
    (b"parent_number,child_number,quantity,quantity\nA,B,1,1\n", "duplicate column"),
    (b"parent_number,child_number,quantity\nA,B,1\x00\n", "NUL"),
    (b'parent_number,child_number,quantity\nA,"B,1\n', "malformed CSV"),
    (b"parent_number,child_number,quantity\n,B,3\n", "item row"),
    (b"parent_number,child_number,quantity\nA,B,1\nA,B,1\n", None),
])
def test_bad_files_are_refused_with_reasons(data, fragment):
    if fragment is None:  # duplicates are legal (validation warns), import succeeds
        assert len(structure_from_csv(data).lines) == 2
        return
    with pytest.raises(CsvImportError, match=fragment):
        structure_from_csv(data)


def test_size_and_row_limits():
    with pytest.raises(CsvImportError, match="byte limit"):
        structure_from_csv(b"x" * 100, max_bytes=10)
    rows = b"parent_number,child_number,quantity\n" + b"A,B,1\n" * 20
    with pytest.raises(CsvImportError, match="more than 5 rows"):
        structure_from_csv(rows, max_rows=5)


def test_problem_list_is_capped():
    data = b"parent_number,child_number,quantity\n" + b"A,B,bad\n" * 500
    with pytest.raises(CsvImportError) as exc:
        structure_from_csv(data)
    assert len(exc.value.problems) == csvio.MAX_PROBLEMS and exc.value.truncated
    assert "not reported" in str(exc.value)


def test_import_from_path(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(structure_to_csv(frame()))
    assert structure_from_csv(p) == frame() and structure_from_csv(str(p)) == frame()


# -------------------------------------------------------------------------------- indented layout
INDENTED = """Level,Item,Part Number,Description,Qty,Unit,Rev,RefDes
0,,FRAME-100,Welded frame,1,EA,C,
1,1,SUB-200,Pump sub,2,EA,B,
2,1,BOLT-M8,Hex bolt,3,EA,,
2,2,CABLE-3C,Cable,0.5,m,,
1,2,BOLT-M8,Hex bolt,4,EA,,B1 B2 B3 B4
1,3,SUB-200,Pump sub,1,EA,B,
2,1,BOLT-M8,Hex bolt,3,EA,,
2,2,CABLE-3C,Cable,0.5,m,,
"""


def test_indented_import_builds_shared_subassemblies_once():
    s, root = structure_from_indented_csv(INDENTED.encode())
    assert root == "pn:frame-100"
    assert len(s.children("pn:sub-200")) == 2  # defined once though listed twice
    r = rollup(s, root)
    assert r.total("pn:bolt-m8") == D(4 + 3 * 3) and r.total("pn:cable-3c") == D("1.5")
    assert s.part("pn:frame-100").revision == "C" and s.children(root)[0].child_revision == "B"
    assert [x.level for x in explode(s, root).rows] == [1, 2, 2, 1, 1, 2, 2]


def test_indented_import_with_dotted_item_numbers_and_named_root():
    data = b"Item,Part Number,Qty\n1,SUB,2\n1.1,BOLT,3\n2,NUT,8\n"
    s, root = structure_from_indented_csv(data, root_number="PUMP")
    assert root == "pn:pump" and rollup(s, root).total("pn:bolt") == D(6)
    assert [ln.find_number for ln in s.children(root)] == ["1", "2"]


def test_indented_conflicting_definitions_and_level_jumps_are_refused():
    bad = INDENTED.rsplit("2,1,BOLT-M8,Hex bolt,3,EA,,", 1)[0] + "2,1,BOLT-M8,Hex bolt,9,EA,,\n2,2,CABLE-3C,Cable,0.5,m,,\n"
    with pytest.raises(CsvImportError, match="different children"):
        structure_from_indented_csv(bad.encode())
    with pytest.raises(CsvImportError, match="may only increase by one"):
        structure_from_indented_csv(b"Level,Part Number,Qty\n0,A,1\n2,B,1\n")
    with pytest.raises(CsvImportError, match="no top item"):
        structure_from_indented_csv(b"Level,Part Number,Qty\n1,B,1\n")
    with pytest.raises(CsvImportError, match="only one level-0"):
        structure_from_indented_csv(b"Level,Part Number,Qty\n0,A,1\n0,B,1\n")
    with pytest.raises(CsvImportError, match="dotted number"):
        structure_from_indented_csv(b"Item,Part Number,Qty\n1.a,B,1\n", root_number="R")


# ------------------------------------------------------------------------------------------ reports
def test_report_exports_are_deterministic_and_guarded():
    from revisionworx.bom import compare
    s = frame()
    ex = csvio.explosion_to_csv(explode(s, "frame"), s, excel=False).decode()
    assert ex.splitlines()[1].startswith("1,FRAME-100 > SUB-200,1,SUB-200,sub")
    ro = csvio.rollup_to_csv(rollup(s, "frame"), s, excel=False).decode()
    assert "BOLT-M8,bolt,Hex bolt M8x20,10,EA,2,no,1,no" in ro
    s2 = s.replacing_line(s.lines[0], s.lines[0].with_changes(quantity=D(3)))
    df = csvio.diff_to_csv(compare(s, s2), s, s2, excel=False).decode()
    assert "changed,FRAME-100,SUB-200,1,quantity,2,3" in df
    assert csvio.explosion_to_csv(explode(s, "frame"), s) == csvio.explosion_to_csv(explode(s, "frame"), s)
    iss = csvio.issues_to_csv(validate(s.with_lines(line("frame", "x", "1", note="")), roots=["frame"]), excel=False)
    assert iss.decode().startswith("severity,code,message")
