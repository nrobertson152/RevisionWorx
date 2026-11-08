# SPDX-License-Identifier: GPL-3.0-or-later
"""Transfers between workspaces (docs/UNITS.md section 16, ADR-031; the Director's decision of 2026-11-08 that the receiver
refers to the source's revisions). One workspace keeps a unit at a time. The one it leaves makes a transfer pack, records it,
and refuses every action on the unit from then on; the receiving workspace checks the pack and records the same unit, keeping
each document it uses as a reference with its PDF, never as a document of its own. Real workspaces, real folders."""
import contextlib
import io
import json
import shutil

import pytest

from revisionworx import documents as D, projects, sshsig
from revisionworx.cli import main as rwx_main
from revisionworx.content import ContentStore
from revisionworx.gui.viewmodels import unit_sections
from revisionworx.projects import NewProject
from revisionworx.units import deviation, pack, query, transfer
from revisionworx.units.model import TRANSFERRED, DuplicateIdentifier, ValidationError, key_id, load_key
from revisionworx.units.service import KIND, service_for
from revisionworx.workspace import Workspace
from test_sshsig import keygen

ACME, BAYSIDE = "Acme Engineering", "Bayside Service"


def release(ws, rid):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rid, state)


@pytest.fixture
def sites(tmp_path, monkeypatch):
    """Acme's workspace, with FRM-200 rev A (a PDF) and LAYOUT rev A (no PDF), and NH-26-0001: a power unit to both, with a
    pump PMP-777 fitted at P1 and its factory lots; and Bayside's, empty. Both closed, as two companies' would be."""
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                     ("APPDATA", "cfg"), ("LOCALAPPDATA", "local")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    acme, bay = tmp_path / "acme", tmp_path / "bayside"
    for f in (acme, bay):
        (f / "Eng").mkdir(parents=True)
    with Workspace.create(acme, ACME) as ws:
        projects.add_root(ws, "Eng", acme / "Eng")
        prj = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Bayside"))
        folder = projects.project_folder(ws, prj)
        (folder / "frame.pdf").write_bytes(b"%PDF-1.4 frame rev A")
        (folder / "layout.txt").write_bytes(b"layout rev A")
        frm, frm_a = D.register_document(ws, prj["id"], folder / "frame.pdf", number="FRM-200", title="Frame")
        lay, lay_a = D.register_document(ws, prj["id"], folder / "layout.txt", number="LAYOUT", title="Layout")
        release(ws, frm_a["id"])
        release(ws, lay_a["id"])
        svc = service_for(ws)
        pump = svc.register(identifiers=[{"scheme": "serial", "value": "PMP-777"}], name="Pump", status="built",
                            documents={frm["id"]: frm_a["id"]})
        hpu = svc.register(identifiers=[{"scheme": "serial", "value": "NH-26-0001"},
                                        {"scheme": "tag", "issuer": "Bayside", "value": "HPU-3"}],
                           name="Power unit", status="in-service", model={"part": "", "number": "HPU-75"},
                           documents={frm["id"]: frm_a["id"], lay["id"]: lay_a["id"]}, milestones={"built": "2026-11-01"},
                           lots=[{"number": "BLT-M8", "lot": "B-7", "quantity": "48"}], customer="Bayside", site="Milltown")
        svc.install(hpu["id"], "P1", component=pump["id"])
        svc.note(hpu["id"], "commissioned by J. Smith")
    with Workspace.create(bay, BAYSIDE):
        pass
    return {"acme": acme, "bay": bay, "tmp": tmp_path, "hpu": hpu["id"], "pump": pump["id"], "frm": frm["id"],
            "frm_a": frm_a["id"], "lay": lay["id"], "lay_a": lay_a["id"]}


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = rwx_main([str(a) for a in args])
    return code, out.getvalue() + err.getvalue()


def send(sites, folder="pack", ref="NH-26-0001", to=BAYSIDE, **kw):
    with Workspace.open(sites["acme"], writable=True) as ws:
        return transfer.make(service_for(ws), ref, sites["tmp"] / folder, to=to, **kw)


