# SPDX-License-Identifier: GPL-3.0-or-later
"""Redesigned window (1.1): themes, About, Go to anything, quick filters, the status card and primary action,
right-click menu, drag-and-drop import, remembered layout and `--document`. Real core, offscreen Qt."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402

from revisionworx import __version__  # noqa: E402
from revisionworx.gui.controller import Controller  # noqa: E402
from revisionworx.gui.qt import theme  # noqa: E402
from revisionworx.projects import NewProject  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_ctl(tmp_path, state="state"):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / state)


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    from revisionworx.gui.qt.mainwindow import MainWindow
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: None))
    ctl = make_ctl(tmp_path)
    p = ctl.quick_start(tmp_path / "home", "Hydraulic Manifold", number="P-1042")
    docs = []
    for name in ("manifold_block.ipt", "port_plate.ipt", "seal_spec.pdf"):
        src = tmp_path / "in" / name
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(name.encode())
        docs.append(ctl.import_document(p["id"], src, number=name.split(".")[0].upper())[0])
    other = ctl.create_project(NewProject(name="Pump Test Stand", number="P-1038"))
    w = MainWindow(ctl)
    w.show()
    assert w.open_location(tmp_path / "home", quiet=True, select_project=p["id"])
    qapp.processEvents()
    yield w, ctl, p, docs, other
    w.close()
    theme.apply(qapp, theme.DEFAULT_THEME)


def test_theme_menu_switches_and_remembers(win, qapp, tmp_path):
    w, ctl, *_ = win
    labels = [a.text() for a in w.theme_actions.actions()]
    assert labels == ["Modern Light", "Modern Dark", "Graphite", "Blueprint", "High Contrast"]
    w.set_theme("dark")
    assert theme.current(qapp) == "dark" and "#191E25" in qapp.styleSheet()
    assert [a.data() for a in w.theme_actions.actions() if a.isChecked()] == ["dark"]
    assert make_ctl(tmp_path).preference("theme") == "dark"
    from revisionworx.gui.qt.mainwindow import MainWindow
    again = MainWindow(make_ctl(tmp_path, "s2"))
    assert again.theme_name == "dark"
    again.close()


def test_state_column_is_coloured_by_the_theme(win, qapp):
    w, *_ = win
    tab = w.documents_tab
    col = [c.key for c in tab.list_vm.COLUMNS].index("latest_state")
    idx = tab.table_model.index(0, col)
    bg = tab.table_model.data(idx, Qt.ItemDataRole.BackgroundRole)
    assert bg is not None and bg.name().upper() == theme.tone_colors("light", "neutral")[0].upper()


def test_about_dialog_like_openschematix(win):
    from revisionworx.gui.qt.about import AboutDialog, system_info
    w, *_ = win
    dlg = AboutDialog(w)
    texts = [lab.text() for lab in dlg.findChildren(QtWidgets.QLabel)]
    assert "RevisionWorx" in texts and f"Version {__version__}" in texts
    assert "GNU General Public License v3.0 or later" in dlg.body.text() and "no telemetry" in dlg.body.text()
    assert "Python" in dlg.info.text() and "Qt" in dlg.info.text() and system_info() == dlg.info.text()
    buttons = [b.text() for b in dlg.findChildren(QtWidgets.QPushButton)]
    assert {"Project page", "User guide", "Licence text", "Copy version info"} <= set(buttons)
    dlg.copy_btn.click()
    assert QtWidgets.QApplication.clipboard().text() == system_info()
    dlg.close()


def test_status_card_and_one_primary_button(win, qapp):
    w, ctl, p, docs, _ = win
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    assert "Available" in tab.card.text() and tab.card.property("tone") == "ok"
    assert tab.checkout_btn.property("primary") is True and tab.checkin_btn.property("primary") is False
    tab.checkout()
    qapp.processEvents()
    assert "Checked out by you" in tab.card.text() and tab.card.property("tone") == "info"
    assert tab.checkin_btn.property("primary") is True and tab.checkout_btn.property("primary") is False
    assert tab.doc_title.text() and tab.doc_title.isVisible()


def test_quick_filter_chips(win, qapp):
    w, ctl, p, docs, _ = win
    tab = w.documents_tab
    assert tab.chips["all"].text().endswith("3") and tab.chips["all"].isChecked()
    ctl.checkout(docs[1]["id"])
    tab.list_vm.refresh()
    tab.chips["mine"].click()
    qapp.processEvents()
    assert [r["id"] for r in tab.list_vm.rows] == [docs[1]["id"]] and tab.chips["mine"].isChecked()
    assert tab.panel.doc_id == docs[1]["id"]
    assert tab.select_document(docs[2]["id"]) and tab.list_vm.quick == "all"   # going to a hidden document shows it


def test_go_to_anything_finds_and_opens_a_document_in_another_project(win, qapp):
    w, ctl, p, docs, other = win
    w.select_project(other["id"])
    qapp.processEvents()
    dlg = w.open_palette()
    dlg.search.setText("port")
    qapp.processEvents()
    kinds = [i["kind"] for i in dlg.model.items]
    row = kinds.index("document")
    dlg.list.setCurrentRow(row)
    dlg.choose(row)
    qapp.processEvents()
    assert w.selected_project_id() == p["id"] and w.documents_tab.panel.doc_id == docs[1]["id"]
    assert not dlg.isVisible()


def test_palette_runs_actions(win, qapp, monkeypatch):
    w, ctl, p, docs, _ = win
    shown = []
    monkeypatch.setattr(w, "show_about", lambda: shown.append("about"))
    dlg = w.open_palette()
    dlg.search.setText("about")
    qapp.processEvents()
    dlg.choose(0)
    assert shown == ["about"]


def test_context_menu_offers_the_document_actions(win, qapp):
    w, ctl, p, docs, _ = win
    tab = w.documents_tab
    tab.table.selectRow(0)
    qapp.processEvents()
    rect = tab.table.visualRect(tab.table_model.index(0, 1))
    tab.context_menu(rect.center())
    texts = {a.text(): a.isEnabled() for a in tab._menu.actions() if a.text()}
    assert texts["Open file"] and texts["Check out"] and not texts["Check in…"]
    assert texts["Copy file path"] and texts["Copy document number"]
    tab._menu.close()
    tab.copy_path()
    assert QtWidgets.QApplication.clipboard().text() == tab.panel.working_path


def test_dropping_files_opens_one_import_window_each(win, qapp, tmp_path):
    w, *_ = win
    files = []
    for name in ("a.step", "b.pdf"):
        f = tmp_path / name
        f.write_bytes(b"x")
        files.append(f)
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
    assert w._dropped_files(type("E", (), {"mimeData": lambda self: md})()) == files
    w.import_dropped(files)
    dialogs = w.documents_tab.dialogs
    assert [d.source.text() for d in dialogs] == [str(f) for f in files]
    for d in list(dialogs):
        d.reject()


def test_my_checkouts_and_status_bar(win, qapp):
    w, ctl, p, docs, _ = win
    assert w.checkouts_label.text() == ""
    ctl.checkout(docs[0]["id"])
    w.update_checkouts_label()
    assert w.checkouts_label.text().startswith("1 checked out by you")
    w.show_my_checkouts()
    dlg = w._palette
    assert [i["payload"][1] for i in dlg.model.items] == [docs[0]["id"]]
    dlg.choose(0)
    assert w.documents_tab.panel.doc_id == docs[0]["id"]


def test_window_layout_and_last_project_are_remembered(win, qapp, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    w, ctl, p, docs, other = win
    w.select_project(other["id"])
    w.main_split.setSizes([300, 900])
    w.close()
    again = MainWindow(make_ctl(tmp_path))
    again.show()
    assert again.open_location(tmp_path / "home", quiet=True, restore_selection=True)
    assert again.selected_project_id() == other["id"]
    assert abs(again.main_split.sizes()[0] - 300) <= 40
    again.close()
    third = MainWindow(make_ctl(tmp_path))
    assert third.open_location(tmp_path / "home", quiet=True)          # a plain open keeps the predictable choice
    assert third.selected_project_id() in (p["id"], other["id"])
    third.close()


def test_document_argument_opens_on_that_document(win, qapp):
    from revisionworx.gui.app import _parse
    w, ctl, p, docs, other = win
    w.select_project(other["id"])
    loc, doc = _parse([str(ctl.location), "--document", docs[2]["id"]])
    assert loc == str(ctl.location) and doc == docs[2]["id"]
    assert w.go_to_document(None, doc)
    assert w.selected_project_id() == p["id"] and w.documents_tab.panel.doc_id == docs[2]["id"]


def test_sidebar_shows_the_workspace(win):
    w, ctl, *_ = win
    assert w.ws_name.text() == "home"                     # quick start names the workspace after its folder
    assert str(ctl.location) == w.ws_sub.text()
    assert w.alpha_banner.parent().objectName() == "sidebar" and not w.alpha_banner.isHidden()
