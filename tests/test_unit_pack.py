# SPDX-License-Identifier: GPL-3.0-or-later
"""Handover packs (docs/UNITS.md section 13): one unit's record as a folder anyone can open, with the PDFs of exactly the
revisions that apply to it, and the SHA-256 of every file. Each pack is numbered and recorded in the unit's history as issued
(ADR-030), so the workspace can say later whether it issued a pack, and whether it is still in effect."""
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys

import pytest

from conftest import tree_snapshot
from revisionworx import derivatives as V, documents as D, projects
from revisionworx.cli import main as rwx_main
from revisionworx.projects import NewProject
from revisionworx.units import history as H, pack, query
from revisionworx.units.model import Changed, MalformedUnitRecord, ValidationError, load_event
from revisionworx.units.service import service_for
from revisionworx.workspace import Workspace, WorkspaceError

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


@pytest.fixture
def plant(ws, tmp_path):
    """FRM-200 (an Inventor drawing: rev A released with its PDF, rev B checked in after it, no PDF), SCH-12 (itself a PDF),
    and SN-1, built to FRM-200 rev A and SCH-12 rev A, with a motor fitted."""
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    (folder / "frame.idw").write_bytes(b"IDW rev A")
    doc, rev_a = D.register_document(ws, rec["id"], folder / "frame.idw", number="FRM-200", title="Frame")
    rev_a = release(ws, rev_a["id"])
    (folder / "schematic.pdf").write_bytes(PDF + b"% the schematic\n")
    sch, sch_a = D.register_document(ws, rec["id"], folder / "schematic.pdf", number="SCH-12", title="Schematic")
    release(ws, sch_a["id"])
    co = D.checkout(ws, doc["id"])
    (folder / "frame.idw").write_bytes(b"IDW rev B")
    rev_b = D.checkin(ws, doc["id"], co["token"], "thicker")
    (tmp_path / "a.pdf").write_bytes(PDF + b"% frame, rev A\n")
    V.attach(ws, rev_a["id"], tmp_path / "a.pdf", tool="Inventor export")
    svc = service_for(ws)
    unit = svc.register(identifiers=[{"scheme": "serial", "issuer": "", "value": "SN-1"}], name="Power unit",
                        customer="Acme", site="Plant 2", documents={doc["id"]: rev_a["id"], sch["id"]: sch_a["id"]},
                        effective="2026-11-01")
    svc.install(unit["id"], "M1", number="MOT-7", serial="M-1001")
    svc.milestone(unit["id"], "built", "2026-11-01")
    return {"svc": svc, "unit": unit, "doc": doc, "a": rev_a, "b": rev_b, "sch": sch, "sch_a": sch_a,
            "a_pdf": (tmp_path / "a.pdf").read_bytes(), "sch_pdf": (folder / "schematic.pdf").read_bytes()}


def sums(folder):
    return {line[66:]: line[:64] for line in (folder / pack.SUMS).read_text("utf-8").splitlines()}


def test_a_pack_has_the_page_the_pdfs_that_apply_the_record_and_their_checksums(share, ws, plant, tmp_path):
    head = ws.head_seq()
    made = pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    out = tmp_path / "pack"
    assert made["pdfs"] == 2 and made["missing"] == [] and made["signed"] is False and made["pack"] == 1
    assert (out / "documents" / "FRM-200 rev A.pdf").read_bytes() == plant["a_pdf"]     # its own PDF, not rev B's
    assert (out / "documents" / "SCH-12 rev A.pdf").read_bytes() == plant["sch_pdf"]    # the revision is itself a PDF
    listed = sums(out)
    assert set(listed) == {"index.html", "unit.json", "documents/FRM-200 rev A.pdf", "documents/SCH-12 rev A.pdf"}
    for rel, digest in listed.items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    record = json.loads((out / "unit.json").read_text("utf-8"))
    assert record["format"] == pack.PACK_FORMAT and record["version"] == pack.PACK_VERSION and record["signed"] is False
    assert record["pack"] == 1 and record["made_by"]["user"] and "host" not in record["made_by"]
    assert record["unit"]["id"] == plant["unit"]["id"] and record["unit"]["customer"] == "Acme"
    assert {(p["document"], p["revision"], p["file"]) for p in record["pdfs"]} == {
        ("FRM-200", "A", "documents/FRM-200 rev A.pdf"), ("SCH-12", "A", "documents/SCH-12 rev A.pdf")}
    page = (out / "index.html").read_text("utf-8")
    for text in ("SN-1", "Power unit", "Acme", "Plant 2", "FRM-200", "Frame", "SCH-12", "MOT-7", "M-1001", "built",
                 "2026-11-01", 'href="documents/FRM-200%20rev%20A.pdf"', "not signed", "Handover pack 1 of SN-1",
                 "records every pack it issues"):
        assert text in page, text
    assert "<script" not in page
    assert pack.check(out) == {"ok": True, "problems": [], "unit": "SN-1", "pack": 1, "files": 5, "signed": False,
                               "register": None}
    assert ws.head_seq() == head + 1                                  # one entry: the pack recorded as issued
    assert not list(tmp_path.glob(".pack.*.tmp"))


