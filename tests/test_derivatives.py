# SPDX-License-Identifier: GPL-3.0-or-later
"""PDF derivatives (ADR-026): a PDF tied to the exact native revision it prints, never shown for another revision; replaced
only with a reason; kept by content clean-up; checked by doc verify; offered to other programs through link."""
import contextlib
import io
import json
import os
import time

import pytest

from revisionworx import derivatives as V, documents as D, integration, projects
from revisionworx.cli import main as rwx_main
from revisionworx.projects import NewProject
from revisionworx.workspace import ConflictError, LockLostError, RecordPut, Workspace

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


@pytest.fixture
def drawing(ws, tmp_path):
    """FRM-200, an Inventor drawing: rev A released, rev B checked in after it. And SCH-12, itself a PDF."""
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    path = folder / "frame.idw"
    path.write_bytes(b"IDW rev A")
    doc, rev_a = D.register_document(ws, rec["id"], path, number="FRM-200", title="Frame")
    rev_a = release(ws, rev_a["id"])
    pdf_path = folder / "schematic.pdf"
    pdf_path.write_bytes(PDF)
    sch, sch_a = D.register_document(ws, rec["id"], pdf_path, number="SCH-12", title="Schematic")
    release(ws, sch_a["id"])
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"IDW rev B")
    rev_b = D.checkin(ws, doc["id"], co["token"], "thicker")
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "a.pdf").write_bytes(PDF + b"% rev A\n")
    (exports / "a2.pdf").write_bytes(PDF + b"% rev A, corrected\n")
    (exports / "b.pdf").write_bytes(PDF + b"% rev B\n")
    (exports / "not.pdf").write_bytes(b"PK\x03\x04 a zip, not a PDF")
    return {"project": rec, "doc": doc, "a": rev_a, "b": rev_b, "sch": sch_a, "exports": exports}


def entries(ws):
    return len(ws.journal_seqs())


def test_a_pdf_is_tied_to_one_revision_and_never_shown_for_another(ws, drawing):
    n = entries(ws)
    d = V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf", tool="Inventor 2025 export")
    assert entries(ws) == n + 1 and ws.read_entry(ws.head_seq())["op"] == "derivative.attach"
    assert (d["revision"], d["document"], d["source_sha256"], d["tool"]) == (
        drawing["a"]["id"], drawing["doc"]["id"], drawing["a"]["sha256"], "Inventor 2025 export")
    assert V.get(ws, drawing["b"]["id"]) is None                      # rev B has no PDF: rev A's is never offered
    assert set(V.of_document(ws, drawing["doc"]["id"])) == {drawing["a"]["id"]}
    out = V.export(ws, drawing["a"]["id"], drawing["exports"] / "copy.pdf")
    assert out.read_bytes() == (drawing["exports"] / "a.pdf").read_bytes()
    assert V.verify(ws) == [] and ws.verify()["problems"] == []


def test_only_a_real_pdf_for_a_revision_that_isnt_one(ws, drawing):
    n = entries(ws)
    with pytest.raises(V.DerivativeError, match="isn't a PDF"):
        V.attach(ws, drawing["a"]["id"], drawing["exports"] / "not.pdf")
    with pytest.raises(V.DerivativeError, match="is a PDF already"):
        V.attach(ws, drawing["sch"]["id"], drawing["exports"] / "a.pdf")
    with pytest.raises(V.DerivativeError, match="no revision"):
        V.attach(ws, "rev_" + "0" * 32, drawing["exports"] / "a.pdf")
    assert entries(ws) == n


def test_replacing_needs_a_reason_and_keeps_the_earlier_one(ws, drawing):
    first = V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf")
    with pytest.raises(V.DerivativeError, match="--replace"):
        V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a2.pdf")
    with pytest.raises(V.DerivativeError, match="reason"):
        V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a2.pdf", replace=True)
    with pytest.raises(V.DerivativeError, match="already has"):
        V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf", replace=True, reason="same")
    d = V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a2.pdf", replace=True, reason="title block was wrong")
    assert d["sha256"] != first["sha256"]
    assert [(o["sha256"], o["reason"]) for o in d["replaced"]] == [(first["sha256"], "title block was wrong")]
    assert V.referenced(ws) == {first["sha256"], d["sha256"]}


