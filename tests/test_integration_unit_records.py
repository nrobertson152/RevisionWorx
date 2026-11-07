# SPDX-License-Identifier: GPL-3.0-or-later
"""Other programs recording facts on units (`revisionworx link unit-*`, feature `unit-records`; docs/INTEGRATION.md and
docs/UNITS.md section 8): OpenMeridian registering what its work orders built, FieldWorx's office bridge accepting a field
replacement or a change made on site. Real workspace on a real folder, the real command line, and the reference client in a
separate process. Every write is one journal entry or nothing, and a retry with the same txid writes nothing."""
import json
import sys
from pathlib import Path

import pytest

from revisionworx import changes, cli, documents as D, projects, units
from revisionworx.projects import NewProject
from revisionworx.units import effect
from revisionworx.workspace import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "contrib"))
import rwx_client  # noqa: E402

TX = ["%02x" % i * 16 for i in range(1, 20)]        # 32 hex each: 0101..., 0202..., ...
FIELD_EVENT = "e" * 64                               # a FieldWorx event id: the evidence a field record leaves


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


@pytest.fixture
def plant(tmp_path, monkeypatch):
    """A workspace with FRM-200 rev A released, numbering NH, a unit NH-26-0001 built to FRM-200 rev A with PMP-10 serial 111
    at P1, and ECO-0001 (FRM-200 to rev B) implemented and decided as a required retrofit of that unit. Closed again, so the
    command line is the only writer."""
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                     ("APPDATA", "cfg"), ("LOCALAPPDATA", "local")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    folder = tmp_path / "plant"
    (folder / "Engineering").mkdir(parents=True)
    ws = Workspace.create(folder, "Plant")
    try:
        projects.add_root(ws, "Engineering", folder / "Engineering")
        rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
        path = projects.project_folder(ws, rec) / "frame.pdf"
        path.write_bytes(b"FRM-200 rev A")
        frm, rev_a = D.register_document(ws, rec["id"], path, number="FRM-200", title="Frame")
        release(ws, rev_a["id"])
        svc = units.service_for(ws)
        svc.create_numbering("NH", "NH-{yy}-{seq:4}")
        u = svc.register(identifiers=[{"scheme": "serial", "value": "NH-26-0001", "issuer": ""}],
                         model={"part": "", "number": "HPU-75"}, documents={frm["id"]: rev_a["id"]}, status="in-service")
        svc.install(u["id"], "P1", number="PMP-10", serial="111")
        cs = changes.service_for(ws)
        ecr = cs.create_request("Thicker frame", "Fatigue cracks", affected=[{"item_type": "document", "item_id": frm["id"]}])
        cs.submit(ecr.id)
        cs.begin_review(ecr.id)
        cs.approve(ecr.id)
        eco = cs.create_order([ecr.id])
        cs.start_work(eco.id)
        co = D.checkout(ws, frm["id"])
        path.write_bytes(b"FRM-200 rev B")
        rev_b = release(ws, D.checkin(ws, frm["id"], co["token"], "thicker")["id"])
        cs.record_implementation(eco.id, cs.get(eco.id).affected[0].key, rev_id=rev_b["id"])
        cs.mark_implemented(eco.id)
        effect.decide(svc, eco.id, "retrofit-required", reason="cracks found in service")
        out = {"folder": folder, "unit": u["id"], "eco": cs.get(eco.id), "frm": frm["id"], "rev_a": rev_a["id"],
               "rev_b": rev_b["id"]}
    finally:
        ws.close()
    return out


def link(capsys, plant, *args):
    capsys.readouterr()
    code = cli.main(["-w", str(plant["folder"]), "link", *args])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def entries(plant) -> int:
    with Workspace.open(plant["folder"]) as ws:
        return len(ws.journal_seqs())


def detail(capsys, plant, ref="NH-26-0001") -> dict:
    code, data = link(capsys, plant, "unit", ref)
    assert code == 0, data
    return data["unit"]


# ------------------------------------------------------------------------------------------------- registering
def test_the_feature_is_announced(plant, capsys):
    code, data = link(capsys, plant, "version")
    assert code == 0 and "unit-records" in data["features"]


def test_a_work_order_registers_the_units_it_built_once(plant, capsys):
    n = entries(plant)
    args = ["unit-register", "--txid", TX[0], "--serial-from", "NH", "--count", "2", "--model", "HPU-75", "--name",
            "Hydraulic power unit", "--status", "built", "--built", "2026-11-05", "--doc", "FRM-200@B", "--project", "J-1",
            "--evidence", "work-order:WO-00012=OpenMeridian work order"]
    code, made = link(capsys, plant, *args)
    assert code == 0 and made["ok"] and made["result"] == "recorded", made
    assert [u["label"] for u in made["units"]] == ["NH-26-0002", "NH-26-0003"]          # the next serials, taken at once
    assert entries(plant) == n + 1                                                       # both units: one entry
    first = made["units"][0]
    assert first["status"] == "built" and first["model"]["number"] == "HPU-75" and first["project"]["number"] == "J-1"
    assert [(d["document"]["number"], d["revision"]["label"]) for d in first["documents"]] == [("FRM-200", "B")]
    registered = first["history"][0]
    assert registered["type"] == "unit.registered" and registered["basis"] == "declared"
    assert registered["evidence"] == [{"kind": "work-order", "ref": "WO-00012", "label": "OpenMeridian work order"}]
    assert any(h["type"] == "milestone" and h["effective"] == "2026-11-05" for h in first["history"])

    code, again = link(capsys, plant, *args)                                            # the answer was lost: ask again
    assert code == 0 and again["result"] == "already-recorded" and entries(plant) == n + 1
    assert [u["id"] for u in again["units"]] == [u["id"] for u in made["units"]]
    code, other = link(capsys, plant, *[a if a != "2" else "3" for a in args])         # same txid, different input
    assert code == 2 and other["kind"] == "txid-used" and entries(plant) == n + 1