def test_a_revision_without_a_pdf_is_listed_never_given_another_revisions(ws, plant, tmp_path):
    svc = plant["svc"]
    svc.set_document(plant["unit"]["id"], plant["doc"]["id"], plant["b"]["id"])
    made = pack.make(svc, "SN-1", tmp_path / "pack")
    assert [(m["document"], m["revision"], m["missing"]) for m in made["missing"]] == [
        ("FRM-200", "B", "no PDF is attached to this revision")]
    assert not (tmp_path / "pack" / "documents" / "FRM-200 rev A.pdf").exists()
    page = (tmp_path / "pack" / "index.html").read_text("utf-8")
    assert "not included" in page and "no PDF is attached to this revision" in page
    assert pack.check(tmp_path / "pack")["ok"]


def test_a_newer_released_revision_is_said_on_the_page(ws, plant, tmp_path):
    release(ws, plant["b"]["id"])
    pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    page = (tmp_path / "pack" / "index.html").read_text("utf-8")
    assert "revision B is released; this unit has A" in page


def test_check_finds_a_changed_file_a_missing_one_and_one_added(ws, plant, tmp_path):
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out)
    pdf = out / "documents" / "SCH-12 rev A.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"% edited\n")
    (out / "documents" / "FRM-200 rev A.pdf").unlink()
    (out / "notes.txt").write_text("added later")
    report = pack.check(out)
    assert not report["ok"] and report["unit"] == "SN-1"
    assert report["problems"] == ["documents/FRM-200 rev A.pdf is missing",
                                  "documents/SCH-12 rev A.pdf has been changed since the pack was made",
                                  "notes.txt isn't part of the pack"]


@pytest.mark.parametrize("damage", ["no-sums", "bad-line", "escaping-path", "newer-version"])
def test_check_refuses_what_isnt_a_pack_it_can_read(ws, plant, tmp_path, damage):
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out)
    sums_file = out / pack.SUMS
    if damage == "no-sums":
        sums_file.unlink()
    elif damage == "bad-line":
        sums_file.write_text(sums_file.read_text("utf-8") + "not a checksum\n", "utf-8")
    elif damage == "escaping-path":
        sums_file.write_text(sums_file.read_text("utf-8") + "0" * 64 + "  ../outside.txt\n", "utf-8")
    else:
        record = json.loads((out / "unit.json").read_text("utf-8"))
        record["version"] = pack.PACK_VERSION + 1
        data = json.dumps(record).encode("utf-8")
        (out / "unit.json").write_bytes(data)
        listed = sums(out)
        listed["unit.json"] = hashlib.sha256(data).hexdigest()
        sums_file.write_text("".join(f"{v}  {k}\n" for k, v in sorted(listed.items())), "utf-8")
    report = pack.check(out)
    assert not report["ok"] and report["problems"]
    if damage == "newer-version":
        assert report["problems"] == [f"unit.json was made by a newer RevisionWorx (pack version {pack.PACK_VERSION + 1}); "
                                      "update RevisionWorx to check it"]


def test_a_pack_goes_only_into_a_new_or_empty_folder(ws, plant, tmp_path):
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("mine")
    with pytest.raises(pack.PackError, match="isn't an empty folder"):
        pack.make(plant["svc"], "SN-1", taken)
    assert [p.name for p in taken.iterdir()] == ["keep.txt"]
    (tmp_path / "file").write_text("a file")
    with pytest.raises(pack.PackError):
        pack.make(plant["svc"], "SN-1", tmp_path / "file")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert pack.make(plant["svc"], "SN-1", empty)["pdfs"] == 2
    assert not list(tmp_path.glob(".*.tmp"))


