# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent GUI keyboard and fail-closed regressions for issue #30.

Pinned starting GUI source: 0773bde599f39aee88a8a536c28673bc4a1e13f5.
These tests intentionally exercise only the published GUI launch contract v1.
Run: QT_QPA_PLATFORM=offscreen python -m pytest -q -ra -m qt tests/test_qt_gui_accessibility_a.py
"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.qt
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx.gui.controller import Controller  # noqa: E402
from revisionworx.gui.qt.mainwindow import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def desktop(qapp, tmp_path):
    ctl = Controller(cfg_dir=tmp_path / "cfg",
                     cache_dir=tmp_path / "cache",
                     state_dir=tmp_path / "state")
    home = tmp_path / "engineering"
    project = ctl.quick_start(home, "Fixture", "A-001")
    source = tmp_path / "drawing.ipt"
    source.write_bytes(b"immutable CAD fixture")
    doc, _rev = ctl.import_document(project["id"], source)
    win = MainWindow(ctl)
    assert win.open_location(home, quiet=True, select_project=project["id"])
    qapp.processEvents()
    win.documents_tab.select_document(doc["id"])
    qapp.processEvents()
    yield win, ctl, home, doc
    win.close()


def test_keyboard_focus_can_reach_document_filter_and_table(qapp, desktop):
    """Navigation targets must remain keyboard focusable, not mouse-only."""
    win, _ctl, _home, _doc = desktop
    tab = win.documents_tab
    win.show()
    qapp.processEvents()
    for widget in (tab.filter, tab.table, tab.history):
        assert widget.isEnabled(), widget.objectName()
        assert widget.focusPolicy() != 0, widget.objectName()
        widget.setFocus()
        qapp.processEvents()
        assert widget.hasFocus() or widget.isAncestorOf(qapp.focusWidget()), widget.objectName()


def test_recovery_blocked_disables_all_document_write_buttons(qapp, desktop):
    """Corrupt authoritative journal evidence must disable every exposed write."""
    win, ctl, home, doc = desktop
    entry = sorted((home / ".revisionworx" / "journal").glob("*.json"))[-1]
    original = entry.read_bytes()
    altered = json.loads(original)
    altered["op"] = str(altered["op"]) + "-tampered"
    entry.write_text(json.dumps(altered))
    try:
        win.refresh_all()
        qapp.processEvents()
        state = win.observable_state()
        assert state["read_only"] is True
        assert "RECOVERY BLOCKED" in state["health_banner"]
        assert state["selected_document"] == doc["id"]
        for name in ("new_project", "import", "register", "checkout",
                     "checkin", "cancel_checkout", "change_state", "restore"):
            assert not state["actions"][name], name
        for button in (win.documents_tab.import_btn,
                       win.documents_tab.register_btn,
                       win.documents_tab.checkout_btn,
                       win.documents_tab.checkin_btn,
                       win.documents_tab.cancel_btn,
                       win.documents_tab.transition_btn,
                       win.documents_tab.restore_btn):
            assert not button.isEnabled(), button.objectName()
    finally:
        entry.write_bytes(original)
    win.refresh_all()
    qapp.processEvents()
    assert win.observable_state()["read_only"] is False
    assert win.observable_state()["actions"]["checkout"] is True
