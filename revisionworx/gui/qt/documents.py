# SPDX-License-Identifier: GPL-3.0-or-later
"""Documents tab (Phase 2). Binds DocumentListModel / DocumentPanel; no business logic."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget)

from ..controller import Controller, describe_error
from ..viewmodels import DocumentListModel, DocumentPanel
from .models import ViewModelTable


class DocumentsTab(QWidget):
    def __init__(self, ctl: Controller, configure_table, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.list_vm = DocumentListModel(ctl)
        self.panel = DocumentPanel(ctl)
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter documents…")
        self.filter.returnPressed.connect(lambda *_: self.apply_filter())
        self.register_btn = QPushButton("Register file…", self)
        self.register_btn.clicked.connect(lambda *_: self.register_file())
        top.addWidget(self.filter, 1)
        top.addWidget(self.register_btn)
        outer.addLayout(top)

        split = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableView(split)
        self.table_model = ViewModelTable(DocumentListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self)
        self.table.setModel(self.table_model)
        configure_table(self.table, DocumentListModel.COLUMNS)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed())

        detail = QWidget(split)
        dl = QVBoxLayout(detail)
        self.status = QLabel(detail)
        self.status.setWordWrap(True)
        dl.addWidget(self.status)
        actions = QHBoxLayout()
        self.checkout_btn = QPushButton("Check out", detail)
        self.checkin_btn = QPushButton("Check in…", detail)
        self.cancel_btn = QPushButton("Cancel checkout…", detail)
        self.transition_box = QComboBox(detail)
        self.transition_btn = QPushButton("Change state", detail)
        self.export_btn = QPushButton("Export revision…", detail)
        for w in (self.checkout_btn, self.checkin_btn, self.cancel_btn, self.transition_box,
                  self.transition_btn, self.export_btn):
            actions.addWidget(w)
        actions.addStretch(1)
        dl.addLayout(actions)
        self.checkout_btn.clicked.connect(lambda *_: self.run(self.panel.checkout))
        self.checkin_btn.clicked.connect(lambda *_: self.checkin())
        self.cancel_btn.clicked.connect(lambda *_: self.cancel())
        self.transition_btn.clicked.connect(lambda *_: self.change_state())
        self.export_btn.clicked.connect(lambda *_: self.export())
        self.history = QTableView(detail)
        self.history_model = ViewModelTable(DocumentPanel.HISTORY_COLUMNS, lambda: len(self.panel.history),
                                            self.panel.history_cell, self)
        self.history.setModel(self.history_model)
        configure_table(self.history, DocumentPanel.HISTORY_COLUMNS)
        dl.addWidget(self.history, 1)
        outer.addWidget(split, 1)

        self.list_vm.subscribe(self.render_list)
        self.panel.subscribe(self.render_panel)
        self.render_list()
        self.render_panel()

    # ------------------------------------------------------------------ binding
    def load_project(self, project_id: str | None) -> None:
        self.list_vm.load(project_id)
        self.panel.load(None)

    def apply_filter(self) -> None:
        self.list_vm.text = self.filter.text()
        self.list_vm.refresh()

    def selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        self.run(lambda: self.panel.load(self.list_vm.row_id(rows[0].row()) if rows else None), refresh=False)

    def render_list(self) -> None:
        self.table_model.reload()
        self.register_btn.setEnabled(self.list_vm.project_id is not None)

    def render_panel(self) -> None:
        p = self.panel
        self.status.setText(p.message + ("\n" if p.message else "") + p.status_text)
        self.checkout_btn.setEnabled(p.can_checkout)
        self.checkin_btn.setEnabled(p.can_checkin)
        self.cancel_btn.setEnabled(p.can_cancel)
        self.transition_box.clear()
        for sid, name in p.transition_choices:
            self.transition_box.addItem(name, sid)
        self.transition_btn.setEnabled(bool(p.transition_choices))
        self.export_btn.setEnabled(bool(p.history))
        self.history_model.reload()

    def run(self, fn, refresh: bool = True) -> None:
        try:
            fn()
        except Exception as exc:
            QMessageBox.critical(self, "RevisionWorx", describe_error(exc))
        if refresh:
            self.list_vm.refresh()

    # ------------------------------------------------------------------ actions
    def register_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "File in this project to register (it is not moved)")
        if not path:
            return
        number, ok = QInputDialog.getText(self, "Document number", "Company document number (optional):")
        if not ok:
            return

        def done(res):
            if res.error:
                QMessageBox.critical(self, "Could not register", describe_error(res.error))
            self.list_vm.refresh()
        self.ctl.start_register(self.list_vm.project_id, Path(path), number=number.strip(), on_done=done)

    def checkin(self) -> None:
        message, ok = QInputDialog.getMultiLineText(self, "Check in", "Describe the change:")
        if ok:
            self.panel.start_checkin(message, on_done=lambda _res: self.list_vm.refresh())
            self.render_panel()

    def cancel(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Cancel checkout")
        box.setText("Release the checkout. Keep your edits in the working file, or discard them and "
                    "restore the latest revision?")
        keep = box.addButton("Keep my edits", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard edits", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() in (keep, discard):
            self.run(lambda: self.panel.cancel(revert=box.clickedButton() is discard))

    def change_state(self) -> None:
        state = self.transition_box.currentData()
        if not state:
            return
        comment, ok = QInputDialog.getText(self, "Change state", f"Comment for “{self.transition_box.currentText()}”:")
        if ok:
            self.run(lambda: self.panel.transition_latest(state, comment))

    def export(self) -> None:
        rows = self.history.selectionModel().selectedRows()
        rev = self.panel.history[rows[0].row()] if rows else self.panel.history[0]
        doc = self.panel.detail["document"]
        suggested = f"{Path(doc['rel_path']).stem}_rev{rev['label']}{Path(doc['rel_path']).suffix}"
        dest, _ = QFileDialog.getSaveFileName(self, "Export revision", suggested)
        if dest:
            self.run(lambda: self.ctl.export_revision(rev["id"], Path(dest)), refresh=False)
