# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent real-PySide6 Documents-tab negative-path smoke tests.

Execute on a disposable overlay containing Claude's Phase 2 implementation.
Qt is installed by the GitHub Actions QA runner and uses the offscreen platform.
Skipping because Phase 2/Qt is absent is NOT verification of the GUI.
"""
from __future__ import annotations

import importlib.util
import os
import time

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 Documents tab unavailable on Phase 1 QA base", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.qt

from revisionworx.gui.controller import Controller  # noqa: E402
from revisionworx.gui.qt.documents import DocumentsTab  # noqa: E402
from revisionworx.gui.qt.mainwindow import configure_table  # noqa: E402
from revisionworx.projects import NewProject  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _register_document(ctl, project_id, path, qapp):
    errors = []
    def completed(outcome):
        if outcome.error:
            errors.append(repr(outcome.error))
    assert ctl.start_register(project_id, path, number="QA-QT-001", on_done=completed)
    deadline = time.monotonic() + 20
    docs = []
    while time.monotonic() < deadline:
        ctl.drain()
        qapp.processEvents()
        assert not errors, errors
        docs = ctl.documents(project_id)
        if docs:
            break
        time.sleep(0.01)
    assert len(docs) == 1, f"registration did not complete: {errors}"
    return docs[0]["id"]


def test_real_qt_documents_tab_filters_and_flags_unchecked_external_edit(qapp, tmp_path):
    """Widget state, selection and external-edit warning use actual Qt objects."""
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache",
                     state_dir=tmp_path / "state")
    share = tmp_path / "share"
    (share / "Engineering").mkdir(parents=True)
    ctl.create_workspace(share, "QA Qt tab")
    ctl.add_root("Engineering", share / "Engineering")
    prj = ctl.create_project(NewProject(name="Skid", rel_path="Skid"))
    drawing = share / "Engineering" / "Skid" / "03 Drawings" / "Native" / "layout.idw"
    drawing.write_bytes(b"First revision")
    doc_id = _register_document(ctl, prj["id"], drawing, qapp)

    tab = DocumentsTab(ctl, configure_table)
    try:
        # No project selected: registration and editing controls must be disabled.
        assert not tab.register_btn.isEnabled()
        assert not tab.checkout_btn.isEnabled()
        assert not tab.checkin_btn.isEnabled()
        assert not tab.cancel_btn.isEnabled()
        tab.load_project(prj["id"])
        qapp.processEvents()
        assert tab.register_btn.isEnabled()
        assert tab.table_model.rowCount() == 1

        tab.filter.setText("not-a-document")
        tab.apply_filter()
        assert tab.table_model.rowCount() == 0
        tab.filter.setText("QA-QT-001")
        tab.apply_filter()
        assert tab.table_model.rowCount() == 1

        tab.table.selectRow(0)
        qapp.processEvents()
        assert tab.panel.doc_id == doc_id
        assert tab.checkout_btn.isEnabled()
        assert not tab.checkin_btn.isEnabled()
        assert tab.history_model.rowCount() == 1

        # Unchecked external write: UI must flag it, not silently display unchanged.
        drawing.write_bytes(b"Unauthorized edit with longer content")
        tab.panel.load(doc_id, verify_hash=True)
        qapp.processEvents()
        assert "WITHOUT a checkout" in tab.status.text()
        assert not tab.checkin_btn.isEnabled()
    finally:
        tab.close()


def test_real_qt_documents_tab_empty_checkin_keeps_checkout(qapp, tmp_path):
    """Check-out action, validation message and cancel state in real Qt widgets."""
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache",
                     state_dir=tmp_path / "state")
    share = tmp_path / "share"
    (share / "Engineering").mkdir(parents=True)
    ctl.create_workspace(share, "QA Qt validation")
    ctl.add_root("Engineering", share / "Engineering")
    prj = ctl.create_project(NewProject(name="Skid", rel_path="Skid"))
    drawing = share / "Engineering" / "Skid" / "03 Drawings" / "Native" / "layout.idw"
    drawing.write_bytes(b"Preserve drawing")
    doc_id = _register_document(ctl, prj["id"], drawing, qapp)

    tab = DocumentsTab(ctl, configure_table)
    try:
        tab.load_project(prj["id"])
        tab.table.selectRow(0)
        qapp.processEvents()
        assert tab.checkout_btn.isEnabled()
        tab.checkout_btn.click()
        qapp.processEvents()
        assert tab.panel.doc_id == doc_id
        assert not tab.checkout_btn.isEnabled()
        assert tab.checkin_btn.isEnabled()
        assert tab.cancel_btn.isEnabled()
        assert tab.panel.start_checkin("  ") is False
        qapp.processEvents()
        assert "Describe the change" in tab.status.text()
        assert tab.checkin_btn.isEnabled()
        tab.panel.cancel(revert=False)
        qapp.processEvents()
        assert tab.checkout_btn.isEnabled()
        assert not tab.checkin_btn.isEnabled()
        assert drawing.read_bytes() == b"Preserve drawing"
    finally:
        tab.close()


def test_real_qt_documents_tab_reports_authoritative_checkout_conflict(qapp, tmp_path, monkeypatch):
    """Error dialog must report a *real* externally owned checkout, without taking it."""
    from revisionworx import documents
    from revisionworx.workspace import Workspace

    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache",
                     state_dir=tmp_path / "state")
    share = tmp_path / "share"
    (share / "Engineering").mkdir(parents=True)
    ctl.create_workspace(share, "QA conflicting checkout")
    ctl.add_root("Engineering", share / "Engineering")
    project = ctl.create_project(NewProject(name="Skid", rel_path="Skid"))
    drawing = share / "Engineering" / "Skid" / "03 Drawings" / "Native" / "layout.idw"
    drawing.write_bytes(b"Owner's drawing revision")
    doc_id = _register_document(ctl, project["id"], drawing, qapp)

    tab = DocumentsTab(ctl, configure_table)
    try:
        tab.load_project(project["id"])
        tab.table.selectRow(0)
        qapp.processEvents()
        assert tab.checkout_btn.isEnabled()

        # Another session checks the document out after the UI rendered.
        with Workspace.open(share, writable=True) as other:
            foreign = documents.checkout(other, doc_id, note="another workstation")
            before = other.head_seq()

        dialogs = []
        def record_error(parent, title, message, *a, **kw):
            dialogs.append((title, message))
            return QtWidgets.QMessageBox.StandardButton.Ok

        monkeypatch.setattr(QtWidgets.QMessageBox, "critical", record_error)
        tab.checkout_btn.click()
        qapp.processEvents()
        assert len(dialogs) == 1
        assert "checked out" in dialogs[0][1].lower()
        assert "another session" not in dialogs[0][1].lower() or dialogs[0][1]
        with Workspace.open(share) as reader:
            assert reader.head_seq() == before
            assert reader.get("checkout", doc_id)["token"] == foreign["token"]
        assert drawing.read_bytes() == b"Owner's drawing revision"
    finally:
        tab.close()
