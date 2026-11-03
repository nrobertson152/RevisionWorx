# SPDX-License-Identifier: GPL-3.0-or-later
"""Offscreen PySide6 tests of the 0.1.0 Alpha journeys (Issue #13). Real core, real temp files;
only modal dialogs that would block (QMessageBox/QFileDialog) are bypassed by driving the widgets
or view-models directly. Skipped (reported as SKIPPED, never passed) when PySide6 is missing.

Run with:  QT_QPA_PLATFORM=offscreen python -m pytest -m qt
"""
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx.gui.controller import Controller  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def ctl(tmp_path):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")


@pytest.fixture(autouse=True)
def no_modal_boxes(monkeypatch):
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


def test_window_starts_on_start_page_with_alpha_banner(qapp, ctl):
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(ctl)
    assert w.stack.currentWidget() is w.start_page
    assert "single user" in w.alpha_banner.text() and "irreplaceable" in w.alpha_banner.text()
    assert not w.alpha_banner.isHidden()
    assert "Alpha" in w.windowTitle()
    w.close()


def test_menu_actions_call_their_own_slots(qapp, ctl, monkeypatch):
    """Regression: a late-binding lambda made every Workspace menu item run the last slot."""
    from revisionworx.gui.qt.mainwindow import MainWindow
    calls = []
    monkeypatch.setattr(MainWindow, "open_ws", lambda self: calls.append("open"))
    monkeypatch.setattr(MainWindow, "close_ws", lambda self: calls.append("close"))
    w = MainWindow(ctl)
    menu = w.menuBar().actions()[0].menu()
    by_text = {a.text(): a for a in menu.actions()}
    by_text["Open workspace…"].trigger()
    by_text["Close workspace"].trigger()
    assert calls == ["open", "close"]
    w.close()


def test_full_alpha_journey_through_widgets(qapp, ctl, tmp_path, no_modal_boxes):
    from revisionworx.gui.qt.dialogs import CheckinDialog, ImportDialog
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(ctl)
    sp = w.start_page
    # 1. create a local project from the start page
    sp.folder_edit.setText(str(tmp_path / "Projects home"))
    sp.create_btn.click()
    assert "name" in sp.error_label.text().lower()  # validation, nothing created
    assert not (tmp_path / "Projects home").exists()
    sp.name_edit.setText("Bracket")
    sp.number_edit.setText("P-1")
    assert sp.preview.text().endswith("P-1 Bracket")
    sp.create_btn.click()
    assert w.stack.currentWidget() is w.workspace_page, sp.error_label.text()
    assert w.table_model.rowCount() == 1
    w.table.selectRow(0)
    qapp.processEvents()
    tab = w.documents_tab
    assert tab.import_btn.isEnabled() and "Import" in tab.notice.text()

    # 2. import a document
    src = tmp_path / "bracket.ipt"
    src.write_bytes(b"v1")
    dlg = ImportDialog(ctl, tab.list_vm.project_id, "Bracket", tab,
                       on_done=lambda res: tab.list_vm.refresh())
    assert not dlg.import_btn.isEnabled()
    dlg.source.setText(str(src))
    assert dlg.title.text() == "bracket" and dlg.dest.text() == "bracket.ipt"
    dlg.description.setText("First issue")
    dlg.start()
    pump(qapp, ctl, lambda: dlg.flow.state in ("done", "error"))
    assert dlg.flow.state == "done", dlg.flow.message
    assert tab.table_model.rowCount() == 1
    tab.table.selectRow(0)
    qapp.processEvents()
    assert tab.checkout_btn.isEnabled() and not tab.checkin_btn.isEnabled()
    assert tab.history_model.rowCount() == 1 and "First issue" in tab.revision_detail.toPlainText()

    # 3. check out, edit, check in through the dialog
    tab.checkout()
    assert tab.checkin_btn.isEnabled() and "Checked out to you" in tab.status.text()
    Path(tab.panel.working_path).write_bytes(b"v2")
    cd = CheckinDialog("bracket", tab)
    cd.submit()
    assert "Describe" in cd.error.text() and cd.result() == 0
    cd.description.setPlainText("Thicker flange for load case B")
    cd.submit()
    assert cd.result() == 1  # QDialog.Accepted
    tab.panel.start_checkin(cd.form.description, on_done=lambda _r: tab.list_vm.refresh())
    pump(qapp, ctl, lambda: tab.panel.message.startswith("Checked in"))
    assert tab.history_model.rowCount() == 2
    assert not tab.checkin_btn.isEnabled() and tab.checkout_btn.isEnabled()

    # 4. restart: a new window + controller with the same user dirs reopens and shows history
    w.close()
    ctl2 = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    w2 = MainWindow(ctl2)
    assert w2.open_location(Path(ctl2.recent_workspaces()[0]), quiet=True)
    assert w2.stack.currentWidget() is w2.workspace_page
    qapp.processEvents()
    tab2 = w2.documents_tab
    tab2.table.selectRow(0)
    qapp.processEvents()
    assert tab2.history_model.rowCount() == 2
    assert not no_modal_boxes, no_modal_boxes
    w2.close()


