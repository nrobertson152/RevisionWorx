# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only and Unicode-path CAD provenance regressions.

These fixtures preserve native file bytes and path spellings; they do not
claim that an Inventor/SolidWorks dependency resolver has parsed the files.
"""
from __future__ import annotations

import hashlib

from revisionworx import documents as D, projects
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_nested_unicode_cad_paths_keep_original_spelling_and_bytes(ws):
    project = projects.create_project(
        ws, NewProject(name="Unicode CAD", number="CAD-UNICODE", customer="Internal")
    )
    folder = projects.project_folder(ws, project)
    part = folder / "02 CAD" / "Pièces 日本語" / "Équerre_轴.ipt"
    assembly = folder / "02 CAD" / "Assemblies" / "外壳_Étage.iam"
    part.parent.mkdir(parents=True, exist_ok=True)
    assembly.parent.mkdir(parents=True, exist_ok=True)
    original = b"native part A\x00\xff"
    updated = b"native part B\x00\xfe"
    reference = "../Pièces 日本語/Équerre_轴.ipt".encode("utf-8")
    assembly_bytes = b"native-assembly-reference:\x00" + reference
    part.write_bytes(original)
    assembly.write_bytes(assembly_bytes)

    doc, first = D.register_document(ws, project["id"], part)
    checkout = D.checkout(ws, doc["id"])
    part.write_bytes(updated)
    second = D.checkin(ws, doc["id"], checkout["token"], "Unicode path revision")
    assert first["sha256"] == _sha(original)
    assert second["sha256"] == _sha(updated)
    assert second["previous_rev_id"] == first["id"]
    assert assembly.read_bytes() == assembly_bytes
    assert part.read_bytes() == updated

    root = ws.meta_dir.parent
    ws.close()
    with Workspace.open(root, writable=False) as reopened:
        assert reopened.get("document", doc["id"])["latest_rev_id"] == second["id"]
        assert reopened.get("revision", first["id"])["sha256"] == _sha(original)
        assert reopened.get("revision", second["id"])["sha256"] == _sha(updated)
    assert assembly.read_bytes() == assembly_bytes
    assert part.read_bytes() == updated
    assert part.name == "Équerre_轴.ipt"
    assert assembly.name == "外壳_Étage.iam"


def test_unmanaged_nested_cad_siblings_remain_untouched_by_revision_and_restart(ws):
    project = projects.create_project(
        ws, NewProject(name="Unmanaged siblings", number="CAD-SIB", customer="Internal")
    )
    folder = projects.project_folder(ws, project)
    managed = folder / "02 CAD" / "Parts" / "managed.ipt"
    unmanaged = folder / "02 CAD" / "Vendor" / "external.sldprt"
    nested = folder / "02 CAD" / "Vendor" / "Subassemblies" / "vendor.sldasm"
    unmanaged.parent.mkdir(parents=True, exist_ok=True)
    nested.parent.mkdir(parents=True, exist_ok=True)
    vendor_bytes = b"third-party native part\x00\xfe"
    assembly_bytes = b"../external.sldprt\x00vendor reference"
    unmanaged.write_bytes(vendor_bytes)
    nested.write_bytes(assembly_bytes)
    managed.write_bytes(b"managed revision 1")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (unmanaged, nested)}

    doc, _ = D.register_document(ws, project["id"], managed)
    checkout = D.checkout(ws, doc["id"])
    managed.write_bytes(b"managed revision 2")
    D.checkin(ws, doc["id"], checkout["token"], "Change managed only")
    root = ws.meta_dir.parent
    ws.close()
    with Workspace.open(root, writable=False) as reopened:
        assert len(D.history(reopened, doc["id"])) == 2
    for path, (data, mtime_ns) in before.items():
        assert path.read_bytes() == data
        assert path.stat().st_mtime_ns == mtime_ns
