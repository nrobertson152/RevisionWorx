# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent Phase 2 document integrity regressions.

The Phase 2 module exists on the stacked phase2-documents branch, not on
the QA PR's Phase 1 base. Skips on the Phase 1 base are intentional and
MUST NOT be counted as verified Phase 2 functionality.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 documents module absent on Phase 1 base; run on phase2-documents",
                allow_module_level=True)

from revisionworx import atomicio, documents as D, projects
from revisionworx.content import ContentStore
from revisionworx.projects import NewProject
from .fixtures import fingerprint


@pytest.fixture
def managed_document(ws):
    project = projects.create_project(ws, NewProject(name="QA pump skid", number="QA-001"))
    folder = projects.project_folder(ws, project)
    source = folder / "pump-bracket.ipt"
    assembly = folder / "pump-skid.iam"
    source.write_bytes(b"Inventor synthetic bracket revision A\n")
    assembly.write_bytes(b"REF pump-bracket.ipt\n")
    before = fingerprint(folder)
    doc, initial = D.register_document(ws, project["id"], source, number="QA-DRW-001")
    assert initial is not None
    return folder, source, assembly, before, doc, initial


def test_registration_preserves_native_file_names_bytes_and_mtimes(ws, managed_document):
    folder, source, assembly, before, doc, initial = managed_document
    assert fingerprint(folder) == before
    assert doc["rel_path"] == source.relative_to(folder).as_posix()
    assert initial["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert D.verify_revisions(ws, doc["id"]) == []


def test_checkin_keeps_historic_blob_and_assembly_reference_unchanged(ws, managed_document):
    folder, source, assembly, before, doc, rev_a = managed_document
    original_assembly = assembly.read_bytes()
    original_blob = ContentStore(ws).path_for(rev_a["sha256"]).read_bytes()
    co = D.checkout(ws, doc["id"])
    source.write_bytes(b"Inventor synthetic bracket revision B with new bore\n")
    rev_b = D.checkin(ws, doc["id"], co["token"], "Change bore diameter")
    assert rev_b["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert rev_b["previous_rev_id"] == rev_a["id"]
    assert rev_b["id"] != rev_a["id"]
    assert [r["label"] for r in D.history(ws, doc["id"])] == ["A", "B"]
    assert ws.get("checkout", doc["id"]) is None
    assert ContentStore(ws).path_for(rev_a["sha256"]).read_bytes() == original_blob
    assert assembly.read_bytes() == original_assembly
    assert assembly.name == "pump-skid.iam"
    assert D.verify_revisions(ws, doc["id"]) == []
    assert ws.verify()["problems"] == []


def test_explicit_hash_detects_external_same_size_same_mtime_edit(ws, managed_document):
    import os

    folder, source, assembly, before, doc, rev_a = managed_document
    old = source.stat()
    data = source.read_bytes()
    source.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert source.stat().st_size == old.st_size
    assert source.stat().st_mtime_ns == old.st_mtime_ns
    # Metadata-only checking deliberately cannot detect this class of change.
    result = D.document_status(ws, doc["id"], verify_hash=True)
    assert result["state"] == "modified-externally"
    assert result["method"] == "hash"
    assert ws.get("document", doc["id"])["rev_count"] == 1


def test_prepublication_failure_preserves_checkout_and_revision(ws, managed_document, monkeypatch):
    folder, source, assembly, before, doc, rev_a = managed_document
    co = D.checkout(ws, doc["id"])
    source.write_bytes(b"Revision B after partial write\n")
    before_seq = ws.head_seq()
    real_publish = atomicio.publish_exclusive

    def fail_journal(path, data):
        if Path(path).parent.name == "journal":
            raise OSError(28, "QA simulated disk full")
        return real_publish(path, data)

    with monkeypatch.context() as m:
        m.setattr(atomicio, "publish_exclusive", fail_journal)
        with pytest.raises(OSError):
            D.checkin(ws, doc["id"], co["token"], "Attempted revision B")
    assert ws.head_seq() == before_seq
    assert ws.get("checkout", doc["id"])["token"] == co["token"]
    assert ws.get("document", doc["id"])["latest_rev_id"] == rev_a["id"]
    assert [r["id"] for r in D.history(ws, doc["id"])] == [rev_a["id"]]
    # A durable but unreferenced content object may remain; no revision was committed.
    assert ws.verify()["problems"] == []


def test_administrative_checkout_release_invalidates_old_token(ws, managed_document):
    folder, source, assembly, before, doc, rev_a = managed_document
    old = D.checkout(ws, doc["id"])
    D.admin_release_checkout(ws, doc["id"], old["token"], reason="Workstation unavailable")
    successor = D.checkout(ws, doc["id"])
    source.write_bytes(b"Another engineer's authorized change\n")
    with pytest.raises(D.TokenMismatch):
        D.checkin(ws, doc["id"], old["token"], "Unauthorized late check-in")
    assert ws.get("checkout", doc["id"])["token"] == successor["token"]
    assert ws.get("document", doc["id"])["latest_rev_id"] == rev_a["id"]
    assert D.verify_revisions(ws, doc["id"]) == []


def test_release_supersedes_prior_revision_without_mutating_history(ws, managed_document):
    folder, source, assembly, before, doc, rev_a = managed_document
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_a["id"], state, comment="QA approval A")
    assert ws.get("document", doc["id"])["released_rev_id"] == rev_a["id"]
    old_sha = ws.get("revision", rev_a["id"])["sha256"]
    co = D.checkout(ws, doc["id"])
    source.write_bytes(b"Inventor synthetic revision B after approval\n")
    rev_b = D.checkin(ws, doc["id"], co["token"], "Revise approved design")
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_b["id"], state, comment="QA approval B")
    earlier = ws.get("revision", rev_a["id"])
    newer = ws.get("revision", rev_b["id"])
    assert earlier["state"] == "superseded"
    assert earlier["sha256"] == old_sha
    assert earlier["label"] == "A"
    assert newer["state"] == "released"
    assert ws.get("document", doc["id"])["released_rev_id"] == rev_b["id"]
    assert len([t for t in earlier["transitions"] if t["to"] == "superseded"]) == 1
    assert ws.verify()["problems"] == []
    assert D.verify_revisions(ws, doc["id"]) == []


