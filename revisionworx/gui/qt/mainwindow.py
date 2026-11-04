# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window (PySide6). Binds view-models to widgets; contains no business logic."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QStackedWidget, QTableView, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import __version__
from ...workspace import CommittedNotApplied
from ..controller import Controller, Progress, SavedWithWarning, describe_error
from ..viewmodels import AppStatus, ProjectDetailsModel, ProjectListModel
from .dialogs import AdoptDialog, NewProjectDialog
from .documents import DocumentsTab
from .models import EnterTable, ViewModelTable, elastic
from .startpage import StartPage

SHORTCUTS_TEXT = """Ctrl+N\tNew project (start page when no workspace is open)
Ctrl+O\tOpen workspace
Ctrl+W\tClose workspace
F5\tRefresh (keeps your selection)
Ctrl+F\tSearch projects
Ctrl+Shift+F\tFilter documents
Ctrl+I\tImport file into the selected project
Ctrl+K\tCheck out the selected document
Ctrl+Shift+K\tCheck in the selected document
Enter / double-click\tOpen project folder or document file"""

ALPHA_STYLE = "background: #fff4ce; color: #4a3b00; padding: 6px; border-bottom: 1px solid #e0c96b;"
BLOCKED_STYLE = "background: #fde7e9; color: #8b0016; padding: 6px; font-weight: bold;"
PENDING_STYLE = "background: #eef4fb; color: #1b3a5c; padding: 6px;"