def test_a_pack_that_fails_leaves_nothing_behind(ws, plant, tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(pack, "render", broken)
    head = ws.head_seq()
    with pytest.raises(OSError):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith((".pack", "pack"))) == []
    assert ws.head_seq() == head and plant["svc"].packs(plant["unit"]["id"]) == []      # and nothing is recorded


def test_the_page_escapes_what_it_shows(ws, plant, tmp_path):
    svc = plant["svc"]
    svc.update(plant["unit"]["id"], name='<script>alert("x")</script> & "co"', site="<b>Plant</b>")
    pack.make(svc, "SN-1", tmp_path / "pack")
    page = (tmp_path / "pack" / "index.html").read_text("utf-8")
    assert "<script" not in page and "<b>Plant</b>" not in page
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &quot;co&quot;" in page


def test_the_computer_name_stays_behind(ws, plant, tmp_path):
    pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    record = json.loads((tmp_path / "pack" / "unit.json").read_text("utf-8"))
    actors = [e["actor"] for e in record["unit"]["history"]]
    assert actors and all("host" not in a and a.get("user") for a in actors)


def test_a_pack_as_known_on_a_date(ws, plant, tmp_path):
    svc = plant["svc"]
    svc.set_document(plant["unit"]["id"], plant["doc"]["id"], plant["b"]["id"], effective="2026-11-05")
    made = pack.make(svc, "SN-1", tmp_path / "pack", on="2026-11-02")
    record = json.loads((tmp_path / "pack" / "unit.json").read_text("utf-8"))
    assert record["as_of"] == "2026-11-02" and made["pdfs"] == 2      # rev A then, with its PDF
    assert "as known on 2026-11-02" in (tmp_path / "pack" / "index.html").read_text("utf-8")
    now = pack.make(svc, "SN-1", tmp_path / "now")
    assert [(m["document"], m["revision"]) for m in now["missing"]] == [("FRM-200", "B")]


def test_the_command_line_makes_a_pack_and_checks_it_without_a_workspace(share, ws, plant, tmp_path):
    ws.close()

    def run(*args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rwx_main(list(args))
        return code, buf.getvalue()
    code, text = run("-w", str(share), "unit", "pack", "SN-1", str(tmp_path / "pack"), "--json")
    assert code == 0 and json.loads(text)["pdfs"] == 2 and json.loads(text)["pack"] == 1
    code, text = run("unit", "check-pack", str(tmp_path / "pack"))
    assert code == 0 and "the handover pack of SN-1 (pack 1): 5 file(s), whole; not signed" in text
    assert "not checked against the register of the workspace that made it" in text
    (tmp_path / "pack" / "index.html").write_text("changed", "utf-8")
    code, text = run("unit", "check-pack", str(tmp_path / "pack"), "--json")
    assert code == 1 and json.loads(text)["problems"] == ["index.html has been changed since the pack was made"]
    code, _text = run("-w", str(share), "unit", "pack", "SN-1", str(tmp_path / "pack"))
    assert code == 2                                                  # the folder is taken: refused, nothing changed


# ------------------------------------------------------------------------------------------- the register (ADR-030)
def sums_digest(folder):
    return hashlib.sha256((folder / pack.SUMS).read_bytes()).hexdigest()


def reseal(folder):
    """What a forger does: change a file, then write SHA256SUMS again so the pack is whole."""
    listed = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(folder.rglob("*")) if p.is_file() and p.name != pack.SUMS}
    (folder / pack.SUMS).write_text("".join(f"{v}  {k}\n" for k, v in sorted(listed.items())), "utf-8")


