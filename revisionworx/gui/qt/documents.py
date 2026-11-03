# SPDX-License-Identifier: GPL-3.0-or-later
"""Documents view: filters, the document list and the panel for the selected document.

Binds DocumentListModel / DocumentPanel; no business logic. One filled button at a time says what to do
next. Rarer actions sit behind More, and revision details open from the history. Hidden buttons keep the
launch contract's widget names working (docs/GUI_LAUNCH_CONTRACT.md)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QStackedWidget, QStyle, QTableView, QToolButton, QVBoxLayout, QWidget)

from ..controller import Controller, SavedWithWarning, describe_error
from ..viewmodels import DocumentListModel, DocumentPanel
from . import theme
from .delegates import GAP, NUMBER_W, PAD, REV_W, STATE_W, DocumentDelegate, HistoryDelegate, flat_list
from .dialogs import CheckinDialog, ImportDialog
from .models import EnterTable, ViewModelTable, elastic

STATUS_ICONS = {"neutral": "check", "info": "pencil", "ok": "check", "warn": "alert", "danger": "alert"}
TONE_FG = {"neutral": "ink", "info": "infoFg", "ok": "okFg", "warn": "warnFg", "danger": "dangerFg"}
# What moving to a state means to a person. A custom lifecycle's other states use their own name.
STATE_VERBS = {"in_review": "Send for review", "approved": "Approve", "released": "Release",
               "wip": "Send back to work", "obsolete": "Mark obsolete"}


def show_at_least_rows(view: QTableView, rows: int) -> None:
    """Keep a table big enough to show ``rows`` rows, whatever the font and platform style."""
    frame = view.frameWidth() * 2
    bar = view.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
    view.setMinimumHeight(view.verticalHeader().defaultSectionSize() * rows + frame + bar)


class DocumentsTab(QWidget):
    def __init__(self, ctl: Controller, configure_table, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.list_vm = DocumentListModel(ctl)
        self.panel = DocumentPanel(ctl)
        self.dialogs: list = []
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Add files sits in the window's header; Register is one action with it, kept for the contract.
        self.import_btn = QPushButton("&Add files", self)
        self.import_btn.setToolTip("Add files to this project (Ctrl+I). Originals are never moved or changed.")
        self.import_btn.clicked.connect(lambda *_: self.import_file())
        self.register_btn = QPushButton("&Register file…", self)
        self.register_btn.setVisible(False)
        self.register_btn.clicked.connect(lambda *_: self.register_file())

        top = QHBoxLayout()
        top.setContentsMargins(24, 0, 24, 12)
        top.setSpacing(4)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key in ("all", "mine", "attention"):
            btn = QPushButton(self)
            btn.setCheckable(True)
            btn.setProperty("role", "segment")
            btn.setObjectName("documents_filter_" + key)
            btn.clicked.connect(lambda *_, k=key: self.set_show(k))
            self.filter_group.addButton(btn)
            self.filter_buttons[key] = btn
            top.addWidget(btn)
        self.filter_buttons["all"].setChecked(True)
        top.addStretch(1)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter this project")
        self.filter.setAccessibleName("Filter documents")
        self.filter.setProperty("role", "quiet")
        self.filter.setMaximumWidth(240)
        self.filter.returnPressed.connect(lambda *_: self.apply_filter())
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self.apply_filter)
        self.filter.textChanged.connect(lambda *_: self._filter_timer.start())
        top.addWidget(self.filter, 1)
        self.filter_row = QWidget(self)
        self.filter_row.setLayout(top)
        outer.addWidget(self.filter_row)
        self.notice = elastic(QLabel(self))
        self.notice.setContentsMargins(24, 0, 24, 8)
        outer.addWidget(self.notice)
        rule = QFrame(self)
        rule.setObjectName("hairline")
        rule.setFixedHeight(1)
        outer.addWidget(rule)

        self.pages = QStackedWidget(self)
        outer.addWidget(self.pages, 1)
        split = QSplitter(Qt.Orientation.Horizontal, self.pages)
        split.setChildrenCollapsible(False)
        self.pages.addWidget(split)

        left = QWidget(split)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 8, 12, 8)
        ll.setSpacing(2)
        heads = QHBoxLayout()
        heads.setContentsMargins(PAD, 2, PAD, 4)
        heads.setSpacing(GAP)
        for text, width in (("Document", 0), ("Number", NUMBER_W), ("Rev", REV_W), ("State", STATE_W)):
            head = QLabel(text, left)
            head.setProperty("role", "columnHead")
            if width:
                head.setFixedWidth(width)
            heads.addWidget(head, 0 if width else 1)
        ll.addLayout(heads)
        self.table = EnterTable(left)
        self.table.setAccessibleName("Documents")
        self.table_model = ViewModelTable(DocumentListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self, severity=self.list_vm.row_severity)
        self.table.setModel(self.table_model)
        configure_table(self.table, DocumentListModel.COLUMNS)
        flat_list(self.table, DocumentListModel.COLUMNS, "title", DocumentDelegate.height, DocumentDelegate(self.list_vm, self))
        show_at_least_rows(self.table, 3)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed())
        self.table.doubleClicked.connect(lambda _: self.open_file())
        self.table.enter_pressed.connect(self.open_file)
        ll.addWidget(self.table, 1)
        self.no_match = QLabel(left)
        self.no_match.setProperty("role", "muted")
        self.no_match.setContentsMargins(PAD, 8, PAD, 8)
        self.no_match.hide()
        ll.addWidget(self.no_match)
        left.setMinimumWidth(420)

        self.detail_host = QWidget(split)
        self.detail_host.setMinimumWidth(320)
        host = QVBoxLayout(self.detail_host)
        host.setContentsMargins(20, 16, 20, 12)
        host.setSpacing(0)
        self.no_selection = QLabel("Select a document to see what you can do with it and its history.", self.detail_host)
        self.no_selection.setProperty("role", "muted")
        self.no_selection.setWordWrap(True)
        self.no_selection.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        host.addWidget(self.no_selection, 1)
        self.body = QWidget(self.detail_host)
        host.addWidget(self.body, 1)
        dl = QVBoxLayout(self.body)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.doc_title = elastic(QLabel(self.body))
        self.doc_title.setProperty("role", "h2")
        self.doc_title.setTextFormat(Qt.TextFormat.PlainText)
        self.summary = elastic(QLabel(self.body))
        self.summary.setProperty("role", "small")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)  # file names are data, not markup
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.doc_title)
        titles.addWidget(self.summary)
        dl.addLayout(titles)

        self.status_box = QFrame(self.body)
        self.status_box.setProperty("tone", "neutral")
        sl = QHBoxLayout(self.status_box)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(10)
        self.status_icon = QLabel(self.status_box)
        self.status_icon.setStyleSheet("background: transparent")
        self.status_icon.setFixedSize(18, 18)
        sl.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        sc = QVBoxLayout()
        sc.setSpacing(1)
        self.status_title = elastic(QLabel(self.status_box))
        self.status_title.setObjectName("documentStatusTitle")
        self.status_title.setProperty("tone", "neutral")
        self.status_title.setProperty("role", "statusTitle")
        self.status_title.setStyleSheet("background: transparent")
        self.status = elastic(QLabel(self.status_box))
        self.status.setObjectName("documentStatus")
        self.status.setStyleSheet("background: transparent; font-size: 13px")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sc.addWidget(self.status_title)
        sc.addWidget(self.status)
        sl.addLayout(sc, 1)
        dl.addWidget(self.status_box)
        self.ownership = QLabel(self.body)  # said by the status block; kept for the contract
        self.ownership.setObjectName("ownershipLabel")
        self.ownership.setVisible(False)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.checkout_btn = QPushButton("Check &out", self.body)
        self.checkout_btn.setToolTip("Reserve this document for editing (Ctrl+K)")
        self.checkin_btn = QPushButton("Chec&k in", self.body)
        self.checkin_btn.setToolTip("Record your edits as a new revision (Ctrl+Shift+K)")
        self.restore_file_btn = QPushButton("Restore &file", self.body)
        self.restore_file_btn.setToolTip("Put the latest revision back in the project folder")
        for btn in (self.checkout_btn, self.checkin_btn, self.restore_file_btn):
            btn.setProperty("role", "primary")
            actions.addWidget(btn, 1)
        self.open_file_btn = QPushButton("Open fi&le", self.body)
        self.open_file_btn.setToolTip("Open in its usual application")
        actions.addWidget(self.open_file_btn)
        self.more_btn = QToolButton(self.body)
        self.more_btn.setObjectName("documents_more_btn")
        self.more_btn.setProperty("role", "icon")
        self.more_btn.setAccessibleName("More actions")
        self.more_btn.setToolTip("More actions")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_btn)
        self.act_show_folder = QAction("Show in folder", self)
        self.act_export = QAction("Export a copy…", self)
        self.act_cancel = QAction("Cancel checkout…", self)
        self.more_menu.addActions([self.act_show_folder, self.act_export, self.act_cancel])
        self.more_btn.setMenu(self.more_menu)
        actions.addWidget(self.more_btn)
        dl.addLayout(actions)
        self.act_show_folder.triggered.connect(lambda *_: self.open_folder())
        self.act_export.triggered.connect(lambda *_: self.export())
        self.act_cancel.triggered.connect(lambda *_: self.cancel())

        # The contract's other buttons do the same jobs from the More menu and the history.
        self.cancel_btn = QPushButton("&Cancel checkout…", self.body)
        self.open_dir_btn = QPushButton("Show in folder", self.body)
        self.transition_box = QComboBox(self.body)
        self.transition_box.setAccessibleName("Next lifecycle state")
        self.transition_btn = QPushButton("Change &state", self.body)
        self.export_btn = QPushButton("&Export revision…", self.body)
        for w in (self.cancel_btn, self.open_dir_btn, self.transition_box, self.transition_btn, self.export_btn):
            w.setVisible(False)
        self.checkout_btn.clicked.connect(lambda *_: self.checkout())
        self.checkin_btn.clicked.connect(lambda *_: self.checkin())
        self.restore_file_btn.clicked.connect(lambda *_: self.restore_file())
        self.cancel_btn.clicked.connect(lambda *_: self.cancel())
        self.open_file_btn.clicked.connect(lambda *_: self.open_file())
        self.open_dir_btn.clicked.connect(lambda *_: self.open_folder())
        self.transition_btn.clicked.connect(lambda *_: self.change_state())
        self.export_btn.clicked.connect(lambda *_: self.export())

        rule = QFrame(self.body)
        rule.setObjectName("hairline")
        rule.setFixedHeight(1)
        dl.addWidget(rule)
        rev_row = QHBoxLayout()
        rev_row.setSpacing(8)
        rev_col = QVBoxLayout()
        rev_col.setSpacing(2)
        self.latest_label = QLabel(self.body)
        self.latest_label.setProperty("role", "muted")
        self.released_label = QLabel(self.body)
        self.released_label.setProperty("role", "muted")
        rev_col.addWidget(self.latest_label)
        rev_col.addWidget(self.released_label)
        rev_row.addLayout(rev_col, 1)
        self.state_btn = QToolButton(self.body)
        self.state_btn.setObjectName("documents_state_btn")
        self.state_btn.setProperty("role", "chip")
        self.state_btn.setProperty("tone", "neutral")
        self.state_btn.setAccessibleName("Lifecycle state")
        self.state_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.state_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # the chevron follows the text
        self.state_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.state_menu = QMenu(self.state_btn)
        self.state_btn.setMenu(self.state_menu)
        rev_row.addWidget(self.state_btn, 0, Qt.AlignmentFlag.AlignTop)
        dl.addLayout(rev_row)
        rule = QFrame(self.body)
        rule.setObjectName("hairline")
        rule.setFixedHeight(1)
        dl.addWidget(rule)

        self.history_label = QLabel("Histor&y", self.body)
        self.history_label.setStyleSheet("font-weight: 600; font-size: 13px")
        dl.addWidget(self.history_label)
        self.history = QTableView(self.body)
        self.history_label.setBuddy(self.history)
        self.history.setAccessibleName("Revision history")
        self.history_model = ViewModelTable(DocumentPanel.HISTORY_COLUMNS, lambda: len(self.panel.history),
                                            self.panel.history_cell, self)
        self.history.setModel(self.history_model)
        configure_table(self.history, DocumentPanel.HISTORY_COLUMNS)
        flat_list(self.history, DocumentPanel.HISTORY_COLUMNS, "description", HistoryDelegate.height, HistoryDelegate(self.panel, self))
        show_at_least_rows(self.history, 3)
        self.history.selectionModel().selectionChanged.connect(lambda *_: self.revision_selected())
        dl.addWidget(self.history, 1)

        self.revision_card = QFrame(self.body)
        self.revision_card.setObjectName("revisionCard")
        cl = QVBoxLayout(self.revision_card)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(6)
        self.revision_detail = QPlainTextEdit(self.revision_card)
        self.revision_detail.setReadOnly(True)
        self.revision_detail.setProperty("role", "plain")
        self.revision_detail.setAccessibleName("Selected revision")
        self.revision_detail.setPlaceholderText("Select a revision to see its full description and state changes.")
        self.revision_detail.setFixedHeight(84)
        cl.addWidget(self.revision_detail)
        card_row = QHBoxLayout()
        card_row.setSpacing(6)
        self.card_export_btn = QPushButton("E&xport", self.revision_card)
        self.card_export_btn.setToolTip("Save a copy of this revision")
        self.compare_btn = QPushButton("Co&mpare with previous", self.revision_card)
        self.compare_btn.setToolTip("Metadata comparison of this revision and the one before it. CAD content is not compared.")
        self.restore_btn = QPushButton("Res&tore", self.revision_card)
        self.restore_btn.setToolTip("While checked out: replace the working file with this older revision. "
                                    "History is never rewritten.")
        for btn in (self.card_export_btn, self.compare_btn, self.restore_btn):
            btn.setStyleSheet("padding: 5px 10px")
            card_row.addWidget(btn)
        card_row.addStretch(1)
        cl.addLayout(card_row)
        self.card_export_btn.clicked.connect(lambda *_: self.export())
        self.compare_btn.clicked.connect(lambda *_: self.compare())
        self.restore_btn.clicked.connect(lambda *_: self.restore())
        self.revision_card.setVisible(False)
        dl.addWidget(self.revision_card)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([640, 372])

        self.empty_box = QWidget(self.pages)
        em = QVBoxLayout(self.empty_box)
        em.setContentsMargins(24, 16, 24, 24)
        zone = QFrame(self.empty_box)
        zone.setObjectName("dropZone")
        zl = QVBoxLayout(zone)
        zl.setSpacing(10)
        zl.addStretch(1)
        self.empty_icon = QLabel(zone)
        self.empty_icon.setStyleSheet("background: transparent")
        zl.addWidget(self.empty_icon, 0, Qt.AlignmentFlag.AlignCenter)
        heading = QLabel("Add your first file", zone)
        heading.setProperty("role", "h2")
        zl.addWidget(heading, 0, Qt.AlignmentFlag.AlignCenter)
        tip = QLabel("Drop a file here, or choose one. Files from elsewhere are copied in,\n"
                     "so your originals are never moved or changed.", zone)
        tip.setProperty("role", "muted")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zl.addWidget(tip, 0, Qt.AlignmentFlag.AlignCenter)
        self.choose_btn = QPushButton("Choose a file", zone)
        self.choose_btn.setProperty("role", "primary")
        self.choose_btn.clicked.connect(lambda *_: self.import_file())
        zl.addWidget(self.choose_btn, 0, Qt.AlignmentFlag.AlignCenter)
        zl.addStretch(1)
        em.addWidget(zone)
        self.pages.addWidget(self.empty_box)

        for name in ("filter", "import_btn", "register_btn", "notice", "table", "checkout_btn",
                     "checkin_btn", "cancel_btn", "open_file_btn", "open_dir_btn", "transition_box",
                     "transition_btn", "export_btn", "compare_btn", "restore_btn", "history",
                     "revision_detail", "restore_file_btn", "choose_btn"):
            getattr(self, name).setObjectName("documents_" + name)

        for keys, slot in (("Ctrl+I", self.import_file), ("Ctrl+K", self.checkout),
                           ("Ctrl+Shift+K", self.checkin), ("Ctrl+Shift+F", self.filter.setFocus)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda s=slot: s())

        self.apply_theme_icons()
        self.list_vm.subscribe(self.render_list)
        self.panel.subscribe(self.render_panel)
        self.render_list()
        self.render_panel()

    def apply_theme_icons(self) -> None:
        t = theme.current
        self.import_btn.setIcon(theme.icon("plus", t["ink"]))
        self.more_btn.setIcon(theme.icon("more", t["ink"], 18))
        self.empty_icon.setPixmap(theme.icon_pixmap("upload", t["accentInk"], 30, 1.6))

    # ------------------------------------------------------------------ binding
    def load_project(self, project_id: str | None) -> None:
        """Show a project's documents. Reloading the SAME project keeps the selected document;
        switching projects clears it and starts from everything in the new project."""
        same = bool(project_id) and project_id == self.list_vm.project_id
        keep = self.panel.doc_id if same else None
        self.panel.load(None)
        self.table.clearSelection()
        if not same:
            self.list_vm.show = "all"
            self.filter_buttons["all"].setChecked(True)
        self.list_vm.load(project_id)
        if not keep and self.list_vm.rows:  # predictable: the actions always have a target when there can be one
            keep = self.list_vm.row_id(0)
        if keep:
            self.select_document(keep)

    def apply_filter(self) -> None:
        self.list_vm.text = self.filter.text()
        self.list_vm.refresh()

    def set_show(self, key: str) -> None:
        self.list_vm.show = key
        self.filter_buttons[key].setChecked(True)
        self.list_vm.refresh()
        if not self.panel.doc_id and self.list_vm.rows:
            self.select_document(self.list_vm.row_id(0))

    def selected_doc_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        return self.list_vm.row_id(rows[0].row()) if rows and rows[0].row() < len(self.list_vm.rows) else None

    def selection_changed(self) -> None:
        self.panel.load_or_report(self.selected_doc_id())

    def select_document(self, doc_id: str) -> None:
        for i, r in enumerate(self.list_vm.rows):
            if r["id"] == doc_id:
                if self.selected_doc_id() == doc_id:  # no selectionChanged will fire
                    if self.panel.doc_id != doc_id:
                        self.panel.load_or_report(doc_id)
                else:
                    self.table.selectRow(i)
                return

    def render_list(self) -> None:
        keep = self.panel.doc_id or self.panel.failed_id
        self.table_model.reload()
        vm = self.list_vm
        counts = vm.counts
        for key, label in (("all", "All"), ("mine", "Checked out by me"), ("attention", "Needs attention")):
            self.filter_buttons[key].setText(f"{label}  {counts.get(key, 0)}")
        empty = vm.is_empty
        self.pages.setCurrentWidget(self.empty_box if empty else self.pages.widget(0))
        self.filter_row.setVisible(not empty and bool(vm.project))
        self.notice.setText(vm.notice)
        self.notice.setToolTip(vm.notice)
        # Empty projects and attention counts already say this in their own place.
        self.notice.setVisible(bool(vm.notice) and not empty and not vm.problem_count)
        self.no_match.setText(vm.no_match_text)
        self.no_match.setVisible(bool(vm.no_match_text))
        self.import_btn.setEnabled(vm.can_add)
        self.register_btn.setEnabled(vm.can_add)
        if keep:
            if any(r["id"] == keep for r in vm.rows):
                self.select_document(keep)
            else:  # no longer listed: its actions must not stay available
                self.panel.load_or_report(None)

    def render_panel(self) -> None:
        p = self.panel
        t = theme.current
        has_doc = bool(p.detail)
        self.body.setVisible(has_doc or bool(p.message))
        self.no_selection.setVisible(not has_doc and not p.message)
        self.doc_title.setText(p.title_text)
        self.doc_title.setToolTip(p.title_text)
        self.doc_title.setVisible(has_doc)
        self.summary.setText(p.meta_text)
        self.summary.setToolTip("  ·  ".join(f"{label}: {value}" for label, value in p.summary_fields))
        self.summary.setVisible(has_doc)
        self.ownership.setText(p.ownership_text)
        tone = p.tone
        self.status_title.setText(p.status_title)
        self.status_title.setVisible(bool(p.status_title))
        self.status.setText(p.message or p.status_text)
        self.status.setToolTip(self.status.text())
        theme.restyle(self.status_box, tone=tone)
        theme.restyle(self.status_title, tone=tone)
        icon_name = "lock" if self.ctl.read_only else STATUS_ICONS[tone]
        self.status_icon.setPixmap(theme.icon_pixmap(icon_name, t[TONE_FG[tone]], 18))

        missing = p.file_missing
        self.restore_file_btn.setVisible(missing)
        self.restore_file_btn.setEnabled(p.can_restore_file)
        self.checkin_btn.setVisible(not missing and has_doc and p.can_checkin_visible)
        self.checkout_btn.setVisible(not missing and has_doc and not p.can_checkin_visible)
        self.checkout_btn.setEnabled(p.can_checkout)
        self.checkin_btn.setEnabled(p.can_checkin)
        self.cancel_btn.setEnabled(p.can_cancel)
        self.open_file_btn.setEnabled(p.can_open_file)
        self.open_file_btn.setVisible(has_doc)
        self.more_btn.setVisible(has_doc)
        self.open_dir_btn.setEnabled(has_doc)
        self.act_show_folder.setEnabled(has_doc)
        self.act_cancel.setVisible(p.can_cancel)
        self.export_btn.setEnabled(bool(p.history) and not self.ctl.busy)
        self.act_export.setEnabled(self.export_btn.isEnabled())
        self.card_export_btn.setEnabled(self.export_btn.isEnabled())

        self.transition_box.clear()
        self.state_menu.clear()
        for sid, name in p.transition_choices:
            self.transition_box.addItem(name, sid)
            act = self.state_menu.addAction(STATE_VERBS.get(sid, f"Move to {name}"))
            act.triggered.connect(lambda *_, s=sid, n=name: self.change_state(s, n))
        self.transition_box.setEnabled(p.can_transition)
        self.transition_btn.setEnabled(p.can_transition)
        self.state_btn.setVisible(has_doc and bool(p.latest_state_name))
        self.state_btn.setEnabled(p.can_transition)
        self.state_btn.setText(p.latest_state_name)
        self.state_btn.setIcon(theme.icon("chevron", t[TONE_FG[p.state_tone]], 14) if p.can_transition else theme.icon("chevron", "#00000000", 1))
        theme.restyle(self.state_btn, tone=p.state_tone if p.state_tone != "danger" else "neutral")
        self.latest_label.setText(p.latest_text)
        self.latest_label.setVisible(has_doc)
        self.released_label.setText(p.released_text)
        self.released_label.setVisible(bool(p.released_text))
        for w in (self.history_label, self.history):
            w.setVisible(has_doc)
        self.history_model.reload()
        self.revision_detail.setPlainText(p.revision_text(0) if p.history else "")
        self.revision_card.setVisible(False)
        self._history_buttons()

    def selected_history_row(self) -> int | None:
        rows = self.history.selectionModel().selectedRows()
        return rows[0].row() if rows and rows[0].row() < len(self.panel.history) else None

    def _history_buttons(self) -> None:
        row = self.selected_history_row()
        self.compare_btn.setEnabled(self.panel.can_compare(0 if row is None else row))
        self.restore_btn.setEnabled(self.panel.can_restore(row))
        self.restore_btn.setVisible(bool(row) and self.panel.can_checkin)

    def revision_selected(self) -> None:
        row = self.selected_history_row()
        if row is not None:
            self.revision_detail.setPlainText(self.panel.revision_text(row))
            self.revision_card.setVisible(True)
        self._history_buttons()

    def compare(self) -> None:
        row = self.selected_history_row()
        row = 0 if row is None else row
        if self.panel.can_compare(row):
            self.run(lambda: self.revision_detail.setPlainText(self.panel.compare_text(row)), refresh=False)
            self.revision_card.setVisible(True)

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

    def restore_file(self) -> None:
        if self.panel.can_restore_file:
            self.run(self.panel.restore_file)

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
    def import_file(self, path: str | None = None) -> None:
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
        self.dialogs.append(dlg)
        dlg.finished.connect(lambda _: self.dialogs.remove(dlg) if dlg in self.dialogs else None)
        if path:
            dlg.source.setText(path)
        dlg.show()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if self.list_vm.can_add and urls and urls[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.import_file(urls[0].toLocalFile())

    def _select_added(self, res, rel_path: str) -> None:
        """Select a document just added. A saved-with-warning result carries no record, so it is
        found by its path."""
        self.set_show("all")
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
        if self.panel.can_checkout and not self.panel.file_missing:
            self.run(self.panel.checkout)

    def checkin(self) -> None:
        if not self.panel.can_checkin:
            return
        title = self.panel.detail["document"].get("title", "")
        dlg = CheckinDialog(title, self, unchanged=not self.panel.file_edited)
        if dlg.exec():
            f = dlg.form
            self.panel.start_checkin(f.description, on_done=lambda _res: self.list_vm.refresh(),
                                     allow_unchanged=f.allow_unchanged, label=f.label.strip() or None)

    def cancel(self) -> None:
        if not self.panel.can_cancel:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Cancel checkout")
        box.setText("Cancel the checkout? No new revision will be recorded. Keep your edits in the working "
                    "file, or discard them and restore the latest revision?")
        keep = box.addButton("Keep my changes", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard my changes", QMessageBox.ButtonRole.DestructiveRole)
        back = box.addButton("Go back", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back)
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

    def change_state(self, state: str | None = None, name: str | None = None) -> None:
        state = state or self.transition_box.currentData()
        name = name or self.transition_box.currentText()
        if not state or not self.panel.can_transition:
            return
        comment, ok = QInputDialog.getText(self, "Change state", f"Comment for “{name}” (optional):")
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
