# SPDX-License-Identifier: GPL-3.0-or-later
"""Documents tab: the document list beside the selected document's panel. Binds DocumentListModel /
DocumentPanel; no business logic.

Layout (docs/UI_REDESIGN_PROPOSAL.md): filter and quick-filter chips over the list; the panel says in one
card what is true and what to do next, and offers one primary action (Check out or Check in) with the rest
secondary. Widget names, shortcuts and the Tab order are part of the tested contract (tests/test_qt_*)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QStyle, QTableView, QVBoxLayout, QWidget)

from ..controller import Controller, SavedWithWarning, describe_error
from ..viewmodels import DocumentListModel, DocumentPanel
from . import theme
from .dialogs import CheckinDialog, ImportDialog
from .models import EnterTable, ViewModelTable, elastic

# Status line tone per panel severity (the colours themselves come from the active theme).
SEVERITY_TONE = {"ok": "", "warning": "warn", "error": "danger"}


def show_at_least_rows(view: QTableView, rows: int) -> None:
    """Keep a table big enough to show ``rows`` rows, whatever the font and platform style."""
    frame = view.frameWidth() * 2
    bar = view.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
    view.setMinimumHeight(view.verticalHeader().defaultSectionSize() * rows
                          + view.horizontalHeader().sizeHint().height() + frame + bar)


def _row(*widgets, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(8)
    for i, w in enumerate(widgets):
        if stretch_at == i:
            lay.addStretch(1)
        lay.addWidget(w)
    if stretch_at is not None and stretch_at >= len(widgets):
        lay.addStretch(1)
    return lay


class DocumentsTab(QWidget):
    def __init__(self, ctl: Controller, configure_table, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.list_vm = DocumentListModel(ctl)
        self.panel = DocumentPanel(ctl)
        self.dialogs: list = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(8)
        top = QHBoxLayout()
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter documents…  (Ctrl+Shift+F)")
        self.filter.setAccessibleName("Filter documents")
        self.filter.setClearButtonEnabled(True)
        self.filter.returnPressed.connect(lambda *_: self.apply_filter())
        self.import_btn = QPushButton("&Import file…", self)
        self.import_btn.setToolTip("Copy a file into this project and record it as the first revision (Ctrl+I). "
                                   "You can also drop files on the window.")
        self.import_btn.clicked.connect(lambda *_: self.import_file())
        theme.set_flag(self.import_btn, "primary", True)
        self.register_btn = QPushButton("&Register file…", self)
        self.register_btn.setToolTip("Register a file that is already inside the project folder (it is not moved)")
        self.register_btn.clicked.connect(lambda *_: self.register_file())
        top.addWidget(self.filter, 1)
        top.addWidget(self.import_btn)
        top.addWidget(self.register_btn)
        outer.addLayout(top)

        chips = QHBoxLayout()
        chips.setSpacing(4)
        self.chips: dict[str, QPushButton] = {}
        for key, title in DocumentListModel.QUICK_FILTERS:
            b = QPushButton(title, self)
            b.setCheckable(True)
            b.setChecked(key == "all")
            theme.set_flag(b, "chip", True)
            b.setAccessibleName(f"Show: {title}")
            b.clicked.connect(lambda *_, k=key: self.set_quick(k))
            b.setObjectName(f"documents_chip_{key}")
            self.chips[key] = b
            chips.addWidget(b)
        chips.addStretch(1)
        outer.addLayout(chips)
        self.notice = elastic(QLabel(self))
        theme.set_flag(self.notice, "role", "muted")
        outer.addWidget(self.notice)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setChildrenCollapsible(False)
        self.table = EnterTable(split)
        self.table.setAccessibleName("Documents")
        self.table.setMinimumWidth(200)
        self.table_model = ViewModelTable(DocumentListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self, severity=self.list_vm.row_severity,
                                          chip_column=[c.key for c in DocumentListModel.COLUMNS].index("latest_state"),
                                          chip_tone=lambda r: theme.STATE_TONES.get(self.list_vm.row_state(r), "neutral"))
        self.table.setModel(self.table_model)
        configure_table(self.table, DocumentListModel.COLUMNS)
        keys = [c.key for c in DocumentListModel.COLUMNS]
        for key in ("released_label", "rel_path"):            # shown in the panel; keeps the list scannable
            self.table.setColumnHidden(keys.index(key), True)
        show_at_least_rows(self.table, 3)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed())
        self.table.doubleClicked.connect(lambda _: self.open_file())
        self.table.enter_pressed.connect(self.open_file)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)

        detail = QWidget(split)
        detail.setObjectName("documentPanel")
        detail.setMinimumWidth(330)
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(16, 4, 4, 0)
        dl.setSpacing(8)
        self.doc_title = elastic(QLabel(detail))
        self.doc_title.setObjectName("documentTitle")
        self.doc_title.setTextFormat(Qt.TextFormat.PlainText)
        dl.addWidget(self.doc_title)
        self.summary = elastic(QLabel(detail))
        self.summary.setTextFormat(Qt.TextFormat.PlainText)  # file names are data, not markup
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        theme.set_flag(self.summary, "role", "muted")
        dl.addWidget(self.summary)
        self.card = elastic(QLabel(detail))
        self.card.setObjectName("statusCard")
        self.card.setTextFormat(Qt.TextFormat.RichText)
        self.card.setAccessibleName("Document status")
        dl.addWidget(self.card)
        self.ownership = elastic(QLabel(detail))
        self.ownership.setObjectName("ownershipLabel")
        theme.set_flag(self.ownership, "role", "muted")
        dl.addWidget(self.ownership)
        self.status = elastic(QLabel(detail))
        self.status.setObjectName("documentStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.status)
        self.checkout_btn = QPushButton("Check &out", detail)
        self.checkout_btn.setToolTip("Reserve this document for editing (Ctrl+K)")
        self.checkin_btn = QPushButton("Chec&k in…", detail)
        self.checkin_btn.setToolTip("Record your edits as a new revision (Ctrl+Shift+K)")
        self.cancel_btn = QPushButton("&Cancel checkout…", detail)
        self.open_file_btn = QPushButton("Open &file", detail)
        self.open_file_btn.setToolTip("Open the working file in its program (Enter or double-click)")
        self.open_dir_btn = QPushButton("Show in fo&lder", detail)
        self.transition_box = QComboBox(detail)
        self.transition_box.setAccessibleName("Next lifecycle state")
        self.transition_btn = QPushButton("Change &state", detail)
        self.export_btn = QPushButton("&Export revision…", detail)
        self.export_btn.setToolTip("Save a copy of the selected revision anywhere (a copy is never managed)")
        dl.addLayout(_row(self.checkout_btn, self.checkin_btn, self.cancel_btn, stretch_at=3))
        dl.addLayout(_row(self.open_file_btn, self.open_dir_btn, self.export_btn, stretch_at=3))
        state_row = _row(self.transition_box, self.transition_btn)
        state_row.setStretch(0, 1)
        dl.addLayout(state_row)
        self.checkout_btn.clicked.connect(lambda *_: self.checkout())
        self.checkin_btn.clicked.connect(lambda *_: self.checkin())
        self.cancel_btn.clicked.connect(lambda *_: self.cancel())
        self.open_file_btn.clicked.connect(lambda *_: self.open_file())
        self.open_dir_btn.clicked.connect(lambda *_: self.open_folder())
        self.transition_btn.clicked.connect(lambda *_: self.change_state())
        self.export_btn.clicked.connect(lambda *_: self.export())

        hist_title = QLabel("History", detail)
        theme.set_flag(hist_title, "role", "section")
        dl.addWidget(hist_title)
        self.compare_btn = QPushButton("Co&mpare with previous", detail)
        self.compare_btn.setToolTip("Metadata comparison of the selected revision and the one before it")
        self.restore_btn = QPushButton("Res&tore to working file…", detail)
        self.restore_btn.setToolTip("While checked out: replace the working file with the selected older "
                                    "revision. History is never rewritten.")
        dl.addLayout(_row(self.compare_btn, self.restore_btn, stretch_at=2))
        self.compare_btn.clicked.connect(lambda *_: self.compare())
        self.restore_btn.clicked.connect(lambda *_: self.restore())
        hist_split = QSplitter(Qt.Orientation.Vertical, detail)
        hist_split.setChildrenCollapsible(False)
        self.history = QTableView(hist_split)
        self.history.setAccessibleName("Revision history")
        self.history_model = ViewModelTable(DocumentPanel.HISTORY_COLUMNS, lambda: len(self.panel.history),
                                            self.panel.history_cell, self)
        self.history.setModel(self.history_model)
        configure_table(self.history, DocumentPanel.HISTORY_COLUMNS)
        show_at_least_rows(self.history, 4)
        self.history.selectionModel().selectionChanged.connect(lambda *_: self.revision_selected())
        self.revision_detail = QPlainTextEdit(hist_split)
        self.revision_detail.setReadOnly(True)
        self.revision_detail.setAccessibleName("Selected revision")
        self.revision_detail.setPlaceholderText("Select a revision to see its full description and state changes.")
        hist_split.setStretchFactor(0, 3)
        hist_split.setStretchFactor(1, 1)
        self.revision_detail.setMinimumHeight(self.revision_detail.fontMetrics().lineSpacing() * 3 + 12)
        dl.addWidget(hist_split, 1)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([620, 380])
        self.split = split
        outer.addWidget(split, 1)
        for name in ("filter", "import_btn", "register_btn", "notice", "table", "checkout_btn",
                     "checkin_btn", "cancel_btn", "open_file_btn", "open_dir_btn", "transition_box",
                     "transition_btn", "export_btn", "compare_btn", "restore_btn", "history",
                     "revision_detail"):
            getattr(self, name).setObjectName("documents_" + name)

        for keys, slot in (("Ctrl+I", self.import_file), ("Ctrl+K", self.checkout),
                           ("Ctrl+Shift+K", self.checkin), ("Ctrl+Shift+F", self.filter.setFocus),
                           ("Ctrl+Shift+C", self.copy_path)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

        self.list_vm.subscribe(self.render_list)
        self.panel.subscribe(self.render_panel)
        self.render_list()
        self.render_panel()

    # ------------------------------------------------------------------ binding
    def load_project(self, project_id: str | None) -> None:
        """Show a project's documents. Reloading the SAME project keeps the selected document;
        switching projects clears it."""
        same = bool(project_id) and project_id == self.list_vm.project_id
        keep = (self.panel.doc_id or self.panel.failed_id) if same else None
        if not same:
            self.panel.load(None)
            self.table.clearSelection()
        self.list_vm.load(project_id)
        if not keep and self.list_vm.rows:  # predictable: the actions always have a target when there can be one
            keep = self.list_vm.row_id(0)
        if keep:
            self.select_document(keep)

    def apply_filter(self) -> None:
        self.list_vm.text = self.filter.text()
        self.list_vm.refresh()

    def set_quick(self, key: str) -> None:
        self.list_vm.set_quick(key)
        if self.list_vm.rows and not self.selected_doc_id():
            self.select_document(self.list_vm.row_id(0))

    def selected_doc_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        return self.list_vm.row_id(rows[0].row()) if rows and rows[0].row() < len(self.list_vm.rows) else None

    def selection_changed(self) -> None:
        self.panel.load_or_report(self.selected_doc_id())

    def select_document(self, doc_id: str) -> bool:
        """Select a document of the current project; shows it even if a quick filter hid it."""
        if self.list_vm.quick != "all" and not any(r["id"] == doc_id for r in self.list_vm.rows) and \
                any(r["id"] == doc_id for r in self.list_vm.all_rows):
            self.list_vm.set_quick("all")
        for i, r in enumerate(self.list_vm.rows):
            if r["id"] == doc_id:
                if self.selected_doc_id() == doc_id:  # no selectionChanged will fire
                    if self.panel.doc_id != doc_id:
                        self.panel.load_or_report(doc_id)
                else:
                    self.table.selectRow(i)
                self.table.scrollTo(self.table_model.index(i, 0))
                return True
        return False

    def render_list(self) -> None:
        keep = self.panel.doc_id or self.panel.failed_id
        self.table_model.reload()
        self.notice.setText(self.list_vm.notice)
        self.notice.setToolTip(self.list_vm.notice)
        self.notice.setVisible(bool(self.list_vm.notice))
        counts = self.list_vm.quick_counts
        for key, title in DocumentListModel.QUICK_FILTERS:
            chip = self.chips[key]
            chip.setText(f"{title}  {counts[key]}" if self.list_vm.project_id else title)
            chip.setChecked(self.list_vm.quick == key)
            chip.setEnabled(bool(self.list_vm.project_id))
        self.update_add_actions()
        if keep:
            if any(r["id"] == keep for r in self.list_vm.rows):
                self.select_document(keep)
            elif self.list_vm.error:  # the list could not be read, which says nothing about the document
                self.panel.report_unreadable(keep, self.list_vm.error)
            else:  # no longer listed: its actions must not stay available
                self.panel.load_or_report(None)

    def update_add_actions(self) -> None:
        self.import_btn.setEnabled(self.list_vm.can_add)
        self.register_btn.setEnabled(self.list_vm.can_add)

    def render_panel(self) -> None:
        p = self.panel
        doc = p.detail["document"] if p.detail else None
        self.doc_title.setText((doc.get("title") or Path(doc["rel_path"]).stem) if doc else "")
        self.doc_title.setToolTip(self.doc_title.text())
        self.doc_title.setVisible(bool(doc))
        self.summary.setText("  ·  ".join(f"{label}: {value}" for label, value in p.summary_fields))
        self.summary.setToolTip(self.summary.text())
        title, sentence, tone = p.headline
        if title:
            self.card.setText(f"<b>{_esc(title)}</b><br>{_esc(sentence)}")
            theme.set_tone(self.card, tone)
            self.card.setAccessibleDescription(f"{title}. {sentence}")
        self.card.setVisible(bool(title))
        self.ownership.setText(p.ownership_text)
        self.status.setText(p.message + ("\n" if p.message else "") + p.status_text)
        self.status.setToolTip(self.status.text())
        theme.set_tone(self.status, SEVERITY_TONE[p.severity])
        self.checkout_btn.setEnabled(p.can_checkout)
        self.checkin_btn.setEnabled(p.can_checkin)
        self.cancel_btn.setEnabled(p.can_cancel)
        primary = p.primary_action
        theme.set_flag(self.checkout_btn, "primary", primary == "checkout")
        theme.set_flag(self.checkin_btn, "primary", primary == "checkin")
        self.open_file_btn.setEnabled(p.can_open_file)
        self.open_dir_btn.setEnabled(bool(p.detail))
        self.transition_box.clear()
        for sid, name in p.transition_choices:
            self.transition_box.addItem(name, sid)
        self.transition_box.setEnabled(p.can_transition)
        self.transition_btn.setEnabled(p.can_transition)
        self.export_btn.setEnabled(bool(p.history) and not self.ctl.busy)
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

    # ------------------------------------------------------------------ context menu and clipboard
    def context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        p = self.panel
        menu = QMenu(self)
        entries = [("Open file", self.open_file, p.can_open_file), ("Show in folder", self.open_folder, bool(p.detail)),
                   None,
                   ("Check out", self.checkout, p.can_checkout), ("Check in…", self.checkin, p.can_checkin),
                   ("Cancel checkout…", self.cancel, p.can_cancel), None,
                   ("Export revision…", self.export, bool(p.history) and not self.ctl.busy), None,
                   ("Copy document number", self.copy_number, bool(p.detail and p.detail["document"].get("number"))),
                   ("Copy file path", self.copy_path, bool(p.detail))]
        for entry in entries:
            if entry is None:
                menu.addSeparator()
                continue
            text, slot, enabled = entry
            act = QAction(text, menu)
            act.setEnabled(bool(enabled))
            act.triggered.connect(lambda *_, s=slot: s())
            menu.addAction(act)
        menu.popup(self.table.viewport().mapToGlobal(pos))
        self._menu = menu

    def copy_path(self) -> None:
        if self.panel.detail:
            QGuiApplication.clipboard().setText(self.panel.working_path)
            self.panel.set_message("Copied the file path.")

    def copy_number(self) -> None:
        if self.panel.detail and self.panel.detail["document"].get("number"):
            QGuiApplication.clipboard().setText(self.panel.detail["document"]["number"])
            self.panel.set_message("Copied the document number.")

    # ------------------------------------------------------------------ actions
    def import_file(self, source: Path | None = None) -> None:
        if not self.list_vm.can_add:
            return
        if self.ctl.location is None or not self.list_vm.project_id:
            return
        name = (self.list_vm.project or {}).get("name", "project")

        def done(res):
            self.list_vm.refresh()
            if not res.error:
                self._select_added(res, dlg.flow.destination_preview)
        dlg = ImportDialog(self.ctl, self.list_vm.project_id, name, self, on_done=done)
        if source is not None:
            dlg.set_source(str(source))
        self.dialogs.append(dlg)
        dlg.finished.connect(lambda _: self.dialogs.remove(dlg) if dlg in self.dialogs else None)
        dlg.show()

    def _select_added(self, res, rel_path: str) -> None:
        """Select a document just added. A saved-with-warning result carries no record, so it is
        found by its path."""
        if res.value:
            self.select_document(res.value[0]["id"])
            return
        for row in self.list_vm.rows:
            if row["rel_path"] == rel_path:
                self.select_document(row["id"])
                return

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
            if res.error:
                return
            if res.warning:
                QMessageBox.warning(self, "Saved with a warning", res.warning)
            try:
                rel = Path(path).relative_to(folder).as_posix()
            except ValueError:
                rel = ""
            self._select_added(res, rel)
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
        if not self.panel.can_cancel:
            return
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
            self.panel.load_or_report(keep)

    def open_file(self) -> None:
        if self.panel.can_open_file:
            self._open_url(self.panel.working_path)

    def open_folder(self) -> None:
        if self.panel.detail:
            self._open_url(str(Path(self.panel.working_path).parent))

    def _open_url(self, path: str) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.panel.set_message(f"⚠ This computer could not open {path}. Is an application "
                                   "installed for this file type?", "warning")

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
        row = rows[0].row() if rows else 0
        rev = self.panel.history[row]
        doc = self.panel.detail["document"]
        suggested = f"{Path(doc['rel_path']).stem}_rev{rev['label']}{Path(doc['rel_path']).suffix}"
        dest, _ = QFileDialog.getSaveFileName(self, "Export revision", suggested)
        if dest:
            self.panel.start_export(row, dest)


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

