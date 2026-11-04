# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18 / backlog #28 item 5: the stable BOM data contract (docs/bom/DATA_CONTRACT.md).

Golden files in tests/fixtures/bom/ freeze the exact bytes of every persistent or exchanged form at
contract version 1: canonical JSON, the structure digest, parent-child CSV, parts CSV and the workspace
record shapes. A change that alters any of them fails here and must bump the contract version and add a
reader for the old form; it can never silently change what existing files or journals mean.

Regenerate only on a deliberate contract change: ``RWX_BOM_REGENERATE_GOLDEN=1 pytest tests/test_bom_contract.py``.
"""
import json
import os
from decimal import Decimal as D
from pathlib import Path

import pytest

from revisionworx.bom import (BomLine, BomSnapshot, DocumentLink, ManufacturerRef, NumberingRule, Part, PartInfo,
                              SourceRef, Structure, VendorRef, load_any, parts_from_csv, parts_to_csv,
                              snapshot_to_json, structure_digest, structure_from_csv, structure_to_csv,
                              structure_to_json)
from revisionworx.bom.catalog_service import line_from_record, line_to_record
from revisionworx.bom.snapshot import FORMAT, VERSION

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "bom"
REGENERATE = os.environ.get("RWX_BOM_REGENERATE_GOLDEN") == "1"

P = {n: "prt_" + c * 32 for n, c in (("frame", "1"), ("sub", "2"), ("bolt", "3"), ("hose", "4"), ("label", "5"))}


def contract_structure() -> Structure:
    parts = [PartInfo(P["frame"], "FRAME-100", "Welded frame", "EA", "C", "assembly"),
             PartInfo(P["sub"], "SUB-200", "Pump sub, \"quoted\"", "EA", "B", "assembly"),
             PartInfo(P["bolt"], "BOLT-M8", "Hex bolt M8×20 – zinc", "EA", "", "part"),
             PartInfo(P["hose"], "HOSE-12", "Hose ½ in", "m", "", "material"),
             PartInfo(P["label"], "=LABEL", "Ünïcode label ✓", "EA", "A", "part")]
    lines = [BomLine(P["frame"], P["sub"], D(2), find_number="10", child_revision="B", line_id="bln_" + "a" * 32),
             BomLine(P["frame"], P["bolt"], D(8), find_number="20", reference_designators=("B1", "B2", "B10"),
                     line_id="bln_" + "b" * 32, note="torque 25 Nm"),
             BomLine(P["sub"], P["bolt"], D(4), find_number="10", line_id="bln_" + "c" * 32,
                     provenance="cad-extracted"),
             BomLine(P["sub"], P["hose"], D("1.25"), unit="m", find_number="20", line_id="bln_" + "d" * 32,
                     attributes={"Route": "+left side"}),
             BomLine(P["frame"], P["label"], D(1), find_number="30", line_id="bln_" + "e" * 32,
                     provenance="inferred")]
    return Structure.build(lines, parts)


def contract_parts() -> list[Part]:
    return [Part(P["frame"], "FRAME-100", "Welded frame", kind="assembly", revision="C",
                 documents=(DocumentLink("doc_" + "f" * 32, "rev_" + "e" * 32, "drawing"),)),
            Part(P["bolt"], "BOLT-M8", "Hex bolt M8×20 – zinc",
                 manufacturers=(ManufacturerRef("Würth", "0057 08 20"), ManufacturerRef("Acme", "-B8")),
                 vendors=(VendorRef("Shop", "S-1"),), attributes={"Material": "8.8"}),
            Part(P["hose"], "HOSE-12", "Hose ½ in", unit="m", kind="material", manual_number=True)]


def golden(name: str, data: bytes) -> bytes:
    path = GOLDEN / name
    if REGENERATE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path.read_bytes()


def test_contract_version():
    assert (FORMAT, VERSION) == ("revisionworx.bom", 1)


@pytest.mark.parametrize("name,make", [
    ("structure.json", lambda: structure_to_json(contract_structure())),
    ("structure.csv", lambda: structure_to_csv(contract_structure())),
    ("snapshot.json", lambda: snapshot_to_json(BomSnapshot(contract_structure(), P["frame"], "C", SourceRef(
        "workspace-revision", "doc_" + "f" * 32, "rev_" + "e" * 32, "C", 3, "ab" * 32, "BOM/frame.csv", "released")))),
    ("parts.csv", lambda: parts_to_csv(contract_parts())),
    ("structure.digest", lambda: (structure_digest(contract_structure()) + "\n").encode()),
    ("records.json", lambda: (json.dumps({
        "part": [p.to_record() for p in contract_parts()],
        "bomline": [line_to_record(ln) for ln in contract_structure().lines],
        "partrule": [NumberingRule("std", "P-", 6, "-A", 1, ((900, 999),)).to_record()],
    }, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")),
])
def test_writers_produce_the_frozen_bytes(name, make):
    assert make() == golden(name, make()), f"{name} changed: this is a data contract change (see module docstring)"


def test_readers_accept_the_frozen_bytes():
    s = contract_structure()
    assert load_any((GOLDEN / "structure.json").read_bytes()) == s
    assert structure_from_csv(GOLDEN / "structure.csv") == s
    snap = load_any((GOLDEN / "snapshot.json").read_bytes())
    assert snap.structure == s and snap.source.released and snap.digest == (GOLDEN / "structure.digest").read_text().strip()
    rows = parts_from_csv(GOLDEN / "parts.csv")
    assert [p for _, p, _ in rows] == sorted(contract_parts(), key=lambda p: (p.key, p.part_id))
    records = json.loads((GOLDEN / "records.json").read_text(encoding="utf-8"))
    assert [Part.from_record(r) for r in records["part"]] == contract_parts()
    assert tuple(line_from_record(r) for r in records["bomline"]) == s.lines
    assert NumberingRule.from_record(records["partrule"][0]).format(7) == "P-000007-A"


def test_golden_csv_is_spreadsheet_safe():
    text = (GOLDEN / "structure.csv").read_bytes()
    assert text.startswith(b"\xef\xbb\xbf") and b"\r\n" in text
    assert b"'=LABEL" in text and b"'+left side" in text
    assert b"'-B8" in (GOLDEN / "parts.csv").read_bytes()