def take(sites, folder="pack", where="bay", **kw):
    with Workspace.open(sites[where], writable=True) as ws:
        return transfer.receive(service_for(ws), sites["tmp"] / folder, **kw)


# ----------------------------------------------------------------------------------------------------- the source
def test_the_unit_and_what_is_fitted_in_it_are_handed_over_in_one_entry(sites):
    with Workspace.open(sites["acme"]) as ws:
        head = ws.head_seq()
    made = send(sites)
    assert made["units"] == ["NH-26-0001", "PMP-777"] and made["pack"] == 1 and made["recorded"] and not made["signed"]
    with Workspace.open(sites["acme"]) as ws:
        svc = service_for(ws)
        assert ws.head_seq() == head + 1
        hpu, pump = svc.unit(sites["hpu"]), svc.unit(sites["pump"])
        assert hpu["status"] == pump["status"] == TRANSFERRED
        assert [e["type"] for e in svc.events(sites["hpu"])][-2:] == ["pack.issued", "unit.transferred"]
        assert svc.events(sites["pump"])[-1]["body"]["units"] == [sites["hpu"], sites["pump"]]
        d = query.unit_detail(svc, hpu)
        assert d["transferred"]["to"] == BAYSIDE and [u["label"] for u in d["transferred"]["units"]] == ["NH-26-0001",
                                                                                                         "PMP-777"]
        assert d["history"][-1]["summary"] == "transferred to Bayside Service, with the 1 unit fitted in it"
        assert query.verify(svc) == []
        reg = pack.check(sites["tmp"] / "pack", svc)
        assert reg["ok"] and reg["register"]["transferred_to"] == BAYSIDE
        assert "as the transfer of the unit to Bayside Service" in reg["register"]["message"]
    files = sorted(p.relative_to(sites["tmp"] / "pack").as_posix() for p in (sites["tmp"] / "pack").rglob("*") if p.is_file())
    assert files == sorted(["SHA256SUMS", "documents/FRM-200 rev A.pdf", f"history/{sites['hpu']}.json",
                            f"history/{sites['pump']}.json", "index.html", "transfer.json", "unit.json"])
    page = (sites["tmp"] / "pack" / "index.html").read_text("utf-8")
    assert "Transferred." in page and "with the 1 unit fitted in it" in page and "Bayside Service" in page
    t = transfer.load(sites["tmp"] / "pack")
    hpu_state = t["units"][0]["state"]
    assert [(i["scheme"], i["issuer"], i["value"]) for i in hpu_state["identifiers"]] == [
        ("serial", ACME, "NH-26-0001"), ("tag", "Bayside", "HPU-3")]          # what Acme issued says so
    assert hpu_state["components"][0]["unit"] == sites["pump"] and t["units"][1]["state"]["parent"]["unit"] == sites["hpu"]
    assert {(d["number"], d["revision"], bool(d["pdf"])) for d in t["documents"]} == {
        ("FRM-200", sites["frm_a"], True), ("LAYOUT", sites["lay_a"], False)}


def test_a_transferred_unit_takes_no_more_actions_here(sites):
    send(sites)
    with Workspace.open(sites["acme"], writable=True) as ws:
        svc = service_for(ws)
        for act in (lambda: svc.note(sites["hpu"], "x"), lambda: svc.set_status(sites["hpu"], "retired", reason="x"),
                    lambda: svc.install(sites["hpu"], "P2", number="X"), lambda: svc.remove(sites["hpu"], "P1"),
                    lambda: svc.note(sites["pump"], "x"),
                    lambda: transfer.make(svc, "NH-26-0001", sites["tmp"] / "again", to="someone"),
                    lambda: pack.make(svc, "NH-26-0001", sites["tmp"] / "handover")):
            with pytest.raises((ValidationError, transfer.TransferError), match="transferred"):
                act()
        with pytest.raises(ValidationError, match="it is recorded in another workspace now"):
            deviation.approve(svc, "deviation", title="t", departure="d", reason="r", units=["NH-26-0001"])
        with pytest.raises(ValidationError, match="transferred"):
            svc.void(sites["hpu"], [5], reason="x")


