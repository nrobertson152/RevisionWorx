# SPDX-License-Identifier: GPL-3.0-or-later
"""The unit register on a real workspace: real documents and revisions, the journaled commit path, claims, conflicts,
retries, lost locks and tampering (docs/UNITS.md sections 4 to 7). Local filesystem, single writer."""
import pytest

from revisionworx import documents as D, projects, units
from revisionworx.projects import NewProject
from revisionworx.units import query
from revisionworx.units.model import (Ambiguous, DuplicateIdentifier, MalformedUnitRecord, NotFound, ReplayRefused,
                                      ValidationError, event_id, key_id)
from revisionworx.workspace import ConflictError, LockLostError, ReadOnlyError, RecordDelete, RecordPut, Workspace

TX1, TX2 = "a1" * 16, "b2" * 16


def serial(value, issuer=""):
    return {"scheme": "serial", "value": value, "issuer": issuer}


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


def revise(ws, doc, path, content, released=True):
    co = D.checkout(ws, doc["id"])
    path.write_bytes(content)
    rev = D.checkin(ws, doc["id"], co["token"], "revised")
    return release(ws, rev["id"]) if released else rev


@pytest.fixture
def eng(ws):
    """Project J-1 with FRM-200 rev A and SCH-12 rev A released, and DWG-9 rev A still work in progress."""
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    out = {"project": rec}
    for key, name, number, released in (("frm", "frame.pdf", "FRM-200", True), ("sch", "schematic.pdf", "SCH-12", True),
                                        ("dwg", "detail.pdf", "DWG-9", False)):
        path = folder / name
        path.write_bytes(f"{number} rev A".encode())
        doc, rev = D.register_document(ws, rec["id"], path, number=number, title=number)
        out[key] = (doc, release(ws, rev["id"]) if released else rev, path)
    return out


@pytest.fixture
def svc(ws):
    return units.service_for(ws)


@pytest.fixture
def std_a(svc, eng):
    (frm, frm_a, _), (sch, sch_a, _) = eng["frm"], eng["sch"]
    return svc.create_standard("HPU-75-A", "HPU-75 standard A", {frm["id"]: frm_a["id"], sch["id"]: sch_a["id"]},
                               model={"part": "", "number": "HPU-75"})


def entries(ws):
    return len(ws.journal_seqs())


def unit(svc, value="NH-26-0001", **spec):
    return svc.register(identifiers=[serial(value)], model={"part": "", "number": "HPU-75"}, **spec)


# ------------------------------------------------------------------------------------------------ registering
def test_registering_is_one_entry_with_its_events_record_and_claims(ws, svc, eng, std_a):
    before = entries(ws)
    u = unit(svc, project=eng["project"]["id"], standard=std_a["id"], milestones={"built": "2026-03-14"}, status="built")
    assert entries(ws) == before + 1
    assert ws.read_entry(ws.head_seq())["op"] == "unit.register"
    assert u["id"].startswith("unt_") and u["status"] == "built" and u["standard"]["id"] == std_a["id"]
    assert [e["type"] for e in svc.events(u["id"])] == ["unit.registered", "standard.set", "milestone"]
    assert svc.events(u["id"])[1]["effective"] == "2026-03-14"          # the configuration it was built to dates from then
    claim = ws.get("unitkey", key_id("serial", "", "NH-26-0001"))
    assert claim["owner"] == u["id"]
    with Workspace.open(ws.meta_dir.parent) as ro:                       # a fresh reader sees the same unit
        assert units.service_for(ro).unit(u["id"]) == u
    assert query.verify(svc) == []


def test_one_serial_one_unit_compared_as_people_type_it(svc):
    first = unit(svc, "NH-26-0001")
    for same in ("nh-26-0001", " NH-26-0001 ", "ＮＨ-26-0001"):
        with pytest.raises(DuplicateIdentifier) as exc:
            unit(svc, same)
        assert exc.value.owner == first["id"] and "NH-26-0001" in str(exc.value)
    other = svc.register(identifiers=[serial("NH-26-0001", "Parker")])      # another maker's serial is another identifier
    assert other["id"] != first["id"]
    with pytest.raises(Ambiguous):
        svc.resolve("NH-26-0001")
    assert svc.resolve(first["id"])["id"] == first["id"]