def test_making_a_pack_records_it_in_the_units_history_as_issued(ws, plant, tmp_path):
    svc, uid = plant["svc"], plant["unit"]["id"]
    before = svc.unit(uid)
    count = before["events"]["count"]
    pack.make(svc, "SN-1", tmp_path / "one")
    events = svc.events(uid)
    assert len(events) == count + 1 and events[-1]["type"] == "pack.issued"
    assert events[-1]["body"] == {"pack": 1, "sums": sums_digest(tmp_path / "one"), "as_of": "", "key": ""}
    assert H.summary(events[-1]) == "handover pack 1 issued"
    second = pack.make(svc, "SN-1", tmp_path / "two", on="2026-11-02")
    assert second["pack"] == 2 and "Handover pack 2 of SN-1" in (tmp_path / "two" / "index.html").read_text("utf-8")
    assert [(p["pack"], p["as_of"], p["sums"], p["withdrawn"]) for p in svc.packs(uid)] == [
        (1, "", sums_digest(tmp_path / "one"), None), (2, "2026-11-02", sums_digest(tmp_path / "two"), None)]
    history = query.unit_detail(svc, svc.unit(uid))["history"]
    assert [h["summary"] for h in history[-2:]] == ["handover pack 1 issued", "handover pack 2 issued, as known on 2026-11-02"]
    # the second pack's own history shows the first being issued; the unit's record is otherwise as it was
    assert "handover pack 1 issued" in (tmp_path / "two" / "index.html").read_text("utf-8")
    def kept(u):
        return {k: v for k, v in u.items() if k not in ("events", "updated", "_rev")}
    assert kept(svc.unit(uid)) == kept(before)
    assert query.verify(svc) == []


def test_checked_against_its_workspace_a_pack_is_confirmed_as_issued(ws, plant, tmp_path):
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out)
    report = pack.check(out, plant["svc"])
    assert report["ok"] and report["problems"] == [] and report["pack"] == 1
    reg = report["register"]
    assert reg["state"] == "issued" and reg["pack"] == 1 and reg["unit"] == "SN-1" and reg["workspace"] == "Test workspace"
    assert reg["by"] and reg["withdrawn"] is None and reg["newer"] is None and reg["key"] == ""
    assert reg["message"] == f"SN-1 handover pack 1, issued by Test workspace on {reg['issued'][:10]} by {reg['by']}"


def test_a_pack_made_whole_again_after_a_change_is_not_one_the_workspace_issued(ws, plant, tmp_path):
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out)
    page = out / "index.html"
    page.write_text(page.read_text("utf-8").replace("Plant 2", "Plant 9"), "utf-8")
    reseal(out)
    assert pack.check(out)["ok"]                                       # whole: SHA256SUMS can't tell on its own
    report = pack.check(out, plant["svc"])
    assert not report["ok"] and report["register"]["state"] == "not-issued"
    assert report["problems"] == ["Test workspace has no record of issuing this pack: no pack of SN-1 it issued has this "
                                  "SHA256SUMS"]


def test_a_pack_checked_against_another_workspace_is_not_confirmed(ws, plant, tmp_path):
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out)
    with Workspace.create(tmp_path / "other", "Other company") as other:
        report = pack.check(out, service_for(other))
    assert not report["ok"] and report["register"]["state"] == "not-issued"
    assert report["problems"] == [f"Other company has no unit {plant['unit']['id']}, so it didn't issue this pack"]


def test_a_withdrawn_pack_is_reported_with_when_who_and_why(ws, plant, tmp_path):
    svc, uid = plant["svc"], plant["unit"]["id"]
    out = tmp_path / "pack"
    pack.make(svc, "SN-1", out)
    with pytest.raises(ValidationError, match="a reason is required"):
        svc.withdraw_pack(uid, 1, "")
    with pytest.raises(ValidationError, match="has no handover pack 2"):
        svc.withdraw_pack(uid, 2, "never made")
    svc.withdraw_pack(uid, 1, "the site was wrong; pack 2 replaces it")
    with pytest.raises(ValidationError, match="already withdrawn"):
        svc.withdraw_pack(uid, 1, "again")
    report = pack.check(out, svc)
    assert not report["ok"] and report["register"]["state"] == "withdrawn"
    w = report["register"]["withdrawn"]
    assert w["reason"] == "the site was wrong; pack 2 replaces it" and w["by"]
    assert report["problems"][-1].endswith(f"was withdrawn on {w['time'][:10]} by {w['by']}: the site was wrong; pack 2 "
                                           "replaces it")
    assert H.summary(svc.events(uid)[-1]) == "handover pack 1 withdrawn"
    assert query.verify(svc) == []


def test_a_pack_checked_later_says_a_newer_one_was_issued(ws, plant, tmp_path):
    svc = plant["svc"]
    pack.make(svc, "SN-1", tmp_path / "one")
    pack.make(svc, "SN-1", tmp_path / "two")
    reg = pack.check(tmp_path / "one", svc)["register"]
    assert reg["state"] == "issued" and reg["newer"]["pack"] == 2 and "pack 2 has been issued since" in reg["message"]
    svc.withdraw_pack(plant["unit"]["id"], 2, "sent to the wrong customer")
    assert pack.check(tmp_path / "one", svc)["register"]["newer"] is None     # a withdrawn pack doesn't replace it


