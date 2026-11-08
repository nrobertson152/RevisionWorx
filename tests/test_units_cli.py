# SPDX-License-Identifier: GPL-3.0-or-later
"""The unit register from the command line, registers imported from spreadsheets (FieldWorx asset registers included), the
`link units` / `link unit` JSON other programs read, and a team workspace where every unit entry is signed (docs/UNITS.md,
docs/INTEGRATION.md)."""
import io
import json

import pytest

from revisionworx import cli, documents as D, projects, units
from revisionworx.projects import NewProject
from revisionworx.units import csvio, query
from revisionworx.units.model import ImportProblems, fieldworx_asset_id
from revisionworx.workspace import Workspace
from team_helpers import PASSWORD


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                     ("APPDATA", "cfg"), ("LOCALAPPDATA", "state")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    return tmp_path


@pytest.fixture
def eng(env, share):
    """A workspace with project J-2001 and FRM-200 rev A released (and rev B in work), SCH-HPU-75 rev A released."""
    ws = Workspace.create(share, "Shop")
    projects.add_root(ws, "Engineering", share / "Engineering")
    rec = projects.create_project(ws, NewProject(name="HPU-75", number="J-2001", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    made = {}
    for number in ("FRM-200", "SCH-HPU-75"):
        path = folder / f"{number}.pdf"
        path.write_bytes(number.encode())
        doc, rev = D.register_document(ws, rec["id"], path, number=number, title=number)
        for st in ("in_review", "approved", "released"):
            D.transition(ws, rev["id"], st)
        made[number] = (doc, rev, path)
    doc, _, path = made["FRM-200"]
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"FRM-200 rev B")
    D.checkin(ws, doc["id"], co["token"], "B, not released")
    ws.close()
    return share


def run(capsys, *args):
    capsys.readouterr()
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def link(capsys, *args):
    code, out, _ = run(capsys, *args)
    return code, json.loads(out.strip().splitlines()[-1])


FIELDWORX_REGISTER = ("tag,serial,name,part,project,location,built,as_built,notes\r\n"
                      "HPU-0001,NH-26-0001,Hydraulic power unit,HPU-75,J-2001,Bay 3,2026-03-14,FRM-200@A;SCH-HPU-75@A,\r\n"
                      "HPU-0002,NH-26-0002,Hydraulic power unit,HPU-75,J-2001,Bay 4,2026-03-20,FRM-200@A,'=not a formula\r\n")


# ------------------------------------------------------------------------------------------------ import
def test_a_fieldworx_asset_register_imports_as_it_is_and_keeps_its_ids(eng, capsys):
    w = ["-w", str(eng)]
    path = eng / "units.fwassets.csv"
    path.write_text(FIELDWORX_REGISTER, encoding="utf-8")
    code, out, _ = run(capsys, *w, "unit", "import", str(path), "--dry-run")
    assert code == 0 and "2 unit(s) would be registered" in out
    with Workspace.open(eng) as ws:
        assert ws.list("unit") == {}
    code, out, err = run(capsys, *w, "unit", "import", str(path))
    assert code == 0, err
    with Workspace.open(eng) as ws:
        svc = units.service_for(ws)
        u1 = svc.resolve("HPU-0001")
        assert svc.resolve("NH-26-0001")["id"] == u1["id"]
        alias = fieldworx_asset_id(ws.id, "HPU-0001")                       # what FieldWorx 0.1 called this unit
        assert svc.resolve(alias)["id"] == u1["id"]
        assert u1["status"] == "in-service" and u1["milestones"]["built"]["date"] == "2026-03-14"
        assert svc.resolve("HPU-0002")["notes"] == "=not a formula"          # the spreadsheet guard is taken off
        assert all(e["basis"] == "legacy" for e in svc.events(u1["id"]))
        rows = {r["document"]["number"]: r for r in query.applicable_documents(svc, u1)["rows"]}
        assert rows["FRM-200"]["revision"]["label"] == "A" and rows["FRM-200"]["source"] == "unit"
        assert ws.read_entry(ws.head_seq())["op"] == "unit.import"
        assert query.verify(svc) == []


def test_an_import_lists_every_problem_and_writes_nothing(eng):
    text = ("serial,tag,model,project,documents,built,colour\r\n")
    with Workspace.open(eng, writable=True) as ws:
        svc = units.service_for(ws)
        with pytest.raises(ImportProblems) as exc:
            csvio.plan_import(svc, text)
        assert exc.value.problems == [(1, "colour", "unknown column (see docs/UNITS.md for the columns a register can have)")]
        svc.register(identifiers=[{"scheme": "serial", "value": "NH-9"}])
        text = ("serial,tag,model,project,documents,built\r\n"
                "NH-1,,HPU-75,J-2001,FRM-200@Z,2026-03-14\r\n"
                "NH-2,,HPU-75,J-9999,,\r\n"
                "nh-1,,HPU-75,,,\r\n"
                "NH-9,,,,,\r\n"
                ",,HPU-75,,,\r\n"
                "NH-5,,,,,2026-02-30\r\n")
        n = len(ws.journal_seqs())
        with pytest.raises(ImportProblems) as exc:
            csvio.import_register(svc, text)
        found = {(row, col) for row, col, _ in exc.value.problems}
        assert {(2, "documents"), (3, "project"), (4, "serial"), (5, "serial"), (6, ""), (7, "")} <= found
        assert len(ws.journal_seqs()) == n and len(svc.units()) == 1


def test_status_words_and_columns_are_read_the_way_people_write_them(eng):
    text = "Serial Number,Asset Tag,Tag Owner,Status,Class\r\nNH-1,P-101A,Acme Refinery,In Service,Product\r\n"
    with Workspace.open(eng, writable=True) as ws:
        svc = units.service_for(ws)
        made = csvio.import_register(svc, text)
        assert made[0]["status"] == "in-service"
        assert [(i["scheme"], i["issuer"]) for i in made[0]["identifiers"]] == [("serial", ""), ("tag", "Acme Refinery")]
        assert "fieldworx" not in {i["scheme"] for i in made[0]["identifiers"]}  # not a FieldWorx register
        exported = csvio.units_to_csv(svc)
        assert exported.splitlines()[1].startswith("NH-1,,P-101A,Acme Refinery,")


# ------------------------------------------------------------------------------------------------ command line
def test_the_command_line_from_standard_to_retrofit(eng, capsys):
    w = ["-w", str(eng)]
    assert run(capsys, *w, "unit", "standard", "new", "HPU-75-A", "--title", "Standard A", "--from-project", "J-2001")[0] == 0
    code, out, err = run(capsys, *w, "unit", "new", "--serial", "NH-26-0001", "--model", "HPU-75", "--standard", "HPU-75-A",
                         "--project", "J-2001", "--built", "2026-03-14", "--status", "built")
    assert code == 0 and "registered NH-26-0001" in out, err
    assert run(capsys, *w, "unit", "new", "--serial", "PMP-1", "--class", "component", "--model", "PMP-10")[0] == 0
    assert run(capsys, *w, "unit", "fit", "NH-26-0001", "P1", "--component", "PMP-1", "--effective", "2026-03-14")[0] == 0
    code, out, _ = run(capsys, *w, "unit", "show", "NH-26-0001")
    assert code == 0 and "FRM-200" in out and "rev A (Released)" in out and "PMP-1" in out
    code, _, err = run(capsys, *w, "unit", "doc", "NH-26-0001", "FRM-200@B", "--reason", "prototype frame")
    assert code == 0, err
    code, out, _ = run(capsys, *w, "unit", "show", "NH-26-0001")
    assert "rev B (Work in Progress)" in out and "not released" in out
    code, out, _ = run(capsys, *w, "unit", "where-used", "--revision", "FRM-200@A")
    assert code == 0 and "0 found" in out
    code, out, _ = run(capsys, *w, "unit", "where-used", "--component", "PMP-1")
    assert "NH-26-0001" in out and "at P1" in out
    code, _, err = run(capsys, *w, "unit", "new", "--serial", "nh-26-0001")
    assert code == 2 and "already belongs to unit NH-26-0001" in err
    code, _, err = run(capsys, *w, "unit", "show", "NOPE")
    assert code == 2 and "no unit" in err
    code, out, _ = run(capsys, *w, "unit", "verify")
    assert code == 0 and "0 problem(s)" in out
    code, out, _ = run(capsys, *w, "unit", "history", "NH-26-0001")
    assert code == 0 and "fitted PMP-1 at P1" in out


def test_unit_help_needs_no_workspace(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["unit", "--help"])
    assert exc.value.code == 0 and "unit register" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ link JSON
def test_link_answers_for_other_programs(eng, capsys):
    w = ["-w", str(eng)]
    path = eng / "units.fwassets.csv"
    path.write_text(FIELDWORX_REGISTER, encoding="utf-8")
    assert run(capsys, *w, "unit", "import", str(path))[0] == 0
    code, ver = link(capsys, *w, "link", "version")
    assert code == 0 and "units" in ver["features"] and ver["api"] == 1
    code, listing = link(capsys, *w, "link", "units", "--project", "J-2001")
    assert code == 0 and [u["label"] for u in listing["units"]] == ["NH-26-0001", "NH-26-0002"]
    code, detailed = link(capsys, *w, "link", "units", "--detail")
    assert code == 0 and all("documents" in u and "history" in u for u in detailed["units"])
    code, one = link(capsys, *w, "link", "unit", "HPU-0001")
    u = one["unit"]
    assert code == 0 and u["label"] == "NH-26-0001" and u["project"]["number"] == "J-2001"
    assert u["aliases"] and u["aliases"][0].startswith("ast_")
    frm = [d for d in u["documents"] if d["document"]["number"] == "FRM-200"][0]
    assert frm["revision"]["label"] == "A" and frm["revision"]["sha256"] and frm["applies"] and frm["source"] == "unit"
    assert {"n", "type", "summary", "effective", "time", "basis", "voided"} <= set(u["history"][0])
    code, then = link(capsys, *w, "link", "unit", "HPU-0001", "--on", "2026-01-01")
    assert code == 0 and then["unit"]["as_of"] == {"date": "2026-01-01", "late": []}
    code, missing = link(capsys, *w, "link", "unit", "HPU-9999")
    assert code == 2 and missing == {"ok": False, "error": "no unit 'HPU-9999'", "kind": "not-found"}


def test_link_where_used_says_which_units_and_where_each_one_is(eng, capsys):
    """Feature unit-where-used: what `unit where-used` answers, as JSON, each row with the unit's summary, so a recall can
    say where every unit is. Read-only."""
    w = ["-w", str(eng)]
    code, ver = link(capsys, *w, "link", "version")
    assert "unit-where-used" in ver["features"]
    for serial, customer, site in (("NH-26-0001", "Acme", "Milltown"), ("NH-26-0002", "Bayside Marine", "Port Ellis")):
        assert run(capsys, *w, "unit", "new", "--serial", serial, "--model", "HPU-75", "--project", "J-2001",
                   "--customer", customer, "--site", site, "--doc", "FRM-200@A", "--status", "in-service")[0] == 0
    assert run(capsys, *w, "unit", "fit", "NH-26-0001", "H1", "--part-number", "HSE-08", "--lot", "DM-26-1104")[0] == 0
    assert run(capsys, *w, "unit", "fit", "NH-26-0002", "H1", "--part-number", "HSE-08", "--lot", "DM-26-1104")[0] == 0
    assert run(capsys, *w, "unit", "fit", "NH-26-0002", "H1", "--part-number", "HSE-08", "--lot", "DM-26-1121", "--replace",
               "--disposition", "scrapped")[0] == 0
    before = sorted(p.name for p in (eng / ".revisionworx").rglob("*"))

    code, now = link(capsys, *w, "link", "where-used", "--part-number", "hse-08", "--lot", "dm-26-1104")
    assert code == 0 and now["coverage"].startswith("every unit in this workspace's register")
    assert [(r["unit"]["label"], r["unit"]["customer"], r["unit"]["site"], r["unit"]["status"], r["position"], r["lot"],
             r["current"]) for r in now["rows"]] == [("NH-26-0001", "Acme", "Milltown", "in-service", "H1", "DM-26-1104", True)]
    code, ever = link(capsys, *w, "link", "where-used", "--part-number", "HSE-08", "--lot", "DM-26-1104", "--history")
    rows = sorted((r["unit"]["label"], r["current"], r.get("disposition", "")) for r in ever["rows"])
    assert code == 0 and rows == [("NH-26-0001", True, ""), ("NH-26-0002", False, "scrapped")]
    code, frm = link(capsys, *w, "link", "where-used", "--revision", "FRM-200@A")
    assert code == 0 and sorted(r["unit"]["label"] for r in frm["rows"]) == ["NH-26-0001", "NH-26-0002"]
    code, doc = link(capsys, *w, "link", "where-used", "--document", "FRM-200")
    assert code == 0 and {r["revision"]["label"] for r in doc["rows"]} == {"A"}

    for args, kind, words in ((["--lot", "DM-26-1104"], "bad-input", "give --part-number with --lot"),
                              ([], "bad-input", "give one of"),
                              (["--revision", "FRM-200@A", "--part-number", "HSE-08"], "bad-input", "give one of"),
                              (["--revision", "FRM-200@A", "--history"], "bad-input", "--history is for what is fitted"),
                              (["--component", "NOPE"], "not-found", "no unit")):
        code, refused = link(capsys, *w, "link", "where-used", *args)
        assert code == 2 and refused["ok"] is False and refused["kind"] == kind and words in refused["error"], args
    assert sorted(p.name for p in (eng / ".revisionworx").rglob("*")) == before, "asking changes nothing"


# ------------------------------------------------------------------------------------------------ team workspace
def test_in_a_team_workspace_unit_entries_are_signed_in_the_persons_name(env, capsys, monkeypatch):
    folder = env / "ws"
    assert cli.main(["init", str(folder), "--name", "Team"]) == 0
    w = ["-w", str(folder)]
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main([*w, "team", "enable", "--admin", "boss", "--password-stdin"]) == 0
    code, out, err = run(capsys, *w, "unit", "new", "--serial", "NH-26-0001", "--model", "HPU-75")
    assert code == 0, err
    assert run(capsys, *w, "unit", "note", "NH-26-0001", "commissioned on site")[0] == 0
    with Workspace.open(folder) as ws:
        assert ws.read_entry(ws.head_seq())["signer"] == "boss"
        svc = units.service_for(ws)
        u = svc.resolve("NH-26-0001")
        assert [e["actor"].get("account") for e in svc.events(u["id"])] == ["boss", "boss"]
        assert query.verify(svc) == []
    assert cli.main([*w, "logout"]) == 0
    code, _, err = run(capsys, *w, "unit", "note", "NH-26-0001", "anonymous")
    assert code == 2 and "log in" in err.lower()
    code, one = link(capsys, *w, "link", "unit", "NH-26-0001")                 # reading needs no login
    assert code == 0 and len(one["unit"]["history"]) == 2
