# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import time
from pathlib import Path

import pytest

from conftest import tree_snapshot
from revisionworx.gui import controller as ctl_mod
from revisionworx.gui.controller import Controller, JobResult, describe_error
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace, WorkspaceLockedError


@pytest.fixture
def ctl(tmp_path):
    c = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    share = tmp_path / "share"
    (share / "Eng").mkdir(parents=True)
    c.create_workspace(share, "GUI test")
    c.add_root("Eng", share / "Eng")
    c.share = share
    return c


def wait_for(c: Controller, name: str, timeout=30) -> JobResult:
    end = time.time() + timeout
    while time.time() < end:
        for ev in c.drain():
            if isinstance(ev, JobResult) and ev.name == name:
                return ev
        time.sleep(0.02)
    raise AssertionError(f"job {name} did not finish")


def test_recent_workspaces_remembered(ctl):
    assert ctl.recent_workspaces() == [str(ctl.share)]


def test_writer_lock_is_not_held_between_actions(ctl):
    ctl.create_project(NewProject(name="A"))
    assert Workspace.read_writer_lock(ctl.share) is None
    Workspace.open(ctl.share, writable=True).close()  # another user can write meanwhile


def test_project_paging(ctl, monkeypatch):
    monkeypatch.setattr(ctl_mod, "PAGE_SIZE", 3)
    for i in range(7):
        ctl.create_project(NewProject(name=f"P{i}", rel_path=f"x/{i}"))
    p0, p2 = ctl.project_page(page=0), ctl.project_page(page=2)
    assert len(p0["rows"]) == 3 and p0["has_more"]
    assert len(p2["rows"]) == 1 and not p2["has_more"]
    assert "never searched" in p0["coverage"]["note"]


def test_background_preview_and_adopt(ctl):
    legacy = ctl.share / "Eng" / "Legacy"
    (legacy / "cad").mkdir(parents=True)
    for i in range(450):
        (legacy / "cad" / f"p{i}.ipt").write_bytes(b"x")
    before = tree_snapshot(legacy)
    assert ctl.start_preview(legacy)
    res = wait_for(ctl, "preview")
    assert res.error is None and res.value["files"] == 450
    assert ctl.start_adopt(legacy, "Legacy", "indexed")
    res = wait_for(ctl, "adopt")
    assert res.error is None and res.value["baseline"]["files"] == 450
    assert tree_snapshot(legacy) == before
    pid = res.value["id"]
    assert len(ctl.project_files(pid, limit=1000)) == 450
    hits = [f["path"] for f in ctl.project_files(pid, "p42")]  # prefix search: p42, p420..p429
    assert hits[0] == "cad/p42.ipt" and len(hits) == 11
    assert ctl.project(pid)["folder"] == str(legacy)


def test_background_errors_are_delivered_not_lost(ctl):
    assert ctl.start_preview(ctl.share / "nowhere")
    res = wait_for(ctl, "preview")
    assert res.error is not None


def test_cancelled_adoption_registers_nothing(ctl):
    legacy = ctl.share / "Eng" / "Big"
    legacy.mkdir()
    for i in range(50):
        (legacy / f"f{i}.ipt").write_bytes(b"x")
    ctl._cancel.set()
    orig = ctl._run

    def run_cancelled(name, fn, on_done):  # cancel already requested before the scan starts
        def wrapped():
            ctl._cancel.set()
            return fn()
        return orig(name, wrapped, on_done)
    ctl._run = run_cancelled
    ctl.start_adopt(legacy, "Big", "indexed")
    res = wait_for(ctl, "adopt")
    assert res.error is not None
    assert ctl.project_page()["rows"] == []


def test_lock_error_message_is_actionable(ctl):
    holder = Workspace.open(ctl.share, writable=True)
    try:
        with pytest.raises(WorkspaceLockedError) as info:
            ctl.create_project(NewProject(name="blocked"))
        assert "lock break" in describe_error(info.value)
    finally:
        holder.close()


def test_project_with_unreachable_root_reports_folder_error(ctl):
    rec = ctl.create_project(NewProject(name="A"))
    from revisionworx.workspace import RecordPut
    with Workspace.open(ctl.share, writable=True) as ws:
        root = ws.get("root", rec["root_id"])
        root["paths"] = {"windows": "Z:/eng"} if root["paths"].get("posix") else {"posix": "/mnt/eng"}
        ws.commit("t", [RecordPut("root", rec["root_id"], root)])
    assert "folder_error" in ctl.project(rec["id"])


def test_gui_module_is_syntactically_valid():
    """The Tk view cannot run without tkinter; at least prove it parses and has an entry point."""
    src = Path(ctl_mod.__file__).with_name("app.py").read_text()
    tree = ast.parse(src)
    assert any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body)


@pytest.mark.gui
def test_gui_starts_and_lists_projects(ctl):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    from revisionworx.gui.app import App
    ctl.create_project(NewProject(name="Visible"))
    app = App(root, ctl)
    app.refresh()
    assert len(app.tree.get_children()) == 1
    root.destroy()