def test_a_pack_isnt_recorded_if_the_unit_changed_while_it_was_put_together(share, ws, plant, tmp_path):
    svc, uid = plant["svc"], plant["unit"]["id"]
    with pack.build(svc, "SN-1", tmp_path / "pack") as draft:
        svc.note(uid, "inspected after the pack was put together")
        head, before = ws.head_seq(), tree_snapshot(share / ".revisionworx")
        with pytest.raises(Changed, match="something was recorded on it since"):
            pack.issue(svc, draft)
    assert ws.head_seq() == head and tree_snapshot(share / ".revisionworx") == before and svc.packs(uid) == []
    assert not (tmp_path / "pack").exists() and not list(tmp_path.glob(".pack.*.tmp"))


def test_a_pack_that_cant_be_recorded_leaves_nothing(ws, plant, tmp_path, monkeypatch):
    def refused(*_args, **_kwargs):
        raise WorkspaceError("the writer lock is held by someone else")
    monkeypatch.setattr(plant["svc"], "issue_pack", refused)
    with pytest.raises(WorkspaceError):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    assert not (tmp_path / "pack").exists() and not list(tmp_path.glob(".pack.*.tmp"))


def test_a_pack_put_together_on_a_read_only_handle_is_recorded_on_a_writable_one(share, ws, plant, tmp_path):
    uid = plant["unit"]["id"]
    ws.close()
    with Workspace.open(share) as ro:
        draft = pack.build(service_for(ro), "SN-1", tmp_path / "pack")
        with pytest.raises(WorkspaceError):
            with draft:
                pack.issue(service_for(ro), draft)                    # a read-only handle can't record it
    assert not list(tmp_path.glob(".pack.*.tmp")) and not (tmp_path / "pack").exists()
    with Workspace.open(share) as ro:
        draft = pack.build(service_for(ro), "SN-1", tmp_path / "pack")
    with draft, Workspace.open(share, writable=True) as rw:
        made = pack.issue(service_for(rw), draft)
    assert made["pack"] == 1 and pack.check(tmp_path / "pack")["ok"]
    with Workspace.open(share) as ro:
        assert pack.check(tmp_path / "pack", service_for(ro))["register"]["state"] == "issued"
        assert [p["pack"] for p in service_for(ro).packs(uid)] == [1]


def test_a_destination_taken_while_the_pack_was_put_together_is_refused_before_recording(ws, plant, tmp_path):
    with pack.build(plant["svc"], "SN-1", tmp_path / "pack") as draft:
        (tmp_path / "pack").mkdir()
        (tmp_path / "pack" / "mine.txt").write_text("someone else's")
        with pytest.raises(pack.PackError, match="isn't an empty folder"):
            pack.issue(plant["svc"], draft)
    assert plant["svc"].packs(plant["unit"]["id"]) == [] and [p.name for p in (tmp_path / "pack").iterdir()] == ["mine.txt"]


def test_an_issued_pack_is_withdrawn_never_voided(ws, plant, tmp_path):
    svc, uid = plant["svc"], plant["unit"]["id"]
    pack.make(svc, "SN-1", tmp_path / "pack")
    n = svc.unit(uid)["events"]["count"]
    with pytest.raises(ValidationError, match="withdraw the pack instead"):
        svc.void(uid, [n], "made by mistake")


def test_the_loader_refuses_a_pack_event_revisionworx_wouldnt_write(ws, plant, tmp_path):
    pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    good = plant["svc"].events(plant["unit"]["id"])[-1]
    assert load_event(good)["body"]["pack"] == 1
    for bad in ({"pack": 0}, {"pack": True}, {"pack": "1"}, {"sums": "ABC"}, {"sums": "0" * 63}, {"as_of": "8 Nov"},
                {"as_of": "2026-11-08T10:00Z"}, {"key": "ed25519 AAAA"}, {"extra": ""}):
        with pytest.raises(MalformedUnitRecord):
            load_event({**good, "body": {**good["body"], **bad}})
    assert load_event({**good, "body": {**good["body"], "key": "SHA256:" + "A" * 43}})["body"]["key"]


