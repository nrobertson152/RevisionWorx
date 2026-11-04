# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: GUI-facing view models, the in-memory session and the file-only CLI."""
from decimal import Decimal as D
from pathlib import Path

import pytest

from bom_helpers import frame, line
from revisionworx.bom import BomSnapshot, Structure, snapshot_from_json, structure_from_csv, structure_to_csv, validate
from revisionworx.bom.__main__ import main
from revisionworx.bom.viewmodels import (VIEWMODEL_CONTRACT, BomSession, change_rows, indented_rows, issue_rows,
                                         summary_rows, where_used_rows)

EXAMPLES = Path(__file__).resolve().parent.parent / "docs" / "bom" / "examples"


def test_indented_and_summary_rows_are_display_ready():
    s = frame()
    rows = indented_rows(s, "frame")
    assert VIEWMODEL_CONTRACT == 1
    assert [(r.indent_label, r.extended_quantity, r.is_assembly) for r in rows] == [
        ("SUB-200", "2", True), ("    BOLT-M8", "6", False), ("    CABLE-3C", "1", False), ("BOLT-M8", "4", False)]
    summ = {r.part_number: (r.total, r.unit, r.occurrences) for r in summary_rows(s, "frame")}
    assert summ == {"BOLT-M8": ("10", "EA", 2), "CABLE-3C": ("1", "m", 1), "SUB-200": ("2", "EA", 1)}
    unresolved = structure_from_csv(b"parent_number,child_number,quantity,provenance\nA,B,1,inferred\n")
    assert indented_rows(unresolved, "pn:a")[0].flags == ("unresolved", "needs-verification")


def test_change_issue_and_where_used_rows():
    a = frame()
    b = a.replacing_line(a.lines[0], a.lines[0].with_changes(quantity=D(3))).with_lines(line("frame", "nut", "1"))
    from revisionworx.bom import compare
    rows = change_rows(compare(a, b), a, b)
    assert ("changed", "FRAME-100", "SUB-200", "quantity", "2", "3") in [
        (r.change, r.parent_number, r.part_number, r.field, r.before, r.after) for r in rows]
    assert any(r.change == "added" and r.part_number == "nut" for r in rows)
    issues = issue_rows(validate(b, roots=["frame"]))
    assert ("warning", "missing-part-info") in [(i.severity, i.code) for i in issues]
    wu = where_used_rows(a, "bolt")
    assert [(r.assembly_number, r.relation, r.quantity, r.top_level) for r in wu] == [
        ("FRAME-100", "direct", "10 EA", True), ("SUB-200", "direct", "3 EA", False)]


def test_session_loads_compares_and_exports(tmp_path):
    rev_a, rev_b = tmp_path / "frame-A.csv", tmp_path / "frame-B.csv"
    rev_a.write_bytes(structure_to_csv(frame()))
    s = frame()
    rev_b.write_bytes(structure_to_csv(s.replacing_line(s.lines[0], s.lines[0].with_changes(quantity=D(3)))))
    session = BomSession()
    assert session.load_file(rev_a) == "frame-A" and session.load_file(rev_b, name="B") == "B"
    diff, rows = session.compare("frame-A", "B")
    assert diff.summary()["changed"] == 1 and rows
    assert {d.item_id: d.delta for d in session.summary_change("frame-A", "B")}["bolt"] == D(3)
    assert session.validate("B").ok
    assert session.revision_impact("bolt").affected_top_level == ("frame",)
    out = session.save_snapshot("B", tmp_path / "B.json")
    assert snapshot_from_json(out.read_bytes()) == session.snapshots["B"]
    session.export_structure_csv("B", tmp_path / "B-out.csv")
    assert session.load_file(tmp_path / "B.json", name="again") == "again"
    session.add("sub-old", BomSnapshot(frame().restricted_to("sub"), "sub"))
    session.add("sub-new", BomSnapshot(Structure.build([line("sub", "bolt", "9")], frame().parts), "sub"))
    rep = session.change_impact("sub-old", "sub-new")
    assert {a.top_level_id for a in rep.affected} == {"frame"}


# ----------------------------------------------------------------------------------------------- CLI
def run(capsysbinary, *args):
    code = main([str(a) for a in args])
    out, err = capsysbinary.readouterr()
    return code, out.decode("utf-8"), err.decode("utf-8")


