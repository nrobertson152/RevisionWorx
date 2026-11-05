# SPDX-License-Identifier: GPL-3.0-or-later
import os
import shutil
import zipfile

import pytest

from conftest import FIXTURES, tree_snapshot
from revisionworx import filetypes, projects
from revisionworx.projects import NewProject, ProjectError
from revisionworx.workspace import Workspace


# ---------------------------------------------------------------- roots and templates
def test_root_must_exist_and_be_unique(ws, share, tmp_path):
    with pytest.raises(ProjectError):
        projects.add_root(ws, "Missing", tmp_path / "nope")
    with pytest.raises(ProjectError):
        projects.add_root(ws, "Again", share / "Engineering")
    (share / "Other").mkdir()
    with pytest.raises(ProjectError):
        projects.add_root(ws, "engineering", share / "Other")  # name collision, case-insensitive


def test_folder_outside_roots_is_refused(ws, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ProjectError):
        projects.preview_adoption(ws, elsewhere)


@pytest.mark.parametrize("bad", [
    {"id": "x", "name": "x", "folders": ["../escape"]},
    {"id": "x", "name": "x", "folders": ["/abs"]},
    {"id": "x", "name": "x", "folders": ["Docs", "docs"]},
    {"id": "Bad Id", "name": "x", "folders": []},
    {"id": "x", "name": "x", "folders": [], "files": [{"path": "a/../../b", "content": ""}]},
])
def test_unsafe_templates_rejected(ws, bad):
    with pytest.raises(Exception):
        projects.save_template(ws, bad)


def test_create_project_from_custom_template_with_starter_file(ws, share):
    projects.save_template(ws, {"id": "hyd", "name": "Hydraulic power unit", "folders": [
        "Hydraulics/Schematics", "Mechanical/Models"],
        "files": [{"path": "README.txt", "content": "Project notes\n"}]})
    rec = projects.create_project(ws, NewProject(name="HPU 40kW", number="J-1", customer="Liebherr",
                                                 template_id="hyd"))
    folder = projects.project_folder(ws, rec)
    assert folder == share / "Engineering" / "Liebherr" / "J-1 HPU 40kW"
    assert (folder / "Hydraulics" / "Schematics").is_dir()
    assert (folder / "README.txt").read_text() == "Project notes\n"
    assert rec["mode"] == "managed" and rec["origin"] == "created"
    assert ws.get("project", rec["id"])["name"] == "HPU 40kW"


def test_create_never_merges_into_existing_folder(ws, share):
    existing = share / "Engineering" / "Acme" / "J-2 Thing"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("old")
    before = tree_snapshot(share / "Engineering")
    with pytest.raises(ProjectError):
        projects.create_project(ws, NewProject(name="Thing", number="J-2", customer="Acme"))
    assert tree_snapshot(share / "Engineering") == before
    assert ws.list("project") == {}


def test_duplicate_project_number_detected(ws):
    projects.create_project(ws, NewProject(name="One", number="J-10", customer="A"))
    with pytest.raises(ProjectError):
        projects.create_project(ws, NewProject(name="Two", number="j-10", customer="B"))
    rec = projects.create_project(ws, NewProject(name="Two", number="j-10", customer="B"),
                                  allow_duplicate_number=True)
    assert rec["number"] == "j-10"


def test_overlapping_registration_refused(ws, share):
    rec = projects.create_project(ws, NewProject(name="Outer", customer="C", rel_path="C/Outer"))
    inner = projects.project_folder(ws, rec) / "02 CAD"
    with pytest.raises(ProjectError):
        projects.adopt_project(ws, inner, name="Inner")
    with pytest.raises(ProjectError):
        projects.adopt_project(ws, share / "Engineering" / "C", name="Parent")


def test_archive_and_mode_change_are_journaled(ws):
    rec = projects.create_project(ws, NewProject(name="P", number="N1"))
    head = ws.head_seq()
    projects.update_project(ws, rec["id"], status="archived")
    assert ws.get("project", rec["id"])["status"] == "archived"
    assert ws.head_seq() == head + 1
    with pytest.raises(ProjectError):
        projects.update_project(ws, rec["id"], rel_path="elsewhere")
    with pytest.raises(ProjectError):
        projects.update_project(ws, rec["id"], status="deleted")