def test_missing_workspace_returns_to_start_page_with_reason(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(ctl)
    assert not w.open_location(tmp_path / "nowhere", quiet=True)
    assert w.stack.currentWidget() is w.start_page
    assert "Cannot open" in w.start_page.error_label.text()
    w.close()


def test_recovery_blocked_banner(qapp, ctl, tmp_path):
    import json
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    ctl.quick_start(home, "X")
    entry = sorted((home / ".revisionworx" / "journal").glob("*.json"))[-1]
    data = json.loads(entry.read_text())
    data["op"] += "-tampered"
    entry.write_text(json.dumps(data))
    w = MainWindow(ctl)
    w.open_location(home, quiet=True)
    assert not w.health_banner.isHidden()
    assert "RECOVERY BLOCKED" in w.health_banner.text()
    w.close()


def test_missing_working_file_status_is_red(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    p = ctl.quick_start(home, "X")
    src = tmp_path / "a.txt"
    src.write_bytes(b"a")
    doc, _ = ctl.import_document(p["id"], src)
    ctl.working_file(doc["id"]).unlink()
    w = MainWindow(ctl)
    w.open_location(home, quiet=True)
    w.table.selectRow(0)
    qapp.processEvents()
    tab = w.documents_tab
    tab.table.selectRow(0)
    qapp.processEvents()
    assert "missing" in tab.status.text().lower()
    assert tab.panel.tone == "danger"
    assert not tab.open_file_btn.isEnabled()
    w.close()


def test_close_workspace_returns_to_start_and_lists_recent(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    ctl.quick_start(home, "X")
    w = MainWindow(ctl)
    w.open_location(home, quiet=True)
    w.close_ws()
    assert w.stack.currentWidget() is w.start_page and ctl.location is None
    assert w.start_page.recent.count() == 1
    w.close()


def test_status_column_colours_and_restore_compare_buttons(qapp, ctl, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    p = ctl.quick_start(home, "X")
    src = tmp_path / "a.txt"
    src.write_bytes(b"one")
    doc, _ = ctl.import_document(p["id"], src)
    ctl.checkout(doc["id"])
    ctl.working_file(doc["id"]).write_bytes(b"two!")
    done = {}
    ctl.start_checkin(doc["id"], "second", on_done=lambda r: done.setdefault("r", r))
    pump(qapp, ctl, lambda: "r" in done)
    w = MainWindow(ctl)
    w.open_location(home, quiet=True)
    w.table.selectRow(0)
    qapp.processEvents()
    tab = w.documents_tab
    col = [c.key for c in tab.list_vm.COLUMNS].index("file_state")
    idx = tab.table_model.index(0, col)
    assert tab.table_model.data(idx) == "OK"
    assert tab.table_model.data(idx, Qt.ItemDataRole.ForegroundRole) is None
    tab.table.selectRow(0)
    qapp.processEvents()
    assert tab.compare_btn.isEnabled() and not tab.restore_btn.isEnabled()
    tab.compare()
    assert "different" in tab.revision_detail.toPlainText()
    tab.checkout()
    tab.history.selectRow(1)
    qapp.processEvents()
    assert tab.restore_btn.isEnabled()
    tab.restore(confirmed=True)
    assert ctl.working_file(doc["id"]).read_bytes() == b"one"
    assert "not rewritten" in tab.status.text()
    # an externally deleted file turns its row red
    ctl.cancel_checkout(doc["id"])
    ctl.working_file(doc["id"]).unlink()
    tab.list_vm.refresh()
    assert tab.table_model.data(tab.table_model.index(0, col)) == "⚠ Missing"
    assert tab.table_model.data(tab.table_model.index(0, col), Qt.ItemDataRole.ForegroundRole) is not None
    assert "need attention" in tab.notice.text()
    w.close()


# ---------------------------------------------------------------------------- RC usability (PR #14 milestone)
def _window_with_doc(qapp, ctl, tmp_path, name="X"):
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    p = ctl.quick_start(home, name)
    src = tmp_path / "a.txt"
    src.write_bytes(b"one")
    doc, _ = ctl.import_document(p["id"], src)
    w = MainWindow(ctl)
    assert w.open_location(home, quiet=True, select_project=p["id"])
    qapp.processEvents()
    w.documents_tab.select_document(doc["id"])
    qapp.processEvents()
    return w, home, p, doc


def test_observable_state_and_object_names_are_stable(qapp, ctl, tmp_path):
    from PySide6.QtWidgets import QPushButton
    w, home, p, doc = _window_with_doc(qapp, ctl, tmp_path)
    st = w.observable_state()
    assert st["contract"] == 1 and st["page"] == "workspace" and st["selected_project"] == p["id"]
    assert st["selected_document"] == doc["id"] and len(st["history"]) == 1
    assert st["actions"]["checkout"] and not st["actions"]["checkin"] and not st["read_only"]
    assert "single user" in st["alpha_banner"]
    for name in ("start_create_btn", "start_name_edit", "main_new_btn", "documents_import_btn",
                 "documents_checkout_btn", "documents_checkin_btn", "documents_history"):
        assert w.findChild(QtWidgets.QWidget, name) is not None, name
    assert w.findChild(QPushButton, "documents_checkout_btn") is w.documents_tab.checkout_btn
    w.close()


def test_recovery_blocked_makes_gui_read_only_and_f5_recovers(qapp, ctl, tmp_path, no_modal_boxes):
    import json
    w, home, p, doc = _window_with_doc(qapp, ctl, tmp_path)
    entry = sorted((home / ".revisionworx" / "journal").glob("*.json"))[-1]
    original = entry.read_bytes()
    data = json.loads(original)
    data["op"] += "-tampered"
    entry.write_text(json.dumps(data))
    w.refresh_all()
    qapp.processEvents()
    st = w.observable_state()
    assert st["read_only"] and "RECOVERY BLOCKED" in st["health_banner"] and "READ-ONLY" in st["health_banner"]
    assert not any(st["actions"][k] for k in ("new_project", "import", "checkout", "checkin", "restore"))
    assert st["selected_document"] == doc["id"] and st["history"]  # browsing still works
    w.documents_tab.checkout()  # guarded: no write, no dialog
    assert not no_modal_boxes
    entry.write_bytes(original)  # administrator repaired
    w.refresh_all()
    qapp.processEvents()
    st = w.observable_state()
    assert not st["read_only"] and st["health_banner"] == "" and st["actions"]["checkout"]
    assert st["selected_document"] == doc["id"]
    w.close()


def test_checkin_saved_with_warning_is_never_shown_as_failure(qapp, ctl, tmp_path, monkeypatch, no_modal_boxes):
    from revisionworx import documents
    from revisionworx.workspace import CommittedNotApplied
    w, home, p, doc = _window_with_doc(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.checkout()
    ctl.working_file(doc["id"]).write_bytes(b"two")
    real = documents.checkin

    def lagging(ws, *a, **k):
        rev = real(ws, *a, **k)
        raise CommittedNotApplied(rev["_rev"], k["txid"], OSError("records lag"))
    monkeypatch.setattr(documents, "checkin", lagging)
    tab.panel.start_checkin("Saved but records lag", on_done=lambda _r: tab.list_vm.refresh())
    pump(qapp, ctl, lambda: not tab.panel.message.startswith("Checking in"))
    assert tab.status.text().startswith("Checked in.")
    assert tab.panel.tone == "warn"  # amber warning, not red error
    assert tab.history_model.rowCount() == 2 and not tab.checkin_btn.isEnabled()
    assert not [b for b in no_modal_boxes if b[0] == "critical"]
    w.close()


def test_ambiguous_checkin_not_in_journal_shows_error_not_success(qapp, ctl, tmp_path, monkeypatch):
    from revisionworx import documents
    w, home, p, doc = _window_with_doc(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.checkout()
    monkeypatch.setattr(documents, "checkin", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "I/O error")))
    tab.panel.start_checkin("Must not claim success")
    pump(qapp, ctl, lambda: not tab.panel.message.startswith("Checking in"))
    assert "Checked in" not in tab.status.text() and tab.panel.tone == "danger"
    assert tab.history_model.rowCount() == 1 and tab.checkin_btn.isEnabled()
    w.close()


def test_selection_is_predictable_after_create_and_import(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    ctl.quick_start(home, "Aardvark")  # sorts before the new one
    w = MainWindow(ctl)
    sp = w.start_page
    sp.folder_edit.setText(str(home))
    sp.name_edit.setText("Zebra")
    sp.create_btn.click()
    qapp.processEvents()
    st = w.observable_state()
    assert [x["name"] for x in st["projects"]][-1] == "Zebra"
    assert st["selected_project"] == sp.last_created_id  # the new project, not row 0
    src = tmp_path / "b.txt"
    src.write_bytes(b"b")
    for name in ("a.txt", "c.txt"):
        (tmp_path / name).write_bytes(name.encode())
        ctl.import_document(sp.last_created_id, tmp_path / name)
    w.documents_tab.list_vm.refresh()
    from revisionworx.gui.qt.dialogs import ImportDialog
    tab = w.documents_tab
    dlg = ImportDialog(ctl, tab.list_vm.project_id, "Zebra", tab,
                       on_done=lambda res: (tab.list_vm.refresh(), tab.select_document(res.value[0]["id"])))
    dlg.source.setText(str(src))
    dlg.start()
    pump(qapp, ctl, lambda: dlg.flow.state == "done")
    qapp.processEvents()
    assert w.observable_state()["selected_document"] == dlg.flow.result[0]["id"]  # middle row b.txt
    w.close()


def test_f5_keeps_selection_and_shows_external_changes(qapp, ctl, tmp_path):
    w, home, p, doc = _window_with_doc(qapp, ctl, tmp_path)
    ctl.working_file(doc["id"]).unlink()
    w.refresh_all()
    qapp.processEvents()
    st = w.observable_state()
    assert st["selected_project"] == p["id"] and st["selected_document"] == doc["id"]
    assert st["documents"][0]["file_state"] == "missing" and "missing" in st["document_status"].lower()
    assert not st["actions"]["open_file"]
    w.close()


def test_empty_and_no_match_states(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    from revisionworx.workspace import Workspace
    home = tmp_path / "empty"
    Workspace.create(home, "Empty").close()
    w = MainWindow(ctl)
    w.open_location(home, quiet=True)
    st = w.observable_state()
    assert "No projects yet" in st["projects_notice"] and st["selected_project"] is None
    assert "Select a project" in st["documents_notice"]
    ctl.close_workspace()
    p = ctl.quick_start(tmp_path / "one", "Only")
    w.open_location(tmp_path / "one", quiet=True)
    w.search.setText("zzz-nothing")
    w.apply_search()
    assert "No registered project matches" in w.observable_state()["projects_notice"]
    assert p["name"] == "Only"
    w.close()


def test_shortcuts_registered(qapp, ctl):
    from PySide6.QtGui import QKeySequence, QShortcut
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(ctl)
    keys = {sc.key().toString() for sc in w.findChildren(QShortcut)}
    assert {"Ctrl+I", "Ctrl+K", "Ctrl+Shift+K", "Ctrl+Shift+F"} <= keys
    menu_keys = {a.shortcut().toString() for m in w.menuBar().actions() if m.menu()
                 for a in m.menu().actions()}
    assert QKeySequence(QKeySequence.StandardKey.Refresh).toString() in menu_keys
    w.close()