def test_a_unit_fitted_in_another_is_transferred_with_that_one(sites):
    with Workspace.open(sites["acme"]) as ws:
        head = ws.head_seq()
    with pytest.raises(transfer.TransferError, match="fitted in unit NH-26-0001 .*: transfer that unit"):
        send(sites, ref="PMP-777")
    with Workspace.open(sites["acme"]) as ws:
        assert ws.head_seq() == head and not (sites["tmp"] / "pack").exists()


def test_the_pack_is_kept_and_written_again_exactly(sites):
    send(sites)
    shutil.rmtree(sites["tmp"] / "pack")                    # lost on the way
    code, out = run("-w", sites["acme"], "unit", "transfer-pack", "NH-26-0001", sites["tmp"] / "again")
    assert code == 0 and "as it was issued" in out
    take(sites, folder="again")
    with Workspace.open(sites["bay"]) as ws:
        assert service_for(ws).unit(sites["hpu"])["status"] == "in-service"
    code, out = run("-w", sites["acme"], "unit", "transfer-pack", "PMP-777", sites["tmp"] / "third")
    assert code == 2 and "transferred fitted in unit NH-26-0001" in out


# ----------------------------------------------------------------------------------------------------- the receiver
def test_the_receiver_records_the_same_units_and_refers_to_the_sources_revisions(sites):
    send(sites)
    got = take(sites)
    assert got["units"] == ["NH-26-0001", "PMP-777"] and got["from"]["name"] == ACME and not got["already"]
    with Workspace.open(sites["bay"]) as ws:
        svc = service_for(ws)
        hpu, pump = svc.unit(sites["hpu"]), svc.unit(sites["pump"])
        assert (hpu["status"], pump["status"]) == ("in-service", "built")
        assert svc.events(sites["hpu"])[0]["type"] == "unit.received" and svc.events(sites["hpu"])[0]["n"] == 1
        assert svc.resolve("NH-26-0001")["id"] == sites["hpu"] and svc.resolve("HPU-3")["id"] == sites["hpu"]
        owner = load_key(ws.get(KIND["key"], key_id("serial", ACME, "NH-26-0001")))["owner"]
        assert owner == sites["hpu"]
        assert hpu["components"]["p1"]["unit"] == sites["pump"] and pump["parent"]["unit"] == sites["hpu"]
        assert [(x["number"], x["lot"]) for x in hpu["lots"]] == [("BLT-M8", "B-7")]
        assert hpu["milestones"]["built"]["date"] == "2026-11-01" and hpu["customer"] == "Bayside"
        assert ws.get("document", sites["frm"]) is None                        # referred to, never copied
        d = query.unit_detail(svc, hpu)
        rows = {r["document"]["number"]: r for r in d["documents"]}
        assert rows["FRM-200"]["flags"] == ["foreign"] and rows["FRM-200"]["revision"]["label"] == "A"
        assert rows["FRM-200"]["foreign"]["name"] == ACME and rows["LAYOUT"]["foreign"]["pdf"] == ""
        assert ContentStore(ws).verify(rows["FRM-200"]["foreign"]["pdf"])
        assert d["received"][0]["from"]["name"] == ACME and d["transferred"] is None
        assert query.verify(svc) == []
    with Workspace.open(sites["bay"], writable=True) as ws:
        svc = service_for(ws)
        svc.install(sites["hpu"], "P2", number="FILTER-9")               # it is Bayside's to record on now
        assert query.verify(svc) == []
    with pytest.raises(transfer.TransferError, match="made by this workspace"):
        take(sites, where="acme")


def test_receiving_again_records_nothing_more(sites):
    send(sites)
    take(sites)
    with Workspace.open(sites["bay"]) as ws:
        head = ws.head_seq()
    assert take(sites)["already"]
    with Workspace.open(sites["bay"]) as ws:
        assert ws.head_seq() == head


