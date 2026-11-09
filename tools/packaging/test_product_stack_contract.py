# SPDX-License-Identifier: GPL-3.0-or-later
"""True four-source cross-domain acceptance, on a disposable local workspace.

Test-only fixture. BOM record persistence is still provisional pending core
API review; this does not authorize a production merge or networked writers.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from revisionworx import changes, documents as Docs, projects
from revisionworx.bom import (
    ChangeControlWhereUsed, DocumentLink, PartsCatalogService, WorkspaceCatalogStore,
)
from revisionworx.changes.impact import impact_summary
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace


def snapshot(files: list[Path]) -> dict[str, tuple[int, int, str]]:
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime_ns,
                 hashlib.sha256(p.read_bytes()).hexdigest())
        for p in files
    }


def test_part_bom_change_impact_with_native_cad_paths_survives_restart(tmp_path):
    """Document -> part -> BOM where-used -> ECR; all journaled without CAD edits."""
    share = tmp_path / "share"
    engineering = share / "Engineering"
    engineering.mkdir(parents=True)

    with Workspace.create(share, "Four-source integration test") as ws:
        projects.add_root(ws, "Engineering", engineering)
        project = projects.create_project(
            ws, NewProject(name="Hydraulic test skid", number="INTEGRATION-1")
        )
        folder = projects.project_folder(ws, project)
        assembly = folder / "02 CAD" / "Assemblies" / "frame.iam"
        component = folder / "02 CAD" / "Parts" / "bracket.ipt"
        assembly.parent.mkdir(parents=True, exist_ok=True)
        component.parent.mkdir(parents=True, exist_ok=True)
        # A literal relative reference in native CAD data must remain unchanged.
        assembly.write_bytes(b"Inventor assembly: ..\\Parts\\bracket.ipt")
        component.write_bytes(b"Inventor bracket original bytes")
        files = [assembly, component]
        original = snapshot(files)

        doc, revision = Docs.register_document(ws, project["id"], component)
        cat = PartsCatalogService(WorkspaceCatalogStore(ws))
        bracket = cat.create_part(
            "BRACKET-100",
            documents=[DocumentLink(doc["id"], revision["id"], "cad-model")],
        )
        frame = cat.create_part("FRAME-100", kind="assembly")
        cat.add_line(frame.part_id, bracket.part_id, "2")

        change_svc = changes.service_for(ws)
        request = change_svc.create_request(
            "Strengthen mounting bracket",
            "Fatigue concern discovered during testing",
            affected=[{"item_type": "part", "item_id": bracket.part_id,
                       "label": "BRACKET-100"}],
        )
        before_seq = ws.head_seq()
        report = impact_summary(
            change_svc, request.id,
            ChangeControlWhereUsed(cat.structure),
        )

        assert report["change"]["id"] == request.id
        assert report["counts"]["items"] == 1
        item = report["items"][0]
        assert item["type"] == "part"
        assert item["verification"] == "declared", (
            "a BOM part is not core-verified without an approved resolver"
        )
        assert any(edge["id"] == frame.part_id and edge["relationship"] == "direct"
                   for edge in item["where_used"]), report
        assert "NOT searched" in item["where_used_coverage"]
        assert ws.head_seq() == before_seq, "impact report performed a write"
        assert ws.verify()["problems"] == []
        assert snapshot(files) == original, "native CAD bytes or mtime changed"

    with Workspace.open(share) as ro:
        reloaded_cat = PartsCatalogService(WorkspaceCatalogStore(ro))
        reloaded_changes = changes.service_for(ro)
        assert reloaded_cat.find_by_number("bracket-100").part_id == bracket.part_id
        reopened_report = impact_summary(
            reloaded_changes, request.id,
            ChangeControlWhereUsed(reloaded_cat.structure),
        )
        assert reopened_report["items"][0]["where_used"] == item["where_used"]
        assert ro.verify()["problems"] == []
        assert snapshot(files) == original, "read-only restart mutated CAD sources"