def test_registering_several_is_all_or_nothing(ws, svc, eng):
    before = entries(ws)
    with pytest.raises(DuplicateIdentifier):
        svc.register_many([{"identifiers": [serial("A-1")]}, {"identifiers": [serial("a-1")]}])
    bad = {"identifiers": [serial("A-3")], "documents": {eng["frm"][0]["id"]: eng["sch"][1]["id"]}}   # SCH's revision
    with pytest.raises(ValidationError, match="another document"):
        svc.register_many([{"identifiers": [serial("A-2")]}, bad])
    assert entries(ws) == before and svc.units() == [] and ws.list("unitkey") == {}
    made = svc.register_many([{"identifiers": [serial("A-1")]}, {"identifiers": [serial("A-2")]}])
    assert entries(ws) == before + 1 and len(made) == 2


def test_a_claim_taken_between_check_and_commit_is_a_conflict(ws, svc, monkeypatch):
    """Two people registering the same serial at the same moment: the claim is written with expect_rev=0, so the core refuses
    the second entry even if its check ran before the first committed."""
    real_commit = ws.commit
    calls = []

    def racing_commit(op, ops, *args, **kwargs):
        if not calls:
            calls.append(op)
            kid = key_id("serial", "", "NH-26-0001")
            real_commit("someone.else", [RecordPut("unitkey", kid, {"schema": 1, "scheme": "serial", "issuer": "",
                                                                    "value": "NH-26-0001", "owner": "unt_" + "e" * 32},
                                                   expect_rev=0)])
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        unit(svc)
    assert svc.units() == []


