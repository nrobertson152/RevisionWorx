# SPDX-License-Identifier: GPL-3.0-or-later
"""Documents tab. Binds DocumentListModel / DocumentPanel; no business logic."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QTableView, QVBoxLayout, QWidget)

from ..controller import Controller, SavedWithWarning, describe_error
from ..viewmodels import DocumentListModel, DocumentPanel
from .dialogs import CheckinDialog, ImportDialog
from .models import ViewModelTable

SEVERITY_STYLE = {"ok": "", "warning": "color: #8a5300", "error": "color: #b00020; font-weight: bold"}


class DocumentsTab(QWidget):
    def __init__(self, ctl: Controller, configure_table, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.list_vm = DocumentListModel(ctl)
        self.panel = DocumentPanel(ctl)
        self.dialogs: list = []
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter documents…")
        self.filter.setAccessibleName("Filter documents")
        self.filter.returnPressed.connect(lambda *_: self.apply_filter())
        self.import_btn = QPushButton("&Import file…", self)
        self.import_btn.setToolTip("Copy a file into this project and record it as revision 1 (Ctrl+I)")
        self.import_btn.clicked.connect(lambda *_: self.import_file())
        self.register_btn = QPushButton("&Register file…", self)
        self.register_btn.setToolTip("Register a file that is already inside the project folder")
        self.register_btn.clicked.connect(lambda *_: self.register_file())
        top.addWidget(self.filter, 1)
        top.addWidget(self.import_btn)
        top.addWidget(self.register_btn)
        outer.addLayout(top)
        self.notice = QLabel(self)
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)

        split = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableView(split)
        self.table.setAccessibleName("Documents")
        self.table_model = ViewModelTable(DocumentListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self, severity=self.list_vm.row_severity)
        self.table.setModel(self.table_model)
        configure_table(self.table, DocumentListModel.COLUMNS)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed())
        self.table.doubleClicked.connect(lambda _: self.open_file())

        detail = QWidget(split)
        dl = QVBoxLayout(detail)
        self.summary = QFormLayout()
        dl.addLayout(self.summary)
        self.ownership = QLabel(detail)
        self.ownership.setObjectName("ownershipLabel")
        self.ownership.setWordWrap(True)
        dl.addWidget(self.ownership)
        self.status = QLabel(detail)
        self.status.setObjectName("documentStatus")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.status)
        actions = QHBoxLayout()
        self.checkout_btn = QPushButton("Check &out", detail)
        self.checkout_btn.setToolTip("Reserve this document for editing (Ctrl+K)")
        self.checkin_btn = QPushButton("Check &in…", detail)
        self.checkin_btn.setToolTip("Record your edits as a new revision (Ctrl+Shift+K)")
        self.cancel_btn = QPushButton("&Cancel checkout…", detail)
        self.open_file_btn = QPushButton("Open &file", detail)
        self.open_dir_btn = QPushButton("Show in fo&lder", detail)
        self.transition_box = QComboBox(detail)
        self.transition_box.setAccessibleName("Next lifecycle state")
        self.transition_btn = QPushButton("Change &state", detail)
        self.export_btn = QPushButton("&Export revision…", detail)
        for w in (self.checkout_btn, self.checkin_btn, self.cancel_btn, self.open_file_btn,
                  self.open_dir_btn, self.transition_box, self.transition_btn, self.export_btn):
            actions.addWidget(w)
        actions.addStretch(1)
        dl.addLayout(actions)
        self.checkout_btn.clicked.connect(lambda *_: self.checkout())
        self.checkin_btn.clicked.connect(lambda *_: self.checkin())
        self.cancel_btn.clicked.connect(lambda *_: self.cancel())
        self.open_file_btn.clicked.connect(lambda *_: self.open_file())
        self.open_dir_btn.clicked.connect(lambda *_: self.open_folder())
        self.transition_btn.clicked.connect(lambda *_: self.change_state())
        self.export_btn.clicked.connect(lambda *_: self.export())

        hist_actions = QHBoxLayout()
        hist_actions.addWidget(QLabel("Revision history", detail))
        hist_actions.addStretch(1)
        self.compare_btn = QPushButton("Compare with &previous", detail)
        self.compare_btn.setToolTip("Metadata comparison of the selected revision and the one before it")
        self.restore_btn = QPushButton("Restore to &working file…", detail)
        self.restore_btn.setToolTip("While checked out: replace the working file with the selected older "
                                    "revision. History is never rewritten.")
        hist_actions.addWidget(self.compare_btn)
        hist_actions.addWidget(self.restore_btn)
        dl.addLayout(hist_actions)
        self.compare_btn.clicked.connect(lambda *_: self.compare())
        self.restore_btn.clicked.connect(lambda *_: self.restore())
        hist_split = QSplitter(Qt.Orientation.Horizontal, detail)
        self.history = QTableView(hist_split)
        self.history.setAccessibleName("Revision history")
        self.history_model = ViewModelTable(DocumentPanel.HISTORY_COLUMNS, lambda: len(self.panel.history),
                                            self.panel.history_cell, self)
        self.history.setModel(self.history_model)
        configure_table(self.history, DocumentPanel.HISTORY_COLUMNS)
        self.history.selectionModel().selectionChanged.connect(lambda *_: self.revision_selected())
        self.revision_detail = QPlainTextEdit(hist_split)
        self.revision_detail.setReadOnly(True)
        self.revision_detail.setAccessibleName("Selected revision")
        self.revision_detail.setPlaceholderText("Select a revision to see its full description and state changes.")
        hist_split.setStretchFactor(0, 3)
        hist_split.setStretchFactor(1, 2)
        dl.addWidget(hist_split, 1)
        outer.addWidget(split, 1)
        for name in ("filter", "import_btn", "register_btn", "notice", "table", "checkout_btn",
                     "checkin_btn", "cancel_btn", "open_file_btn", "open_dir_btn", "transition_box",
                     "transition_btn", "export_btn", "compare_btn", "restore_btn", "history",
                     "revision_detail"):
            getattr(self, name).setObjectName("documents_" + name)

        for keys, slot in (("Ctrl+I", self.import_file), ("Ctrl+K", self.checkout),
                           ("Ctrl+Shift+K", self.checkin), ("Ctrl+Shift+F", self.filter.setFocus)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

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

    def selected_doc_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        return self.list_vm.row_id(rows[0].row()) if rows and rows[0].row() < len(self.list_vm.rows) else None

    def selection_changed(self) -> None:
        self.run(lambda: self.panel.load(self.selected_doc_id()), refresh=False)

    def select_document(self, doc_id: str) -> None:
        for i, r in enumerate(self.list_vm.rows):
            if r["id"] == doc_id:
                self.table.selectRow(i)
                return

    def render_list(self) -> None:
        keep = self.panel.doc_id
        self.table_model.reload()
        self.notice.setText(self.list_vm.notice)
        self.import_btn.setEnabled(self.list_vm.can_add)
        self.register_btn.setEnabled(self.list_vm.can_add)
        if keep:
            self.select_document(keep)

    def render_panel(self) -> None:
        p = self.panel
        while self.summary.rowCount():
            self.summary.removeRow(0)
        for label, value in p.summary_fields:
            v = QLabel(value)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.summary.addRow(label, v)
        self.ownership.setText(p.ownership_text)
        self.status.setText(p.message + ("\n" if p.message else "") + p.status_text)
        self.status.setStyleSheet(SEVERITY_STYLE[p.severity])
        self.checkout_btn.setEnabled(p.can_checkout)
        self.checkin_btn.setEnabled(p.can_checkin)
        self.cancel_btn.setEnabled(p.can_cancel)
        self.open_file_btn.setEnabled(p.can_open_file)
        self.open_dir_btn.setEnabled(bool(p.detail))
        self.transition_box.clear()
        for sid, name in p.transition_choices:
            self.transition_box.addItem(name, sid)
        self.transition_btn.setEnabled(bool(p.transition_choices))
        self.export_btn.setEnabled(bool(p.history))
        self.history_model.reload()
        self.revision_detail.setPlainText(p.revision_text(0) if p.history else "")
        self._history_buttons()

    def selected_history_row(self) -> int | None:
        rows = self.history.selectionModel().selectedRows()
        return rows[0].row() if rows and rows[0].row() < len(self.panel.history) else None

    def _history_buttons(self) -> None:
        row = self.selected_history_row()
        self.compare_btn.setEnabled(self.panel.can_compare(0 if row is None else row))
        self.restore_btn.setEnabled(self.panel.can_restore(row))

    def revision_selected(self) -> None:
        row = self.selected_history_row()
        if row is not None:
            self.revision_detail.setPlainText(self.panel.revision_text(row))
        self._history_buttons()

    def compare(self) -> None:
        row = self.selected_history_row()
        row = 0 if row is None else row
        if self.panel.can_compare(row):
            self.run(lambda: self.revision_detail.setPlainText(self.panel.compare_text(row)), refresh=False)

    def restore(self, confirmed: bool = False) -> None:
        row = self.selected_history_row()
        if not self.panel.can_restore(row):
            return
        label = self.panel.history[row]["label"]
        if not confirmed:
            answer = QMessageBox.question(
                self, "Restore earlier revision",
                f"Replace the working file with revision {label}?\n\nAny unsaved or un-checked-in edits in "
                "the working file will be overwritten. Revision history is not changed.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.run(lambda: self.panel.restore(row))

    def run(self, fn, refresh: bool = True) -> bool:
        """Run a quick action. A saved-with-warning outcome is a success shown as a warning, never
        as an error (CORE_API §4: CommittedNotApplied means the change IS saved)."""
        from ...workspace import CommittedNotApplied
        try:
            fn()
        except (SavedWithWarning, CommittedNotApplied) as exc:
            QMessageBox.warning(self, "Saved with a warning", describe_error(exc))
            ok = True
        except Exception as exc:
            QMessageBox.critical(self, "RevisionWorx", describe_error(exc))
            ok = False
        else:
            ok = True
        if refresh:
            self.list_vm.refresh()
        return ok

    # ------------------------------------------------------------------ actions
    def import_file(self) -> None:
        if not self.list_vm.can_add:
            return
        if self.ctl.location is None or not self.list_vm.project_id:
            return
        name = (self.list_vm.project or {}).get("name", "project")

        def done(res):
            self.list_vm.refresh()
            if not res.error:
                self.select_document(res.value[0]["id"])
        dlg = ImportDialog(self.ctl, self.list_vm.project_id, name, self, on_done=done)
        self.dialogs.append(dlg)
        dlg.finished.connect(lambda _: self.dialogs.remove(dlg) if dlg in self.dialogs else None)
        dlg.show()

    def register_file(self) -> None:
        folder = (self.list_vm.project or {}).get("folder", "")
        path, _ = QFileDialog.getOpenFileName(self, "File in this project to register (it is not moved)", folder)
        if not path:
            return
        number, ok = QInputDialog.getText(self, "Document number", "Company document number (optional):")
        if not ok:
            return

        def done(res):
            if res.error:
                QMessageBox.critical(self, "Could not register", describe_error(res.error))
            self.list_vm.refresh()
        if not self.ctl.start_register(self.list_vm.project_id, Path(path), number=number.strip(), on_done=done):
            QMessageBox.information(self, "Busy", "Another task is still running; try again in a moment.")

    def checkout(self) -> None:
        if self.panel.can_checkout:
            self.run(self.panel.checkout)

    def checkin(self) -> None:
        if not self.panel.can_checkin:
            return
        title = self.panel.detail["document"].get("title", "")
        dlg = CheckinDialog(title, self)
        if dlg.exec():
            f = dlg.form
            self.panel.start_checkin(f.description, on_done=lambda _res: self.list_vm.refresh(),
                                     allow_unchanged=f.allow_unchanged, label=f.label.strip() or None)

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

    def refresh(self) -> None:
        """Reload the list and the selected document, keeping the selection (F5)."""
        keep = self.panel.doc_id
        self.list_vm.refresh()
        if keep and self.selected_doc_id() == keep:
            self.run(self.panel.reload, refresh=False)

    def open_file(self) -> None:
        if self.panel.can_open_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.panel.working_path))

    def open_folder(self) -> None:
        if self.panel.detail:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.panel.working_path).parent)))

    def change_state(self) -> None:
        state = self.transition_box.currentData()
        if not state:
            return
        comment, ok = QInputDialog.getText(self, "Change state", f"Comment for “{self.transition_box.currentText()}”:")
        if ok:
            self.run(lambda: self.panel.transition_latest(state, comment))

    def export(self) -> None:
        if not self.panel.history:
            return
        rows = self.history.selectionModel().selectedRows()
        rev = self.panel.history[rows[0].row()] if rows else self.panel.history[0]
        doc = self.panel.detail["document"]
        suggested = f"{Path(doc['rel_path']).stem}_rev{rev['label']}{Path(doc['rel_path']).suffix}"
        dest, _ = QFileDialog.getSaveFileName(self, "Export revision", suggested)
        if dest:
            self.run(lambda: self.ctl.export_revision(rev["id"], Path(dest)), refresh=False)