def test_registering_is_all_or_nothing_and_names_who_has_a_serial(plant, capsys):
    n = entries(plant)
    code, refused = link(capsys, plant, "unit-register", "--txid", TX[1], "--serial", "NH-77", "--serial", "NH-26-0001")
    assert code == 2 and refused["kind"] == "duplicate" and "NH-26-0001" in refused["error"]
    assert entries(plant) == n
    code, gone = link(capsys, plant, "unit", "NH-77")
    assert code == 2 and gone["kind"] == "not-found"                                     # not half registered
    code, made = link(capsys, plant, "unit-register", "--txid", TX[2], "--serial", "NH-77", "--serial", "NH-78",
                      "--issuer", "Acme")
    assert code == 0 and [u["label"] for u in made["units"]] == ["NH-77", "NH-78"]


@pytest.mark.parametrize("args, says", [
    ([], "give each unit"),
    (["--serial", "A", "--count", "2"], "--count is for --serial-from"),
    (["--serial-from", "NH", "--count", "2", "--tag", "T-1"], "belongs to one unit"),
    (["--serial-from", "NH", "--count", "0"], "whole number from 1"),
    (["--serial-from", "NH", "--serial", "A"], "not both"),
])
def test_registering_refuses_what_cant_be_right(plant, capsys, args, says):
    n = entries(plant)
    code, refused = link(capsys, plant, "unit-register", "--txid", TX[3], *args)
    assert code == 2 and refused["kind"] == "bad-input" and says in refused["error"] and entries(plant) == n


# ------------------------------------------------------------------------------------------------ field records
def test_a_field_replacement_is_recorded_with_its_basis_and_evidence(plant, capsys):
    before = detail(capsys, plant)
    n = entries(plant)
    code, done = link(capsys, plant, "unit-fit", "NH-26-0001", "P1", "--part-number", "PMP-11", "--serial", "222",
                      "--replace", "--disposition", "repair", "--basis", "reported", "--effective", "2026-11-03",
                      "--if-events", str(before["events"]["count"]), "--txid", TX[4],
                      "--evidence", f"fieldworx-event:{FIELD_EVENT}=replaced on site by J. Smith")
    assert code == 0 and done["result"] == "recorded", done
    assert entries(plant) == n + 1
    u = done["unit"]
    assert [(c["position"], c["number"], c["serial"], c["basis"]) for c in u["components"]] == \
        [("P1", "PMP-11", "222", "reported")]
    written = [h for h in u["history"] if h["n"] in done["events"]]
    assert [h["type"] for h in written] == ["component.removed", "component.installed"]
    for h in written:
        assert h["basis"] == "reported" and h["effective"] == "2026-11-03"
        assert h["evidence"] == [{"kind": "fieldworx-event", "ref": FIELD_EVENT, "label": "replaced on site by J. Smith"}]
    code, again = link(capsys, plant, "unit-fit", "NH-26-0001", "P1", "--part-number", "PMP-11", "--serial", "222",
                       "--replace", "--disposition", "repair", "--basis", "reported", "--effective", "2026-11-03",
                       "--if-events", str(before["events"]["count"]), "--txid", TX[4],
                       "--evidence", f"fieldworx-event:{FIELD_EVENT}=replaced on site by J. Smith")
    assert code == 0 and again["result"] == "already-recorded" and again["events"] == done["events"]
    assert entries(plant) == n + 1                       # the retry is answered even though the unit has changed since


def test_a_decision_on_a_unit_that_changed_since_is_refused(plant, capsys):
    seen = detail(capsys, plant)["events"]["count"]
    assert link(capsys, plant, "unit-note", "NH-26-0001", "guard loose", "--txid", TX[5])[0] == 0  # someone else, meanwhile
    n = entries(plant)
    code, refused = link(capsys, plant, "unit-fit", "NH-26-0001", "P1", "--part-number", "PMP-11", "--replace",
                         "--if-events", str(seen), "--txid", TX[6])
    assert code == 2 and refused["kind"] == "changed" and entries(plant) == n
    assert detail(capsys, plant)["components"][0]["serial"] == "111"
    code, done = link(capsys, plant, "unit-fit", "NH-26-0001", "P1", "--part-number", "PMP-11", "--replace",
                      "--if-events", str(seen + 1), "--txid", TX[7])
    assert code == 0 and done["result"] == "recorded"