def test_a_unit_changed_since_it_was_read_is_a_conflict(ws, svc, monkeypatch):
    u = unit(svc)
    real_commit = ws.commit
    other = units.service_for(ws)
    state = {"raced": False}

    def racing_commit(op, ops, *args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            monkeypatch.setattr(ws, "commit", real_commit)
            other.update(u["id"], customer="Someone else")      # lands between this action's read and its commit
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        svc.update(u["id"], customer="Acme")
    assert svc.unit(u["id"])["customer"] == "Someone else"
    assert [e["type"] for e in svc.events(u["id"])] == ["unit.registered", "unit.updated"]


# ------------------------------------------------------------------------------------------------ identifiers
def test_a_tag_moves_on_and_a_serial_stays(svc):
    old = unit(svc, "NH-1")
    svc.add_identifier(old["id"], "tag", "P-101A", "Acme")
    new = unit(svc, "NH-2")
    with pytest.raises(DuplicateIdentifier):
        svc.add_identifier(new["id"], "tag", "P-101A", "Acme")
    svc.retire_identifier(old["id"], "tag", "p-101a", "acme")                  # the old machine leaves position P-101A
    svc.add_identifier(new["id"], "tag", "P-101A", "Acme")
    assert [h["unit"]["id"] for h in svc.find("P-101A")] == [old["id"], new["id"]]
    assert [h["why"] for h in svc.find("P-101A")] == ["tag (former)", "tag"]
    svc.retire_identifier(old["id"], "serial", "NH-1")                          # re-serialized: the old serial still names it
    with pytest.raises(DuplicateIdentifier):
        svc.add_identifier(new["id"], "serial", "NH-1")
    with pytest.raises(ValidationError, match="reason"):
        svc.remove_identifier(old["id"], "serial", "NH-1")
    svc.remove_identifier(old["id"], "serial", "NH-1", reason="typed instead of NH-01")
    svc.add_identifier(new["id"], "serial", "NH-1")                             # a mistaken entry frees its value
    assert query.verify(svc) == []


def test_identifier_refusals(svc):
    u = unit(svc)
    with pytest.raises(ValidationError, match="already has"):
        svc.add_identifier(u["id"], "serial", "nh-26-0001")
    with pytest.raises(ValidationError, match="no current"):
        svc.retire_identifier(u["id"], "tag", "T-1")
    with pytest.raises(ValidationError):
        svc.add_identifier(u["id"], "fieldworx", "not-an-asset-id")


# ------------------------------------------------------------------------------------------------ status and fields
def test_status_rules(svc):
    u = unit(svc)
    with pytest.raises(ValidationError, match="already"):
        svc.set_status(u["id"], "planned")
    svc.set_status(u["id"], "retired")
    with pytest.raises(ValidationError, match="reason"):
        svc.set_status(u["id"], "in-service")
    svc.set_status(u["id"], "in-service", reason="refurbished and resold")
    svc.set_status(u["id"], "scrapped")
    with pytest.raises(ValidationError, match="scrapped"):
        svc.set_status(u["id"], "in-service", reason="?")


def test_updating_records_only_what_changed(svc, eng):
    u = unit(svc)
    u = svc.update(u["id"], project="J-1", customer="Acme", cls="product")
    assert u["project"] == eng["project"]["id"] and u["customer"] == "Acme"
    assert svc.events(u["id"])[-1]["body"] == {"fields": {"project": eng["project"]["id"], "customer": "Acme"}}
    with pytest.raises(ValidationError, match="nothing to change"):
        svc.update(u["id"], customer="Acme")
    with pytest.raises(ValidationError, match="can't update"):
        svc.update(u["id"], status="built")


# ------------------------------------------------------------------------------------------------ build standards
def test_build_standards_are_checked_unique_and_written_once(ws, svc, eng, std_a):
    (frm, frm_a, _), (_, sch_a, _), (dwg, dwg_a, _) = eng["frm"], eng["sch"], eng["dwg"]
    with pytest.raises(DuplicateIdentifier):
        svc.create_standard("hpu-75-a", "copy", {frm["id"]: frm_a["id"]})
    with pytest.raises(ValidationError, match="not released"):
        svc.create_standard("HPU-75-PROTO", "", {dwg["id"]: dwg_a["id"]})
    proto = svc.create_standard("HPU-75-PROTO", "", {dwg["id"]: dwg_a["id"]}, allow_unreleased=True)
    with pytest.raises(ValidationError, match="another document"):
        svc.create_standard("BAD", "", {frm["id"]: sch_a["id"]})
    assert [s["number"] for s in svc.standards()] == ["HPU-75-A", "HPU-75-PROTO"]
    assert svc.resolve_standard("hpu-75-proto")["id"] == proto["id"]
    altered = {**ws.get("buildstd", std_a["id"]), "title": "quietly changed"}
    ws.commit("tamper", [RecordPut("buildstd", std_a["id"], altered)])
    assert [p["code"] for p in query.verify(svc)] == ["standard-altered"]
    with pytest.raises(MalformedUnitRecord):
        svc.standard(std_a["id"])


# ------------------------------------------------------------------------------------------------ documents
def test_a_unit_keeps_its_revisions_when_a_newer_one_is_released(ws, svc, eng, std_a):
    (frm, frm_a, frm_path) = eng["frm"]
    u = unit(svc, standard=std_a["id"])
    frm_b = revise(ws, frm, frm_path, b"FRM-200 rev B")
    rows = {r["document"]["number"]: r for r in query.applicable_documents(svc, svc.unit(u["id"]))["rows"]}
    assert rows["FRM-200"]["revision"]["id"] == frm_a["id"]                 # never the newer one
    assert rows["FRM-200"]["flags"] == ["newer-released"] and rows["FRM-200"]["released"]["label"] == "B"
    assert rows["SCH-12"]["flags"] == [] and rows["SCH-12"]["source"] == "standard"
    svc.set_document(u["id"], frm["id"], frm_b["id"], basis="verified", reason="retrofit")
    rows = {r["document"]["number"]: r for r in query.applicable_documents(svc, svc.unit(u["id"]))["rows"]}
    assert rows["FRM-200"]["revision"]["label"] == "B" and rows["FRM-200"]["source"] == "unit"
    assert rows["FRM-200"]["basis"] == "verified" and rows["FRM-200"]["flags"] == []
    assert [r["unit"]["id"] for r in query.units_using_revision(svc, frm_a["id"])] == []
    other = unit(svc, "NH-26-0002", standard=std_a["id"])
    assert [r["unit"]["id"] for r in query.units_using_revision(svc, frm_a["id"])] == [other["id"]]
    svc.reset_document(u["id"], frm["id"])
    assert query.applicable_documents(svc, svc.unit(u["id"]))["rows"][0]["revision"]["label"] == "A"
    with pytest.raises(ValidationError, match="no difference"):
        svc.reset_document(u["id"], frm["id"])


def test_not_applicable_unreleased_and_unrecorded(svc, eng, std_a):
    (dwg, dwg_a, _), (sch, _, _) = eng["dwg"], eng["sch"]
    bare = unit(svc, "NH-0")
    assert query.applicable_documents(svc, bare) == {"recorded": False, "standard": None, "standard_problem": "", "rows": []}
    u = unit(svc, standard=std_a["id"])
    svc.set_document(u["id"], sch["id"], "")                                   # an option not fitted
    svc.set_document(u["id"], dwg["id"], dwg_a["id"])                          # a prototype detail
    rows = {r["document"]["number"]: r for r in query.applicable_documents(svc, svc.unit(u["id"]))["rows"]}
    assert rows["SCH-12"]["applies"] is False and rows["SCH-12"]["revision"] is None
    assert rows["DWG-9"]["flags"] == ["not-released"]
    with pytest.raises(ValidationError, match="already records"):
        svc.set_document(u["id"], sch["id"], "")


def test_a_damaged_reference_is_shown_as_missing_never_replaced(ws, svc, std_a):
    u = unit(svc, standard=std_a["id"])
    rec = ws.get("unit", u["id"])
    ghost = "doc_" + "9" * 32
    ws.commit("tamper", [RecordPut("unit", u["id"], {**rec, "documents": {ghost: {"revision": "rev_" + "9" * 32, "event": 1,
                                                                                    "basis": "declared"}}})])
    rows = query.applicable_documents(svc, svc.unit(u["id"]))["rows"]
    assert [r["flags"] for r in rows if r["document"]["id"] == ghost] == [["missing"]]
    codes = {p["code"] for p in query.verify(svc)}
    assert {"record-mismatch", "dangling-reference"} <= codes


def test_many_units_move_to_a_new_standard_in_one_entry(ws, svc, eng, std_a):
    (frm, frm_a, frm_path) = eng["frm"]
    fleet = svc.register_many([{"identifiers": [serial(f"NH-{i}")], "standard": std_a["id"]} for i in range(5)])
    frm_b = revise(ws, frm, frm_path, b"FRM-200 rev B")
    std_b = svc.create_standard("HPU-75-B", "after ECO", {**std_a["documents"], frm["id"]: frm_b["id"]},
                                based_on=std_a["id"])
    svc.set_document(fleet[0]["id"], frm["id"], frm_b["id"])                  # retrofitted early, on its own
    before = entries(ws)
    moved = svc.set_standard([u["id"] for u in fleet[:3]], std_b["id"], clear_differences=True, basis="verified")
    assert entries(ws) == before + 1
    assert all(u["standard"]["id"] == std_b["id"] for u in moved) and moved[0]["documents"] == {}
    assert svc.events(fleet[0]["id"])[-1]["body"] == {"standard": std_b["id"], "cleared": [frm["id"]]}
    assert {u["id"] for u in query.units_on_standard(svc, std_a["id"])} == {u["id"] for u in fleet[3:]}
    with pytest.raises(ValidationError, match="already conforms"):
        svc.set_standard([fleet[1]["id"]], std_b["id"])
    assert query.verify(svc) == []


# ------------------------------------------------------------------------------------------------ components
def test_fitting_replacing_and_tracing_a_component(svc):
    hpu = unit(svc)
    pump1 = svc.register(identifiers=[serial("P-111", "Parker")], cls="component", model={"part": "", "number": "PMP-10"})
    pump2 = svc.register(identifiers=[serial("P-222", "Parker")], cls="component", model={"part": "", "number": "PMP-10"})
    svc.install(hpu["id"], "P1", component=pump1["id"], effective="2026-03-14")
    svc.install(hpu["id"], "M1", number="MTR-7.5", serial="M-9")
    with pytest.raises(ValidationError, match="replace"):
        svc.install(hpu["id"], "p1", component=pump2["id"])
    svc.install(hpu["id"], "P1", component=pump2["id"], replace=True, disposition="repair", effective="2028-05-02")
    hpu_now = svc.unit(hpu["id"])
    assert hpu_now["components"]["p1"]["unit"] == pump2["id"] and hpu_now["components"]["p1"]["number"] == "PMP-10"
    assert svc.unit(pump1["id"])["parent"] is None
    assert svc.unit(pump2["id"])["parent"] == {"unit": hpu["id"], "position": "P1"}
    trail = query.fitted_history(svc, pump1["id"])
    assert [(t["in"], t["position"], t["removed"] is not None) for t in trail] == [(hpu["id"], "P1", True)]
    where = query.units_with_component(svc, number="PMP-10", history=True)
    assert sorted((w["current"], w.get("disposition")) for w in where) == [(False, "repair"), (True, None)]
    assert [w["position"] for w in query.units_with_component(svc, serial="m-9")] == ["M1"]
    svc.remove(hpu["id"], "M1", "scrapped")
    with pytest.raises(ValidationError, match="nothing is recorded"):
        svc.remove(hpu["id"], "M1")
    assert query.verify(svc) == []


def test_a_component_is_in_one_place_and_never_inside_itself(svc):
    a, b = unit(svc, "A"), unit(svc, "B")
    pump = svc.register(identifiers=[serial("P-1")], cls="component")
    svc.install(a["id"], "P1", component=pump["id"])
    with pytest.raises(ValidationError, match="fitted in unit A"):
        svc.install(b["id"], "P1", component=pump["id"])
    with pytest.raises(ValidationError, match="itself"):
        svc.install(a["id"], "X1", component=a["id"])
    with pytest.raises(ValidationError, match="contains"):
        svc.install(pump["id"], "X1", component=a["id"])                   # A holds the pump: it can't go inside it
    with pytest.raises(ValidationError, match="say what is fitted"):
        svc.install(a["id"], "X2")


def test_two_installs_of_one_component_race_and_one_conflicts(ws, svc, monkeypatch):
    a, b = unit(svc, "A"), unit(svc, "B")
    pump = svc.register(identifiers=[serial("P-1")], cls="component")
    real_commit = ws.commit
    other = units.service_for(ws)
    raced = []

    def racing_commit(op, ops, *args, **kwargs):
        if not raced:
            raced.append(op)
            monkeypatch.setattr(ws, "commit", real_commit)
            other.install(b["id"], "P1", component=pump["id"])
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        svc.install(a["id"], "P1", component=pump["id"])
    assert svc.unit(pump["id"])["parent"]["unit"] == b["id"] and svc.unit(a["id"])["components"] == {}


# ------------------------------------------------------------------------------------------------ corrections
def test_voiding_corrects_without_rewriting(svc, eng):
    (frm, frm_a, _) = eng["frm"]
    u = unit(svc)
    svc.set_document(u["id"], frm["id"], frm_a["id"])
    svc.note(u["id"], "checked on site")
    svc.void(u["id"], [2], "recorded on the wrong unit")
    now = svc.unit(u["id"])
    assert now["documents"] == {} and now["events"]["count"] == 4
    assert [e["type"] for e in svc.events(u["id"])] == ["unit.registered", "document.set", "note", "void"]
    with pytest.raises(ValidationError, match="already void"):
        svc.void(u["id"], [2], "again")
    with pytest.raises(ValidationError, match="can't be voided"):
        svc.void(u["id"], [1], "no")
    with pytest.raises(ValidationError, match="no event"):
        svc.void(u["id"], [99], "no")
    with pytest.raises(ValidationError, match="reason"):
        svc.void(u["id"], [3], "")
    detail = query.unit_detail(svc, now)
    assert [h["voided"] for h in detail["history"]] == [False, True, False, False]
    assert query.verify(svc) == []


def test_voiding_a_component_event_takes_its_mirror_with_it(svc):
    hpu, other = unit(svc, "H"), unit(svc, "O")
    p1 = svc.register(identifiers=[serial("P-1")], cls="component")
    p2 = svc.register(identifiers=[serial("P-2")], cls="component")
    svc.install(hpu["id"], "P1", component=p1["id"])
    svc.install(hpu["id"], "P1", component=p2["id"], replace=True)             # events 3 (removed) and 4 (fitted)
    with pytest.raises(ValidationError, match="event 4 also records P1"):
        svc.void(hpu["id"], [3], "wrong")
    svc.void(hpu["id"], [3, 4], "the pump was never changed")
    assert svc.unit(hpu["id"])["components"]["p1"]["unit"] == p1["id"]
    assert svc.unit(p1["id"])["parent"] == {"unit": hpu["id"], "position": "P1"} and svc.unit(p2["id"])["parent"] is None
    assert query.verify(svc) == []
    svc.install(other["id"], "P9", component=p2["id"])
    fitted = svc.events(other["id"])[-1]["n"]
    svc.remove(other["id"], "P9")
    svc.install(hpu["id"], "P2", component=p2["id"])                            # the pump has moved on since
    with pytest.raises(ValidationError, match="also records P9"):
        svc.void(other["id"], [fitted], "wrong")
    with pytest.raises(ValidationError, match="moved since"):
        svc.void(other["id"], [fitted, fitted + 1], "wrong")
    assert query.verify(svc) == []


# ------------------------------------------------------------------------------------------------ retries and authority
def test_a_retry_with_the_same_txid_writes_nothing_and_returns_the_unit(ws, svc):
    first = svc.register(identifiers=[serial("NH-1")], txid=TX1)
    n = entries(ws)
    again = svc.register(identifiers=[serial("NH-1")], txid=TX1)
    assert again["id"] == first["id"] and entries(ws) == n and svc.last_replayed
    with pytest.raises(ReplayRefused):
        svc.register(identifiers=[serial("NH-2")], txid=TX1)
    svc.update(first["id"], customer="Acme", txid=TX2)
    svc.update(first["id"], customer="Acme", txid=TX2)
    assert entries(ws) == n + 1 and svc.last_replayed
    with pytest.raises(ReplayRefused):
        svc.update(first["id"], customer="Other", txid=TX2)


def test_a_writer_whose_lock_was_broken_writes_nothing(share, ws, svc):
    u = unit(svc)
    n = entries(ws)
    Workspace.break_writer_lock(share, Workspace.read_writer_lock(share)["token"], "test")
    with pytest.raises(LockLostError):
        svc.note(u["id"], "too late")
    with Workspace.open(share) as ro:
        assert len(ro.journal_seqs()) == n + 1                                  # only the lock break's own audit entry
        assert units.service_for(ro).unit(u["id"])["events"]["count"] == 1


def test_reads_work_on_a_read_only_handle(share, ws, svc):
    u = unit(svc)
    with Workspace.open(share) as ro:
        r = units.service_for(ro)
        assert r.unit(u["id"])["id"] == u["id"] and r.find("nh-26-0001")[0]["why"] == "serial"
        with pytest.raises(ReadOnlyError):
            r.note(u["id"], "read-only")


# ------------------------------------------------------------------------------------------------ tampering
def test_verify_finds_altered_records_events_and_claims(ws, svc):
    u = unit(svc)
    svc.note(u["id"], "first")
    rec = ws.get("unit", u["id"])
    ws.commit("tamper", [RecordPut("unit", u["id"], {**rec, "customer": "changed by hand"})])
    assert [p["code"] for p in query.verify(svc)] == ["record-mismatch"]
    n = entries(ws)
    with pytest.raises(MalformedUnitRecord, match="doesn't match its history"):
        svc.note(u["id"], "nothing is added on top of a damaged record")
    assert entries(ws) == n
    ws.commit("tamper", [RecordPut("unit", u["id"], rec)])
    ev2 = ws.get("unitevt", event_id(u["id"], 2))
    ws.commit("tamper", [RecordPut("unitevt", ev2["id"], {**ev2, "body": {"text": "rewritten"}})])
    assert "altered-event" in {p["code"] for p in query.verify(svc)}
    with pytest.raises(MalformedUnitRecord, match="altered"):
        svc.note(u["id"], "an action refuses a damaged history")
    ws.commit("tamper", [RecordPut("unitevt", ev2["id"], ev2)])
    kid = key_id("serial", "", "NH-26-0001")
    ws.commit("tamper", [RecordDelete("unitkey", kid)])
    assert [p["code"] for p in query.verify(svc)] == ["claim-missing"]


def test_unknown_references_are_refused_before_anything_is_written(ws, svc):
    n = entries(ws)
    for spec in ({"project": "prj_" + "0" * 32}, {"standard": "bst_" + "0" * 32},
                 {"documents": {"doc_" + "0" * 32: ""}}, {"milestones": {"built": "2026-02-30"}},
                 {"status": "lost"}, {"model": {"part": "prt_" + "0" * 32, "number": "X"}}):
        with pytest.raises((ValidationError, NotFound)):
            svc.register(identifiers=[serial("NH-X")], **spec)
    assert entries(ws) == n


def test_reconstruction_through_the_detail(svc, eng, std_a):
    (frm, frm_a, _) = eng["frm"]
    u = unit(svc, standard=std_a["id"], milestones={"built": "2026-03-14"})
    svc.set_status(u["id"], "in-service", effective="2026-04-01")
    svc.set_document(u["id"], frm["id"], "", effective="2027-01-10", reason="option removed")
    now = svc.unit(u["id"])
    then = query.unit_detail(svc, now, on="2026-06-01")
    assert then["status"] == "in-service" and all(r["applies"] for r in then["documents"])
    early = query.unit_detail(svc, now, on="2026-01-01")
    assert early["status"] == "planned" and early["configuration_recorded"] is False
    first = query.unit_detail(svc, now, at=1)
    assert first["standard"] is None and first["as_of"] == {"event": 1}
    with pytest.raises(NotFound):
        query.unit_detail(svc, now, at=99)
