# SPDX-License-Identifier: GPL-3.0-or-later
"""PySide6 widget tests. Skipped (reported as SKIPPED, never as passed) when PySide6 is missing.

Run with:  QT_QPA_PLATFORM=offscreen python -m pytest -m qt
"""
import os
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx.gui.controller import Controller  # noqa: E402
from revisionworx.projects import NewProject  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def ctl(tmp_path):
    c = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    (tmp_path / "share" / "Eng").mkdir(parents=True)
    c.create_workspace(tmp_path / "share", "Qt test")
    c.add_root("Eng", tmp_path / "share" / "Eng")
    c.share = tmp_path / "share"
    return c


def test_main_window_lists_and_selects_projects(qapp, ctl):
    from revisionworx.gui.qt.mainwindow import MainWindow
    ctl.create_project(NewProject(name="Alpha", number="A-1"))
    ctl.create_project(NewProject(name="Beta", number="B-1"))
    w = MainWindow(ctl)
    w.list_vm.refresh()
    assert w.table_model.rowCount() == 2
    from PySide6.QtCore import Qt
    assert w.table_model.headerData(1, Qt.Orientation.Horizontal) == "Name"
    w.table.selectRow(0)
    qapp.processEvents()
    assert w.detail_vm.project is not None
    assert w.open_btn.isEnabled()
    w.search.setText("beta")
    w.apply_search()
    assert w.table_model.rowCount() == 1
    w.close()


def test_new_project_dialog_validates(qapp, ctl):
    from revisionworx.gui.qt.dialogs import NewProjectDialog
    dlg = NewProjectDialog(ctl)
    dlg.submit()
    assert "name" in dlg.form.errors and dlg.result_record is None
    dlg.edits["name"].setText("Gamma")
    dlg.submit()
    assert dlg.result_record["name"] == "Gamma"


def test_adopt_dialog_buttons_follow_flow(qapp, ctl):
    from revisionworx.gui.qt.dialogs import AdoptDialog
    legacy = ctl.share / "Eng" / "Old"
    legacy.mkdir()
    (legacy / "a.ipt").write_bytes(b"a")
    dlg = AdoptDialog(ctl)
    assert not dlg.preview_btn.isEnabled() and not dlg.adopt_btn.isEnabled()
    dlg.flow.set_folder(str(legacy))
    dlg.name_edit.setText("Old")
    assert dlg.preview_btn.isEnabled()
    dlg.flow.preview()
    end = time.time() + 30
    while dlg.flow.state != "previewed" and time.time() < end:
        ctl.drain()
        qapp.processEvents()
        time.sleep(0.02)
    assert dlg.adopt_btn.isEnabled()
    dlg.reject()
