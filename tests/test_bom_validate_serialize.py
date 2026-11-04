# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: structure validation, canonical JSON, digests and snapshot loading (fail closed)."""
import json
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

from bom_helpers import frame, line, part
from revisionworx.bom import (BomFormatError, BomSnapshot, InMemoryCatalog, SourceRef, Structure, load_any,
                              snapshot_from_json, snapshot_to_json, structure_digest, structure_from_json,
                              structure_to_json, validate)
from revisionworx.bom.units import Limits


def codes(report):
    return sorted(set(report.codes()))


def test_clean_structure_validates():
    rep = validate(frame(), roots=["frame"])
    assert rep.ok and rep.issues == ()


def test_every_structural_problem_is_reported_together():
    s = Structure.build([
        line("a", "a"),                                           # self reference (and cycle)
        line("a", "b", "1.5", find_number="1"),                   # EA must be whole
        line("a", "c", "2", find_number="1", unit="parsec"),      # duplicate find number, unknown unit
        line("a", "d", "2", reference_designators="R1 R2 R3"),    # refdes count mismatch
        line("a", "e", "1", reference_designators="R1"),          # duplicate refdes R1 (with d)
        line("a", "e", "1", provenance="inferred"),               # duplicate child, needs verification
        line("f", "g", "1", unit="kg"),                           # g is stocked in metres
        line("orphan", "b"),
    ], [part("g", unit="m"), part("b", "DUP"), part("c", "dup"), part("u", resolved=False)])
    rep = validate(s, roots=["a", "nope"])
    assert codes(rep) == sorted(["self-reference", "cycle", "invalid-quantity", "duplicate-find-number", "unknown-unit",
                                 "refdes-count-mismatch", "duplicate-refdes", "duplicate-child", "needs-verification",
                                 "unit-mismatch", "duplicate-part-number", "unknown-root", "orphan-assembly",
                                 "missing-part-info", "unresolved-identity"])
    assert not rep.ok
    assert [i.severity for i in rep.issues] == sorted((i.severity for i in rep.issues),
                                                      key=["error", "warning", "info"].index)


def test_refdes_ranges_are_not_counted():
    s = Structure.build([line("a", "r", "4", reference_designators="R1-R4")], [part("a"), part("r")])
    assert validate(s).ok and "refdes-count-mismatch" not in validate(s).codes()


def test_dangling_references_against_the_catalog():
    cat = InMemoryCatalog([part("frame"), part("sub"), part("bolt")])
    rep = validate(frame(), catalog=cat)
    assert [(i.code, i.child_id) for i in rep.errors] == [("dangling-reference", "cable")]


def test_depth_limit_is_a_validation_error():
    s = Structure.build([line(f"n{k}", f"n{k + 1}") for k in range(10)])
    assert "too-deep" in validate(s, limits=Limits(max_depth=5)).codes()
    assert "too-deep" in validate(s, roots=["n0"], limits=Limits(max_depth=5)).codes()


def test_validation_is_deterministic():
    s = frame().with_lines(line("bolt", "frame"), line("frame", "x", "0.5"))
    assert validate(s) == validate(Structure.build(reversed(s.lines), s.parts))


# ------------------------------------------------------------------------------------------ JSON
def test_json_roundtrip_is_exact_and_canonical():
    s = frame().with_lines(line("frame", "label", "1", note="Ünïcode ✓ 中文", attributes={"Finish": "Zn"},
                                provenance="cad-extracted", line_id="L-9", child_revision="C"))
    data = structure_to_json(s)
    back = structure_from_json(data)
    assert back == s and structure_to_json(back) == data
    assert structure_digest(back) == structure_digest(s)
    assert b'"quantity": "500"' in data  # decimal text, never a JSON number
    assert "Ünïcode ✓ 中文" in data.decode("utf-8")


