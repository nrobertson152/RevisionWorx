# SPDX-License-Identifier: GPL-3.0-or-later
"""Engineering changes reaching units (docs/UNITS.md section 10): effectivity beside a change order, compliance per unit, the
documents of a unit moving only through its own history, retries, races and tampering. Real workspace, real change orders."""
import io
import json

import pytest

from revisionworx import changes, documents as D, projects, units
from revisionworx.projects import NewProject
from revisionworx.units import effect, query
from revisionworx.units.__main__ import main as unit_main
from revisionworx.units.model import (MalformedUnitRecord, NotFound, ReplayRefused, ValidationError, compliance_id,
                                      effectivity_id)
from revisionworx.workspace import ConflictError, LockLostError, RecordPut, Workspace

TX1, TX2, TX3 = "a1" * 16, "b2" * 16, "c3" * 16


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


def revise(ws, doc, path, content, released=True):
    co = D.checkout(ws, doc["id"])
    path.write_bytes(content)
    rev = D.checkin(ws, doc["id"], co["token"], "revised")
    return release(ws, rev["id"]) if released else rev


def entries(ws):
    return len(ws.journal_seqs())


def serial(value):
    return {"scheme": "serial", "value": value, "issuer": ""}


@pytest.fixture
def plant(ws):
    """FRM-200 and SCH-12 rev A released; standard HPU-75-A names both; units NH-26-0001..3, NH-9 and NH-10 on it (customers
    Acme and Bolt); a scrapped one; one where FRM-200 doesn't apply. Then ECO-0001 revises FRM-200 to rev B and is implemented."""
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    out = {"project": rec}
    for key, name, number in (("frm", "frame.pdf", "FRM-200"), ("sch", "schematic.pdf", "SCH-12")):
        path = folder / name
        path.write_bytes(f"{number} rev A".encode())
        doc, rev = D.register_document(ws, rec["id"], path, number=number, title=number)
        out[key] = (doc, release(ws, rev["id"]), path)
    svc = units.service_for(ws)
    (frm, frm_a, frm_path), (sch, sch_a, _) = out["frm"], out["sch"]
    std = svc.create_standard("HPU-75-A", "HPU-75 standard A", {frm["id"]: frm_a["id"], sch["id"]: sch_a["id"]})
    made = {}
    for value, customer in (("NH-26-0001", "Acme"), ("NH-26-0002", "Acme"), ("NH-26-0003", "Bolt"), ("NH-9", "Bolt"),
                            ("NH-10", "Bolt"), ("NH-26-0099", "Acme"), ("NH-26-0050", "Acme")):
        made[value] = svc.register(identifiers=[serial(value)], standard=std["id"], customer=customer, status="in-service")
    svc.set_status(made["NH-26-0099"]["id"], "scrapped")
    svc.set_document(made["NH-26-0050"]["id"], frm["id"], "")          # FRM-200 doesn't apply to this one
    cs = changes.service_for(ws)
    ecr = cs.create_request("Thicker frame", "Fatigue cracks", affected=[{"item_type": "document", "item_id": frm["id"]}])
    cs.submit(ecr.id)
    cs.begin_review(ecr.id)
    cs.approve(ecr.id)
    eco = cs.create_order([ecr.id])
    cs.start_work(eco.id)
    frm_b = revise(ws, frm, frm_path, b"FRM-200 rev B")
    cs.record_implementation(eco.id, cs.get(eco.id).affected[0].key, rev_id=frm_b["id"])
    cs.mark_implemented(eco.id)
    out.update(svc=svc, std=std, units=made, eco=cs.get(eco.id), frm_b=frm_b, cs=cs)
    return out


def uid(plant, value):
    return plant["units"][value]["id"]


def frm_row(svc, plant, value):
    detail = query.unit_detail(svc, svc.unit(uid(plant, value)), history=False)
    return next(r for r in detail["documents"] if r["document"]["number"] == "FRM-200")


# ------------------------------------------------------------------------------------------------- candidates
def test_candidates_hold_the_revision_the_change_replaces(plant):
    svc, eco = plant["svc"], plant["eco"]
    rows = effect.order_rows(eco)
    assert rows == [{"document": plant["frm"][0]["id"], "from": plant["frm"][1]["id"], "to": plant["frm_b"]["id"]}]
    found = [query.unit_summary(c["unit"])["label"] for c in effect.candidates(svc, eco)]
    assert found == ["NH-9", "NH-10", "NH-26-0001", "NH-26-0002", "NH-26-0003"]     # people's order; not the scrapped
    # one, not the one FRM-200 doesn't apply to


