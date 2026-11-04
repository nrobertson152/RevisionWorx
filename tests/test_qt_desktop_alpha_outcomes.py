# SPDX-License-Identifier: GPL-3.0-or-later
"""What the user is told must match what happened, and a change must go to the workspace the user
is looking at. Each test reproduces a defect found by reviewing the desktop alpha on 2026-11-04.
Real core and real files; only the single failure being studied is injected."""
import json
import os
import threading
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx import documents, workspace  # noqa: E402
from revisionworx.gui import viewmodels  # noqa: E402
from revisionworx.gui.controller import Controller, SavedWithWarning, describe_error  # noqa: E402
from revisionworx.workspace import Workspace, WorkspaceCorruptError  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def ctl(tmp_path):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")


@pytest.fixture(autouse=True)
def boxes(monkeypatch):
    """Record instead of blocking on modal message boxes."""
    shown = []
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[1:])) or 0))
    return shown


def pump(qapp, ctl, until, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        ctl.drain()
        qapp.processEvents()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def window(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    p = ctl.quick_start(home, "Proj", "P-1")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    doc = ctl.import_document(p["id"], src)[0]
    w = MainWindow(ctl)
    assert w.open_location(home, quiet=True, select_project=p["id"])
    qapp.processEvents()
    return w, home, p, doc


def journal_bytes(home):
    journal = Workspace.meta_dir_for(home) / "journal"
    return {str(f.relative_to(journal)): f.read_bytes() for f in journal.rglob("*") if f.is_file()}


def release_fails_after_commit(monkeypatch, error):
    """The real change commits; releasing the writer lock then fails, once."""
    real_close = Workspace.close
    state = {"armed": True}

    def close(self):
        if state["armed"] and self.writable and self.head_seq() > state.get("start", self.head_seq()):
            state["armed"] = False
            real_close(self)
            raise error
        real_close(self)
    real_open = Workspace.open

    def tracking_open(location, writable=False, **kw):
        ws = real_open(location, writable=writable, **kw)
        if writable and state["armed"]:
            state["start"] = ws.head_seq()
        return ws
    monkeypatch.setattr(Workspace, "open", staticmethod(tracking_open))
    monkeypatch.setattr(Workspace, "close", close)
    return state


# ---------------------------------------------------------------- per-user preferences
@pytest.mark.parametrize("content", ['[1, 2]', '"text"', '7', 'null', '{"recent": 5}', '{"recent": [1, null]}',
                                     '{not json'])
def test_damaged_preferences_do_not_stop_the_application_starting(qapp, ctl, content):
    """Regression: a preferences file holding valid JSON of the wrong shape raised out of the window's
    constructor, so the application could not start at all until the user found and deleted it."""
    from revisionworx.gui.qt.mainwindow import MainWindow
    ctl.cfg_dir.mkdir(parents=True)
    ctl._prefs_path().write_text(content, encoding="utf-8")
    assert ctl.recent_workspaces() == []
    w = MainWindow(ctl)
    assert w.observable_state()["page"] == "start"
    w.close()


def test_recent_list_keeps_only_usable_entries(ctl):
    ctl.cfg_dir.mkdir(parents=True)
    ctl._prefs_path().write_text(json.dumps({"recent": ["/a", 3, "", None, "/b"]}), encoding="utf-8")
    assert ctl.recent_workspaces() == ["/a", "/b"]


def test_workspace_opens_when_preferences_cannot_be_saved(qapp, tmp_path):
    """Regression: failing to record the recent list reported the workspace as impossible to open,
    with the controller already pointing at it."""
    from revisionworx.gui.qt.mainwindow import MainWindow
    blocked = tmp_path / "cfg-is-a-file"
    blocked.write_text("x")
    ctl = Controller(cfg_dir=blocked, cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    home = tmp_path / "home"
    Workspace.create(home, "W").close()
    w = MainWindow(ctl)
    assert w.open_location(home, quiet=True) is True
    assert w.observable_state()["page"] == "workspace"
    assert ctl.preferences_warning and str(blocked) in ctl.preferences_warning
    w.close()


def test_preferences_are_valid_after_damage_and_a_successful_open(ctl, tmp_path):
    ctl.cfg_dir.mkdir(parents=True)
    ctl._prefs_path().write_text("[1]", encoding="utf-8")
    home = tmp_path / "home"
    Workspace.create(home, "W").close()
    ctl.open_workspace(home)
    assert ctl.recent_workspaces() == [str(home)] and ctl.preferences_warning is None


# ---------------------------------------------------------------- a saved change is never a failure
def test_a_corrupt_error_while_releasing_after_a_commit_is_not_nothing_was_written(qapp, ctl, tmp_path, monkeypatch):
    """Regression: the change was saved, then the release raised WorkspaceCorruptError, and the user
    read 'Nothing was written'."""
    p = ctl.quick_start(tmp_path / "home", "Proj")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    doc = ctl.import_document(p["id"], src)[0]
    release_fails_after_commit(monkeypatch, WorkspaceCorruptError("orphaned lock-release file"))
    with pytest.raises(SavedWithWarning) as caught:
        ctl.checkout(doc["id"])
    assert "Nothing was written" not in describe_error(caught.value)
    assert "orphaned lock-release file" in caught.value.warning
    assert ctl.read_only, "the block must still make the GUI read-only"
    monkeypatch.undo()
    assert ctl.document_detail(doc["id"])["checkout"] is not None


def test_journal_unreadable_after_the_change_still_releases_the_lock(ctl, tmp_path, monkeypatch):
    """Regression: reading the journal head after the change raised past the release, leaving the
    lock held and reporting a saved change as a failure."""
    p = ctl.quick_start(tmp_path / "home", "Proj")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    doc = ctl.import_document(p["id"], src)[0]
    real_checkout, real_head = documents.checkout, Workspace.head_seq
    armed = []

    def head(self):
        if armed:
            armed.clear()
            raise OSError(5, "I/O error")
        return real_head(self)

    def checkout(ws, *a, **k):
        co = real_checkout(ws, *a, **k)
        armed.append(True)
        return co
    monkeypatch.setattr(Workspace, "head_seq", head)
    monkeypatch.setattr(documents, "checkout", checkout)
    ctl.checkout(doc["id"])
    assert Workspace.read_writer_lock(tmp_path / "home") is None
    assert ctl.document_detail(doc["id"])["mine"]


def test_corrupt_error_while_releasing_with_nothing_committed_is_still_a_refusal(ctl, tmp_path, monkeypatch):
    ctl.quick_start(tmp_path / "home", "Proj")

    def close(self):
        real_close(self)
        raise WorkspaceCorruptError("orphaned lock-release file")
    real_close = Workspace.close
    monkeypatch.setattr(Workspace, "close", close)
    with pytest.raises(WorkspaceCorruptError):
        with ctl._write():
            pass
    assert ctl.read_only


def test_quick_start_saved_but_lock_not_released_opens_the_project(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: the project existed, but the start page stayed put with a red message, inviting a
    second try that fails as a duplicate."""
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    Workspace.create(home, "W").close()
    release_fails_after_commit(monkeypatch, OSError("rename refused"))
    w = MainWindow(ctl)
    sp = w.start_page
    sp.folder_edit.setText(str(home))
    sp.name_edit.setText("Saved")
    # the root is committed first by the same handle: one release, one warning
    sp.create_btn.click()
    qapp.processEvents()
    st = w.observable_state()
    assert st["page"] == "workspace" and [x["name"] for x in st["projects"]] == ["Saved"]
    assert [b for b in boxes if b[0] == "warning"] and not [b for b in boxes if b[0] == "critical"]
    assert st["start_error"] == "" and sp.name_edit.text() == ""
    w.close()


def test_new_project_saved_but_lock_not_released_closes_the_dialog(qapp, ctl, tmp_path, monkeypatch, boxes):
    from revisionworx.gui.qt.dialogs import NewProjectDialog
    w, *_ = window(qapp, ctl, tmp_path)
    release_fails_after_commit(monkeypatch, OSError("rename refused"))
    dlg = NewProjectDialog(ctl, w)
    dlg.edits["name"].setText("Second")
    dlg.submit()
    assert dlg.result() == 1
    assert [b for b in boxes if b[0] == "warning"] and not [b for b in boxes if b[0] == "critical"]
    monkeypatch.undo()
    w.list_vm.refresh()
    assert "Second" in [r["name"] for r in w.list_vm.rows]
    w.close()


def test_archive_saved_with_a_warning_shows_the_new_status(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: the panel kept the old status and record version, so the next click was a conflict."""
    w, _home, _p, _doc = window(qapp, ctl, tmp_path)
    w.archived.setChecked(True)  # archived projects stay listed, so the panel keeps showing this one
    release_fails_after_commit(monkeypatch, OSError("rename refused"))
    w.toggle_archive()
    assert [b[0] for b in boxes] == ["warning"]
    assert w.detail_vm.project["status"] == "archived" and w.archive_btn.text() == "Restore"
    monkeypatch.undo()
    w.toggle_archive()
    assert w.detail_vm.project["status"] == "active" and [b[0] for b in boxes] == ["warning"]
    w.close()


# ---------------------------------------------------------------- import leaves nothing behind
def test_failed_import_with_nothing_committed_can_simply_be_retried(ctl, tmp_path, monkeypatch):
    """Regression: an OS error during registration left the copy behind, and the retry was refused
    because the file 'already exists in the project'."""
    p = ctl.quick_start(tmp_path / "home", "Proj")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    real = documents.register_document

    def refused(*a, **k):
        raise PermissionError(13, "Permission denied", "objects")
    monkeypatch.setattr(documents, "register_document", refused)
    before = journal_bytes(tmp_path / "home")
    with pytest.raises(PermissionError):
        ctl.import_document(p["id"], src)
    assert journal_bytes(tmp_path / "home") == before
    assert not (tmp_path / "home" / "Projects" / "Proj" / "a.txt").exists()
    monkeypatch.setattr(documents, "register_document", real)
    assert ctl.import_document(p["id"], src)[0]["rel_path"] == "a.txt"


def test_import_keeps_the_copy_once_the_journal_has_moved(ctl, tmp_path, monkeypatch):
    """An error after the entry was published: the file may be the registered working file."""
    p = ctl.quick_start(tmp_path / "home", "Proj")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    real = documents.register_document

    def commits_then_raises(*a, **k):
        real(*a, **k)
        raise OSError(5, "I/O error")
    monkeypatch.setattr(documents, "register_document", commits_then_raises)
    with pytest.raises(OSError):
        ctl.import_document(p["id"], src)
    assert (tmp_path / "home" / "Projects" / "Proj" / "a.txt").read_bytes() == b"a"


def test_import_keeps_the_copy_when_the_journal_cannot_be_read(ctl, tmp_path, monkeypatch):
    """Nothing is deleted on a guess."""
    p = ctl.quick_start(tmp_path / "home", "Proj")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    calls = {"n": 0}
    real_head = Workspace.head_seq

    def head(self):
        calls["n"] += 1
        if calls["armed"]:
            raise OSError(5, "I/O error")
        return real_head(self)
    calls["armed"] = False

    def refused(*a, **k):
        calls["armed"] = True
        raise OSError(5, "I/O error")
    monkeypatch.setattr(Workspace, "head_seq", head)
    monkeypatch.setattr(documents, "register_document", refused)
    with pytest.raises(OSError):
        ctl.import_document(p["id"], src)
    calls["armed"] = False
    assert (tmp_path / "home" / "Projects" / "Proj" / "a.txt").exists()


# ---------------------------------------------------------------- the right workspace
def test_adopt_dialog_cannot_write_into_a_workspace_opened_after_it(qapp, ctl, tmp_path, boxes):
    """Regression: the dialog is not modal. Preview in workspace A, open workspace B, click Adopt:
    the folder was registered in B."""
    w, home, _p, _doc = window(qapp, ctl, tmp_path)
    other = tmp_path / "other"
    Workspace.create(other, "Other").close()
    ctl_b = Controller(cfg_dir=tmp_path / "cfg2", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state2")
    ctl_b.open_workspace(other)
    root = ctl_b.add_root("R", tmp_path)
    assert root
    folder = home / "Projects" / "Legacy"
    folder.mkdir()
    (folder / "x.txt").write_bytes(b"x")
    from revisionworx.gui.qt.dialogs import AdoptDialog
    dlg = AdoptDialog(ctl, w)
    w.adopt_dialogs.append(dlg)
    dlg.finished.connect(lambda _: w.adopt_dialogs.remove(dlg) if dlg in w.adopt_dialogs else None)
    dlg.show()
    dlg.flow.set_folder(str(folder))
    dlg.name_edit.setText("Legacy")
    assert dlg.flow.preview()
    pump(qapp, ctl, lambda: dlg.flow.state == "previewed")
    assert w.open_location(other, quiet=True)
    before = journal_bytes(other)
    assert not dlg.isVisible() and w.adopt_dialogs == []
    assert dlg.flow.adopt() is False
    pump(qapp, ctl, lambda: not ctl.busy)
    assert journal_bytes(other) == before
    w.close()


def test_closing_the_workspace_closes_its_windows(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.dialogs import AdoptDialog
    w, *_ = window(qapp, ctl, tmp_path)
    w.documents_tab.import_file()
    imp = w.documents_tab.dialogs[0]
    dlg = AdoptDialog(ctl, w)
    w.adopt_dialogs.append(dlg)
    dlg.show()
    w.close_ws()
    assert not imp.isVisible() and not dlg.isVisible()
    assert w.documents_tab.dialogs == []
    w.close()


def test_import_dialog_refuses_after_its_workspace_was_replaced(qapp, ctl, tmp_path):
    from revisionworx.gui.viewmodels import ImportFlow
    p = ctl.quick_start(tmp_path / "home", "Proj")
    flow = ImportFlow(ctl, p["id"])
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    flow.set_source(str(src))
    other = tmp_path / "other"
    Workspace.create(other, "Other").close()
    ctl.open_workspace(other)
    assert flow.start() is False and "another workspace" in flow.message
    assert not ctl.busy


def test_failing_to_open_another_workspace_keeps_the_current_one_on_screen(qapp, ctl, tmp_path, boxes):
    """Regression: the start page appeared while the controller still pointed at the old workspace,
    so Ctrl+N and F5 acted on a workspace the window no longer showed."""
    w, home, p, _doc = window(qapp, ctl, tmp_path)
    missing = tmp_path / "not-a-workspace"
    missing.mkdir()
    assert w.open_location(missing) is False
    st = w.observable_state()
    assert st["page"] == "workspace" and st["workspace"] == str(home) and st["selected_project"] == p["id"]
    assert [b[0] for b in boxes] == ["critical"] and str(missing) in boxes[0][1][1]
    w.close()


def test_failing_to_open_with_nothing_open_shows_the_start_page(qapp, ctl, tmp_path, boxes):
    from revisionworx.gui.qt.mainwindow import MainWindow
    missing = tmp_path / "not-a-workspace"
    missing.mkdir()
    w = MainWindow(ctl)
    assert w.open_location(missing) is False
    st = w.observable_state()
    assert st["page"] == "start" and str(missing) in st["start_error"]
    w.close()


# ---------------------------------------------------------------- a folder that goes away
def test_workspace_folder_removed_while_open(qapp, ctl, tmp_path, boxes):
    """F5 and the actions that re-read must explain, and a change must not be offered as if it could work."""
    import shutil
    w, home, _p, _doc = window(qapp, ctl, tmp_path)
    moved = tmp_path / "moved-away"
    shutil.move(str(home), str(moved))
    w.refresh_all()
    qapp.processEvents()
    st = w.observable_state()
    assert st["health_banner"], "the window must say the workspace is unavailable"
    assert not any(st["actions"][k] for k in ("new_project", "adopt", "archive", "import", "register", "checkout",
                                              "checkin", "cancel_checkout", "change_state", "restore"))
    assert not home.exists(), "nothing may recreate the folder"
    w.close_ws()
    assert w.observable_state()["page"] == "start"
    w.close()


def test_workspace_comes_back_and_f5_recovers(qapp, ctl, tmp_path, boxes):
    import shutil
    w, home, _p, _doc = window(qapp, ctl, tmp_path)
    moved = tmp_path / "moved-away"
    shutil.move(str(home), str(moved))
    w.refresh_all()
    shutil.move(str(moved), str(home))
    w.refresh_all()
    qapp.processEvents()
    st = w.observable_state()
    assert st["health_banner"] == "" and st["actions"]["checkout"] and st["actions"]["new_project"]
    w.close()


@pytest.mark.parametrize("action", ["filter_files", "new_project"])
def test_actions_that_read_report_a_failure_instead_of_vanishing(qapp, ctl, tmp_path, monkeypatch, boxes, action):
    """Regression: these raised out of a Qt slot. With no console (the installed gui-script) the user
    saw nothing at all."""
    w, *_ = window(qapp, ctl, tmp_path)
    monkeypatch.setattr(Controller, "project", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied", "x")))
    monkeypatch.setattr(Controller, "roots", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied", "x")))
    getattr(w, action)()
    assert [b[0] for b in boxes] == ["critical"]
    w.close()


def test_unexpected_error_reaches_the_user(qapp, ctl, boxes, monkeypatch):
    """A last resort for a defect in a slot: say something, and say that nothing is known to be lost."""
    from revisionworx.gui import app
    log = []
    hook = app.exception_reporter(log.append)
    try:
        raise ValueError("boom")
    except ValueError as exc:
        hook(type(exc), exc, exc.__traceback__)
    assert [b[0] for b in boxes] == ["critical"] and "boom" in boxes[0][1][1]
    assert "Traceback" in log[0] and "boom" in log[0]


def test_interrupt_is_not_reported_as_an_error(qapp, boxes):
    from revisionworx.gui import app
    log = []
    hook = app.exception_reporter(log.append)
    hook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert boxes == [] and log == []


def test_reporter_survives_having_no_console(qapp, boxes, monkeypatch):
    """pythonw / gui-script: sys.stderr is None."""
    import sys
    from revisionworx.gui import app
    monkeypatch.setattr(sys, "stderr", None)
    hook = app.exception_reporter()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        hook(type(exc), exc, exc.__traceback__)
    assert [b[0] for b in boxes] == ["critical"]


# ---------------------------------------------------------------- flows
def test_adopt_while_another_job_runs_does_not_hang_the_dialog(qapp, ctl, tmp_path):
    """Regression: the start was refused, but the flow said 'adopting' forever with every button off."""
    home = tmp_path / "home"
    ctl.quick_start(home, "Proj")
    folder = home / "Projects" / "Legacy"
    folder.mkdir()
    flow = viewmodels.AdoptFlow(ctl)
    flow.set_folder(str(folder))
    flow.name = "Legacy"
    assert flow.preview()
    pump(qapp, ctl, lambda: flow.state == "previewed")
    release = threading.Event()
    assert ctl._run("hold", release.wait, None)
    try:
        assert flow.adopt() is False
        assert flow.state == "previewed" and "try again" in flow.progress_text.lower()
    finally:
        release.set()
    pump(qapp, ctl, lambda: not ctl.busy)
    assert flow.can_adopt and flow.adopt()
    pump(qapp, ctl, lambda: flow.state == "adopted")


def test_rebuild_while_another_job_runs_says_so(qapp, ctl, tmp_path, boxes):
    w, *_ = window(qapp, ctl, tmp_path)
    release = threading.Event()
    assert ctl._run("hold", release.wait, None)
    try:
        w.rebuild()
        assert [b[0] for b in boxes] == ["information"]
    finally:
        release.set()
    pump(qapp, ctl, lambda: not ctl.busy)
    w.close()


# ---------------------------------------------------------------- errors core can raise
def test_busy_lock_release_file_says_try_again(ctl, tmp_path, monkeypatch):
    """CORE_API §1: not corruption and not an administrator action, and the GUI does not go read-only."""
    cls = getattr(workspace, "LockReleaseBusyError", None)
    if cls is None:  # this branch's core predates it; the merged core has the real class
        class cls(workspace.WorkspaceError):
            def __init__(self, name, cause):
                self.name = name
                super().__init__(f"lock-release file {name} could not be read ({cause})")
        monkeypatch.setattr(workspace, "LockReleaseBusyError", cls, raising=False)
    exc = cls(".releasing-x", PermissionError(13, "Access is denied"))
    text = describe_error(exc)
    assert ".releasing-x" in text and "Nothing was written" in text and "try again" in text.lower()
    ctl.quick_start(tmp_path / "home", "Proj")
    real_open = Workspace.open
    monkeypatch.setattr(Workspace, "open", staticmethod(
        lambda loc, writable=False, **k: (_ for _ in ()).throw(exc) if writable else real_open(loc, **k)))
    with pytest.raises(cls):
        with ctl._write():
            pass
    assert not ctl.read_only