def test_root_without_path_for_this_os_is_explained(ws, share):
    root = ws.get("root", ws.root_id)
    root["paths"] = {"other-os": "X:/eng"}
    from revisionworx.workspace import RecordPut
    ws.commit("t", [RecordPut("root", ws.root_id, root)])
    with pytest.raises(ProjectError, match="no path configured"):
        projects.create_project(ws, NewProject(name="P"))


# ---------------------------------------------------------------- adoption
def _company_a(root):  # customer / year / job
    for job in ("Acme/2018/J-1001 Press", "Acme/2019/J-1100 Lift", "Beta Corp/2020/J-1200 Skid"):
        d = root / job
        (d / "CAD").mkdir(parents=True)
        (d / "CAD" / "frame.iam").write_bytes(b"IAM" * 100)
        (d / "CAD" / "plate.ipt").write_bytes(b"IPT" * 50)
        (d / "Drawings").mkdir()
        (d / "Drawings" / "plate.idw").write_bytes(b"IDW")
        (d / "Drawings" / "plate.pdf").write_bytes(b"%PDF-1.4")
    return root / "Acme/2019/J-1100 Lift"


def _company_b(root):  # year / project number only, SolidWorks
    d = root / "2021" / "21-044"
    d.mkdir(parents=True)
    (d / "pump.SLDASM").write_bytes(b"asm")
    (d / "impeller.sldprt").write_bytes(b"prt")
    (d / "impeller.SLDDRW").write_bytes(b"drw")
    (d / "~$impeller.SLDDRW").write_bytes(b"lockfile")
    (d / "notes.docx").write_bytes(b"docx")
    return d


def _company_c(root):  # department / product family / deep nesting, schematics
    d = root / "Fluid Power" / "Cylinders" / "CY-80" / "Rev history" / "current"
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "openschematix" / "start_stop_latch.osch", d / "control.osch")
    shutil.copy(FIXTURES / "openschematix" / "demo_project.schematix", d / "circuit.schematix")
    (d / "fake.schematix").write_bytes(b"not a zip")
    (d / "step.STEP").write_bytes(b"ISO-10303-21;")
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref")
    return root / "Fluid Power" / "Cylinders" / "CY-80"


@pytest.mark.parametrize("builder", [_company_a, _company_b, _company_c])
@pytest.mark.parametrize("mode", ["indexed", "managed"])
def test_adoption_is_non_destructive_across_conventions(ws, share, builder, mode):
    root = share / "Engineering"
    scope = builder(root)
    before_all = tree_snapshot(root)
    preview_head = ws.head_seq()
    preview = projects.preview_adoption(ws, scope)
    assert ws.head_seq() == preview_head  # preview writes nothing
    assert tree_snapshot(root) == before_all
    rec = projects.adopt_project(ws, scope, name=scope.name, mode=mode)
    assert tree_snapshot(root) == before_all  # nothing created, renamed, touched or deleted
    assert rec["origin"] == "adopted" and rec["mode"] == mode
    assert rec["baseline"]["files"] == preview["files"]
    baseline = ws.read_blob(rec["baseline"]["blob"])
    assert "No historical revisions" in baseline["note"]
    assert all(len(f["sha256"]) == 64 for f in baseline["files"])
    assert projects.project_folder(ws, rec) == scope


def test_adoption_preview_details(ws, share):
    scope = _company_c(share / "Engineering")
    preview = projects.preview_adoption(ws, scope)
    assert preview["rel_path"] == "Fluid Power/Cylinders/CY-80"
    assert preview["by_category"] == {"cad-exchange": 1, "schematic": 3}
    assert any(".git" in e for e in [str(preview["excluded"])]) or preview["excluded"] >= 1
    assert "NOT extracted" in preview["cad_references"]


def test_openschematix_content_verification_uses_real_files(ws, share):
    scope = _company_c(share / "Engineering")
    rec = projects.adopt_project(ws, scope, name="CY-80")
    files = {f["path"].rsplit("/", 1)[-1]: f for f in ws.read_blob(rec["baseline"]["blob"])["files"]}
    assert files["control.osch"].get("verified_format") == "openschematix"
    assert files["circuit.schematix"].get("verified_format") == "openschematix"
    assert "verified_format" not in files["fake.schematix"]  # extension alone is not proof


