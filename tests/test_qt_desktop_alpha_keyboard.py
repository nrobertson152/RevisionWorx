# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard and screen-reader surface of the alpha GUI, checked offscreen (issue #13). These check the
structure a keyboard or screen-reader user depends on. They do not replace the interactive walkthrough in
docs/DESKTOP_ALPHA_PROGRESS.md, because no assistive technology runs here."""
import os
import re
from collections import defaultdict

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAccessible, QDesktopServices  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from revisionworx.gui.controller import Controller  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def setup(qapp, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    p = ctl.quick_start(tmp_path / "home", "X")
    (tmp_path / "a.txt").write_bytes(b"a")
    doc, _ = ctl.import_document(p["id"], tmp_path / "a.txt")
    w = MainWindow(ctl)
    w.show()
    assert w.open_location(tmp_path / "home", quiet=True, select_project=p["id"])
    w.documents_tab.select_document(doc["id"])
    qapp.processEvents()
    yield w, ctl, p, doc
    w.close()


def focus_chain(start) -> list[str]:
    names, seen, f = [], set(), start
    while f not in seen:
        seen.add(f)
        if f.focusPolicy() & Qt.FocusPolicy.TabFocus and f.isVisible() and f.objectName():
            names.append(f.objectName())
        f = f.nextInFocusChain()
    return names


def test_tab_order_follows_the_screen(setup):
    """Regression: Tab went through the whole document panel before the paging buttons under the project list."""
    w, *_ = setup
    w.prev_btn.setEnabled(True)
    w.next_btn.setEnabled(True)
    chain = focus_chain(w.search)
    order = ["main_search", "main_archived", "main_adopt_btn", "main_new_btn", "main_table", "main_prev_btn",
             "main_next_btn", "main_open_btn", "main_archive_btn", "documents_filter", "documents_table",
             "documents_checkout_btn", "documents_checkin_btn", "documents_transition_box",
             "documents_compare_btn", "documents_history", "documents_revision_detail"]
    assert [n for n in chain if n in order] == order


def test_mnemonics_do_not_clash_on_the_workspace_page(setup):
    """Regression: Alt+W, Alt+P and Alt+I each meant two things."""
    w, *_ = setup
    keys = defaultdict(list)
    texts = [a.text() for a in w.menuBar().actions()]
    texts += [b.text() for b in w.workspace_page.findChildren(QtWidgets.QAbstractButton)]
    for text in texts:
        m = re.search(r"&(\w)", text)
        if m:
            keys[m[1].lower()].append(text)
    assert not {k: v for k, v in keys.items() if len(v) > 1}


def _unnamed(root) -> list:
    out = []
    for x in root.findChildren(QtWidgets.QWidget):
        if not x.focusPolicy() & Qt.FocusPolicy.TabFocus or type(x).__name__ in ("QTabBar", "QScrollBar", "QListView"):
            continue
        iface = QAccessible.queryAccessibleInterface(x)
        if not iface or not iface.text(QAccessible.Text.Name).strip():
            out.append((type(x).__name__, x.objectName()))
    return out


def test_every_control_has_a_name_for_screen_readers(setup):
    """Uses the name Qt actually exposes, which includes form labels."""
    from revisionworx.gui.qt.dialogs import CheckinDialog, ImportDialog
    w, ctl, p, _doc = setup
    assert not _unnamed(w)
    assert not _unnamed(CheckinDialog("x", w))
    assert not _unnamed(ImportDialog(ctl, p["id"], "X", w))


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda u: urls.append(u.toLocalFile()) or True))
    return urls


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_in_the_document_list_opens_the_file(setup, opened, key):
    """Regression: Help > Keyboard shortcuts promised this, but only a double-click did it."""
    w, ctl, _p, doc = setup
    table = w.documents_tab.table
    table.setCurrentIndex(table.model().index(0, 0))
    QTest.keyClick(table, key)
    assert opened == [str(ctl.working_file(doc["id"])).replace("\\", "/")]


def test_enter_in_the_project_list_opens_the_folder(setup, opened):
    w, *_ = setup
    w.table.setCurrentIndex(w.table_model.index(0, 0))
    QTest.keyClick(w.table, Qt.Key.Key_Return)
    assert len(opened) == 1 and opened[0].endswith("Projects/X")


def test_double_click_opens_once(setup, opened):
    w, *_ = setup
    table = w.documents_tab.table
    table.doubleClicked.emit(table.model().index(0, 0))
    assert len(opened) == 1


def test_enter_with_no_row_does_nothing(setup, opened):
    w, *_ = setup
    tab = w.documents_tab
    tab.filter.setText("zzz-nothing")
    tab.apply_filter()
    QTest.keyClick(tab.table, Qt.Key.Key_Return)
    assert not opened


def test_arrow_keys_change_the_selected_document(qapp, setup, tmp_path):
    w, ctl, p, doc = setup
    (tmp_path / "b.txt").write_bytes(b"b")
    other, _ = ctl.import_document(p["id"], tmp_path / "b.txt")
    tab = w.documents_tab
    tab.list_vm.refresh()
    tab.table.setFocus()
    tab.table.setCurrentIndex(tab.table.model().index(0, 0))
    QTest.keyClick(tab.table, Qt.Key.Key_Down)
    qapp.processEvents()
    assert w.observable_state()["selected_document"] == other["id"]
