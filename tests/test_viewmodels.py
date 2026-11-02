# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless tests of GUI behaviour (toolkit-free view-models)."""
import time

import pytest

from conftest import tree_snapshot
from revisionworx.gui import controller as ctl_mod
from revisionworx.gui.controller import Controller, JobResult
from revisionworx.gui.viewmodels import (AdoptFlow, NewProjectForm, ProjectDetailsModel,
                                         ProjectListModel, format_bytes, preview_report)
from revisionworx.projects import NewProject


@pytest.fixture
def ctl(tmp_path):
    c = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    share = tmp_path / "share"
    (share / "Eng").mkdir(parents=True)
    c.create_workspace(share, "VM test")
    c.add_root("Eng", share / "Eng")
    c.share = share
    return c


def pump(c: Controller, until, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        c.drain()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_format_bytes():
    assert format_bytes(5) == "5 B"
    assert format_bytes(2048) == "2.0 KiB"
    assert format_bytes(5 * 1024 ** 3) == "5.0 GiB"


def test_list_model_without_workspace(tmp_path):
    vm = ProjectListModel(Controller(cfg_dir=tmp_path / "c", cache_dir=tmp_path / "x"))
    vm.refresh()
    assert vm.rows == [] and not vm.can_next and "No workspace" in vm.status_text


def test_list_model_paging_search_and_notifications(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod, "PAGE_SIZE", 2)
    for i in range(5):
        ctl.create_project(NewProject(name=f"Hydraulic {i}", number=f"H-{i}", rel_path=f"h/{i}"))
    ctl.create_project(NewProject(name="Pneumatic", rel_path="p"))
    vm = ProjectListModel(ctl)
    calls = []
    vm.subscribe(lambda: calls.append(1))
    vm.refresh()
    assert len(vm.rows) == 2 and vm.can_next and not vm.can_prev
    assert [c.title for c in vm.COLUMNS][:2] == ["Number", "Name"]
    assert vm.cell(0, 1) == "Hydraulic 0"
    vm.next_page(); vm.next_page()
    assert vm.page == 2 and len(vm.rows) == 2 and not vm.can_next
    vm.next_page()  # no-op at the end
    assert vm.page == 2
    vm.set_query("pneum")
    assert vm.page == 0 and [r["name"] for r in vm.rows] == ["Pneumatic"]
    assert "unmanaged folders are not searched" in vm.status_text
    assert len(calls) >= 4


def test_details_model_archive_toggle_uses_revision(ctl):
    rec = ctl.create_project(NewProject(name="P"))
    vm = ProjectDetailsModel(ctl)
    vm.load(rec["id"])
    assert dict(vm.fields)["Name"] == "P" and vm.can_open_folder and vm.archive_label == "Archive"
    stale = dict(vm.project)
    vm.toggle_archive()
    assert vm.project["status"] == "archived" and vm.archive_label == "Restore"
    vm.project = stale  # simulate a view that was not refreshed after someone else's change
    from revisionworx.workspace import ConflictError
    with pytest.raises(ConflictError):
        vm.toggle_archive()


def test_details_model_empty_selection(ctl):
    vm = ProjectDetailsModel(ctl)
    vm.load(None)
    assert vm.fields == [] and vm.files == [] and not vm.can_open_folder


def test_new_project_form_validation_and_submit(ctl):
    form = NewProjectForm(roots=ctl.roots(), templates=ctl.templates())
    assert not form.validate() and "name" in form.errors
    form.name, form.rel_path = "Skid", "../escape"
    assert not form.validate() and "rel_path" in form.errors
    form.rel_path, form.template_id = "", "nope"
    assert not form.validate() and "template_id" in form.errors
    form.template_id = "standard"
    rec = form.submit(ctl)
    assert rec["name"] == "Skid"
    assert NewProjectForm(roots=[], templates={"standard": {}}, name="x").validate() is False


def test_adopt_flow_state_machine(ctl):
    legacy = ctl.share / "Eng" / "Legacy"
    (legacy / "cad").mkdir(parents=True)
    for i in range(30):
        (legacy / "cad" / f"p{i}.sldprt").write_bytes(b"x" * i)
    before = tree_snapshot(legacy)
    flow = AdoptFlow(ctl)
    assert not flow.can_preview and not flow.can_adopt
    flow.set_folder(str(legacy))
    assert flow.name == "Legacy" and flow.can_preview and not flow.can_adopt
    assert flow.preview()
    assert flow.busy and not flow.can_preview
    pump(ctl, lambda: flow.state == "previewed")
    assert flow.can_adopt and "Nothing has been changed" in flow.report_text
    assert "cad-part: 30" in flow.report_text
    flow.name = "  "
    assert not flow.can_adopt  # name required
    flow.name = "Legacy"
    assert flow.adopt()
    pump(ctl, lambda: flow.state == "adopted")
    assert flow.result["baseline"]["files"] == 30
    assert tree_snapshot(legacy) == before


def test_adopt_flow_blocks_conflicts_and_reports_errors(ctl):
    rec = ctl.create_project(NewProject(name="Outer", rel_path="Outer"))
    flow = AdoptFlow(ctl)
    flow.set_folder(str(ctl.share / "Eng" / "Outer" / "02 CAD"))
    flow.preview()
    pump(ctl, lambda: flow.state == "previewed")
    assert flow.summary["conflicts"][0]["id"] == rec["id"]
    assert not flow.can_adopt and "Overlaps registered project" in flow.report_text
    bad = AdoptFlow(ctl)
    bad.set_folder(str(ctl.share / "Eng" / "missing"))
    bad.preview()
    pump(ctl, lambda: bad.state == "error")
    assert "Preview failed" in bad.progress_text


def test_preview_report_includes_every_warning():
    s = {"scope": "/x", "rel_path": "x", "files": 1, "directories": 1, "bytes": 3, "by_category": {"other": 1},
         "conflicts": [], "warnings": ["W1", "W2"], "cad_references": "NOT extracted"}
    text = preview_report(s)
    assert "⚠ W1" in text and "⚠ W2" in text and "NOT extracted" in text


def test_progress_only_shown_while_busy(ctl):
    from revisionworx.gui.controller import Progress
    flow = AdoptFlow(ctl)
    flow.on_progress(Progress("preview", 10, "a"))
    assert flow.progress_text == ""
    flow.state = "previewing"
    flow.on_progress(Progress("preview", 1200, "b"))
    assert flow.progress_text.startswith("1,200 files")


def test_gui_entry_point_without_pyside6_explains(monkeypatch, capsys):
    import builtins
    import sys
    real_import = builtins.__import__

    def fake(name, *a, **kw):
        if name.startswith("PySide6"):
            raise ImportError("no PySide6")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.delitem(sys.modules, "PySide6", raising=False)
    monkeypatch.delitem(sys.modules, "PySide6.QtWidgets", raising=False)
    from revisionworx.gui import app
    assert app.main([]) == 3
    assert "pip install" in capsys.readouterr().err


def test_job_result_callbacks_run_on_drain_thread(ctl):
    seen = []
    ctl.results.put(JobResult("x", value=1, on_done=lambda r: seen.append(r.value)))
    ctl.drain()
    assert seen == [1]