def test_two_attachments_at_once_one_conflicts(ws, drawing, monkeypatch):
    """The record is created with expect_rev=0: a PDF attached between this one's read and its commit makes it a conflict."""
    real_commit = ws.commit
    state = {"raced": False}

    def racing_commit(op, ops, *args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            monkeypatch.setattr(ws, "commit", real_commit)
            V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a2.pdf")
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf")
    import hashlib
    current = V.get(ws, drawing["a"]["id"])
    assert current["sha256"] == hashlib.sha256((drawing["exports"] / "a2.pdf").read_bytes()).hexdigest()
    assert current["replaced"] == []                                    # the loser left nothing behind


def test_content_clean_up_keeps_every_pdf(ws, drawing):
    V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf")
    V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a2.pdf", replace=True, reason="fix")
    for p in (V.ContentStore(ws).root).rglob("*"):
        if p.is_file():
            old = time.time() - 7200
            os.utime(p, (old, old))
    report = D.gc_content(ws, apply=True)
    assert report["orphans"] == [] and V.verify(ws) == []


def test_verify_reports_damaged_content_and_a_moved_pdf(ws, drawing):
    d = V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf")
    path = V.ContentStore(ws).path_for(d["sha256"])
    os.chmod(path, 0o644)
    path.write_bytes(PDF + b"% tampered\n")
    rec = ws.get(V.KIND, d["id"])
    ws.commit("tamper", [RecordPut(V.KIND, d["id"], {**{k: v for k, v in rec.items() if k != "_rev"},
                                                     "source_sha256": drawing["b"]["sha256"]}, expect_rev=rec["_rev"])])
    problems = {p["problem"] for p in V.verify(ws)}
    assert problems == {"content missing or damaged", "attached to other content"}


def test_a_writer_whose_lock_was_broken_attaches_nothing(share, ws, drawing):
    n = entries(ws)
    Workspace.break_writer_lock(share, Workspace.read_writer_lock(share)["token"], "test")
    with pytest.raises(LockLostError):
        V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf")
    with Workspace.open(share) as ro:
        assert len(ro.journal_seqs()) == n + 1 and V.get(ro, drawing["a"]["id"]) is None


def test_missing_lists_released_revisions_without_a_pdf(ws, drawing):
    assert [(m["number"], m["label"]) for m in V.missing(ws)] == [("FRM-200", "A")]   # SCH-12 is a PDF itself
    V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf")
    assert V.missing(ws) == []
    release(ws, drawing["b"]["id"])
    assert [(m["number"], m["label"]) for m in V.missing(ws)] == [("FRM-200", "B")]   # a new release needs its own


def test_link_offers_each_revisions_own_pdf(share, ws, drawing):
    V.attach(ws, drawing["a"]["id"], drawing["exports"] / "a.pdf", tool="Inventor 2025 export")
    ws.close()
    doc = integration.document("FRM-200", workspace=share)["document"]
    assert [(r["label"], r["pdf"] and r["pdf"]["tool"]) for r in doc["history"]] == [("A", "Inventor 2025 export"),
                                                                                    ("B", None)]
    got = integration.fetch("FRM-200", share / "out", revision="A", workspace=share, pdf=True)
    assert got["pdf"] and open(got["path"], "rb").read() == (drawing["exports"] / "a.pdf").read_bytes()
    with pytest.raises(integration.LinkError) as exc:
        integration.fetch("FRM-200", share / "out", revision="B", workspace=share, pdf=True)
    assert exc.value.kind == "not-found"


def test_the_command_line_attaches_lists_and_verifies(share, ws, drawing):
    ws.close()

    def run(*args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = rwx_main(["-w", str(share), *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    code, out, err = run("doc", "pdf", "FRM-200@A", str(drawing["exports"] / "a.pdf"), "--tool", "Inventor 2025 export")
    assert code in (0, None), err
    assert "FRM-200 rev A: PDF" in out
    code, out, _ = run("doc", "pdfs")
    assert "FRM-200 rev A" in out and "Inventor 2025 export" in out
    code, out, _ = run("--json", "doc", "pdfs", "--missing")
    assert json.loads(out) == []
    code, out, _ = run("doc", "verify")
    assert code in (0, None) and "and PDFs verified" in out
    code, _, err = run("doc", "pdf", "FRM-200@Z", str(drawing["exports"] / "a.pdf"))
    assert code not in (0, None) and "no revision 'Z'" in err
