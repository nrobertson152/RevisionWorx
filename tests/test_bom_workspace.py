# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: BOM snapshots from real core document revisions - read-only, revision-aware, fail closed.

Uses only public core calls (CORE_API.md section 3 and ADR-018 read_revision_bytes) and checks that the
authoritative .revisionworx tree is byte-for-byte unchanged by every BOM operation."""
from decimal import Decimal as D

import pytest

from conftest import tree_snapshot
from revisionworx import documents as Docs, projects
from revisionworx.bom import BomError, BomSourceError, revision_impact, snapshot_from_json, snapshot_to_json
from revisionworx.bom.workspace_adapter import WorkspaceBomReader
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace, WorkspaceCorruptError

REV1 = "parent_number,child_number,quantity,find_number\nFRAME,SUB,2,1\nSUB,BOLT,3,1\n"
REV2 = "parent_number,child_number,quantity,find_number\nFRAME,SUB,2,1\nSUB,BOLT,4,1\nSUB,NUT,4,2\n"


@pytest.fixture
def bomdoc(ws, share):
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1"))
    folder = projects.project_folder(ws, rec)
    (folder / "BOM").mkdir(exist_ok=True)
    f = folder / "BOM" / "frame.csv"
    f.write_text(REV1, encoding="utf-8")
    (folder / "BOM" / "notes.txt").write_text("not a bom")
    doc, rev1 = Docs.register_document(ws, rec["id"], f)
    Docs.register_document(ws, rec["id"], folder / "BOM" / "notes.txt")
    co = Docs.checkout(ws, doc["id"])
    f.write_text(REV2, encoding="utf-8")
    rev2 = Docs.checkin(ws, doc["id"], co["token"], "more bolts, add nuts")
    ws.close()
    return share, rec, doc, rev1, rev2, f


def test_revisions_compare_read_only(bomdoc):
    share, rec, doc, rev1, rev2, _ = bomdoc
    before = tree_snapshot(share / ".revisionworx")
    with Workspace.open(share) as ro:  # read-only handle is enough
        reader = WorkspaceBomReader(ro)
        assert [d["id"] for d in reader.bom_documents(rec["id"])] == [doc["id"]]
        revs = reader.revisions(doc["id"])
        assert [(r.number, r.label) for r in revs] == [(1, rev1["label"]), (2, rev2["label"])]
        a, b, diff = reader.compare_revisions(rev1["id"], rev2["id"])
        latest = reader.latest_snapshot(doc["id"])
    assert diff.summary() == {"added": 1, "removed": 0, "changed": 1, "parts_changed": 1}
    assert a.source.revision_id == rev1["id"] and a.source.sha256 == rev1["sha256"]
    assert b.source.kind == "workspace-revision" and b.source.revision_number == 2 and b.source.path == "BOM/frame.csv"
    assert latest == b and a.root_id == "pn:frame"
    assert snapshot_from_json(snapshot_to_json(b)) == b
    assert tree_snapshot(share / ".revisionworx") == before


def test_old_revision_is_read_from_immutable_content_not_the_working_file(bomdoc):
    share, _, doc, rev1, _, working = bomdoc
    working.write_text("garbage,that,is,not,a,bom\n")  # mutable working file changes; revisions do not
    with Workspace.open(share) as ro:
        snap = WorkspaceBomReader(ro).snapshot(rev1["id"])
    assert snap.structure.children("pn:sub")[0].quantity == D(3)


def test_impact_across_revisions_uses_snapshot_sources(bomdoc):
    share, _, doc, rev1, rev2, _ = bomdoc
    with Workspace.open(share) as ro:
        reader = WorkspaceBomReader(ro)
        snaps = [reader.snapshot(rev1["id"]), reader.snapshot(rev2["id"])]
    rep = revision_impact(snaps, "pn:bolt")
    assert {c.revision_id for c in rep.coverage} == {rev1["id"], rev2["id"]}
    assert {e.quantity_per_top_level for e in rep.entries if e.assembly_id == "pn:sub"} == {D(6), D(8)}


def test_damaged_content_fails_closed(bomdoc):
    share, _, doc, rev1, _, _ = bomdoc
    obj = share / ".revisionworx" / "content" / "sha256" / rev1["sha256"][:2] / rev1["sha256"]
    obj.chmod(0o644)
    obj.write_bytes(obj.read_bytes().replace(b"3", b"9"))
    with Workspace.open(share) as ro:
        with pytest.raises(WorkspaceCorruptError):
            WorkspaceBomReader(ro).snapshot(rev1["id"])


def test_oversized_revision_is_refused(bomdoc):
    share, _, _, rev1, _, _ = bomdoc
    with Workspace.open(share) as ro:
        with pytest.raises(BomSourceError, match="too large"):
            WorkspaceBomReader(ro, max_bytes=10).snapshot(rev1["id"])


def test_layouts_and_root_selection(ws, share):
    rec = projects.create_project(ws, NewProject(name="P", number="J-2"))
    folder = projects.project_folder(ws, rec)
    (folder / "two.csv").write_text("parent_number,child_number,quantity\nA,X,1\nB,X,2\n")
    (folder / "ind.csv").write_text("Level,Part Number,Qty\n0,TOP,1\n1,LEAF,5\n")
    _, two = Docs.register_document(ws, rec["id"], folder / "two.csv")
    _, ind = Docs.register_document(ws, rec["id"], folder / "ind.csv")
    reader = WorkspaceBomReader(ws)
    with pytest.raises(BomError, match="2 top-level items"):
        reader.snapshot(two["id"])
    assert reader.snapshot(two["id"], root_number="b").root_id == "pn:b"
    snap = reader.snapshot(ind["id"])
    assert snap.root_id == "pn:top" and snap.structure.children("pn:top")[0].quantity == D(5)
    with pytest.raises(BomError, match="unknown BOM layout"):
        reader.snapshot(ind["id"], layout="xml")
