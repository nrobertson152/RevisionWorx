#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental FROZEN GUI widget probe. Only disposable directories are accepted.

This module is bundled into a TEST-ONLY PyInstaller GUI wrapper. It exercises
real Qt widgets and real storage APIs, but does not demonstrate human-driven,
screen-visible or accessibility acceptance. Never ship this wrapper to users.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from revisionworx.gui.controller import Controller
from revisionworx.gui.qt.dialogs import CheckinDialog
from revisionworx.gui.qt.mainwindow import MainWindow

ORIGINAL = b"alpha frozen drawing version 1\n"
REVISED = b"alpha frozen drawing version 2\n"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def pump(app: QApplication, ctl: Controller, until, *, timeout: float = 35.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ctl.drain()
        app.processEvents()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for GUI import/check-in: " + repr(until))


def controller(root: Path) -> Controller:
    profile = root / "profile"
    return Controller(cfg_dir=profile / "config", cache_dir=profile / "cache",
                      state_dir=profile / "state")


def selected_project(window: MainWindow, app: QApplication) -> None:
    require(window.table_model.rowCount() == 1,
            f"GUI expected one project: {window.table_model.rowCount()}")
    window.table.selectRow(0)
    app.processEvents()
    require(bool(window.documents_tab.list_vm.project_id),
            "GUI project selection did not load the Documents tab")


def create(root: Path, app: QApplication) -> dict:
    ctl = controller(root)
    w = MainWindow(ctl)
    w.show()
    app.processEvents()
    require("single user" in w.alpha_banner.text().lower(), "alpha warning missing")
    require(w.stack.currentWidget() is w.start_page, "new profile not on start page")

    ws = root / "Projects with spaces Ω"
    sp = w.start_page
    sp.folder_edit.setText(str(ws))
    sp.name_edit.setText("Alpha Bracket")
    sp.number_edit.setText("A-001")
    sp.create_btn.click()
    app.processEvents()
    require(w.stack.currentWidget() is w.workspace_page,
            "Create project did not open workspace: " + sp.error_label.text())
    selected_project(w, app)

    fixture = root / "external source Ω.txt"
    fixture.write_bytes(ORIGINAL)
    tab = w.documents_tab
    require(tab.import_btn.isEnabled(), "Import file not enabled")
    tab.import_btn.click()
    require(bool(tab.dialogs), "Import button did not create a dialog")
    dlg = tab.dialogs[-1]
    dlg.source.setText(str(fixture))
    dlg.description.setText("Initial engineering revision")
    require(dlg.import_btn.isEnabled(), "Valid import was not enabled")
    dlg.import_btn.click()
    pump(app, ctl, lambda: dlg.flow.state in ("done", "error"))
    require(dlg.flow.state == "done", "Import failed: " + dlg.flow.message)
    require(tab.table_model.rowCount() == 1, "Imported document missing from GUI")
    tab.table.selectRow(0)
    app.processEvents()
    require(tab.history_model.rowCount() == 1, "First revision not displayed")

    doc_id = tab.selected_doc_id()
    require(bool(doc_id), "GUI has no selected document")
    require(tab.checkout_btn.isEnabled(), "Checkout not enabled")
    tab.checkout_btn.click()
    app.processEvents()
    require(tab.checkin_btn.isEnabled(), "Checkout did not enable check-in")

    working_file = Path(tab.panel.working_path)
    require(working_file.is_file(), "Checked-out working file absent")
    working_file.write_bytes(REVISED)

    # QDialog.exec() runs a real nested Qt event loop. Populate its visible
    # validation form through a queued callback instead of mocking controller
    # commits or bypassing the GUI check-in action.
    filled = []
    def submit_visible_checkin() -> None:
        dialog = app.activeModalWidget()
        if isinstance(dialog, CheckinDialog):
            dialog.description.setPlainText("Adjusted bracket geometry after review")
            dialog.submit()
            filled.append(True)
        else:
            QTimer.singleShot(50, submit_visible_checkin)

    QTimer.singleShot(75, submit_visible_checkin)
    tab.checkin_btn.click()
    require(bool(filled), "GUI check-in dialog was not submitted")
    pump(app, ctl, lambda: tab.history_model.rowCount() == 2)
    require(not tab.checkin_btn.isEnabled(), "Checkout not cleared after check-in")
    require(fixture.read_bytes() == ORIGINAL, "Import modified external source file")

    history = list(tab.panel.history)
    actual_hashes = {row["sha256"] for row in history}
    expected_hashes = {hashlib.sha256(blob).hexdigest() for blob in (ORIGINAL, REVISED)}
    require(actual_hashes == expected_hashes, "GUI showed unexpected revision hashes")
    result = {
        "phase": "create",
        "workspace": str(ws),
        "document_id": doc_id,
        "project_id": tab.list_vm.project_id,
        "revision_ids": sorted(row["id"] for row in history),
        "revision_hashes": sorted(actual_hashes),
        "history_rows": tab.history_model.rowCount(),
        "gui_alpha_banner": True,
        "gui_import": "passed",
        "gui_checkout_checkin": "passed",
        "gui_create_open": "passed",
        "qt_mode": "offscreen automation; no human interactive test",
    }
    w.close()
    app.processEvents()
    return result


def reopen(root: Path, app: QApplication) -> dict:
    ctl = controller(root)
    w = MainWindow(ctl)
    w.show()
    app.processEvents()
    ws = root / "Projects with spaces Ω"
    require(w.open_location(ws, quiet=True),
            "GUI could not reopen workspace: " + w.start_page.error_label.text())
    selected_project(w, app)
    tab = w.documents_tab
    require(tab.table_model.rowCount() == 1, "Reopened GUI lost imported document")
    tab.table.selectRow(0)
    app.processEvents()
    require(tab.history_model.rowCount() == 2, "Reopened GUI lost revision history")
    require(tab.checkout_btn.isEnabled() and not tab.checkin_btn.isEnabled(),
            "Reopened GUI checkout state inconsistent")
    history = list(tab.panel.history)
    hashes = sorted(row["sha256"] for row in history)
    expected = sorted(hashlib.sha256(blob).hexdigest() for blob in (ORIGINAL, REVISED))
    require(hashes == expected, "Reopened GUI revision hashes differ")
    require("single user" in w.alpha_banner.text().lower(), "Reopened alpha banner missing")
    result = {
        "phase": "reopen",
        "workspace": str(ws),
        "document_id": tab.selected_doc_id(),
        "revision_ids": sorted(row["id"] for row in history),
        "revision_hashes": hashes,
        "history_rows": tab.history_model.rowCount(),
        "gui_reopen": "passed",
        "qt_mode": "offscreen automation; no human interactive test",
    }
    w.close()
    app.processEvents()
    return result


def main(args: list[str]) -> int:
    if len(args) != 3 or args[0] not in ("create", "reopen"):
        return 2
    phase, root_raw, report_raw = args
    root = Path(root_raw).resolve()
    report = Path(report_raw).resolve()
    # The invocation harness must create this isolated root first. Refuse
    # arbitrary existing user workspaces and in-tree report destinations.
    if not root.is_dir() or report.parent != root or not root.name.startswith("rw-alpha-fixture-"):
        return 2
    if phase == "create" and (root / "Projects with spaces Ω").exists():
        return 2
    app = QApplication.instance() or QApplication(["RevisionWorxAlphaProbe"])
    try:
        payload = create(root, app) if phase == "create" else reopen(root, app)
        report.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return 0
    except BaseException as exc:
        report.write_text(json.dumps({
            "phase": phase, "error": repr(exc), "traceback": traceback.format_exc()
        }, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