def test_an_occupied_position_needs_replace_and_an_empty_one_cant_be_emptied(plant, capsys):
    n = entries(plant)
    code, refused = link(capsys, plant, "unit-fit", "NH-26-0001", "P1", "--part-number", "PMP-11", "--txid", TX[8])
    assert code == 2 and refused["kind"] == "bad-input" and "already holds" in refused["error"]
    code, refused = link(capsys, plant, "unit-remove", "NH-26-0001", "P9", "--txid", TX[9])
    assert code == 2 and "nothing is recorded at P9" in refused["error"] and entries(plant) == n
    code, removed = link(capsys, plant, "unit-remove", "NH-26-0001", "P1", "--disposition", "scrapped", "--txid", TX[9])
    assert code == 0 and removed["unit"]["components"] == []


def test_notes_and_milestones_from_another_program(plant, capsys):
    code, noted = link(capsys, plant, "unit-note", "NH-26-0001", "Guard on P1 missing; frame cracked at weld 3",
                       "--basis", "reported", "--txid", TX[10], "--evidence", f"fieldworx-event:{FIELD_EVENT}")
    assert code == 0 and noted["unit"]["history"][-1]["type"] == "note" and len(noted["events"]) == 1
    code, shipped = link(capsys, plant, "unit-milestone", "NH-26-0001", "commissioned", "--on", "2026-11-04",
                         "--txid", TX[11])
    assert code == 0 and shipped["unit"]["history"][-1]["effective"] == "2026-11-04"
    code, unknown = link(capsys, plant, "unit-note", "NO-SUCH-UNIT", "x", "--txid", TX[12])
    assert code == 2 and unknown["kind"] == "not-found"


def test_a_txid_is_required_and_checked(plant, capsys):
    n = entries(plant)
    code, refused = link(capsys, plant, "unit-note", "NH-26-0001", "x")
    assert code == 2 and refused["kind"] == "bad-input"
    code, refused = link(capsys, plant, "unit-note", "NH-26-0001", "x", "--txid", "not-hex")
    assert code == 2 and "32 lowercase hex" in refused["error"] and entries(plant) == n


def test_a_change_made_on_site_moves_the_units_drawings_reported(plant, capsys):
    """FieldWorx reports ECO-0001 done on NH-26-0001: the drawings move to rev B in the same entry, flagged reported, and the
    compliance record says installed. Verifying it stays engineering's step."""
    n = entries(plant)
    number = plant["eco"].number
    code, done = link(capsys, plant, "unit-change-install", "NH-26-0001", number, "--effective", "2026-11-02",
                      "--txid", TX[13], "--evidence", f"fieldworx-event:{FIELD_EVENT}=done on site")
    assert code == 0 and done["result"] == "recorded", done
    assert entries(plant) == n + 1
    assert done["compliance"]["state"] == "installed" and done["compliance"]["unit"] == plant["unit"]
    row = next(d for d in done["unit"]["documents"] if d["document"]["number"] == "FRM-200")
    assert row["revision"]["label"] == "B" and row["basis"] == "reported"
    change = next(c for c in done["unit"]["changes"] if c["number"] == number)
    assert change["state"] == "installed" and change["outstanding"]
    code, again = link(capsys, plant, "unit-change-install", "NH-26-0001", number, "--effective", "2026-11-02",
                       "--txid", TX[13], "--evidence", f"fieldworx-event:{FIELD_EVENT}=done on site")
    assert code == 0 and again["result"] == "already-recorded" and entries(plant) == n + 1
    code, twice = link(capsys, plant, "unit-change-install", "NH-26-0001", number, "--txid", TX[14])
    assert code == 2 and twice["kind"] == "bad-input" and entries(plant) == n + 1


def test_the_reference_client_records_on_units(plant):
    """The client other programs copy, running `python -m revisionworx` in a real separate process."""
    rwx = rwx_client.RevisionWorx(executable=None, workspace=str(plant["folder"]))
    rwx.command = [sys.executable, "-m", "revisionworx"]
    assert rwx.has("unit-records")
    made = rwx.unit_register(TX[15], serial_from="NH", model="HPU-75", status="built", milestones={"built": "2026-11-05"},
                             evidence=["work-order:WO-00013"])
    assert made["result"] == "recorded" and made["units"][0]["label"] == "NH-26-0002"
    unit = rwx.unit("NH-26-0002")
    fitted = rwx.unit_fit(unit["id"], "P1", TX[16], part_number="PMP-10", serial="333", basis="reported",
                          if_events=unit["events"]["count"], evidence=[f"fieldworx-event:{FIELD_EVENT}"])
    assert fitted["result"] == "recorded" and fitted["unit"]["components"][0]["serial"] == "333"
    with pytest.raises(rwx_client.RwxError) as exc:
        rwx.unit_note(unit["id"], "stale", TX[17], if_events=unit["events"]["count"])
    assert exc.value.kind == "changed"
    assert rwx.unit_note(unit["id"], "fresh", TX[17])["result"] == "recorded"
    assert [u["label"] for u in rwx.units(text="NH-26-000")] == ["NH-26-0001", "NH-26-0002"]
