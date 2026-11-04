# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18 / backlog #28 item 5: native file reference preservation and document links.

Every catalog, BOM, report and adapter operation leaves the project folder - registered Inventor
assembly/part/drawing files and BOM CSVs included - byte- and mtime-identical (conftest.tree_snapshot).
Parts reference native files by core document id only; links are verified read-only and a stale pin is
reported, never rewritten."""
import pytest

from conftest import tree_snapshot
from revisionworx import documents as Docs, projects
from revisionworx.bom import (DocumentLink, PartsCatalogService, WorkspaceCatalogStore, change_impact, explode,
                              revision_impact, rollup, save, structure_to_csv, validate)
from revisionworx.bom.integrations import ChangeControlWhereUsed
from revisionworx.bom.workspace_adapter import WorkspaceBomReader, verify_document_links
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace

ASM = b"IAM assembly; refs: ..\\Parts\\bracket.ipt ; ..\\Parts\\bolt.ipt"


@pytest.fixture
def cad(ws, share):
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-7"))
    folder = projects.project_folder(ws, rec)
    files = {"02 CAD/Assemblies/frame.iam": ASM, "02 CAD/Parts/bracket.ipt": b"IPT bracket",
             "02 CAD/Parts/bolt.ipt": b"IPT bolt", "03 Drawings/Native/frame.idw": b"IDW frame",
             "BOM/frame.csv": b"parent_number,child_number,quantity\nFRAME,BRACKET,2\nFRAME,BOLT,8\n"}
    docs = {}
    for rel, data in files.items():
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        docs[rel], _ = Docs.register_document(ws, rec["id"], p)
    return rec, folder, docs


def test_catalog_and_bom_operations_never_touch_project_files(ws, share, cad, tmp_path):
    rec, folder, docs = cad
    before = tree_snapshot(folder)
    svc = PartsCatalogService(WorkspaceCatalogStore(ws))
    frame = svc.create_part("FRAME", kind="assembly",
                            documents=[DocumentLink(docs["02 CAD/Assemblies/frame.iam"]["id"], role="cad-model"),
                                       DocumentLink(docs["03 Drawings/Native/frame.idw"]["id"])])
    bracket = svc.create_part("BRACKET", documents=[DocumentLink(docs["02 CAD/Parts/bracket.ipt"]["id"],
                                                                 role="cad-model")])
    bolt = svc.create_part("BOLT")
    svc.add_line(frame.part_id, bracket.part_id, 2)
    line = svc.add_line(frame.part_id, bolt.part_id, 8)
    svc.update_line(line.line_id, svc.line_rev(line.line_id), quantity=10)
    reader = WorkspaceBomReader(ws)
    bom_rev = Docs.history(ws, docs["BOM/frame.csv"]["id"])[0]
    file_snap = reader.snapshot(bom_rev["id"])
    cat_snap = svc.snapshot(frame.part_id)
    explode(cat_snap.structure, frame.part_id)
    rollup(cat_snap.structure, frame.part_id)
    validate(svc.structure(), catalog=svc.catalog())
    revision_impact([cat_snap, file_snap], bolt.part_id)
    change_impact(cat_snap, cat_snap, [cat_snap])
    ChangeControlWhereUsed(svc.structure).where_used(type("I", (), {"item_type": "part", "item_id": bolt.part_id,
                                                                     "label": ""})())
    verify_document_links(ws, svc.parts())
    save(structure_to_csv(svc.structure()), tmp_path / "export.csv")
    svc.import_structure(file_snap.structure, replace_existing=True)
    assert tree_snapshot(folder) == before


def test_document_links_are_verified_read_only(ws, share, cad):
    rec, folder, docs = cad
    model = docs["02 CAD/Parts/bracket.ipt"]
    rev1 = Docs.history(ws, model["id"])[0]
    co = Docs.checkout(ws, model["id"])
    (folder / "02 CAD/Parts/bracket.ipt").write_bytes(b"IPT bracket v2")
    rev2 = Docs.checkin(ws, model["id"], co["token"], "thicker")
    svc = PartsCatalogService(WorkspaceCatalogStore(ws))
    svc.create_part("PINNED", documents=[DocumentLink(model["id"], rev1["id"], "cad-model")])
    svc.create_part("CURRENT", documents=[DocumentLink(model["id"], rev2["id"], "cad-model")])
    svc.create_part("FLOATING", documents=[DocumentLink(model["id"])])
    svc.create_part("GONE", documents=[DocumentLink("doc_" + "0" * 32)])
    svc.create_part("BADREV", documents=[DocumentLink(model["id"], "rev_" + "0" * 32)])
    ws.close()
    meta_before = tree_snapshot(share / ".revisionworx")
    with Workspace.open(share) as ro:
        got = {s.part_number: (s.status, s.linked_label, s.latest_label) for s in
               verify_document_links(ro, PartsCatalogService(WorkspaceCatalogStore(ro)).parts())}
    assert got == {"PINNED": ("newer-revision-available", rev1["label"], rev2["label"]),
                   "CURRENT": ("ok", rev2["label"], rev2["label"]),
                   "FLOATING": ("ok", "(latest)", rev2["label"]),
                   "GONE": ("missing-document", "", ""),
                   "BADREV": ("missing-revision", "", rev2["label"])}
    assert tree_snapshot(share / ".revisionworx") == meta_before


def test_exports_refuse_the_metadata_folder(ws, share):
    from revisionworx.bom import ExportError, Structure
    with pytest.raises(ExportError, match="metadata folder"):
        save(structure_to_csv(Structure()), share / ".revisionworx" / "records" / "x.csv")
