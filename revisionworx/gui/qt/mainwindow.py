# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window (PySide6). Binds view-models to widgets; contains no business logic."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget)

from ... import __version__
from ..controller import Controller, Progress, describe_error
from ..viewmodels import ProjectDetailsModel, ProjectListModel
from .dialogs import AdoptDialog, NewProjectDialog
from .documents import DocumentsTab
from .models import ViewModelTable


def configure_table(view: QTableView, columns) -> None:
    view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    view.setAlternatingRowColors(True)
    view.verticalHeader().setVisible(False)
    view.setWordWrap(False)
    header = view.horizontalHeader()
    for i, c in enumerate(columns):
        header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if c.stretch
                                    else QHeaderView.ResizeMode.Interactive)
        if not c.stretch:
            view.setColumnWidth(i, c.width)


class MainWindow(QMainWindow):
    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.adopt_dialogs: list[AdoptDialog] = []
        self.list_vm = ProjectListModel(ctl)
        self.detail_vm = ProjectDetailsModel(ctl)
        self.setWindowTitle(f"RevisionWorx {__version__}")
        self.resize(1200, 720)
        self._build_menu()

        central = QWidget(self)
        outer = QVBoxLayout(central)
        bar = QHBoxLayout()
        self.search = QLineEdit(central)
        self.search.setPlaceholderText("Search projects by name, number, customer…  (Ctrl+F)")
        self.search.returnPressed.connect(lambda *_: self.apply_search())
        self.archived = QCheckBox("Include archived", central)
        self.archived.toggled.connect(lambda _: self.apply_search())
        adopt_btn = QPushButton("Adopt existing…", central)
        adopt_btn.clicked.connect(lambda *_: self.adopt())
        new_btn = QPushButton("New project", central)
        new_btn.clicked.connect(lambda *_: self.new_project())
        bar.addWidget(self.search, 1)
        bar.addWidget(self.archived)
        bar.addWidget(adopt_btn)
        bar.addWidget(new_btn)
        outer.addLayout(bar)

        split = QSplitter(central)
        self.table = QTableView(split)
        self.table_model = ViewModelTable(ProjectListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self)
        self.table.setModel(self.table_model)
        configure_table(self.table, ProjectListModel.COLUMNS)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.table.doubleClicked.connect(lambda _: self.open_folder())

        detail = QWidget(split)
        dl = QVBoxLayout(detail)
        self.detail_form = QFormLayout()
        dl.addLayout(self.detail_form)
        self.warning_label = QLabel(detail)
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #8a5300")
        dl.addWidget(self.warning_label)
        buttons = QHBoxLayout()
        self.open_btn = QPushButton("Open folder", detail)
        self.open_btn.clicked.connect(lambda *_: self.open_folder())
        self.archive_btn = QPushButton("Archive", detail)
        self.archive_btn.clicked.connect(lambda *_: self.toggle_archive())
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.archive_btn)
        buttons.addStretch(1)
        dl.addLayout(buttons)
        tabs = QTabWidget(detail)
        self.documents_tab = DocumentsTab(ctl, configure_table, tabs)
        tabs.addTab(self.documents_tab, "Documents")
        files_page = QWidget(tabs)
        fl = QVBoxLayout(files_page)
        self.file_filter = QLineEdit(files_page)
        self.file_filter.setPlaceholderText("Filter files…")
        self.file_filter.returnPressed.connect(lambda *_: self.filter_files())
        fl.addWidget(self.file_filter)
        self.files = QTableView(files_page)
        self.files_model = ViewModelTable(ProjectDetailsModel.FILE_COLUMNS, lambda: len(self.detail_vm.files),
                                          self.detail_vm.file_cell, self)
        self.files.setModel(self.files_model)
        configure_table(self.files, ProjectDetailsModel.FILE_COLUMNS)
        fl.addWidget(self.files, 1)
        tabs.addTab(files_page, "Baseline files")
        dl.addWidget(tabs, 1)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous", central)
        self.prev_btn.clicked.connect(lambda *_: self.list_vm.prev_page())
        self.next_btn = QPushButton("Next ▶", central)
        self.next_btn.clicked.connect(lambda *_: self.list_vm.next_page())
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        outer.addLayout(nav)
        self.setCentralWidget(central)

        self.list_vm.subscribe(self.render_list)
        self.detail_vm.subscribe(self.render_details)
        self.render_list()
        self.render_details()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        ws = self.menuBar().addMenu("&Workspace")
        for text, slot, shortcut in (("Open workspace…", self.open_ws, QKeySequence.StandardKey.Open),
                                     ("Create workspace…", self.create_ws, None),
                                     ("Add engineering root…", self.add_root, None),
                                     ("Rebuild local index", self.rebuild, None),
                                     ("Refresh", lambda: self.list_vm.refresh(), QKeySequence.StandardKey.Refresh)):
            act = QAction(text, self)
            if shortcut is not None:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(lambda *_: slot())
            ws.addAction(act)
        self.recent_menu = ws.addMenu("Recent")
        ws.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
        quit_act.triggered.connect(lambda *_: self.close())
        ws.addAction(quit_act)
        prj = self.menuBar().addMenu("&Projects")
        new_act = QAction("New project…", self)
        new_act.setShortcut(QKeySequence(QKeySequence.StandardKey.New))
        new_act.triggered.connect(lambda *_: self.new_project())
        prj.addAction(new_act)
        adopt_act = QAction("Adopt existing project…", self)
        adopt_act.triggered.connect(lambda *_: self.adopt())
        prj.addAction(adopt_act)
        find = QAction("Find", self)
        find.setShortcut(QKeySequence(QKeySequence.StandardKey.Find))
        find.triggered.connect(lambda: self.search.setFocus())
        self.addAction(find)
        self._fill_recent()

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for loc in self.ctl.recent_workspaces():
            act = QAction(loc, self)
            act.triggered.connect(lambda _=False, l=loc: self.open_location(Path(l)))
            self.recent_menu.addAction(act)

    # ------------------------------------------------------------------ rendering
    def render_list(self) -> None:
        self.table_model.reload()
        self.prev_btn.setEnabled(self.list_vm.can_prev)
        self.next_btn.setEnabled(self.list_vm.can_next)
        self.statusBar().showMessage(self.list_vm.status_text)

    def render_details(self) -> None:
        while self.detail_form.rowCount():
            self.detail_form.removeRow(0)
        for label, value in self.detail_vm.fields:
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.detail_form.addRow(label, v)
        self.warning_label.setText("\n".join(f"⚠ {w}" for w in self.detail_vm.warnings))
        self.open_btn.setEnabled(self.detail_vm.can_open_folder)
        self.archive_btn.setEnabled(self.detail_vm.project is not None)
        self.archive_btn.setText(self.detail_vm.archive_label)
        self.files_model.reload()

    # ------------------------------------------------------------------ actions
    def error(self, title: str, exc: Exception) -> None:
        QMessageBox.critical(self, title, describe_error(exc))

    def open_location(self, location: Path) -> None:
        try:
            info = self.ctl.open_workspace(location)
        except Exception as exc:
            self.error("Cannot open workspace", exc)
            return
        self.setWindowTitle(f"RevisionWorx — {info['name']}")
        self._fill_recent()
        self.list_vm.set_query(self.search.text(), self.archived.isChecked())

    def open_ws(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder containing .revisionworx")
        if d:
            self.open_location(Path(d))

    def create_ws(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Where should the shared .revisionworx folder live?")
        if not d:
            return
        name, ok = QInputDialog.getText(self, "Workspace name", "Name for this workspace:")
        if not ok or not name.strip():
            return
        try:
            self.ctl.create_workspace(Path(d), name.strip())
        except Exception as exc:
            self.error("Cannot create workspace", exc)
            return
        self.open_location(Path(d))

    def add_root(self) -> None:
        if self.ctl.location is None:
            return
        d = QFileDialog.getExistingDirectory(self, "Engineering root folder")
        if not d:
            return
        name, ok = QInputDialog.getText(self, "Root name", "Name for this root:", text=Path(d).name)
        if ok and name.strip():
            try:
                self.ctl.add_root(name.strip(), Path(d))
            except Exception as exc:
                self.error("Cannot add root", exc)

    def rebuild(self) -> None:
        self.ctl.start_rebuild_index(on_done=lambda r: self.error("Rebuild failed", r.error) if r.error
                                     else self.list_vm.refresh())

    def apply_search(self) -> None:
        self.list_vm.set_query(self.search.text(), self.archived.isChecked())

    def selection_changed(self, *_):
        rows = self.table.selectionModel().selectedRows()
        pid = self.list_vm.row_id(rows[0].row()) if rows else None
        try:
            self.detail_vm.load(pid)
            self.documents_tab.load_project(pid)
        except Exception as exc:
            self.error("Cannot load project", exc)

    def filter_files(self) -> None:
        self.detail_vm.file_filter = self.file_filter.text()
        if self.detail_vm.project:
            self.detail_vm.load(self.detail_vm.project["id"])

    def open_folder(self) -> None:
        if self.detail_vm.can_open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.detail_vm.project["folder"]))

    def toggle_archive(self) -> None:
        try:
            self.detail_vm.toggle_archive()
        except Exception as exc:
            self.error("Cannot change status", exc)
        self.list_vm.refresh()

    def new_project(self) -> None:
        if self.ctl.location is None:
            QMessageBox.information(self, "No workspace", "Open or create a workspace first.")
            return
        dlg = NewProjectDialog(self.ctl, self)
        if dlg.exec():
            self.list_vm.refresh()

    def adopt(self) -> None:
        if self.ctl.location is None:
            QMessageBox.information(self, "No workspace", "Open or create a workspace first.")
            return
        dlg = AdoptDialog(self.ctl, self)
        dlg.flow.subscribe(lambda: self.list_vm.refresh() if dlg.flow.state == "adopted" else None)
        self.adopt_dialogs.append(dlg)
        dlg.finished.connect(lambda _: self.adopt_dialogs.remove(dlg) if dlg in self.adopt_dialogs else None)
        dlg.show()

    def poll(self) -> None:
        """Deliver background results on the GUI thread (never block on network I/O)."""
        for ev in self.ctl.drain():
            if isinstance(ev, Progress):
                for d in self.adopt_dialogs:
                    d.flow.on_progress(ev)