def test_fluidworx_content_verification_uses_a_real_file(ws, share):
    d = share / "Engineering" / "sim"
    d.mkdir(parents=True)
    shutil.copy(FIXTURES / "fluidworx" / "sample.fluidworx", d / "real.fluidworx")
    (d / "fake.fluidworx").write_text("not a study", encoding="utf-8")
    with zipfile.ZipFile(d / "other.fluidworx", "w") as z:      # a ZIP, but somebody else's
        z.writestr("mimetype", "application/x-openschematix-project")
    rec = projects.adopt_project(ws, d, name="sim")
    files = {f["path"].rsplit("/", 1)[-1]: f for f in ws.read_blob(rec["baseline"]["blob"])["files"]}
    assert files["real.fluidworx"].get("verified_format") == "fluidworx"
    assert "verified_format" not in files["fake.fluidworx"]     # extension alone is not proof
    assert "verified_format" not in files["other.fluidworx"]


def test_fluidworx_file_type():
    assert filetypes.classify("A.FLUIDWORX").category == "simulation"
    assert filetypes.classify("a.fluidworx").label == "FluidWorx simulation study"
    assert not filetypes.sniff_fluidworx(FIXTURES / "openschematix" / "demo_project.schematix")


def test_filetype_classification_is_case_insensitive_and_honest():
    assert filetypes.classify("A.SLDPRT").category == "cad-part"
    assert filetypes.classify("a.ipt").capability == "recognized-only"
    assert filetypes.classify("x.weird").category == "other"


def test_adoption_respects_exclusions(ws, share):
    scope = _company_b(share / "Engineering")
    rec = projects.adopt_project(ws, scope, name="21-044")
    names = [f["path"] for f in ws.read_blob(rec["baseline"]["blob"])["files"]]
    assert "~$impeller.SLDDRW" not in names
    assert "pump.SLDASM" in names


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_adoption_does_not_follow_symlinks(ws, share, tmp_path):
    scope = share / "Engineering" / "Proj"
    scope.mkdir()
    (scope / "a.ipt").write_bytes(b"a")
    outside = tmp_path / "huge_archive"
    outside.mkdir()
    for i in range(50):
        (outside / f"f{i}.ipt").write_bytes(b"x")
    try:
        os.symlink(outside, scope / "link_to_archive")
    except OSError:
        pytest.skip("cannot create symlinks here")
    preview = projects.preview_adoption(ws, scope)
    assert preview["files"] == 1
    assert preview["links_skipped"] == ["link_to_archive"]
    assert any("not followed" in w for w in preview["warnings"])


def test_adoption_over_limit_registers_nothing(ws, share):
    from revisionworx.workspace import RecordPut
    scope = _company_a(share / "Engineering")
    settings = ws.settings()
    settings["adoption_max_files"] = 2
    ws.commit("settings", [RecordPut("settings", "workspace", settings)])
    head = ws.head_seq()
    preview = projects.preview_adoption(ws, scope)
    assert preview["truncated"] and not preview["complete"]
    with pytest.raises(ProjectError):
        projects.adopt_project(ws, scope, name="too big")
    assert ws.head_seq() == head and ws.list("project") == {}


def test_adoption_cancellation(ws, share):
    from revisionworx.scan import ScanCancelled
    scope = _company_a(share / "Engineering")
    with pytest.raises(ScanCancelled):
        projects.adopt_project(ws, scope, name="x", cancelled=lambda: True)
    assert ws.list("project") == {}


def test_case_collisions_reported(ws, share):
    scope = share / "Engineering" / "Mixed"
    scope.mkdir()
    (scope / "Part.ipt").write_bytes(b"1")
    try:
        (scope / "part.IPT").write_bytes(b"2")
    except OSError:
        pytest.skip("case-insensitive filesystem")
    if len(list(scope.iterdir())) < 2:
        pytest.skip("case-insensitive filesystem")
    preview = projects.preview_adoption(ws, scope)
    assert preview["case_collisions"] == [["Part.ipt", "part.IPT"]]


def test_registry_visible_to_second_client(ws, share):
    rec = projects.create_project(ws, NewProject(name="Shared", number="S-1"))
    with Workspace.open(share) as other:
        assert other.get("project", rec["id"])["number"] == "S-1"