def configure_table(view: QTableView, columns) -> None:
    view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    view.setTabKeyNavigation(False)  # Tab leaves the table; arrow keys move within it
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
        self.status_vm = AppStatus(ctl)
        self.setWindowTitle(f"RevisionWorx {__version__} (Alpha)")
        # No fixed minimum: the layout's own minimum keeps every action and table usable.
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        # availableGeometry excludes the taskbar but not this window's own title bar.
        self.resize(min(1200, avail.width()) if avail else 1200, min(760, avail.height() - 40) if avail else 760)
        self._restoring_selection = False
        self._write_actions: list[QAction] = []
        # Errors hold paths and commands people need to copy; 3 = selectable by mouse and keyboard.
        self.setStyleSheet("QMessageBox { messagebox-text-interaction-flags: 3; }")
        self._build_menu()

        shell = QWidget(self)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.alpha_banner = QLabel(self.status_vm.alpha_notice, shell)
        self.alpha_banner.setObjectName("alphaBanner")
        self.alpha_banner.setWordWrap(True)
        self.alpha_banner.setStyleSheet(ALPHA_STYLE)
        self.alpha_banner.setAccessibleName("Alpha warning")
        shell_layout.addWidget(self.alpha_banner)
        self.health_banner = elastic(QLabel(shell))
        self.health_banner.setObjectName("healthBanner")
        self.health_banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.health_banner.hide()
        shell_layout.addWidget(self.health_banner)
        self.stack = QStackedWidget(shell)
        shell_layout.addWidget(self.stack, 1)
        self.start_page = StartPage(ctl, self.stack)
        self.start_page.opened.connect(
            lambda loc: self.open_location(Path(loc), select_project=self.start_page.last_created_id))
        self.start_page.open_requested.connect(self.open_ws)
        self.stack.addWidget(self.start_page)

        central = QWidget(self.stack)
        outer = QVBoxLayout(central)
        bar = QHBoxLayout()
        self.search = QLineEdit(central)
        self.search.setPlaceholderText("Search projects by name, number, customer…  (Ctrl+F)")
        self.search.setAccessibleName("Search projects")
        self.search.returnPressed.connect(lambda *_: self.apply_search())
        self.archived = QCheckBox("Include archived", central)
        self.archived.toggled.connect(lambda _: self.apply_search())
        self.adopt_btn = QPushButton("&Adopt existing…", central)
        self.adopt_btn.clicked.connect(lambda *_: self.adopt())
        self.new_btn = QPushButton("&New project", central)
        self.new_btn.clicked.connect(lambda *_: self.new_project())
        bar.addWidget(self.search, 1)
        bar.addWidget(self.archived)
        bar.addWidget(self.adopt_btn)
        bar.addWidget(self.new_btn)
        outer.addLayout(bar)
        self.projects_notice = QLabel(central)
        self.projects_notice.setObjectName("projectsNotice")
        self.projects_notice.setWordWrap(True)
        outer.addWidget(self.projects_notice)

        split = QSplitter(central)
        split.setChildrenCollapsible(False)
        left = QWidget(split)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.table = EnterTable(left)
        self.table.setAccessibleName("Projects")
        ll.addWidget(self.table, 1)
        self.table_model = ViewModelTable(ProjectListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self)
        self.table.setModel(self.table_model)
        configure_table(self.table, ProjectListModel.COLUMNS)
        keys = [c.key for c in ProjectListModel.COLUMNS]
        for key in ("customer", "mode", "rel_path"):  # all shown in Project details; keep the list narrow
            self.table.setColumnHidden(keys.index(key), True)
        self.table.setColumnWidth(keys.index("number"), 80)
        self.table.setColumnWidth(keys.index("status"), 65)
        self.table.setMinimumWidth(220)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.table.doubleClicked.connect(lambda _: self.open_folder())
        self.table.enter_pressed.connect(self.open_folder)

        detail = QWidget(split)
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        self.project_heading = elastic(QLabel(detail))
        self.project_heading.setObjectName("projectHeading")
        self.project_heading.setTextFormat(Qt.TextFormat.PlainText)
        self.project_heading.setStyleSheet("font-weight: bold")
        self.open_btn = QPushButton("Open folder", detail)
        self.open_btn.clicked.connect(lambda *_: self.open_folder())
        self.archive_btn = QPushButton("Archive", detail)
        self.archive_btn.clicked.connect(lambda *_: self.toggle_archive())
        buttons.addWidget(self.project_heading, 1)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.archive_btn)
        dl.addLayout(buttons)
        self.warning_label = elastic(QLabel(detail))  # above the tabs: must not hide on another tab
        self.warning_label.setStyleSheet("color: #8a5300")
        dl.addWidget(self.warning_label)
        tabs = QTabWidget(detail)
        tabs.setAccessibleName("Project contents")
        self.tabs = tabs
        self.documents_tab = DocumentsTab(ctl, configure_table, tabs)
        tabs.addTab(self.documents_tab, "Documents")
        files_page = QWidget(tabs)
        fl = QVBoxLayout(files_page)
        self.file_filter = QLineEdit(files_page)
        self.file_filter.setPlaceholderText("Filter files…")
        self.file_filter.setAccessibleName("Filter baseline files")
        self.file_filter.returnPressed.connect(lambda *_: self.filter_files())
        fl.addWidget(self.file_filter)
        self.files = QTableView(files_page)
        self.files.setAccessibleName("Baseline files")
        self.files_model = ViewModelTable(ProjectDetailsModel.FILE_COLUMNS, lambda: len(self.detail_vm.files),
                                          self.detail_vm.file_cell, self)
        self.files.setModel(self.files_model)
        configure_table(self.files, ProjectDetailsModel.FILE_COLUMNS)
        fl.addWidget(self.files, 1)
        tabs.addTab(files_page, "Baseline files")
        details_page = QWidget(tabs)
        self.detail_form = QFormLayout(details_page)
        tabs.addTab(details_page, "Project details")
        dl.addWidget(tabs, 1)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 900])
        outer.addWidget(split, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous", central)
        self.prev_btn.clicked.connect(lambda *_: self.list_vm.prev_page())
        self.next_btn = QPushButton("Next ▶", central)
        self.next_btn.clicked.connect(lambda *_: self.list_vm.next_page())
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        ll.addLayout(nav)
        QWidget.setTabOrder(self.table, self.prev_btn)  # they sit under the list, so they follow it
        QWidget.setTabOrder(self.prev_btn, self.next_btn)
        QWidget.setTabOrder(self.next_btn, self.open_btn)
        self.workspace_page = central
        self.stack.addWidget(central)
        self.setCentralWidget(shell)

        self.list_vm.subscribe(self.render_list)
        self.detail_vm.subscribe(self.render_details)
        self.status_vm.subscribe(self.render_health)
        self.render_list()
        self.render_details()
        self.busy_label = QLabel(self)
        self.busy_label.setAccessibleName("Background task status")
        self.statusBar().addPermanentWidget(self.busy_label)
        self._was_busy = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        for name in ("search", "archived", "adopt_btn", "new_btn", "table", "open_btn", "archive_btn",
                     "prev_btn", "next_btn", "file_filter", "files", "stack", "busy_label"):
            getattr(self, name).setObjectName("main_" + name)

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        ws = self.menuBar().addMenu("&Workspace")
        for text, slot, shortcut in (("Open workspace…", self.open_ws, QKeySequence.StandardKey.Open),
                                     ("Create workspace…", self.create_ws, None),
                                     ("Close workspace", self.close_ws, QKeySequence.StandardKey.Close),
                                     ("Check workspace health", lambda: self.status_vm.check(), None),
                                     ("Add engineering root…", self.add_root, None),
                                     ("Rebuild local index", self.rebuild, None),
                                     ("Refresh", lambda: self.refresh_all(), QKeySequence.StandardKey.Refresh)):
            act = QAction(text, self)
            if shortcut is not None:
                act.setShortcut(QKeySequence(shortcut))
            act.triggered.connect(lambda *_, s=slot: s())
            ws.addAction(act)
            if text == "Add engineering root…":
                self._write_actions.append(act)
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
        self._write_actions.append(new_act)
        adopt_act = QAction("Adopt existing project…", self)
        adopt_act.triggered.connect(lambda *_: self.adopt())
        prj.addAction(adopt_act)
        self._write_actions.append(adopt_act)
        help_menu = self.menuBar().addMenu("&Help")
        keys_act = QAction("Keyboard shortcuts", self)
        keys_act.triggered.connect(lambda *_: QMessageBox.information(self, "Keyboard shortcuts", SHORTCUTS_TEXT))
        help_menu.addAction(keys_act)
        about_act = QAction("About RevisionWorx", self)
        about_act.triggered.connect(lambda *_: QMessageBox.about(
            self, "About RevisionWorx", f"RevisionWorx {__version__}\n\n{self.status_vm.alpha_notice}\n\n"
            "Offline engineering PDM. GPL-3.0-or-later."))
        help_menu.addAction(about_act)
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
    def render_health(self) -> None:
        st = self.status_vm
        if st.health_text:
            self.health_banner.setText(st.health_text)
            self.health_banner.setToolTip(st.health_text)
            self.health_banner.setStyleSheet(BLOCKED_STYLE if (st.blocked or not st.health.get("ok"))
                                             else PENDING_STYLE)
            self.health_banner.show()
        else:
            self.health_banner.hide()
        self.update_write_actions()
        self.documents_tab.render_list()
        self.documents_tab.render_panel()

    def update_write_actions(self) -> None:
        """Project-level changes: off while recovery-blocked or while a job holds the workspace."""
        writable = self.ctl.location is not None and not self.ctl.read_only and not self.ctl.busy
        self.new_btn.setEnabled(writable)
        self.adopt_btn.setEnabled(writable)
        self.archive_btn.setEnabled(writable and self.detail_vm.project is not None)
        for act in self._write_actions:
            act.setEnabled(writable or (self.ctl.location is None and act.text() != "Add engineering root…"))

    def render_list(self) -> None:
        keep = self.detail_vm.project["id"] if self.detail_vm.project else None
        self.table_model.reload()
        self.projects_notice.setText(self.list_vm.notice)
        self.projects_notice.setVisible(bool(self.list_vm.notice))
        self.prev_btn.setEnabled(self.list_vm.can_prev)
        self.next_btn.setEnabled(self.list_vm.can_next)
        self.statusBar().showMessage(self.list_vm.status_text)
        if keep:  # a model reset drops the selection without a signal
            row = self.list_vm.index_of(keep)
            if row is None:  # no longer listed: its details and actions must not stay available
                self.selection_changed()
            else:
                self._restoring_selection = True
                self.table.selectRow(row)
                self._restoring_selection = False

    def render_details(self) -> None:
        while self.detail_form.rowCount():
            self.detail_form.removeRow(0)
        for label, value in self.detail_vm.fields:
            v = elastic(QLabel(value))
            v.setToolTip(value)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.detail_form.addRow(label, v)
        p = self.detail_vm.project
        self.project_heading.setText(
            " ".join(x for x in (p.get("number"), p.get("name")) if x)
            + f"  ({p.get('mode')}, {p.get('status')})" if p else "")
        self.warning_label.setText("\n".join(f"⚠ {w}" for w in self.detail_vm.warnings))
        self.warning_label.setToolTip(self.warning_label.text())
        self.warning_label.setVisible(bool(self.detail_vm.warnings))
        self.open_btn.setEnabled(self.detail_vm.can_open_folder)
        self.update_write_actions()
        self.archive_btn.setText(self.detail_vm.archive_label)
        self.files_model.reload()

    # ------------------------------------------------------------------ actions
    def error(self, title: str, exc: Exception) -> None:
        if isinstance(exc, (CommittedNotApplied, SavedWithWarning)):  # saved: never shown as a failure
            QMessageBox.warning(self, "Saved with a warning", describe_error(exc))
        else:
            QMessageBox.critical(self, title, describe_error(exc))

    def refuse_while_busy(self, quiet: bool = False) -> bool:
        """True if a background job is still running, so the workspace must not be closed or
        switched under it. The short wait covers a job that is just finishing."""
        if self.ctl.wait_idle(0.25):
            return False
        if not quiet:
            QMessageBox.information(self, "Still working", "A background task is still running. Wait "
                                    "for it to finish (or cancel the scan), then try again.")
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Do not quit under a running job: its worker thread would die with the process."""
        if self.refuse_while_busy():
            event.ignore()
        else:
            event.accept()

    def open_location(self, location: Path, quiet: bool = False, select_project: str | None = None) -> bool:
        """Open a workspace; on failure stay on (or return to) the start page with the reason."""
        if self.refuse_while_busy(quiet):
            return False
        try:
            info = self.ctl.open_workspace(location)
        except Exception as exc:
            self.show_start(f"Cannot open {location}:\n{describe_error(exc)}")
            if not quiet:
                self.error("Cannot open workspace", exc)
            return False
        self.setWindowTitle(f"RevisionWorx {__version__} (Alpha) — {info['name']}")
        self._fill_recent()
        self.stack.setCurrentWidget(self.workspace_page)
        self.status_vm.check()
        self.list_vm.set_query(self.search.text(), self.archived.isChecked())
        self.select_project(select_project)
        return True

    def select_project(self, project_id: str | None) -> None:
        """Predictable selection: the given project, else the first row, else nothing."""
        row = self.list_vm.index_of(project_id) if project_id else None
        if row is None and self.list_vm.rows:
            row = 0
        if row is not None and self.selected_project_id() != self.list_vm.row_id(row):
            self.table.selectRow(row)
        else:  # nothing to select, or already selected (no signal would fire): load explicitly
            self.selection_changed()

    def selected_project_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        return self.list_vm.row_id(rows[0].row()) if rows and rows[0].row() < len(self.list_vm.rows) else None

    def refresh_all(self) -> None:
        """F5: re-check health, reload projects and documents, keep the selected project/document."""
        if self.ctl.location is None:
            self.start_page.fill_recent()
            return
        pid, did = self.selected_project_id(), self.documents_tab.panel.doc_id
        self.status_vm.check()
        self.list_vm.refresh()
        self.select_project(pid)
        if did:
            self.documents_tab.select_document(did)
        self.documents_tab.refresh()

    def show_start(self, message: str = "") -> None:
        self.start_page.fill_recent()
        self.start_page.show_error(message)
        self.stack.setCurrentWidget(self.start_page)

    def close_ws(self) -> None:
        if self.refuse_while_busy():
            return
        self.ctl.close_workspace()
        self.setWindowTitle(f"RevisionWorx {__version__} (Alpha)")
        self.list_vm.refresh()
        self.detail_vm.load(None)
        self.documents_tab.load_project(None)
        self.status_vm.check()
        self.show_start()

    def open_ws(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder containing .revisionworx")
        if d:
            self.open_location(Path(d))

    def create_ws(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder on this computer for the new workspace")
        if not d or self.refuse_while_busy():
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
        if self._restoring_selection:
            return
        pid = self.selected_project_id()
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
            self.show_start()
            self.start_page.name_edit.setFocus()
            return
        if self.ctl.read_only:
            return
        dlg = NewProjectDialog(self.ctl, self)
        if dlg.exec():
            self.list_vm.refresh()
            self.select_project(dlg.result_record["id"] if dlg.result_record else None)

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
        """Deliver background results on the GUI thread (never block on slow disk I/O)."""
        events = self.ctl.drain()  # first: a finished job's result and its actions arrive together
        if self.ctl.read_only and not self.status_vm.blocked:
            self.status_vm.note_write_refused()
        busy = self.ctl.busy
        if busy != self._was_busy:  # enable/disable actions that would collide with the job
            self._was_busy = busy
            self.busy_label.setText("Working… (copying and hashing)" if busy else "")
            self.update_write_actions()
            self.documents_tab.update_add_actions()  # no list rebuild: that re-reads the workspace mid-write
            self.documents_tab.render_panel()
        for ev in events:
            if isinstance(ev, Progress):
                for d in self.adopt_dialogs:
                    d.flow.on_progress(ev)

    # ------------------------------------------------------------------ test observable (stable)
    def observable_state(self) -> dict:
        """Deterministic snapshot of what the user can see, for automated GUI acceptance (see
        docs/GUI_LAUNCH_CONTRACT.md). Read-only: never triggers I/O or changes state."""
        tab, p = self.documents_tab, self.documents_tab.panel
        return {
            "contract": 1,
            "page": "workspace" if self.stack.currentWidget() is self.workspace_page else "start",
            "title": self.windowTitle(),
            "alpha_banner": self.alpha_banner.text(),
            "health_banner": "" if self.health_banner.isHidden() else self.health_banner.text(),
            "read_only": self.ctl.read_only,
            "busy": self.ctl.busy,
            "workspace": str(self.ctl.location) if self.ctl.location else None,
            "start_error": self.start_page.error_label.text(),
            "projects_notice": self.list_vm.notice,
            "projects": [{"id": r["id"], "name": r.get("name"), "number": r.get("number")} for r in self.list_vm.rows],
            "selected_project": self.selected_project_id(),
            "documents_notice": tab.list_vm.notice,
            "documents": [{"id": r["id"], "rel_path": r["rel_path"], "rev": r.get("latest_label"),
                           "file_state": r.get("file_state"), "checked_out_by": r.get("checked_out_by")}
                          for r in tab.list_vm.rows],
            "selected_document": p.doc_id,
            "document_status": tab.status.text(),
            "message_level": p.message_level,
            "ownership": p.ownership_text,
            "history": [{"id": r["id"], "label": r["label"], "sha256": r["sha256"],
                         "description": r.get("description")} for r in p.history],
            "actions": {"new_project": self.new_btn.isEnabled(), "adopt": self.adopt_btn.isEnabled(),
                        "archive": self.archive_btn.isEnabled(), "register": tab.register_btn.isEnabled(),
                        "change_state": tab.transition_btn.isEnabled(), "import": tab.import_btn.isEnabled(),
                        "checkout": tab.checkout_btn.isEnabled(), "checkin": tab.checkin_btn.isEnabled(),
                        "cancel_checkout": tab.cancel_btn.isEnabled(), "open_file": tab.open_file_btn.isEnabled(),
                        "restore": tab.restore_btn.isEnabled(), "compare": tab.compare_btn.isEnabled()},
        }