def test_digest_ignores_order_and_unicode_composition_but_not_content():
    s = frame()
    nfd = Structure.build([line("frame", "café", "1")], [part("café", "Café")])
    nfc = Structure.build([line("frame", "café", "1")], [part("café", "Café")])
    assert structure_digest(nfd) == structure_digest(nfc)
    assert structure_digest(Structure.build(reversed(s.lines), reversed(s.parts))) == structure_digest(s)
    changed = s.replacing_line(s.lines[0], s.lines[0].with_changes(quantity=D("2.0")))
    assert structure_digest(changed) == structure_digest(s)  # 2.0 == 2: same quantity
    changed = s.replacing_line(s.lines[0], s.lines[0].with_changes(quantity=D("2.001")))
    assert structure_digest(changed) != structure_digest(s)


def test_digest_is_stable_across_processes():
    """Restart check: a fresh interpreter (different hash seed) computes the same digest."""
    tests = Path(__file__).resolve().parent
    code = (f"import sys; sys.path[:0] = [{str(tests)!r}, {str(tests.parent)!r}]; from bom_helpers import frame; "
            "from revisionworx.bom import structure_digest; print(structure_digest(frame()))")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                           env=_pyenv() | {"PYTHONHASHSEED": seed}).stdout.strip()
            for seed in ("1", "2", "3")}
    assert outs == {structure_digest(frame())}


def _pyenv():
    import os
    keep = {k: v for k, v in os.environ.items() if k in ("PYTHONPATH", "SYSTEMROOT", "PATH", "HOME", "TEMP", "TMP")}
    return keep


def test_snapshot_roundtrip_keeps_source_and_verifies_digest():
    src = SourceRef("workspace-revision", "doc_1", "rev_2", "B", 2, "ab" * 32, "BOM/frame.csv", "released")
    snap = BomSnapshot(frame(), "frame", "Rev B", src)
    data = snapshot_to_json(snap)
    back = snapshot_from_json(data)
    assert back == snap and back.released and back.display_name == "FRAME-100 Rev B"
    assert isinstance(load_any(data), BomSnapshot) and load_any(structure_to_json(frame())) == frame()
    tampered = data.replace(b'"quantity": "500"', b'"quantity": "501"')
    with pytest.raises(BomFormatError, match="digest does not match"):
        snapshot_from_json(tampered)


@pytest.mark.parametrize("mutate,fragment", [
    (lambda d: d.update(format="other"), "not a RevisionWorx BOM"),
    (lambda d: d.update(version=2), "newer than this program"),
    (lambda d: d.update(version=0), "unsupported"),
    (lambda d: d.update(version="1"), "integer version"),
    (lambda d: d.update(extra=1), "unknown field"),
    (lambda d: d["structure"]["lines"][0].update(quantity=2), "decimal text"),
    (lambda d: d["structure"]["lines"][0].update(quantity="-2"), "negative"),
    (lambda d: d["structure"]["lines"][0].update(colour="red"), "unknown field"),
    (lambda d: d["structure"]["lines"][0].pop("child_id"), "missing field"),
    (lambda d: d["structure"]["parts"][0].update(resolved="yes"), "boolean"),
    (lambda d: d["structure"].update(lines={}), "must be lists"),
    (lambda d: d.update(kind="snapshot"), "missing field"),
])
def test_loading_fails_closed(mutate, fragment):
    doc = json.loads(structure_to_json(frame()))
    mutate(doc)
    with pytest.raises(BomFormatError, match=fragment):
        load_any(json.dumps(doc).encode())


@pytest.mark.parametrize("data,fragment", [(b"\xff\xfe", "UTF-8"), (b"{", "malformed"), (b"[]", "not a RevisionWorx"),
                                           (b'{"format":"revisionworx.bom","version":1,"kind":"structure",'
                                            b'"structure":{"lines":[],"parts":[]},"x":1.5}', "JSON number"),
                                           (b'{"format":"revisionworx.bom","version":NaN}', "constant")])
def test_malformed_json_is_refused(data, fragment):
    with pytest.raises(BomFormatError, match=fragment):
        load_any(data)


def test_snapshot_root_must_exist():
    from revisionworx.bom import BomError
    with pytest.raises(BomError):
        BomSnapshot(frame(), "missing")
