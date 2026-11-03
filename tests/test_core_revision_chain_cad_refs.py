# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent immutable revision-chain and native CAD-reference regressions.

The managed CAD files are never rewritten by registration, checkout, check-in,
or read-only restart. The journal and content store retain earlier bytes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from revisionworx import documents as D, projects
from revisionworx.content import ContentStore
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_native_assembly_reference_survives_part_revisions_and_readonly_restart(ws):
    project = projects.create_project(
        ws, NewProject(name="CAD refs", number="CAD-101", customer="Internal")
    )
    folder = projects.project_folder(ws, project)
    part = folder / "02 CAD" / "Parts" / "bracket.ipt"
    assembly = folder / "02 CAD" / "Assemblies" / "frame.iam"
    original_part = b"native part revision A"
    updated_part = b"native part revision B"
    original_assembly = b"Native assembly reference: ../Parts/bracket.ipt\n"
    part.write_bytes(original_part)
    assembly.write_bytes(original_assembly)
    doc, first = D.register_document(ws, project["id"], part)
    assert assembly.read_bytes() == original_assembly
    assert part.read_bytes() == original_part
    checkout = D.checkout(ws, doc["id"])
    part.write_bytes(updated_part)
    second = D.checkin(ws, doc["id"], checkout["token"], "Update part geometry")
    assert first["sha256"] == _digest(original_part)
    assert second["sha256"] == _digest(updated_part)
    assert second["previous_rev_id"] == first["id"]
    assert assembly.read_bytes() == original_assembly
    assert part.read_bytes() == updated_part
    assert ContentStore(ws).path_for(first["sha256"]).read_bytes() == original_part
    assert ContentStore(ws).path_for(second["sha256"]).read_bytes() == updated_part
    workspace_root = ws.meta_dir.parent
    ws.close()
    with Workspace.open(workspace_root, writable=False) as reopened:
        persisted = D.history(reopened, doc["id"])
        assert [r["id"] for r in persisted] == [first["id"], second["id"]]
        assert reopened.get("document", doc["id"])["latest_rev_id"] == second["id"]
        assert reopened.get("revision", first["id"])["sha256"] == _digest(original_part)
        assert reopened.get("revision", second["id"])["previous_rev_id"] == first["id"]
        assert ContentStore(reopened).path_for(first["sha256"]).read_bytes() == original_part
        assert ContentStore(reopened).path_for(second["sha256"]).read_bytes() == updated_part
    assert assembly.read_bytes() == original_assembly
    assert part.read_bytes() == updated_part


def test_three_revision_chain_remains_linear_and_original_objects_immutable(ws):
    project = projects.create_project(
        ws, NewProject(name="Revision chain", number="CAD-102", customer="Internal")
    )
    folder = projects.project_folder(ws, project)
    part = folder / "02 CAD" / "Parts" / "shaft.ipt"
    payloads = [b"shaft-A", b"shaft-B", b"shaft-C"]
    part.write_bytes(payloads[0])
    doc, first = D.register_document(ws, project["id"], part)
    revisions = [first]
    for payload in payloads[1:]:
        co = D.checkout(ws, doc["id"])
        part.write_bytes(payload)
        revisions.append(D.checkin(ws, doc["id"], co["token"], "Update shaft"))
    assert [r["previous_rev_id"] for r in revisions[1:]] == [
        revisions[0]["id"], revisions[1]["id"]
    ]
    assert [r["sha256"] for r in revisions] == [_digest(p) for p in payloads]
    root = ws.meta_dir.parent
    ws.close()
    with Workspace.open(root, writable=False) as reopened:
        history = D.history(reopened, doc["id"])
        assert [r["id"] for r in history] == [r["id"] for r in revisions]
        assert reopened.get("document", doc["id"])["rev_count"] == 3
        for rev, payload in zip(revisions, payloads):
            assert reopened.get("revision", rev["id"])["sha256"] == _digest(payload)
            assert ContentStore(reopened).path_for(rev["sha256"]).read_bytes() == payload
    assert part.read_bytes() == payloads[-1]