def test_a_damaged_pack_is_refused_and_nothing_is_recorded(sites):
    send(sites)
    with Workspace.open(sites["bay"]) as ws:
        head = ws.head_seq()
    (sites["tmp"] / "pack" / "documents" / "FRM-200 rev A.pdf").write_bytes(b"%PDF-1.4 something else")
    with pytest.raises(transfer.TransferError, match="has been changed since the pack was made"):
        take(sites)
    with Workspace.open(sites["bay"]) as ws:
        assert ws.head_seq() == head and service_for(ws).scan()[0] == []


def test_a_handover_pack_isnt_a_transfer_pack(sites):
    with Workspace.open(sites["acme"], writable=True) as ws:
        pack.make(service_for(ws), "NH-26-0001", sites["tmp"] / "handover")
    with pytest.raises(transfer.TransferError, match="this is a handover pack, not a transfer pack"):
        take(sites, folder="handover")


def test_an_identifier_another_unit_holds_here_refuses_the_whole_receipt(sites):
    send(sites)
    with Workspace.open(sites["bay"], writable=True) as ws:
        svc = service_for(ws)
        svc.register(identifiers=[{"scheme": "tag", "issuer": "Bayside", "value": "HPU-3"}], name="registered by hand")
        head = ws.head_seq()
    with pytest.raises(DuplicateIdentifier, match="HPU-3"):
        take(sites)
    with Workspace.open(sites["bay"]) as ws:
        assert ws.head_seq() == head and len(service_for(ws).scan()[0]) == 1


@pytest.mark.skipif(sshsig.ssh_keygen() is None, reason="ssh-keygen (OpenSSH) isn't installed here")
def test_a_signed_transfer_and_a_receiver_that_expects_the_key(sites):
    keys = sites["tmp"] / "keys"
    keys.mkdir()
    company, other = keygen(keys), keygen(keys, "other")
    with Workspace.open(sites["acme"], writable=True) as ws:
        pack.set_signing(ws, (keys / "company.pub").read_text())
    with pytest.raises(transfer.TransferError, match="signs every handover pack"):
        send(sites)
    made = send(sites, sign_with=company)
    fp = sshsig.parse_public_key((keys / "company.pub").read_text()).fingerprint
    assert made["signed"] and made["key"] == fp
    wrong = sshsig.parse_public_key((keys / "other.pub").read_text()).fingerprint
    with pytest.raises(transfer.TransferError, match="not with the key you gave"):
        take(sites, key=wrong)
    code, out = run("-w", sites["bay"], "unit", "receive", sites["tmp"] / "pack", "--key", keys / "company.pub")
    assert code == 0 and f"signed with the key {fp}" in out, out
    with Workspace.open(sites["bay"]) as ws:
        svc = service_for(ws)
        assert svc.events(sites["hpu"])[0]["body"]["key"] == fp
        assert query.unit_detail(svc, svc.unit(sites["hpu"]))["received"][0]["key"] == fp
    del other


def test_handed_back_the_unit_is_the_sources_own_again(sites):
    send(sites)
    take(sites)
    with Workspace.open(sites["bay"], writable=True) as ws:
        svc = service_for(ws)
        svc.install(sites["hpu"], "P2", number="FILTER-9", lot="F-1")
        transfer.make(svc, "NH-26-0001", sites["tmp"] / "back", to=ACME)
    take(sites, folder="back", where="acme")
    with Workspace.open(sites["acme"], writable=True) as ws:
        svc = service_for(ws)
        hpu = svc.unit(sites["hpu"])
        assert hpu["status"] == "in-service" and set(hpu["components"]) == {"p1", "p2"}
        assert [(i["scheme"], i["issuer"], i["value"]) for i in hpu["identifiers"]][0] == ("serial", "", "NH-26-0001")
        d = query.unit_detail(svc, hpu)
        assert all(r["flags"] == [] for r in d["documents"]), "Acme's own revisions are its own again"
        assert [e["type"] for e in svc.events(sites["hpu"])][-1] == "unit.received"
        assert query.verify(svc) == []
        svc.note(sites["hpu"], "back for refurbishment")                 # Acme records on it again
    code, out = run("-w", sites["acme"], "unit", "history", "NH-26-0001")
    assert code == 0 and f"recorded in {BAYSIDE}, before it came here:" in out and "fitted FILTER-9 lot F-1 at P2" in out
    assert out.count(f"recorded in {ACME}") == 0, "its own earlier record isn't shown twice"


