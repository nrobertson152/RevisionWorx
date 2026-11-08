# SPDX-License-Identifier: GPL-3.0-or-later
"""Handover packs (docs/UNITS.md section 13): one unit's record as a folder anyone can open, with the PDFs of exactly the
revisions that apply to it, and the SHA-256 of every file. Made from a read-only handle; the workspace doesn't change."""
import contextlib
import hashlib
import io
import json

import pytest

from conftest import tree_snapshot
from revisionworx import derivatives as V, documents as D, projects
from revisionworx.cli import main as rwx_main
from revisionworx.projects import NewProject
from revisionworx.units import pack
from revisionworx.units.service import service_for
from revisionworx.workspace import Workspace

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
    before = tree_snapshot(share / ".revisionworx")
    head = ws.head_seq()
    made = pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    out = tmp_path / "pack"
    assert made["pdfs"] == 2 and made["missing"] == [] and made["signed"] is False
    assert (out / "documents" / "FRM-200 rev A.pdf").read_bytes() == plant["a_pdf"]     # its own PDF, not rev B's
    assert (out / "documents" / "SCH-12 rev A.pdf").read_bytes() == plant["sch_pdf"]    # the revision is itself a PDF
    listed = sums(out)
    assert set(listed) == {"index.html", "unit.json", "documents/FRM-200 rev A.pdf", "documents/SCH-12 rev A.pdf"}
    for rel, digest in listed.items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest
    record = json.loads((out / "unit.json").read_text("utf-8"))
    assert record["format"] == pack.PACK_FORMAT and record["version"] == pack.PACK_VERSION and record["signed"] is False
    assert record["unit"]["id"] == plant["unit"]["id"] and record["unit"]["customer"] == "Acme"
    assert {(p["document"], p["revision"], p["file"]) for p in record["pdfs"]} == {
        ("FRM-200", "A", "documents/FRM-200 rev A.pdf"), ("SCH-12", "A", "documents/SCH-12 rev A.pdf")}
    page = (out / "index.html").read_text("utf-8")
    for text in ("SN-1", "Power unit", "Acme", "Plant 2", "FRM-200", "Frame", "SCH-12", "MOT-7", "M-1001", "built",
                 "2026-11-01", 'href="documents/FRM-200%20rev%20A.pdf"', "not signed"):
        assert text in page, text
    assert "<script" not in page
    assert pack.check(out) == {"ok": True, "problems": [], "unit": "SN-1", "files": 5, "signed": False}
    assert ws.head_seq() == head and tree_snapshot(share / ".revisionworx") == before     # nothing written there
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
    with pytest.raises(OSError):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith((".pack", "pack"))) == []


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
    assert code == 0 and json.loads(text)["pdfs"] == 2
    code, text = run("unit", "check-pack", str(tmp_path / "pack"))
    assert code == 0 and "the handover pack of SN-1: 5 file(s), whole; not signed" in text
    (tmp_path / "pack" / "index.html").write_text("changed", "utf-8")
    code, text = run("unit", "check-pack", str(tmp_path / "pack"), "--json")
    assert code == 1 and json.loads(text)["problems"] == ["index.html has been changed since the pack was made"]
    code, _text = run("-w", str(share), "unit", "pack", "SN-1", str(tmp_path / "pack"))
    assert code == 2                                                  # the folder is taken: refused, nothing changed
