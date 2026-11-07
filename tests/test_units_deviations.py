# SPDX-License-Identifier: GPL-3.0-or-later
"""Deviations and concessions (docs/UNITS.md section 12): an approved departure from the documents for named units, written
once, with a record per unit of whether it is in effect, cleared or wasn't needed; retries, races, tampering, other programs
and the command line. Real workspace."""
import contextlib
import io
import json

import pytest

from revisionworx import documents as D, integration, projects, units
from revisionworx.projects import NewProject
from revisionworx.units import deviation as dv, query
from revisionworx.units.__main__ import main as unit_main
from revisionworx.units.model import (MalformedUnitRecord, NotFound, ReplayRefused, ValidationError, deviation_id,
                                      devunit_id, load_deviation, load_devunit)
from revisionworx.workspace import ConflictError, RecordDelete, RecordPut

TX1, TX2, TX3 = "d1" * 16, "e2" * 16, "f3" * 16
PAST, FUTURE = "2020-01-31", "2999-12-31"


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


def entries(ws):
    return len(ws.journal_seqs())


def serial(value):
    return {"scheme": "serial", "value": value, "issuer": ""}


@pytest.fixture
def fleet(ws):
    """BOM-75 rev A released (it calls for PMP-10 at P1); units NH-26-0001..3 with PMP-10 fitted at P1, and a scrapped one."""
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1"))
    path = projects.project_folder(ws, rec) / "bom.pdf"
    path.write_bytes(b"BOM-75 rev A")
    doc, rev = D.register_document(ws, rec["id"], path, number="BOM-75", title="HPU-75 BOM")
    rev = release(ws, rev["id"])
    svc = units.service_for(ws)
    made = {}
    for value in ("NH-26-0001", "NH-26-0002", "NH-26-0003", "NH-26-0099"):
        made[value] = svc.register(identifiers=[serial(value)], model={"part": "", "number": "HPU-75"},
                                   documents={doc["id"]: rev["id"]}, status="in-build")
        svc.install(made[value]["id"], "P1", number="PMP-10")
    svc.set_status(made["NH-26-0099"]["id"], "scrapped")
    return {"svc": svc, "doc": doc, "rev": rev, "units": made, "bom": f"BOM-75@{rev['label']}"}


def uid(fleet, value):
    return fleet["units"][value]["id"]


def substitute(fleet, *values, **kw):
    """DEV: PMP-11 instead of PMP-10 at P1, while PMP-10 is out of stock, until the right pump is fitted."""
    args = dict(title="PMP-11 for PMP-10 at P1", departure="P1 is a PMP-11 pump instead of the PMP-10 the BOM calls for",
                reason="PMP-10 out of stock until March; PMP-11 has the same flow and mounting", units=values or
                ("NH-26-0001", "NH-26-0002"), documents=(fleet["bom"],), temporary=True, until=FUTURE)
    args.update(kw)
    return dv.approve(fleet["svc"], "deviation", **args)


# ---------------------------------------------------------------------------------------------------- approving
def test_a_deviation_is_written_once_with_a_record_per_unit_in_one_entry(ws, fleet):
    svc = fleet["svc"]
    counts = {v: svc.unit(uid(fleet, v))["events"]["count"] for v in fleet["units"]}
    n = entries(ws)
    dev = substitute(fleet)
    assert entries(ws) == n + 1 and ws.read_entry(ws.head_seq())["op"] == "unit.deviation.approve"
    assert (dev["number"], dev["kind"], dev["temporary"], dev["until"]) == ("DEV-0001", "deviation", True, FUTURE)
    assert dev["id"] == deviation_id("DEV-0001") and dev["approved_by"]["user"]
    assert dev["units"] == sorted([uid(fleet, "NH-26-0001"), uid(fleet, "NH-26-0002")])
    assert dev["documents"] == [{"document": fleet["doc"]["id"], "revision": fleet["rev"]["id"]}]
    for v in ("NH-26-0001", "NH-26-0002"):
        rec = dv.devunit(svc, dev["id"], uid(fleet, v))
        assert rec["state"] == "in-effect" and len(rec["steps"]) == 1
    assert dv.devunit(svc, dev["id"], uid(fleet, "NH-26-0003")) is None
    assert {v: svc.unit(uid(fleet, v))["events"]["count"] for v in fleet["units"]} == counts, \
        "approving writes no unit history: the departure is in the work that follows"
    assert query.verify(svc) == []


