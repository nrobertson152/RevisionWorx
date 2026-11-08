# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory lots (docs/UNITS.md sections 4 and 8; the Director's decision of 2026-11-08 that OpenMeridian records the lots in
RevisionWorx): the units a completion builds are registered with the lots that completion used, in the same entry and under
the same txid. Each unit's history says which lots, and how many units were built with them; its record lists them; unit
show, link unit, where-used and the handover pack show them; a mistake is voided."""
import json

import pytest

from revisionworx import cli, units
from revisionworx.gui.viewmodels import unit_sections
from revisionworx.units import pack, query
from revisionworx.units.model import MalformedUnitRecord, ValidationError, load_event, load_unit
from revisionworx.workspace import Workspace
from test_integration_unit_records import TX, entries, link, plant  # noqa: F401  (the fixture and its helpers)

LOTS = [{"number": "PMP-10", "lot": "L-7", "quantity": "2"}, {"number": "BLT-M8", "lot": "B-2024-11", "quantity": "48"}]


def register(capsys, plant, txid, *serials, lots=LOTS, extra=()):
    return link(capsys, plant, "unit-register", f"--txid={txid}", "--model=HPU-75", "--status=built", "--built=2026-11-08",
                *[f"--serial={s}" for s in serials], *([f"--lots={json.dumps(lots)}"] if lots is not None else []),
                "--evidence=work-order:WO-17=completion 1", *extra)


def detail(capsys, plant, ref):
    code, out = link(capsys, plant, "unit", ref)
    assert code == 0, out
    return out["unit"]


def test_a_completion_registers_its_units_with_the_lots_it_used(capsys, plant):
    before = entries(plant)
    code, out = register(capsys, plant, TX[0], "NH-26-0101", "NH-26-0102", "NH-26-0103")
    assert code == 0 and out["result"] == "recorded", out
    assert entries(plant) == before + 1                       # the units and their lots: one entry
    for u in out["units"]:
        d = detail(capsys, plant, u["id"])
        assert [(x["number"], x["lot"], x["quantity"], x["units"], x["since"]) for x in d["factory_lots"]] == [
            ("PMP-10", "L-7", "2", 3, "2026-11-08"), ("BLT-M8", "B-2024-11", "48", 3, "2026-11-08")]
        used = [h for h in d["history"] if h["type"] == "lots.used"]
        assert len(used) == 1 and "PMP-10 lot L-7 (2)" in used[0]["summary"]
        assert "shared by the 3 units built together" in used[0]["summary"]
        assert {"kind": "work-order", "ref": "WO-17", "label": "completion 1"} in used[0]["evidence"]
    with Workspace.open(plant["folder"]) as ws:
        assert query.verify(units.service_for(ws)) == []


def test_one_unit_built_alone_holds_its_lots(capsys, plant):
    code, out = register(capsys, plant, TX[0], "NH-26-0101")
    assert code == 0, out
    d = detail(capsys, plant, "NH-26-0101")
    assert {x["units"] for x in d["factory_lots"]} == {1}
    assert "shared" not in [h for h in d["history"] if h["type"] == "lots.used"][0]["summary"]


def test_where_used_finds_the_units_a_lot_went_into(capsys, plant):
    register(capsys, plant, TX[0], "NH-26-0101", "NH-26-0102")
    for extra in ((), ("--history",)):
        code, out = link(capsys, plant, "where-used", "--part-number=PMP-10", "--lot=L-7", *extra)
        assert code == 0, out
        built = [r for r in out["rows"] if r.get("built_from")]
        assert sorted(r["unit"]["label"] for r in built) == ["NH-26-0101", "NH-26-0102"]
        assert all(r["built_from"] == {"units": 2, "quantity": "2"} and r["position"] == "" for r in built)
    code, out = link(capsys, plant, "where-used", "--part-number=PMP-10", "--lot=l-7")      # lots compare like identifiers
    assert len([r for r in out["rows"] if r.get("built_from")]) == 2
    code, out = link(capsys, plant, "where-used", "--part-number=PMP-10", "--serial=111")    # a factory lot has no serial
    assert not [r for r in out["rows"] if r.get("built_from")]


def test_a_retry_records_nothing_more_and_other_lots_under_the_txid_are_refused(capsys, plant):
    register(capsys, plant, TX[0], "NH-26-0101")
    before = entries(plant)
    code, out = register(capsys, plant, TX[0], "NH-26-0101")
    assert code == 0 and out["result"] == "already-recorded" and entries(plant) == before
    code, out = register(capsys, plant, TX[0], "NH-26-0101", lots=LOTS[:1])
    assert code != 0 and out["kind"] == "txid-used" and entries(plant) == before


def test_a_registration_without_lots_asks_for_what_it_asked_before(plant):
    """So a completion registered before lots were sent, retried with its txid, is still recognised."""
    with Workspace.open(plant["folder"]) as ws:
        svc = units.service_for(ws)
        assert "lots" not in svc._spec({"identifiers": [{"scheme": "serial", "value": "X-1"}]}, "unit")
        assert "lots" not in svc._spec({"identifiers": [{"scheme": "serial", "value": "X-1"}], "lots": []}, "unit")


@pytest.mark.parametrize("lots, why", [
    ([{"number": "PMP-10"}], "{number, lot, quantity}"),
    ([{"number": "PMP-10", "lot": ""}], "required"),
    ([{"number": "", "lot": "L-7"}], "required"),
    ([{"number": "PMP-10", "lot": "L-7"}, {"number": "pmp-10", "lot": "l-7"}], "given twice"),
    ([{"number": "PMP-10", "lot": "L-7", "colour": "red"}], "{number, lot, quantity}"),
    ([{"number": "PMP-10", "lot": "L\x007"}], "control characters"),
])
def test_bad_lots_are_refused_and_nothing_is_written(capsys, plant, lots, why):
    before = entries(plant)
    code, out = register(capsys, plant, TX[0], "NH-26-0101", lots=lots)
    assert code != 0 and out["kind"] == "bad-input" and why in out["error"], out
    assert entries(plant) == before


def test_lots_as_json_from_the_command_line_or_a_file(capsys, plant, tmp_path):
    f = tmp_path / "lots.json"
    f.write_text(json.dumps(LOTS), encoding="utf-8")
    code, out = register(capsys, plant, TX[0], "NH-26-0101", lots=None, extra=(f"--lots-file={f}",))
    assert code == 0, out
    assert len(detail(capsys, plant, "NH-26-0101")["factory_lots"]) == 2
    for args, why in (((f"--lots-file={f}", f"--lots={json.dumps(LOTS)}"), "not both"),
                      (("--lots=[{",), "is JSON"), (("--lots=[]",), "at least one lot"),
                      ((f"--lots-file={tmp_path / 'missing.json'}",), "can't be read")):
        code, out = register(capsys, plant, TX[1], "NH-26-0102", lots=None, extra=args)
        assert code != 0 and out["kind"] == "bad-input" and why in out["error"], (args, out)


def test_link_says_it_can(capsys, plant):
    code, out = link(capsys, plant, "version")
    assert "unit-lots" in out["features"]


def test_voiding_the_lots_takes_them_off_the_unit(capsys, plant):
    register(capsys, plant, TX[0], "NH-26-0101")
    n = next(h["n"] for h in detail(capsys, plant, "NH-26-0101")["history"] if h["type"] == "lots.used")
    with Workspace.open(plant["folder"], writable=True) as ws:
        svc = units.service_for(ws)
        u = svc.void(svc.resolve("NH-26-0101")["id"], [n], reason="the lots of another completion")
        assert "lots" not in u                                 # the record reads as one without factory lots
    d = detail(capsys, plant, "NH-26-0101")
    assert d["factory_lots"] == [] and [h["voided"] for h in d["history"] if h["type"] == "lots.used"] == [True]
    code, out = link(capsys, plant, "where-used", "--part-number=PMP-10", "--lot=L-7", "--history")
    assert not [r for r in out["rows"] if r.get("built_from")]


def test_the_record_as_it_stood(capsys, plant):
    register(capsys, plant, TX[0], "NH-26-0101")
    with Workspace.open(plant["folder"]) as ws:
        svc = units.service_for(ws)
        u = svc.resolve("NH-26-0101")
        n = next(e["n"] for e in svc.events(u["id"]) if e["type"] == "lots.used")
        assert query.unit_detail(svc, u, at=n - 1)["factory_lots"] == []
        assert len(query.unit_detail(svc, u, at=n)["factory_lots"]) == 2
        assert query.unit_detail(svc, u, on="2026-11-07")["factory_lots"] == []
        assert len(query.unit_detail(svc, u, on="2026-11-08")["factory_lots"]) == 2


def test_unit_show_the_pack_and_verify_show_them(capsys, plant, tmp_path):
    register(capsys, plant, TX[0], "NH-26-0101", "NH-26-0102")
    capsys.readouterr()
    assert cli.main(["-w", str(plant["folder"]), "unit", "show", "NH-26-0101"]) == 0
    text = capsys.readouterr().out
    assert "built from these lots (shared by the 2 units built together" in text and "BLT-M8" in text
    with Workspace.open(plant["folder"], writable=True) as ws:
        svc = units.service_for(ws)
        made = pack.make(svc, "NH-26-0101", tmp_path / "pack")
        assert made["recorded"]
        assert query.verify(svc) == []
    record = json.loads((tmp_path / "pack" / "unit.json").read_text(encoding="utf-8"))
    assert [x["lot"] for x in record["unit"]["factory_lots"]] == ["L-7", "B-2024-11"]
    page = (tmp_path / "pack" / "index.html").read_text(encoding="utf-8")
    assert "Built from these lots" in page and "B-2024-11" in page and "Shared by the 2 units built together" in page
    assert pack.check(tmp_path / "pack")["ok"]


def test_a_record_without_lots_still_reads_and_a_damaged_one_is_refused(capsys, plant):
    register(capsys, plant, TX[0], "NH-26-0101")
    with Workspace.open(plant["folder"]) as ws:
        svc = units.service_for(ws)
        rec = dict(ws.get("unit", svc.resolve("NH-26-0101")["id"]))
        assert "lots" in load_unit(rec)
        old = dict(ws.get("unit", plant["unit"]))
        assert "lots" not in old and "lots" not in load_unit(old)
        for bad in ([], [{**rec["lots"][0], "units": 0}], [{k: v for k, v in rec["lots"][0].items() if k != "basis"}]):
            with pytest.raises(MalformedUnitRecord):
                load_unit({**rec, "lots": bad})
        ev = next(e for e in svc.events(rec["id"]) if e["type"] == "lots.used")
        for body in ({**ev["body"], "units": 0}, {**ev["body"], "lots": []},
                     {**ev["body"], "lots": [{"number": "PMP-10", "lot": " L-7 "}]}):   # not as it would have been written
            with pytest.raises(MalformedUnitRecord):
                load_event({**ev, "body": body})


def test_a_lot_is_given_by_the_service_too(plant):
    with Workspace.open(plant["folder"], writable=True) as ws:
        svc = units.service_for(ws)
        with pytest.raises(ValidationError, match="from 1 to 1000"):
            svc.register(identifiers=[{"scheme": "serial", "value": "X-1"}],
                         lots=[{"number": "P", "lot": str(i)} for i in range(1001)])
        u = svc.register(identifiers=[{"scheme": "serial", "value": "X-1"}], lots=[{"number": "P", "lot": "1"}])
        assert [(x["number"], x["lot"], x["quantity"], x["units"]) for x in u["lots"]] == [("P", "1", "", 1)]


def test_the_history_line_stays_one_line(plant):
    with Workspace.open(plant["folder"], writable=True) as ws:
        svc = units.service_for(ws)
        u = svc.register(identifiers=[{"scheme": "serial", "value": "X-1"}],
                         lots=[{"number": "P", "lot": f"L{i}"} for i in range(12)])
        line = query.unit_detail(svc, u)["history"][-1]["summary"]
        assert line.startswith("built from P lot L0; ") and line.endswith("P lot L9; and 2 more lots")
        assert len(u["lots"]) == 12


def test_the_units_window_shows_them(capsys, plant):
    register(capsys, plant, TX[0], "NH-26-0101", "NH-26-0102")
    sections = dict(unit_sections(detail(capsys, plant, "NH-26-0101")))
    assert sections["Built from these lots"] == [
        "PMP-10 lot L-7 (2)", "BLT-M8 lot B-2024-11 (48)",
        "Shared by the 2 units built together: this one holds some of these lots, not necessarily all."]
    assert "Built from these lots" not in dict(unit_sections(detail(capsys, plant, "NH-26-0001")))
