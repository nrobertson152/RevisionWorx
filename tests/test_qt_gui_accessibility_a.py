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


def test_checkin_dialog_tab_shift_tab_and_escape_do_not_commit(qapp, desktop):
    """Real Qt keystrokes traverse the form and Escape cancels without a write."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import CheckinDialog

    win, ctl, _home, doc = desktop
    win.show()
    qapp.processEvents()
    win.documents_tab.checkout()
    before = len(win.observable_state()["history"])
    dialog = CheckinDialog("drawing", win)
    dialog.show()
    dialog.activateWindow()
    dialog.description.setFocus()
    qapp.processEvents()
    assert dialog.description.hasFocus()
    QTest.keyClick(dialog.description, Qt.Key.Key_Tab)
    qapp.processEvents()
    assert dialog.label.hasFocus(), "Tab must leave multiline description for revision label"
    QTest.keyClick(dialog.label, Qt.Key.Key_Backtab)
    qapp.processEvents()
    assert dialog.description.hasFocus(), "Shift+Tab must return to change description"
    QTest.keyClick(dialog.description, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
    assert not dialog.isVisible()
    assert len(win.observable_state()["history"]) == before
    assert win.documents_tab.checkin_btn.isEnabled(), "Cancellation must retain checkout"



def test_checkin_cancel_restores_parent_focus_without_writing(qapp, desktop):
    """Closing a modal form must not strand focus or mutate the revision chain."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import CheckinDialog

    win, _ctl, _home, _doc = desktop
    win.show()
    qapp.processEvents()
    tab = win.documents_tab
    tab.checkout()
    qapp.processEvents()
    before = tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"])
    tab.checkin_btn.setFocus()
    qapp.processEvents()
    dialog = CheckinDialog("drawing", win)
    dialog.setModal(True)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    assert dialog.description.hasFocus()
    QTest.keyClick(dialog.description, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
    assert not dialog.isVisible()
    assert win.isEnabled()
    assert tab.checkin_btn.isEnabled()
    # The exact focused control is window-manager dependent. A valid focus
    # target must be in the main window, never the destroyed modal.
    focus = qapp.focusWidget()
    assert focus is None or focus is win or win.isAncestorOf(focus)
    after = tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"])
    assert after == before


def test_checkin_dialog_large_font_keeps_controls_inside_layout(qapp):
    """Simulate large text independently of host DPI and check widget clipping."""
    from PySide6.QtGui import QFont
    from revisionworx.gui.qt.dialogs import CheckinDialog

    dialog = CheckinDialog("Long engineering drawing title")
    font = QFont(dialog.font())
    font.setPointSize(max(18, font.pointSize() * 2))
    dialog.setFont(font)
    dialog.show()
    qapp.processEvents()
    try:
        # This is font scaling, not a native 150/200% Windows DPI run.
        for widget in (dialog.description, dialog.label, dialog.unchanged):
            assert widget.isVisible()
            rect = widget.geometry()
            assert rect.width() > 0 and rect.height() > 0
            # mapTo expects widget-local coordinates, not parent-relative geometry.
            assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().topLeft()))
            assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    finally:
        dialog.reject()