def test_corrupt_content_blocks_release_without_journal_mutation(ws, managed_document):
    from revisionworx.workspace import WorkspaceError

    folder, source, assembly, before, doc, rev_a = managed_document
    D.transition(ws, rev_a["id"], "in_review", comment="review")
    D.transition(ws, rev_a["id"], "approved", comment="approved")
    before_seq = ws.head_seq()
    blob = ContentStore(ws).path_for(rev_a["sha256"])
    blob.write_bytes(b"damaged content object")
    with pytest.raises(WorkspaceError):
        D.transition(ws, rev_a["id"], "released", comment="attempt invalid release")
    assert ws.head_seq() == before_seq
    assert ws.get("revision", rev_a["id"])["state"] == "approved"
    assert ws.get("document", doc["id"])["released_rev_id"] is None
    assert [p["revision_id"] for p in D.verify_revisions(ws, doc["id"])] == [rev_a["id"]]


def test_drawing_register_csv_preserves_source_path_and_revision(ws, managed_document):
    folder, source, assembly, before, doc, rev_a = managed_document
    drawing = folder / "pump-skid.idw"
    drawing.write_bytes(b"IDW synthetic drawing revision A\n")
    draw_doc, draw_rev = D.register_document(
        ws, doc["project_id"], drawing, number="QA-DWG-002",
        title="Pump skid drawing")
    assert draw_rev is not None
    rows = D.drawing_register(ws, doc["project_id"])
    item = next(r for r in rows if r["number"] == "QA-DWG-002")
    assert item["source_file"] == "pump-skid.idw"
    assert item["revision"] == "A"
    assert item["title"] == "Pump skid drawing"
    csv_text = D.drawing_register_csv(rows)
    assert "QA-DWG-002" in csv_text
    assert "pump-skid.idw" in csv_text
    assert "Pump skid drawing" in csv_text