def test_cli_on_the_shipped_examples(capsysbinary, tmp_path):
    a, b = EXAMPLES / "hpu-frame-rev-A.csv", EXAMPLES / "hpu-frame-rev-B.csv"
    code, out, _ = run(capsysbinary, "validate", a)
    body = out.splitlines()[1:]
    assert code == 0 and body and all(r.startswith("info,unresolved-identity,") for r in body)
    code, out, _ = run(capsysbinary, "explode", a)
    assert code == 0 and out.count("\n") > 5
    code, out, _ = run(capsysbinary, "rollup", "--leaves-only", b, "--quantity", "2")
    assert code == 0 and "BOLT-M10-30" in out
    code, out, _ = run(capsysbinary, "diff", a, b)
    assert code == 0 and "changed" in out
    code, out, _ = run(capsysbinary, "diff", "--summary", a, b)
    assert code == 0 and out.startswith("part_number,part_id,before,after,change,unit")
    code, out, _ = run(capsysbinary, "impact", "BOLT-M10-30", a, b, EXAMPLES / "hpu-skid.csv")
    assert code == 0 and "COVERAGE" in out and "directly-referenced" in out
    code, out, _ = run(capsysbinary, "--root", "HPU-FRAME", "change-impact",
                       a, b, EXAMPLES / "hpu-skid.csv")
    assert code == 0 and "HPU-SKID" in out
    code, out, _ = run(capsysbinary, "explode", EXAMPLES / "pump-indented.csv")
    assert code == 0 and "PUMP-ASSY" in out
    target = tmp_path / "converted.json"
    code, _, err = run(capsysbinary, "-o", target, "convert", a)
    assert code == 0 and target.exists() and "wrote" in err
    code, _, err = run(capsysbinary, "-o", target, "convert", a)
    assert code == 2 and "already exists" in err
    code, out, _ = run(capsysbinary, "--layout", "json", "rollup", target)
    assert code == 0 and "BOLT-M10-30" in out


def test_cli_reports_errors_with_exit_codes(capsysbinary, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("parent_number,child_number,quantity\nA,B,-2\n")
    code, _, err = run(capsysbinary, "explode", bad)
    assert code == 2 and "row 2" in err and "negative" in err
    cyc = tmp_path / "cyc.csv"
    cyc.write_text("parent_number,child_number,quantity\nTOP,A,1\nA,B,1\nB,A,1\n")
    code, out, _ = run(capsysbinary, "validate", cyc)
    assert code == 1 and "cycle" in out
    code, _, err = run(capsysbinary, "explode", tmp_path / "missing.csv")
    assert code == 2 and "error" in err


def test_examples_are_valid_and_consistent():
    for name in ("hpu-frame-rev-A.csv", "hpu-frame-rev-B.csv", "hpu-skid.csv"):
        s = structure_from_csv(EXAMPLES / name)
        assert validate(s, roots=s.top_level_ids).ok, name
    with pytest.raises(Exception):
        structure_from_csv(EXAMPLES / "pump-indented.csv")  # indented: needs the indented importer


def test_cli_xlsx_and_purchase(capsysbinary, tmp_path):
    a = EXAMPLES / "hpu-frame-rev-B.csv"
    book = tmp_path / "frame.xlsx"
    code, _, _ = run(capsysbinary, "-o", book, "convert", a)
    assert code == 0 and book.read_bytes()[:2] == b"PK"
    code, csv_out, _ = run(capsysbinary, "rollup", a)
    code2, xlsx_out, _ = run(capsysbinary, "rollup", book)
    assert code == code2 == 0 and csv_out == xlsx_out          # same answer from either file format
    code, out, _ = run(capsysbinary, "purchase", book, "--quantity", "2")
    assert code == 0 and "BOLT-M10-30,pn:bolt-m10-30,Hex bolt M10x30 8.8 zinc,40,40,EA" in out and "NOTE" in out
    report = tmp_path / "rollup.xlsx"
    code, _, _ = run(capsysbinary, "-o", report, "rollup", a)
    assert code == 0 and report.read_bytes()[:2] == b"PK"