def test_a_change_request_or_an_unknown_order_is_refused(plant):
    svc = plant["svc"]
    ecr = plant["cs"].get(plant["eco"].request_ids[0])
    with pytest.raises(ValidationError, match="change request"):
        effect.order(svc, ecr.number)
    with pytest.raises(NotFound):
        effect.order(svc, "ECO-9999")


# ---------------------------------------------------------------------------------------------------- deciding
def test_deciding_a_required_retrofit_is_one_entry_with_a_record_per_unit(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    n = entries(ws)
    eff = effect.decide(svc, eco.number, "retrofit-required", reason="cracks found in service")
    assert entries(ws) == n + 1 and ws.read_entry(ws.head_seq())["op"] == "unit.change.decide"
    assert eff["id"] == effectivity_id(eco.id) and eff["kind"] == "retrofit-required"
    assert eff["scope"] == {"rule": "candidates", "value": ""} and len(eff["units"]) == 5
    assert eff["documents"] == effect.order_rows(eco)
    for value in ("NH-26-0001", "NH-10"):
        rec = effect.compliance(svc, eco.id, uid(plant, value))
        assert rec["required"] and rec["state"] == "pending" and rec["id"] == compliance_id(eco.id, uid(plant, value))
    assert effect.compliance(svc, eco.id, uid(plant, "NH-26-0050")) is None
    assert len(effect.out_of_compliance(svc)) == 5
    assert frm_row(svc, plant, "NH-26-0001")["revision"]["label"] == "A"      # deciding moves no document
    assert query.verify(svc) == []
    with pytest.raises(ValidationError, match="written once"):
        effect.decide(svc, eco.number, "retrofit-optional", reason="again")
    assert entries(ws) == n + 1


def test_an_order_is_decided_once_it_is_implemented(ws, plant):
    svc, cs = plant["svc"], plant["cs"]
    frm = plant["frm"][0]
    ecr = cs.create_request("Another", "why", affected=[{"item_type": "document", "item_id": frm["id"]}])
    cs.submit(ecr.id)
    cs.begin_review(ecr.id)
    cs.approve(ecr.id)
    eco = cs.create_order([ecr.id])
    n = entries(ws)
    with pytest.raises(ValidationError, match="once it is implemented"):
        effect.decide(svc, eco.number, "retrofit-required", reason="r")
    eff = effect.decide(svc, eco.number, "production", standard="HPU-75-A", reason="new units only")   # needs no rows
    assert eff["kind"] == "production" and eff["units"] == [] and eff["scope"]["rule"] == "none"
    assert entries(ws) == n + 1


def test_production_needs_a_standard_and_reaches_no_unit(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    with pytest.raises(ValidationError, match="standard"):
        effect.decide(svc, eco.number, "production", reason="r")
    with pytest.raises(ValidationError, match="no scope"):
        effect.decide(svc, eco.number, "production", standard="HPU-75-A", rule="candidates", reason="r")
    frm, sch = plant["frm"][0], plant["sch"]
    std_b = svc.create_standard("HPU-75-B", "B", {frm["id"]: plant["frm_b"]["id"], sch[0]["id"]: sch[1]["id"]},
                                change=eco.id)
    eff = effect.decide(svc, eco.number, "production", standard="HPU-75-B", reason="from serial 4 on")
    assert eff["standard"] == std_b["id"] and effect.all_compliance(svc) == ([], [])
    assert query.verify(svc) == []


@pytest.mark.parametrize("rule,value,named,expected", [
    ("serial-range", "NH-9..NH-26-0002", (), ["NH-9", "NH-10", "NH-26-0001", "NH-26-0002"]),
    ("customer", "bolt", (), ["NH-9", "NH-10", "NH-26-0003"]),
    ("standard", "HPU-75-A", (), ["NH-9", "NH-10", "NH-26-0001", "NH-26-0002", "NH-26-0003"]),
    ("units", "", ("NH-26-0002", "NH-26-0050"), ["NH-26-0002", "NH-26-0050"]),    # named units needn't be candidates
])
def test_scope_rules_choose_the_units(plant, rule, value, named, expected):
    svc, eco = plant["svc"], plant["eco"]
    eff = effect.decide(svc, eco.number, "retrofit-required", rule=rule, value=value, units=named, reason="r")
    labels = sorted(query.unit_summary(svc.unit(u))["label"] for u in eff["units"])
    assert labels == sorted(expected) and eff["scope"] == {"rule": rule, "value": value}


def test_a_scope_that_chooses_nothing_or_a_bad_range_is_refused(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    n = entries(ws)
    with pytest.raises(ValidationError, match="chooses no unit"):
        effect.decide(svc, eco.number, "retrofit-required", rule="customer", value="Nobody", reason="r")
    with pytest.raises(ValidationError, match="FIRST..LAST"):
        effect.decide(svc, eco.number, "retrofit-required", rule="serial-range", value="NH-1", reason="r")
    with pytest.raises(ValidationError, match="comes after"):
        effect.decide(svc, eco.number, "retrofit-required", rule="serial-range", value="NH-26-0009..NH-26-0001", reason="r")
    with pytest.raises(ValidationError, match="scrapped"):
        effect.decide(svc, eco.number, "retrofit-required", rule="units", units=["NH-26-0099"], reason="r")
    with pytest.raises(ValidationError, match="exactly one"):
        effect.decide(svc, eco.number, "unit", rule="units", units=["NH-26-0001", "NH-26-0002"], reason="r")
    with pytest.raises(ValidationError, match="reason"):
        effect.decide(svc, eco.number, "retrofit-required", reason="")
    assert entries(ws) == n


def test_two_decisions_at_once_one_conflicts(ws, plant, monkeypatch):
    """The decision's id comes from the order's and is written with expect_rev=0: a decision recorded between this one's check
    and its commit makes this one a conflict, and nothing of it is written."""
    svc, eco = plant["svc"], plant["eco"]
    real_commit = ws.commit
    other = units.service_for(ws)
    state = {"raced": False}

    def racing_commit(op, ops, *args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            monkeypatch.setattr(ws, "commit", real_commit)
            effect.decide(other, eco.number, "retrofit-optional", reason="someone else")
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        effect.decide(svc, eco.number, "retrofit-required", reason="mine")
    assert effect.effectivity(svc, eco.id)["kind"] == "retrofit-optional"
    assert {r["required"] for r in effect.all_compliance(svc)[0]} == {False}
    assert query.verify(svc) == []


def test_documentation_effectivity_moves_documents_in_one_entry(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    n = entries(ws)
    eff = effect.decide(svc, eco.number, "documentation", reason="dimension typo")
    assert entries(ws) == n + 1 and len(eff["units"]) == 5
    row = frm_row(svc, plant, "NH-26-0001")
    assert row["revision"]["label"] == "B" and row["source"] == "unit" and row["basis"] == "declared"
    ev = svc.events(uid(plant, "NH-26-0001"))[-1]
    assert ev["type"] == "document.set" and ev["change"] == eco.id and ev["reason"].startswith(effect.DOC_CORRECTION)
    assert effect.all_compliance(svc) == ([], [])                     # nothing to comply with: no machine changed
    assert frm_row(svc, plant, "NH-26-0050")["applies"] is False       # out of scope: untouched
    assert query.verify(svc) == []


# ---------------------------------------------------------------------------------------------------- compliance
def test_install_reported_then_verify_moves_documents_once(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="cracks")
    u = uid(plant, "NH-26-0001")
    n = entries(ws)
    rec = effect.install(svc, "NH-26-0001", eco.number, effective="2026-11-02", reason="frame swapped",
                         evidence=[{"kind": "inspection", "ref": "ins_" + "1" * 32, "label": "field report"}])
    assert entries(ws) == n + 1 and rec["state"] == "installed"
    row = frm_row(svc, plant, "NH-26-0001")
    assert (row["revision"]["label"], row["source"], row["basis"]) == ("B", "unit", "reported")   # shown, flagged reported
    ev = svc.events(u)[-1]
    assert (ev["type"], ev["change"], ev["effective"]) == ("document.set", eco.id, "2026-11-02")
    assert rec["steps"][-1]["events"] == [ev["id"]]
    assert [c["unit"] for c in effect.out_of_compliance(svc)].count(u) == 1       # installed is not yet done
    rec = effect.verify(svc, "NH-26-0001", eco.number, reason="seen on site")
    assert rec["state"] == "verified" and u not in [c["unit"] for c in effect.out_of_compliance(svc)]
    row = frm_row(svc, plant, "NH-26-0001")
    assert (row["revision"]["label"], row["basis"]) == ("B", "verified")
    assert [e["type"] for e in svc.events(u)][-2:] == ["document.set", "verified"]   # the document didn't move twice
    assert query.verify(svc) == []
    with pytest.raises(ValidationError, match="only an installed change"):
        effect.verify(svc, "NH-26-0002", eco.number)


def test_install_seen_by_someone_goes_straight_to_verified(plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="cracks")
    rec = effect.install(svc, "NH-26-0002", eco.number, basis="verified")
    assert rec["state"] == "verified" and frm_row(svc, plant, "NH-26-0002")["basis"] == "verified"


def test_install_refuses_a_unit_with_another_revision_and_writes_nothing(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    frm, _, path = plant["frm"]
    frm_c = revise(ws, frm, path, b"FRM-200 rev C")
    svc.set_document(uid(plant, "NH-26-0003"), frm["id"], frm_c["id"])
    effect.decide(svc, eco.number, "retrofit-required", rule="units", units=["NH-26-0003", "NH-26-0050"], reason="r")
    n = entries(ws)
    with pytest.raises(ValidationError, match="by hand"):
        effect.install(svc, "NH-26-0003", eco.number)
    with pytest.raises(ValidationError, match="not applicable"):
        effect.install(svc, "NH-26-0050", eco.number)                  # FRM-200 doesn't apply to it
    assert entries(ws) == n
    rec = effect.set_state(svc, "NH-26-0050", eco.number, "not-applicable", reason="FRM-200 isn't on this variant")
    assert rec["state"] == "not-applicable" and uid(plant, "NH-26-0050") not in [
        c["unit"] for c in effect.out_of_compliance(svc)]


def test_a_unit_already_at_the_new_revision_installs_without_moving(plant):
    svc, eco = plant["svc"], plant["eco"]
    svc.set_document(uid(plant, "NH-26-0001"), plant["frm"][0]["id"], plant["frm_b"]["id"], change=eco.id)
    effect.decide(svc, eco.number, "retrofit-required", rule="units", units=["NH-26-0001"], reason="r")
    count = svc.unit(uid(plant, "NH-26-0001"))["events"]["count"]
    rec = effect.install(svc, "NH-26-0001", eco.number)
    assert rec["state"] == "installed" and rec["steps"][-1]["events"] == []
    assert svc.unit(uid(plant, "NH-26-0001"))["events"]["count"] == count
    assert effect.verify(svc, "NH-26-0001", eco.number)["state"] == "verified"    # nothing to upgrade, still recorded


def test_states_follow_the_table_and_ends_are_ends(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r")
    assert effect.set_state(svc, "NH-26-0001", eco.number, "scheduled", effective="2026-12-01")["state"] == "scheduled"
    with pytest.raises(ValidationError, match="needs a reason"):
        effect.set_state(svc, "NH-26-0001", eco.number, "deferred")
    assert effect.set_state(svc, "NH-26-0001", eco.number, "deferred", reason="customer shutdown in March")["state"] == \
        "deferred"
    with pytest.raises(ValidationError, match="can become"):
        effect.set_state(svc, "NH-26-0001", eco.number, "accepted")      # accepting is for optional changes
    effect.set_state(svc, "NH-26-0001", eco.number, "not-applicable", reason="frame already replaced by the customer")
    n = entries(ws)
    for attempt in (lambda: effect.install(svc, "NH-26-0001", eco.number),
                    lambda: effect.set_state(svc, "NH-26-0001", eco.number, "pending")):
        with pytest.raises(ValidationError, match="nothing else"):
            attempt()
    assert entries(ws) == n
    steps = effect.compliance(svc, eco.id, uid(plant, "NH-26-0001"))["steps"]
    assert [s["state"] for s in steps] == ["pending", "scheduled", "deferred", "not-applicable"]


def test_an_optional_retrofit_is_offered_and_never_outstanding(plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-optional", rule="customer", value="Bolt", reason="offered to Bolt")
    assert effect.compliance(svc, eco.id, uid(plant, "NH-9"))["state"] == "offered"
    assert effect.out_of_compliance(svc) == []
    effect.set_state(svc, "NH-9", eco.number, "declined", reason="customer said no")
    effect.set_state(svc, "NH-9", eco.number, "accepted")
    rec = effect.install(svc, "NH-9", eco.number)
    assert [s["state"] for s in rec["steps"]] == ["offered", "declined", "accepted", "installed"]
    assert effect.out_of_compliance(svc) == []


def test_reversing_moves_documents_back_and_voids_nothing(plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r")
    effect.install(svc, "NH-26-0001", eco.number, basis="verified")
    with pytest.raises(ValidationError, match="reason"):
        effect.reverse(svc, "NH-26-0001", eco.number, reason="")
    rec = effect.reverse(svc, "NH-26-0001", eco.number, reason="new frame cracked; old one refitted")
    assert rec["state"] == "reversed"
    assert frm_row(svc, plant, "NH-26-0001")["revision"]["label"] == "A"
    types = [e["type"] for e in svc.events(uid(plant, "NH-26-0001"))]
    assert "void" not in types and types.count("document.set") == 2
    out = effect.out_of_compliance(svc)
    assert uid(plant, "NH-26-0001") in [c["unit"] for c in out]          # a reversed required change is outstanding


def test_a_unit_found_later_joins_a_retrofit(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", rule="customer", value="Acme", reason="r")
    before = effect.effectivity(svc, eco.id)
    rec = effect.add(svc, eco.number, "NH-10")
    assert rec["state"] == "pending" and rec["steps"][0]["reason"] == "added after the decision"
    assert effect.effectivity(svc, eco.id) == before                     # the decision is as it was
    with pytest.raises(ValidationError, match="already"):
        effect.add(svc, eco.number, "NH-10")
    assert query.verify(svc) == []


# ------------------------------------------------------------------------------------------------ retries, locks
def test_retries_write_nothing_and_a_reused_txid_is_refused(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r", txid=TX1)
    n = entries(ws)
    again = effect.decide(svc, eco.number, "retrofit-required", reason="r", txid=TX1)
    assert entries(ws) == n and svc.last_replayed and again["kind"] == "retrofit-required"
    effect.install(svc, "NH-26-0001", eco.number, txid=TX2)
    n = entries(ws)
    assert effect.install(svc, "NH-26-0001", eco.number, txid=TX2)["state"] == "installed" and entries(ws) == n
    with pytest.raises(ReplayRefused):
        effect.set_state(svc, "NH-26-0001", eco.number, "scheduled", txid=TX2)   # same txid, other action
    with pytest.raises(ReplayRefused):
        effect.install(svc, "NH-26-0001", eco.number, reason="different", txid=TX2)
    assert entries(ws) == n


def test_a_writer_whose_lock_was_broken_records_nothing(share, ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r")
    n = entries(ws)
    Workspace.break_writer_lock(share, Workspace.read_writer_lock(share)["token"], "test")
    with pytest.raises(LockLostError):
        effect.install(svc, "NH-26-0001", eco.number)
    with Workspace.open(share) as ro:
        r = units.service_for(ro)
        assert len(ro.journal_seqs()) == n + 1                           # only the lock break's own audit entry
        assert effect.compliance(r, eco.id, uid(plant, "NH-26-0001"))["state"] == "pending"


# ------------------------------------------------------------------------------------------------------- tampering
def test_verify_reports_an_altered_decision_and_impossible_compliance(ws, plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r")
    effect.install(svc, "NH-26-0001", eco.number)
    eid = effectivity_id(eco.id)
    eff = ws.get("uniteff", eid)
    ws.commit("tamper", [RecordPut("uniteff", eid, {**{k: v for k, v in eff.items() if k != "_rev"}, "kind": "unit"},
                                   expect_rev=eff["_rev"])])
    cid = compliance_id(eco.id, uid(plant, "NH-26-0002"))
    rec = ws.get("unitcmp", cid)
    bad_steps = rec["steps"] + [{**rec["steps"][0], "state": "reversed"}]              # pending can't become reversed
    ws.commit("tamper", [RecordPut("unitcmp", cid, {**{k: v for k, v in rec.items() if k != "_rev"}, "steps": bad_steps,
                                                    "state": "reversed"}, expect_rev=rec["_rev"])])
    codes = {(p["code"], p["subject"]) for p in query.verify(svc)}
    assert ("effectivity-altered", eid) in codes and ("compliance-invalid", cid) in codes
    with pytest.raises(MalformedUnitRecord):
        effect.set_state(svc, "NH-26-0002", eco.number, "scheduled")    # nothing is added on top of a damaged record
    with pytest.raises(MalformedUnitRecord, match="altered"):
        effect.install(svc, "NH-26-0003", eco.number)                   # nor carried out from an altered decision


# ---------------------------------------------------------------------------------------------------- other programs
def test_a_units_answer_names_the_changes_that_reach_it(plant):
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r")
    effect.install(svc, "NH-26-0001", eco.number)
    detail = query.unit_detail(svc, svc.unit(uid(plant, "NH-26-0001")))
    [c] = detail["changes"]
    assert (c["number"], c["kind"], c["required"], c["state"], c["outstanding"]) == (eco.number, "retrofit-required", True,
                                                                                     "installed", True)
    assert [(d["number"], d["from_label"], d["to_label"]) for d in c["documents"]] == [("FRM-200", "A", "B")]
    assert "changes" not in query.unit_detail(svc, svc.unit(uid(plant, "NH-26-0001")), on="2026-01-01")


def run(share, *args):
    out, err = io.StringIO(), io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = unit_main([str(share), *args])
    return code, out.getvalue(), err.getvalue()


def test_the_command_line_decides_installs_and_reports(share, ws, plant):
    eco = plant["eco"]
    ws.close()
    code, out, _ = run(share, "change", "candidates", eco.number)
    assert code == 0 and "5 candidate(s)" in out and "FRM-200" in out
    code, out, err = run(share, "change", "decide", eco.number, "--kind", "retrofit-required", "--scope", "customer",
                         "--value", "Acme", "--reason", "cracks")
    assert code == 0, err
    assert "retrofit-required (2 unit(s) in scope)" in out
    code, out, _ = run(share, "compliance")
    assert code == 1 and "2 unit(s) out of compliance" in out
    assert run(share, "change", "install", "NH-26-0001", eco.number, "--basis", "verified")[0] == 0
    code, out, _ = run(share, "change", "status", eco.number, "--json")
    st = json.loads(out)
    assert code == 0 and st["counts"] == {"pending": 1, "verified": 1} and st["outstanding"] == 1
    code, out, _ = run(share, "show", "NH-26-0001")
    assert "engineering changes:" in out and "verified" in out
    code, _, err = run(share, "change", "set", "NH-26-0002", eco.number, "deferred")
    assert code == 2 and "needs a reason" in err


# ------------------------------------------------------------------------------------------------------- the model
def test_a_verified_event_upgrades_only_what_it_names(plant):
    from revisionworx.units import history as H
    svc, frm = plant["svc"], plant["frm"][0]
    u = svc.set_document(uid(plant, "NH-26-0001"), frm["id"], plant["frm_b"]["id"], basis="reported")
    n = u["events"]["count"]
    ev = {"n": n + 1, "type": "verified", "body": {"events": [n]}, "digest": "0" * 64, "time": "2026-11-07T00:00:00Z"}
    state, problems = H.apply(u, ev)
    assert problems == [] and state["documents"][frm["id"]]["basis"] == "verified"
    assert state["standard"]["basis"] == u["standard"]["basis"]                 # what it doesn't name keeps its basis
    _, problems = H.apply(u, {**ev, "body": {"events": [1]}})
    assert problems and "set nothing" in problems[0]


def test_the_loaders_refuse_anything_but_the_documented_shape(plant):
    from revisionworx.units.model import load_compliance, load_effectivity
    svc, eco = plant["svc"], plant["eco"]
    effect.decide(svc, eco.number, "retrofit-required", reason="r")
    eff = {k: v for k, v in effect.effectivity(svc, eco.id).items() if k != "_rev"}
    rec = {k: v for k, v in effect.compliance(svc, eco.id, uid(plant, "NH-26-0001")).items() if k != "_rev"}
    for bad in ({**eff, "extra": 1}, {**eff, "id": "uef_" + "0" * 32}, {**eff, "kind": "someday"},
                {**eff, "documents": [{"document": "x", "from": "y", "to": "z"}]}, {**eff, "schema": 2}):
        with pytest.raises(MalformedUnitRecord):
            load_effectivity(bad)
    step = rec["steps"][0]
    for bad in ({**rec, "required": "yes"}, {**rec, "steps": []}, {**rec, "steps": [{**step, "state": "offered"}],
                                                                   "state": "offered"},
                {**rec, "state": "scheduled"}, {**rec, "steps": [step, {**step, "state": "declined"}], "state": "declined"},
                {**rec, "unit": uid(plant, "NH-26-0002")}):
        with pytest.raises(MalformedUnitRecord):
            load_compliance(bad)


def test_link_units_detail_carries_each_units_changes(share, ws, plant):
    from revisionworx import integration
    effect.decide(plant["svc"], plant["eco"].number, "retrofit-required", reason="r")
    ws.close()
    answer = integration.list_units(workspace=share, detail=True)
    by_label = {u["label"]: u for u in answer["units"]}
    assert [c["state"] for c in by_label["NH-26-0001"]["changes"]] == ["pending"]
    assert by_label["NH-26-0050"]["changes"] == []                      # FRM-200 doesn't apply to it: not in scope