def test_repeated_checkin_escape_preserves_checkout_and_revision_chain(qapp, desktop):
    """Repeated cancellation cannot commit or strand the user in a disabled UI."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import CheckinDialog

    win, _ctl, _home, _doc = desktop
    win.show()
    qapp.processEvents()
    tab = win.documents_tab
    tab.checkout()
    qapp.processEvents()
    before = tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"])
    assert tab.checkin_btn.isEnabled()

    for attempt in range(3):
        dialog = CheckinDialog(f"Repeated cancellation {attempt}", win)
        dialog.setModal(True)
        dialog.show()
        dialog.activateWindow()
        dialog.description.setFocus()
        qapp.processEvents()
        assert dialog.description.hasFocus()
        QTest.keyClick(dialog.description, Qt.Key.Key_Escape)
        qapp.processEvents()
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
        assert not dialog.isVisible()
        assert win.isEnabled()
        assert tab.checkin_btn.isEnabled()
        assert tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"]) == before


def test_production_checkin_button_escape_keeps_checkout(qapp, desktop):
    """Exercise the real button/signal/modal path, not a standalone dialog."""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import CheckinDialog

    win, _ctl, home, _doc = desktop
    tab = win.documents_tab
    win.show()
    qapp.processEvents()
    tab.checkout()
    qapp.processEvents()
    before = tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"])
    journal = home / ".revisionworx" / "journal"
    journal_before = {p.name: p.read_bytes() for p in journal.glob("*.json")}
    assert tab.checkin_btn.isEnabled()

    observed = []

    def cancel_real_modal():
        modal = qapp.activeModalWidget()
        observed.append(isinstance(modal, CheckinDialog))
        if isinstance(modal, CheckinDialog):
            QTest.keyClick(modal.description, Qt.Key.Key_Escape)
        else:
            # Never leave a test hung if the production wiring opens another dialog.
            for widget in qapp.topLevelWidgets():
                if isinstance(widget, QtWidgets.QDialog) and widget.isVisible():
                    widget.reject()

    QTimer.singleShot(0, cancel_real_modal)
    QTest.mouseClick(tab.checkin_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert observed == [True], "The production check-in button must open CheckinDialog"
    assert tab.checkin_btn.isEnabled(), "Escape must retain the checkout"
    assert tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"]) == before
    assert {p.name: p.read_bytes() for p in journal.glob("*.json")} == journal_before



def test_escape_cancel_survives_controller_restart_without_cad_mutation(qapp, desktop, tmp_path):
    """A cancelled production modal must not create a revision across restart.

    This covers a fresh controller and window, not merely a widget refresh.
    It does not establish recovery after an actual process crash.
    """
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import CheckinDialog

    win, _ctl, home, doc = desktop
    tab = win.documents_tab
    source = tmp_path / "drawing.ipt"
    source_before = source.read_bytes()
    journal = home / ".revisionworx" / "journal"
    journal_before = {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()}

    win.show()
    qapp.processEvents()
    tab.checkout()
    qapp.processEvents()
    # Checkout itself may legitimately add journal evidence. Pin the state
    # immediately before cancellation, not before checkout.
    journal_at_checkout = {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()}
    history_before = tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"])
    assert tab.checkin_btn.isEnabled()
    # Pin the managed content tree as well as the external CAD source. A
    # cancelled dialog must not replace immutable revision objects on disk.
    managed = home / ".revisionworx" / "content" / "sha256"
    assert managed.is_dir(), "Fixture must have a managed content store"
    objects_at_checkout = {
        str(p.relative_to(managed)): p.read_bytes()
        for p in managed.rglob("*") if p.is_file()
    } if managed.exists() else {}

    seen = []

    def escape_modal():
        modal = qapp.activeModalWidget()
        seen.append(isinstance(modal, CheckinDialog))
        if isinstance(modal, CheckinDialog):
            QTest.keyClick(modal.description, Qt.Key.Key_Escape)
        else:
            for widget in qapp.topLevelWidgets():
                if isinstance(widget, QtWidgets.QDialog) and widget.isVisible():
                    widget.reject()

    QTimer.singleShot(0, escape_modal)
    QTest.mouseClick(tab.checkin_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert seen == [True]
    assert source.read_bytes() == source_before
    assert {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()} == journal_at_checkout
    assert tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"]) == history_before

    # A rejected check-in must preserve every journal file, including any
    # future non-JSON evidence, rather than only the currently known suffix.
    assert {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()} == journal_at_checkout

    win.close()
    qapp.processEvents()
    restarted = Controller(cfg_dir=tmp_path / "cfg",
                           cache_dir=tmp_path / "cache",
                           state_dir=tmp_path / "state")
    reopened = MainWindow(restarted)
    try:
        assert reopened.open_location(home, quiet=True)
        reopened.documents_tab.select_document(doc["id"])
        qapp.processEvents()
        state = reopened.observable_state()
        assert tuple((r["id"], r["sha256"]) for r in state["history"]) == history_before
        assert state["selected_document"] == doc["id"]
        assert reopened.documents_tab.checkin_btn.isEnabled(), (
            "The pre-cancellation checkout must remain usable after reopening"
        )
        assert source.read_bytes() == source_before
        assert {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()} == journal_at_checkout
        assert ({str(p.relative_to(managed)): p.read_bytes()
                 for p in managed.rglob("*") if p.is_file()}
                if managed.exists() else {}) == objects_at_checkout
        # The initial import must remain represented in the journal.
        assert objects_at_checkout, "Imported revision must create managed content"
    assert set(journal_before).issubset(set(journal_at_checkout))
    finally:
        reopened.close()



def test_keyboard_space_opens_production_checkin_and_escape_preserves_checkout(qapp, desktop):
    """Keyboard activation must reach the real check-in handler without committing."""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import CheckinDialog

    win, _ctl, home, _doc = desktop
    tab = win.documents_tab
    win.show()
    qapp.processEvents()
    tab.checkout()
    qapp.processEvents()
    assert tab.checkin_btn.isEnabled()
    before = tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"])
    journal = home / ".revisionworx" / "journal"
    journal_before = {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()}
    managed = home / ".revisionworx" / "content" / "sha256"
    assert managed.is_dir(), "Fixture must have a managed content store"
    objects_before = {str(p.relative_to(managed)): p.read_bytes() for p in managed.rglob("*") if p.is_file()} if managed.exists() else {}
    assert objects_before, "Imported revision must create managed content"
    tab.checkin_btn.setFocus()
    qapp.processEvents()
    assert tab.checkin_btn.hasFocus(), "Check-in button must accept keyboard focus"

    seen = []

    def cancel_modal():
        modal = qapp.activeModalWidget()
        seen.append(isinstance(modal, CheckinDialog))
        if isinstance(modal, CheckinDialog):
            QTest.keyClick(modal.description, Qt.Key.Key_Escape)
        else:
            for widget in qapp.topLevelWidgets():
                if isinstance(widget, QtWidgets.QDialog) and widget.isVisible():
                    widget.reject()

    QTimer.singleShot(0, cancel_modal)
    QTest.keyClick(tab.checkin_btn, Qt.Key.Key_Space)
    qapp.processEvents()
    assert seen == [True], "Space on focused production button must open CheckinDialog"
    assert tab.checkin_btn.isEnabled(), "Escape must retain the checkout"
    assert tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"]) == before
    assert {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()} == journal_before
    assert ({str(p.relative_to(managed)): p.read_bytes() for p in managed.rglob("*") if p.is_file()} if managed.exists() else {}) == objects_before

    # A second keyboard activation must remain connected to the production
    # handler after the first modal is rejected. This catches accidental
    # one-shot signal disconnection and disabled-button regressions.
    seen.clear()
    # Offscreen Qt can leave no active window after a modal closes; reactivate
    # the existing parent before exercising the second keyboard activation.
    win.activateWindow()
    qapp.processEvents()
    tab.checkin_btn.setFocus()
    qapp.processEvents()
    focus = qapp.focusWidget()
    active = qapp.activeWindow()
    modal = qapp.activeModalWidget()
    assert tab.checkin_btn.hasFocus(), (
        "Second activation focus not restored after Escape: "
        f"focus={type(focus).__name__ if focus else None} "
        f"focus_name={focus.objectName() if focus else None!r} "
        f"active={type(active).__name__ if active else None} "
        f"active_name={active.objectName() if active else None!r} "
        f"modal={type(modal).__name__ if modal else None} "
        f"modal_visible={modal.isVisible() if modal else None} "
        f"window_active={win.isActiveWindow()} "
        f"button_visible={tab.checkin_btn.isVisible()} "
        f"button_enabled={tab.checkin_btn.isEnabled()} "
        f"button_focus_policy={tab.checkin_btn.focusPolicy()}"
    )
    QTimer.singleShot(0, cancel_modal)
    QTest.keyClick(tab.checkin_btn, Qt.Key.Key_Space)
    qapp.processEvents()
    assert seen == [True], "Repeated Space must reopen the production modal"
    assert tab.checkin_btn.isEnabled()
    assert tuple((r["id"], r["sha256"]) for r in win.observable_state()["history"]) == before
    assert {str(p.relative_to(journal)): p.read_bytes() for p in journal.rglob("*") if p.is_file()} == journal_before
    assert ({str(p.relative_to(managed)): p.read_bytes() for p in managed.rglob("*") if p.is_file()} if managed.exists() else {}) == objects_before