def test_numbers_run_by_kind_and_two_never_share_one(ws, fleet):
    svc = fleet["svc"]
    assert substitute(fleet)["number"] == "DEV-0001"
    assert substitute(fleet, "NH-26-0003")["number"] == "DEV-0002"
    con = dv.approve(svc, "concession", title="Weld undercut on frame", departure="Frame weld W4 undercut 0.6 mm (0.5 allowed)",
                     reason="Load test passed at 150 %", units=["NH-26-0003"])
    assert con["number"] == "CON-0001" and con["temporary"] is False
    assert dv.approve(svc, "deviation", title="t", departure="d", reason="r", units=["NH-26-0003"], number="MRB-17")["number"] \
        == "MRB-17"
    assert substitute(fleet, "NH-26-0003")["number"] == "DEV-0003", "another numbering doesn't move DEV-"
    n = entries(ws)
    for taken in ("DEV-0002", "dev-0002", "mrb-17"):
        with pytest.raises(ValidationError, match="already"):
            substitute(fleet, "NH-26-0003", number=taken)
    assert entries(ws) == n
    assert dv.deviation(svc, "dev-0001")["id"] == deviation_id("DEV-0001")
    with pytest.raises(NotFound):
        dv.deviation(svc, "DEV-9999")


@pytest.mark.parametrize("change, why", [
    ({"units": ()}, "name the units"),
    ({"units": ("NH-26-0099",)}, "scrapped"),
    ({"temporary": False}, "only a temporary deviation has an end date"),
    ({"until": "31/01/2027"}, "end date"),
    ({"title": "  "}, "title"),
    ({"departure": ""}, "departure"),
    ({"reason": ""}, "reason"),
    ({"documents": ("BOM-75@A", "BOM-75@A")}, "named once"),
    ({"documents": ("BOM-75",)}, "DWG-100@B"),
])
def test_what_a_deviation_needs(ws, fleet, change, why):
    n = entries(ws)
    with pytest.raises(ValidationError, match=why):
        substitute(fleet, **change)
    assert entries(ws) == n


def test_an_unknown_unit_or_document_writes_nothing(ws, fleet):
    n = entries(ws)
    with pytest.raises(NotFound):
        substitute(fleet, "NH-404")
    with pytest.raises((NotFound, ValidationError)):
        substitute(fleet, documents=("DWG-404@A",))
    assert entries(ws) == n


# ------------------------------------------------------------------------------------------------- on each unit
def test_a_temporary_departure_is_cleared_when_the_unit_conforms_again(ws, fleet):
    svc = fleet["svc"]
    dev = substitute(fleet)
    u1 = uid(fleet, "NH-26-0001")
    svc.install(u1, "P1", number="PMP-11", replace=True, disposition="stock",
                evidence=[{"kind": "deviation", "ref": dev["number"], "label": ""}])
    fixed = svc.install(u1, "P1", number="PMP-10", replace=True, disposition="returned")
    n = fixed["events"]["count"]                          # the right pump fitted
    rec = dv.clear(svc, "NH-26-0001", "DEV-0001", events=[n], reason="PMP-10 back in stock and fitted")
    assert rec["state"] == "cleared" and rec["steps"][-1]["events"] == [svc.events(u1)[n - 1]["id"]]
    detail = query.unit_detail(svc, svc.unit(u1), history=False)
    assert [(x["number"], x["state"], x["in_effect"], x["overdue"]) for x in detail["deviations"]] == [
        ("DEV-0001", "cleared", False, False)]
    for again in (lambda: dv.clear(svc, "NH-26-0001", "DEV-0001", reason="twice"),
                  lambda: dv.not_used(svc, "NH-26-0001", "DEV-0001", reason="no")):
        with pytest.raises(ValidationError, match="cleared on the unit"):
            again()
    with pytest.raises(ValidationError, match="has no event 99"):
        dv.clear(svc, "NH-26-0002", "DEV-0001", events=[99])
    with pytest.raises(ValidationError, match="doesn't cover NH-26-0003"):
        dv.clear(svc, "NH-26-0003", "DEV-0001")
    assert query.verify(svc) == []


