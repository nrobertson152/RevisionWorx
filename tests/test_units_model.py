# SPDX-License-Identifier: GPL-3.0-or-later
"""The unit register's values, strict loaders and history, without a workspace (docs/UNITS.md sections 3 to 5)."""
import copy

import pytest

from revisionworx.units import history as H
from revisionworx.units.model import (MalformedUnitRecord, ValidationError, check_date, check_identifier, clean, event_id,
                                      fieldworx_asset_id, key_id, load_event, load_key, load_standard, load_unit, norm,
                                      standard_digest, unit_label)

UNIT = "unt_" + "1" * 32
ACTOR = {"user": "dw", "host": "pc"}


def ev(n, prev, type_, body, effective="", time="2026-11-06T10:00:00+00:00", basis="declared"):
    return H.make_event(UNIT, n, prev, type_, body, effective=effective, time=time, actor=ACTOR, basis=basis, evidence=[],
                        reason="", change="", txid="", input_digest="")


def registered(n=1, idents=({"scheme": "serial", "issuer": "", "value": "NH-26-0001"},)):
    return ev(n, "", "unit.registered", {"class": "product", "name": "HPU", "model": {"part": "", "number": "HPU-75"},
                                         "status": "built", "project": "", "customer": "", "site": "", "location": "",
                                         "notes": "", "identifiers": [dict(i) for i in idents]})


def chain(*specs):
    """Events from (type, body, extra) specs, each chained to the one before."""
    out = [registered()]
    for type_, body, extra in specs:
        out.append(ev(len(out) + 1, out[-1]["digest"], type_, body, **extra))
    return out


# ------------------------------------------------------------------------------------------------- values
def test_identifiers_compare_like_people_type_them():
    assert norm("  NH-26-0001 ") == norm("nh-26-0001")
    assert norm("ＮＨ２６") == norm("nh26")                         # full-width forms (NFKC)
    assert norm("A  B\tC") == "a b c"
    assert key_id("serial", "", "NH-26-0001") == key_id("serial", "", " nh-26-0001")
    assert key_id("serial", "Parker", "123") != key_id("serial", "", "123")       # another maker's serial
    assert key_id("serial", "", "123") != key_id("tag", "", "123")


def test_a_fieldworx_asset_id_is_the_one_its_bridge_made():
    # fieldworx_bridge.packbuilder.asset_id: "ast_" + sha256(f"{workspace_id}\0{tag_or_serial.casefold()}")[:32]
    import hashlib
    ws = "ws_" + "a" * 32
    want = "ast_" + hashlib.sha256(f"{ws}\0hpu-0001".encode()).hexdigest()[:32]
    assert fieldworx_asset_id(ws, "HPU-0001") == want


@pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "14/03/2026", "2026-03-14 10:00", 20260314, "2026-03-14T25:00Z"])
def test_dates_are_real_dates(value):
    with pytest.raises(ValidationError):
        check_date(value, "date")


def test_dates_accepted():
    assert check_date("", "d") == "" and check_date("2026-03-14", "d") == "2026-03-14"
    assert check_date("2026-03-14T09:30Z", "d") and check_date("2026-03-14T09:30:05Z", "d")


def test_text_is_cleaned_and_limited():
    assert clean("  Pumṕ ", "x", 10) == "Pumṕ".strip() or clean("  Pump ", "x", 10) == "Pump"
    with pytest.raises(ValidationError):
        clean("a\x07b", "x", 10)
    with pytest.raises(ValidationError):
        clean("line\nbreak", "x", 20)                       # single-line field
    assert clean("line\r\nbreak", "x", 20, multiline=True) == "line\nbreak"
    with pytest.raises(ValidationError):
        clean("\ud800", "x", 10)
    with pytest.raises(ValidationError):
        clean("x" * 11, "x", 10)
    with pytest.raises(ValidationError):
        clean("", "x", 10, required=True)


def test_identifier_rules():
    assert check_identifier("serial", " NH-1 ", " Parker ") == {"scheme": "serial", "issuer": "Parker", "value": "NH-1"}
    for bad in (("serial", ""), ("colour", "red"), ("fieldworx", "HPU-0001")):
        with pytest.raises(ValidationError):
            check_identifier(*bad)
    with pytest.raises(ValidationError):
        check_identifier("fieldworx", "ast_" + "0" * 32, "someone")


# ------------------------------------------------------------------------------------------------- loaders
def test_loaders_refuse_anything_but_the_exact_schema():
    state, problems = H.reduce([registered()])
    assert problems == [] and load_unit(state) == state
    for mutate in (lambda r: r.pop("notes"), lambda r: r.update(extra=1), lambda r: r.update(schema=2),
                   lambda r: r.update(status="lost"), lambda r: r.update(identifiers=[{"scheme": "serial"}]),
                   lambda r: r.update(events={"count": 0, "head": "x"})):
        bad = copy.deepcopy(state)
        mutate(bad)
        with pytest.raises(MalformedUnitRecord):
            load_unit(bad)
    e = registered()
    assert load_event(e)["n"] == 1
    for mutate in (lambda r: r.update(type="unit.exploded"), lambda r: r["body"].pop("name"),
                   lambda r: r.update(schema=2), lambda r: r.update(basis="rumour")):
        bad = copy.deepcopy(e)
        mutate(bad)
        with pytest.raises(MalformedUnitRecord):
            load_event(bad)
    with pytest.raises(MalformedUnitRecord, match="schema 2"):
        load_key({"schema": 2, "scheme": "serial", "issuer": "", "value": "x", "owner": UNIT})
    std = {"schema": 1, "id": "bst_" + "2" * 32, "number": "A", "title": "", "model": {"part": "", "number": ""},
           "documents": {"doc_" + "3" * 32: "rev_" + "4" * 32}, "based_on": "", "change": "", "notes": "",
           "created": "t", "created_by": ACTOR}
    std["digest"] = standard_digest(std)
    assert load_standard(std)["number"] == "A"
    with pytest.raises(MalformedUnitRecord):
        load_standard({**std, "documents": {}})