def test_unit_verify_finds_pack_numbers_and_withdrawals_revisionworx_wouldnt_write():
    def ev(n, type_, body):
        return {"n": n, "type": type_, "body": body, "time": "2026-11-08T10:00:00Z", "actor": {}, "reason": ""}
    issued = {"sums": "0" * 64, "as_of": "", "key": ""}
    assert H.pack_problems([ev(1, "unit.registered", {}), ev(2, "pack.issued", {"pack": 1, **issued}),
                            ev(3, "pack.issued", {"pack": 2, **issued}), ev(4, "pack.withdrawn", {"pack": 1})]) == []
    assert H.pack_problems([ev(1, "pack.issued", {"pack": 2, **issued}), ev(2, "pack.withdrawn", {"pack": 5}),
                            ev(3, "void", {"events": [1]})]) == [
        "event 1 issues handover pack 2, but the next pack is 1",
        "event 2 withdraws handover pack 5, which isn't issued and in effect",
        "event 3 voids event 1, a handover pack event, which is never voided"]


def test_a_pack_made_before_packs_were_recorded_is_whole_but_cant_be_confirmed(ws, plant, tmp_path):
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out)
    record = json.loads((out / "unit.json").read_text("utf-8"))
    record["version"] = 1
    del record["pack"], record["made_by"]
    (out / "unit.json").write_text(json.dumps(record), "utf-8")
    reseal(out)
    assert pack.check(out)["ok"] and pack.check(out)["pack"] is None
    report = pack.check(out, plant["svc"])
    assert not report["ok"] and report["register"]["state"] == "not-issued"
    assert report["problems"] == ["this pack was made before RevisionWorx recorded the packs it issues, so Test workspace "
                                  "can't confirm that it issued it"]


def test_the_command_line_lists_withdraws_and_confirms_packs(share, ws, plant, tmp_path):
    ws.close()

    def run(*args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rwx_main(list(args))
        return code, buf.getvalue()
    code, text = run("-w", str(share), "unit", "pack", "SN-1", str(tmp_path / "pack"))
    assert code == 0 and text.startswith(f"handover pack 1 of SN-1 in {tmp_path / 'pack'}: 5 file(s), 2 PDF(s)")
    assert "recorded in its history as issued; not signed" in text
    code, text = run("-w", str(share), "unit", "check-pack", str(tmp_path / "pack"))
    assert code == 0 and "issued: SN-1 handover pack 1, issued by Test workspace on " in text
    code, text = run("-w", str(share), "unit", "packs", "SN-1", "--json")
    assert code == 0 and [p["pack"] for p in json.loads(text)["packs"]] == [1]
    code, text = run("-w", str(share), "unit", "withdraw-pack", "SN-1", "1", "--reason", "replaced by pack 2")
    assert code == 0 and "handover pack 1 of SN-1 withdrawn" in text
    code, text = run("-w", str(share), "unit", "packs", "SN-1")
    assert code == 0 and "withdrawn" in text and "replaced by pack 2" in text and "1 handover pack(s) of SN-1" in text
    code, text = run("-w", str(share), "unit", "check-pack", str(tmp_path / "pack"))
    assert code == 1 and "NOT CONFIRMED: SN-1 handover pack 1, issued by Test workspace" in text
    assert "whole; not signed" in text and "replaced by pack 2" in text
    code, text = run("-w", str(share), "unit", "check-pack", str(tmp_path / "pack"), "--json")
    assert code == 1 and json.loads(text)["register"]["state"] == "withdrawn"
    code, text = run("unit", "check-pack", str(tmp_path / "pack"))
    assert code == 0                                                   # whole, and no register to ask
    code, text = run("-w", str(share), "unit", "history", "SN-1", "--json")
    issued = [e["n"] for e in json.loads(text) if e["type"] == "pack.issued"]
    code, _ = run("-w", str(share), "unit", "void", "SN-1", str(issued[0]), "--reason", "x")
    assert len(issued) == 1 and code == 2                              # a pack event isn't voided


def test_check_pack_runs_as_a_module_too(ws, plant, tmp_path):
    pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    done = subprocess.run([sys.executable, "-m", "revisionworx.units", "-", "check-pack", str(tmp_path / "pack")],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "the handover pack of SN-1 (pack 1): 5 file(s), whole" in done.stdout, done.stderr
    shutil.rmtree(tmp_path / "pack" / "documents")
    done = subprocess.run([sys.executable, "-m", "revisionworx.units", "-", "check-pack", str(tmp_path / "pack")],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 1 and "documents/FRM-200 rev A.pdf is missing" in done.stdout, done.stderr