def test_a_voided_event_doesnt_clear_anything(fleet):
    svc = fleet["svc"]
    substitute(fleet)
    u2 = uid(fleet, "NH-26-0002")
    n = svc.note(u2, "pump swapped")["events"]["count"]
    svc.void(u2, [n], reason="wrong unit")
    with pytest.raises(ValidationError, match=f"event {n} on NH-26-0002 was voided"):
        dv.clear(svc, "NH-26-0002", "DEV-0001", events=[n])


def test_a_permanent_one_isnt_cleared_and_not_used_needs_a_reason(fleet):
    svc = fleet["svc"]
    con = dv.approve(svc, "concession", title="Weld undercut", departure="W4 undercut 0.6 mm", reason="load test passed",
                     units=["NH-26-0003"])
    with pytest.raises(ValidationError, match="permanent"):
        dv.clear(svc, "NH-26-0003", con["number"])
    with pytest.raises(ValidationError, match="reason"):
        dv.not_used(svc, "NH-26-0003", con["number"], reason="")
    assert dv.not_used(svc, "NH-26-0003", con["number"], reason="frame replaced before shipping")["state"] == "not-used"


def test_a_temporary_departure_past_its_end_date_is_overdue_until_cleared(fleet):
    svc = fleet["svc"]
    late = substitute(fleet, until=PAST)
    substitute(fleet, "NH-26-0003")                                       # ends in the future: not overdue
    rows = dv.overdue_units(svc)
    assert [(r["label"], [d["number"] for d in r["deviations"]]) for r in rows] == [
        ("NH-26-0001", [late["number"]]), ("NH-26-0002", [late["number"]])]
    first = query.unit_detail(svc, svc.unit(uid(fleet, "NH-26-0001")), history=False)["deviations"]
    assert [(x["number"], x["overdue"], x["until"]) for x in first] == [("DEV-0001", True, PAST)]
    dv.clear(svc, "NH-26-0001", "DEV-0001")
    dv.not_used(svc, "NH-26-0002", "DEV-0001", reason="built after PMP-10 arrived")
    assert dv.overdue_units(svc) == []
    assert [d["overdue"] for d in dv.listing(svc)] == [0, 0]


def test_a_unit_joins_later_with_a_reason(ws, fleet):
    svc = fleet["svc"]
    dev = substitute(fleet)
    with pytest.raises(ValidationError, match="reason"):
        dv.add(svc, dev["number"], "NH-26-0003", reason="")
    with pytest.raises(ValidationError, match="already covers NH-26-0001"):
        dv.add(svc, dev["number"], "NH-26-0001", reason="again")
    with pytest.raises(ValidationError, match="scrapped"):
        dv.add(svc, dev["number"], "NH-26-0099", reason="r")
    n = entries(ws)
    rec = dv.add(svc, dev["number"], "NH-26-0003", reason="third unit built during the shortage")
    assert entries(ws) == n + 1 and rec["state"] == "in-effect" and rec["steps"][0]["reason"].startswith("third unit")
    assert dv.deviation(svc, dev["number"])["units"] == dev["units"], "the approval stays as it was"
    assert [u["label"] for u in dv.status(svc, dev["number"])["covered"]] == ["NH-26-0001", "NH-26-0002", "NH-26-0003"]
    assert query.verify(svc) == []


# ------------------------------------------------------------------------------------------- retries and races
def test_retries_write_nothing_and_a_reused_txid_is_refused(ws, fleet):
    svc = fleet["svc"]
    dev = substitute(fleet, txid=TX1)
    n = entries(ws)
    again = substitute(fleet, txid=TX1)                    # numbered automatically: the retry answers the same one
    assert again["id"] == dev["id"] and again["number"] == "DEV-0001" and entries(ws) == n and svc.last_replayed
    with pytest.raises(ReplayRefused):
        substitute(fleet, reason="different", txid=TX1)
    dv.clear(svc, "NH-26-0001", dev["number"], txid=TX2)
    n = entries(ws)
    assert dv.clear(svc, "NH-26-0001", dev["number"], txid=TX2)["state"] == "cleared" and entries(ws) == n
    with pytest.raises(ReplayRefused):
        dv.not_used(svc, "NH-26-0002", dev["number"], reason="r", txid=TX2)
    with pytest.raises(ReplayRefused):
        dv.approve(svc, "concession", title="t", departure="d", reason="r", units=["NH-26-0003"], txid=TX2)
    assert entries(ws) == n