def test_a_workspace_that_has_its_own_record_of_the_unit_refuses_it(sites):
    send(sites)
    take(sites)
    with Workspace.open(sites["bay"], writable=True) as ws:
        transfer.make(service_for(ws), "NH-26-0001", sites["tmp"] / "onward", to="Third Co")
    with pytest.raises(transfer.TransferError, match="made by this workspace"):
        take(sites, folder="onward")


# ------------------------------------------------------------------------------------------------- other programs
def test_link_answers_and_fetches_the_kept_pdf(sites, capsys):
    send(sites)
    take(sites)
    capsys.readouterr()

    def link(*args):
        code = rwx_main(["-w", str(sites["bay"]), "link", *map(str, args)])
        return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    code, out = link("version")
    assert "unit-transfers" in out["features"]
    code, out = link("unit", "NH-26-0001")
    rows = {r["document"]["number"]: r for r in out["unit"]["documents"]}
    assert rows["FRM-200"]["foreign"]["name"] == ACME and out["unit"]["received"][0]["from"]["name"] == ACME
    code, out = link("fetch", sites["frm"], "--to", sites["tmp"] / "out", "--pdf")
    assert code == 0 and out["revision"]["foreign"]["name"] == ACME
    assert (sites["tmp"] / "out" / "FRM-200 (rev A).pdf").read_bytes() == b"%PDF-1.4 frame rev A"
    code, out = link("fetch", sites["frm"], "--to", sites["tmp"] / "out")
    assert code != 0 and "only the PDF of the revision a unit uses is kept here" in out["error"]
    code, out = link("fetch", sites["lay"], "--to", sites["tmp"] / "out", "--pdf")
    assert code != 0 and "without a PDF" in out["error"]
    capsys.readouterr()
    rwx_main(["-w", str(sites["acme"]), "link", "unit", "NH-26-0001"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["unit"]["status"] == TRANSFERRED and out["unit"]["transferred"]["to"] == BAYSIDE


def test_content_clean_up_keeps_what_transfers_and_receipts_keep(sites):
    send(sites)
    take(sites)
    for where in ("acme", "bay"):
        with Workspace.open(sites[where], writable=True) as ws:
            report = D.gc_content(ws, apply=True, min_age_seconds=0)
            assert report["orphans"] == [], where
            assert query.verify(service_for(ws)) == []
    code, _ = run("-w", sites["acme"], "unit", "transfer-pack", "NH-26-0001", sites["tmp"] / "again")
    assert code == 0


def test_verify_finds_kept_content_that_is_missing(sites):
    send(sites)
    take(sites)
    with Workspace.open(sites["bay"]) as ws:
        svc = service_for(ws)
        history = svc.events(sites["hpu"])[0]["body"]["history"]
        ContentStore(ws).path_for(history).unlink()
        assert {p["code"] for p in query.verify(svc)} == {"received-content"}
    with Workspace.open(sites["acme"]) as ws:
        svc = service_for(ws)
        kept = svc.events(sites["hpu"])[-1]["body"]["kept"]
        ContentStore(ws).path_for(kept).unlink()
        assert {p["code"] for p in query.verify(svc)} == {"kept-content"}


def test_transfer_json_is_read_strictly(sites):
    send(sites)
    good = json.loads((sites["tmp"] / "pack" / "transfer.json").read_text("utf-8"))
    for change in (lambda t: t.update(version=2), lambda t: t.update(extra=1),
                   lambda t: t["units"][1]["state"].update(parent=None),
                   lambda t: t["units"][0]["state"].update(status="merged"),
                   lambda t: t["units"][0]["state"]["model"].update(part="prt_" + "1" * 32),
                   lambda t: t["documents"][0].update(pdf_file="../evil.pdf"),
                   lambda t: t["units"].reverse()):
        bad = json.loads(json.dumps(good))
        change(bad)
        (sites["tmp"] / "pack" / "transfer.json").write_text(json.dumps(bad), "utf-8")
        with pytest.raises(transfer.TransferError):
            transfer.load(sites["tmp"] / "pack")


# ---------------------------------------------------------------------------------------------- people
def test_the_command_line_and_the_window_say_where_it_is(sites):
    code, out = run("-w", sites["acme"], "unit", "transfer", "NH-26-0001", sites["tmp"] / "pack", "--to", BAYSIDE)
    assert code == 0 and "transfer pack of NH-26-0001 for Bayside Service" in out and "PMP-777 fitted in it" in out
    code, out = run("-w", sites["acme"], "unit", "show", "NH-26-0001", "--no-history")
    assert "TRANSFERRED to Bayside Service" in out
    code, out = run("-w", sites["acme"], "unit", "note", "NH-26-0001", "x")
    assert code == 2 and "it is recorded in that workspace now" in out
    code, out = run("-w", sites["bay"], "unit", "receive", sites["tmp"] / "pack")
    assert code == 0 and "received: NH-26-0001, PMP-777 from Acme Engineering, NOT SIGNED" in out
    code, out = run("-w", sites["bay"], "unit", "show", "NH-26-0001", "--no-history")
    assert "received from Acme Engineering" in out and "from Acme Engineering's workspace, kept here as a reference" in out
    with Workspace.open(sites["bay"]) as ws:
        svc = service_for(ws)
        sections = dict(unit_sections(query.unit_detail(svc, svc.unit(sites["hpu"]))))
    head = sections[next(k for k in sections if k.startswith("NH-26-0001"))]
    assert any(line.startswith("Received from Acme Engineering on") for line in head)
    assert any("FRM-200: rev A" in line and "kept here as a reference" in line for line in sections["Drawings"])
    with Workspace.open(sites["acme"]) as ws:
        svc = service_for(ws)
        sections = dict(unit_sections(query.unit_detail(svc, svc.unit(sites["hpu"]))))
    head = sections[next(k for k in sections if k.startswith("NH-26-0001"))]
    assert any(line.startswith("Transferred to Bayside Service on") for line in head)


def test_a_received_units_handover_pack_has_the_kept_pdf(sites):
    send(sites)
    take(sites)
    with Workspace.open(sites["bay"], writable=True) as ws:
        made = pack.make(service_for(ws), "NH-26-0001", sites["tmp"] / "handover")
    assert made["pdfs"] == 1 and [m["document"] for m in made["missing"]] == ["LAYOUT"]
    assert "no PDF came with it from Acme Engineering" in made["missing"][0]["missing"]
    page = (sites["tmp"] / "handover" / "index.html").read_text("utf-8")
    assert "kept from Acme Engineering, read-only" in page


def test_a_change_never_reaches_a_transferred_unit(sites):
    """A change's candidates are the units its documents or parts reach: never one recorded in another workspace now."""
    from types import SimpleNamespace
    from revisionworx.units import effect
    rows = [{"document": sites["frm"], "from": sites["frm_a"], "to": sites["frm_a"]}]
    order = SimpleNamespace(affected=[])
    with Workspace.open(sites["acme"]) as ws:
        before = {c["unit"]["id"] for c in effect.candidates(service_for(ws), order, rows)}
    assert before == {sites["hpu"], sites["pump"]}
    send(sites)
    with Workspace.open(sites["acme"]) as ws:
        assert effect.candidates(service_for(ws), order, rows) == []