# ------------------------------------------------------------------------------------------------- history
def test_reduction_follows_the_events():
    evs = chain(("identifier.added", {"scheme": "tag", "issuer": "Acme", "value": "P-101A"}, {}),
                ("status.changed", {"from": "built", "to": "in-service"}, {}),
                ("milestone", {"milestone": "commissioned"}, {"effective": "2026-04-01"}),
                ("component.installed", {"position": "P1", "part": "", "number": "PMP-10", "unit": "", "serial": "S1",
                                         "lot": ""}, {"effective": "2026-03-01"}),
                ("identifier.retired", {"scheme": "tag", "issuer": "Acme", "value": "p-101a"}, {}))
    state, problems = H.reduce(evs)
    assert problems == []
    assert state["status"] == "in-service" and state["milestones"]["commissioned"] == {"date": "2026-04-01", "event": 4}
    assert state["components"]["p1"]["serial"] == "S1" and state["components"]["p1"]["since"] == "2026-03-01"
    assert [(i["value"], i["active"]) for i in state["identifiers"]] == [("NH-26-0001", True), ("P-101A", False)]
    assert state["events"] == {"count": 6, "head": evs[-1]["digest"]}
    assert unit_label(state) == "NH-26-0001"


def test_a_void_removes_an_events_effect_but_not_the_event():
    evs = chain(("document.set", {"document": "doc_" + "3" * 32, "revision": "rev_" + "4" * 32}, {}),
                ("note", {"text": "checked"}, {}))
    evs.append(ev(4, evs[-1]["digest"], "void", {"events": [2]}))
    state, problems = H.reduce(evs)
    assert problems == [] and state["documents"] == {} and state["events"]["count"] == 4
    assert H.after(evs, 3)["documents"] != {}                  # before the void was recorded
    assert H.voided_by(evs) == {2}


def test_problems_are_reported_not_hidden():
    evs = chain(("component.removed", {"position": "P1", "disposition": "scrapped", "unit": ""}, {}),
                ("document.reset", {"document": "doc_" + "3" * 32}, {}))
    _, problems = H.reduce(evs)
    assert len(problems) == 2 and "empty" in problems[0]


def test_reconstruction_on_a_date_and_late_records():
    evs = chain(("status.changed", {"from": "built", "to": "in-service"}, {"effective": "2026-05-01"}),
                ("status.changed", {"from": "in-service", "to": "out-of-service"}, {"effective": "2026-07-01"}),
                ("note", {"text": "found later: pump swapped in June"}, {"effective": "2026-06-10"}))
    state, late = H.on(evs, "2026-06-15")
    assert state["status"] == "in-service" and late == []        # what happened by then is all in, in its own order
    state, late = H.on(evs, "2026-04-01")
    assert state["status"] == "built" and late == []
    state, late = H.on(evs, "2026-08-01")
    assert state["status"] == "out-of-service" and late == [4]   # recorded after something that happened later


def test_the_chain_check_finds_every_kind_of_damage():
    evs = chain(("note", {"text": "a"}, {}), ("note", {"text": "b"}, {}))
    store = {e["n"]: e for e in evs}
    head = evs[-1]["digest"]
    assert H.verify_chain(UNIT, 3, head, store.get) == []
    altered = {**store, 2: {**store[2], "body": {"text": "changed"}}}
    assert {p["problem"] for p in H.verify_chain(UNIT, 3, head, altered.get)} >= {"altered-event"}
    missing = {k: v for k, v in store.items() if k != 2}
    assert [p["problem"] for p in H.verify_chain(UNIT, 3, head, missing.get)] == ["missing-event"]
    swapped = {**store, 2: store[3], 3: store[2]}
    assert "misplaced-event" in {p["problem"] for p in H.verify_chain(UNIT, 3, head, swapped.get)}
    relinked = dict(store)
    fake = ev(3, "f" * 64, "note", {"text": "b"})
    relinked[3] = fake
    assert {p["problem"] for p in H.verify_chain(UNIT, 3, fake["digest"], relinked.get)} == {"broken-link"}
    extra = {**store, 4: ev(4, head, "note", {"text": "c"})}
    assert [p["problem"] for p in H.verify_chain(UNIT, 3, head, extra.get)] == ["unrecorded-event"]
    assert [p["problem"] for p in H.verify_chain(UNIT, 3, "0" * 64, store.get)] == ["head-mismatch"]
    assert event_id(UNIT, 2) == store[2]["id"]