def test_two_approvals_of_one_number_at_once_one_conflicts(ws, fleet, monkeypatch):
    svc = fleet["svc"]
    real_commit = ws.commit
    other = units.service_for(ws)
    state = {"raced": False}

    def racing_commit(op, ops, *args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            monkeypatch.setattr(ws, "commit", real_commit)
            substitute({**fleet, "svc": other}, "NH-26-0003", title="someone else's")
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        substitute(fleet)
    [only] = dv.all_deviations(svc)[0]
    assert (only["number"], only["title"]) == ("DEV-0001", "someone else's")
    assert [r["unit"] for r in dv.all_devunits(svc)[0]] == [uid(fleet, "NH-26-0003")]
    assert substitute(fleet)["number"] == "DEV-0002", "tried again, it takes the next number"
    assert query.verify(svc) == []


# ------------------------------------------------------------------------------------------------------- tampering
def test_verify_reports_an_altered_deviation_and_impossible_records(ws, fleet):
    svc = fleet["svc"]
    dev = substitute(fleet)
    con = dv.approve(svc, "concession", title="Weld", departure="W4", reason="ok", units=["NH-26-0003"])
    rec = ws.get("unitdev", dev["id"])
    ws.commit("tamper", [RecordPut("unitdev", dev["id"], {**{k: v for k, v in rec.items() if k != "_rev"},
                                                          "until": "2999-01-01"}, expect_rev=rec["_rev"])])
    rid = devunit_id(con["id"], uid(fleet, "NH-26-0003"))
    r = ws.get("unitdvu", rid)
    cleared = r["steps"] + [{**r["steps"][0], "state": "cleared"}]       # a permanent concession can't be cleared
    ws.commit("tamper", [RecordPut("unitdvu", rid, {**{k: v for k, v in r.items() if k != "_rev"}, "steps": cleared,
                                                    "state": "cleared"}, expect_rev=r["_rev"])])
    gone = devunit_id(dev["id"], uid(fleet, "NH-26-0002"))
    ws.commit("tamper", [RecordDelete("unitdvu", gone, expect_rev=ws.get("unitdvu", gone)["_rev"])])
    codes = {(p["code"], p["subject"]) for p in query.verify(svc)}
    assert ("deviation-altered", dev["id"]) in codes
    assert ("deviation-invalid", rid) in codes and ("deviation-invalid", dev["id"]) in codes
    with pytest.raises(MalformedUnitRecord, match="altered"):
        dv.clear(svc, "NH-26-0001", dev["number"])                       # nothing is carried out from an altered approval
    assert [x["number"] for x in query.unit_detail(svc, svc.unit(uid(fleet, "NH-26-0001")))["deviations"]] == [], \
        "an altered approval isn't shown as if it held"


def test_the_loaders_refuse_anything_but_the_documented_shape(fleet):
    svc = fleet["svc"]
    dev = {k: v for k, v in substitute(fleet).items() if k != "_rev"}
    rec = {k: v for k, v in dv.devunit(svc, dev["id"], uid(fleet, "NH-26-0001")).items() if k != "_rev"}
    for bad in ({**dev, "extra": 1}, {**dev, "number": "DEV-0002"}, {**dev, "kind": "waiver"}, {**dev, "units": []},
                {**dev, "units": dev["units"] + dev["units"][:1]}, {**dev, "temporary": "yes"},
                {**dev, "temporary": False}, {**dev, "until": "soon"}, {**dev, "schema": 2},
                {**dev, "documents": dev["documents"] * 2}, {**dev, "title": ""}):
        with pytest.raises(MalformedUnitRecord):
            load_deviation(bad)
    step = rec["steps"][0]
    for bad in ({**rec, "steps": []}, {**rec, "state": "cleared"}, {**rec, "steps": [{**step, "state": "cleared"}],
                                                                   "state": "cleared"},
                {**rec, "steps": [step, {**step, "state": "cleared"}, {**step, "state": "not-used"}], "state": "not-used"},
                {**rec, "unit": uid(fleet, "NH-26-0002")}, {**rec, "steps": [{**step, "events": ["x"]}]}):
        with pytest.raises(MalformedUnitRecord):
            load_devunit(bad)


# ---------------------------------------------------------------------------------------------------- other programs
def test_a_units_answer_lists_its_deviations_in_effect_first(share, ws, fleet):
    svc = fleet["svc"]
    substitute(fleet)
    con = dv.approve(svc, "concession", title="Weld undercut", departure="W4 undercut 0.6 mm", reason="load test passed",
                     units=["NH-26-0001"])
    dv.clear(svc, "NH-26-0001", "DEV-0001")
    detail = query.unit_detail(svc, svc.unit(uid(fleet, "NH-26-0001")))
    assert [(x["number"], x["kind"], x["state"], x["in_effect"], x["temporary"]) for x in detail["deviations"]] == [
        (con["number"], "concession", "in-effect", True, False), ("DEV-0001", "deviation", "cleared", False, True)]
    cleared = detail["deviations"][1]
    assert cleared["documents"] == [{"document": fleet["doc"]["id"], "number": "BOM-75", "revision": fleet["rev"]["id"],
                                     "label": fleet["rev"]["label"]}]
    assert cleared["departure"].startswith("P1 is a PMP-11") and cleared["until"] == FUTURE
    assert "deviations" not in query.unit_detail(svc, svc.unit(uid(fleet, "NH-26-0001")), on="2026-01-01")
    ws.close()
    listed = {u["label"]: u for u in integration.list_units(workspace=share, detail=True)["units"]}
    assert [x["state"] for x in listed["NH-26-0002"]["deviations"]] == ["in-effect"]
    assert listed["NH-26-0003"]["deviations"] == []
    one = integration.unit("NH-26-0001", workspace=share)["unit"]
    assert [x["number"] for x in one["deviations"]] == [con["number"], "DEV-0001"]


def run(share, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = unit_main([str(share), *args])
        except SystemExit as exc:                     # the argument parser refuses before anything is opened
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def test_the_command_line_approves_clears_and_reports(share, ws, fleet):
    bom = fleet["bom"]
    ws.close()
    code, out, err = run(share, "deviation", "new", "--kind", "deviation", "--title", "PMP-11 for PMP-10",
                         "--departure", "PMP-11 at P1", "--reason", "shortage", "--unit", "NH-26-0001", "--unit",
                         "NH-26-0002", "--doc", bom, "--temporary", "--until", PAST, "--json")
    assert code == 0, err
    made = json.loads(out)
    assert made["number"] == "DEV-0001" and len(made["units"]) == 2
    code, out, _ = run(share, "deviation", "overdue")
    assert code == 1 and "2 unit(s) with a temporary deviation past its end date" in out
    assert run(share, "deviation", "clear", "NH-26-0001", "DEV-0001", "--event", "2", "--reason", "fitted PMP-10")[0] == 0
    code, _, err = run(share, "deviation", "not-used", "NH-26-0002", "DEV-0001")
    assert code == 2 and "--reason" in err
    assert run(share, "deviation", "not-used", "NH-26-0002", "DEV-0001", "--reason", "built later")[0] == 0
    assert run(share, "deviation", "overdue")[0] == 0
    code, _, err = run(share, "deviation", "new", "--kind", "concession", "--title", "t", "--departure", "d",
                       "--reason", "r", "--unit", "NH-26-0003", "--until", FUTURE)
    assert code == 2 and "only a temporary deviation has an end date" in err
    assert run(share, "deviation", "new", "--kind", "concession", "--title", "Weld", "--departure", "W4", "--reason", "ok",
               "--unit", "NH-26-0003")[0] == 0
    assert run(share, "deviation", "add", "CON-0001", "NH-26-0002", "--reason", "same weld")[0] == 0
    code, out, _ = run(share, "deviation", "list")
    assert code == 0 and "DEV-0001" in out and "CON-0001" in out and "in effect on 2 of 2" in out
    code, out, _ = run(share, "deviation", "show", "DEV-0001", "--json")
    st = json.loads(out)
    assert st["counts"] == {"cleared": 1, "not-used": 1} and [u["state"] for u in st["covered"]] == ["cleared", "not-used"]
    code, out, _ = run(share, "deviation", "show", "DEV-0001")
    assert "departs from BOM-75 rev" in out and "cleared" in out
    code, out, _ = run(share, "show", "NH-26-0002")
    assert "deviations and concessions:" in out and "CON-0001" in out and "DEV-0001" in out
    assert run(share, "verify")[0] == 0
