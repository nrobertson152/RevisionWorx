# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window (PySide6). Binds view-models to widgets; contains no business logic."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressDialog,
                               QPushButton, QSplitter, QStackedWidget, QTableView, QTabWidget,
                               QVBoxLayout, QWidget)

from ... import __version__
from ...workspace import CommittedNotApplied, Workspace
from ..controller import Controller, Progress, SavedWithWarning, describe_error
from ..viewmodels import AppStatus, CommandPalette, ProjectDetailsModel, ProjectListModel
from . import branding, theme
from .about import DOCS_URL, AboutDialog
from .dialogs import AdoptDialog, NewProjectDialog
from .documents import DocumentsTab
from .models import EnterTable, ViewModelTable, elastic
from .palette import PaletteDialog
from .startpage import StartPage
from .team import (AccountsDialog, ChangePasswordDialog, LoginDialog, RecoveryCodeDialog, SetUpTeamDialog, show_share_check)

SHORTCUTS_TEXT = """Ctrl+P\tGo to anything: projects, documents and actions
Ctrl+N\tNew project (start page when no workspace is open)
Ctrl+O\tOpen workspace
Ctrl+W\tClose workspace
F5\tRefresh (keeps your selection)
Ctrl+F\tSearch projects
Ctrl+Shift+F\tFilter documents
Ctrl+I\tImport file into the selected project (or drop files on the window)
Ctrl+K\tCheck out the selected document
Ctrl+Shift+K\tCheck in the selected document
Ctrl+Shift+C\tCopy the selected document's file path
Ctrl+Shift+M\tMy checkouts
Enter / double-click\tOpen project folder or document file
Right-click a document\tEverything you can do with it
F1\tThis list"""


TEAM_WATCH_TICKS = 50    # of the 100 ms timer: every five seconds, see whether anyone else has changed anything


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
        self.setWindowTitle(f"RevisionWorx {__version__}")
        self.setWindowIcon(branding.app_icon())
        # No fixed minimum: the layout's own minimum keeps every action and table usable.
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        # availableGeometry excludes the taskbar but not this window's own title bar.
        self.resize(min(1200, avail.width()) if avail else 1200, min(760, avail.height() - 40) if avail else 760)
        self._restoring_selection = False
        self._write_actions: list[QAction] = []
        self.theme_name = theme.apply(QApplication.instance(), ctl.preference("theme", theme.DEFAULT_THEME)) \
            if QApplication.instance() is not None else theme.DEFAULT_THEME
        self.setAcceptDrops(True)
        self._build_menu()

        shell = QWidget(self)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self.health_banner = elastic(QLabel(shell))
        self.health_banner.setObjectName("healthBanner")
        self.health_banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.health_banner.hide()
        shell_layout.addWidget(self.health_banner)
        self.stack = QStackedWidget(shell)
        shell_layout.addWidget(self.stack, 1)
        self.start_page = StartPage(ctl, self.stack)
        self.start_page.setObjectName("startPage")
        self.start_page.opened.connect(
            lambda loc: self.open_location(Path(loc), select_project=self.start_page.last_created_id))
        self.start_page.open_requested.connect(self.open_ws)
        self.stack.addWidget(self.start_page)

        central = QWidget(self.stack)
        central.setObjectName("workspacePage")
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        split = QSplitter(central)
        split.setChildrenCollapsible(False)
        self.main_split = split

        # ---- sidebar: workspace, search, projects, help, scope notice
        left = QWidget(split)
        left.setObjectName("sidebar")
        left.setMinimumWidth(210)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 14, 12, 12)
        ll.setSpacing(8)
        self.ws_name = elastic(QLabel("RevisionWorx", left))
        self.ws_name.setObjectName("workspaceName")
        self.ws_sub = elastic(QLabel("", left))
        self.ws_sub.setObjectName("workspaceSub")
        ll.addWidget(self.ws_name)
        ll.addWidget(self.ws_sub)
        self.search = QLineEdit(left)
        self.search.setPlaceholderText("Search projects…  (Ctrl+F)")
        self.search.setAccessibleName("Search projects")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(lambda *_: self.apply_search())
        ll.addWidget(self.search)
        self.archived = QCheckBox("Include archived", left)
        self.archived.toggled.connect(lambda _: self.apply_search())
        ll.addWidget(self.archived)
        head = QHBoxLayout()
        projects_title = QLabel("Projects", left)
        theme.set_flag(projects_title, "role", "section")
        head.addWidget(projects_title, 1)
        ll.addLayout(head)
        self.adopt_btn = QPushButton("&Adopt existing…", left)
        self.adopt_btn.setToolTip("Register an existing project folder as it is (read-only preview first)")
        self.adopt_btn.clicked.connect(lambda *_: self.adopt())
        self.new_btn = QPushButton("&New project", left)
        self.new_btn.setToolTip("Create a project folder from a template (Ctrl+N)")
        self.new_btn.clicked.connect(lambda *_: self.new_project())
        theme.set_flag(self.new_btn, "primary", True)
        add_row = QHBoxLayout()
        add_row.addWidget(self.new_btn)
        add_row.addWidget(self.adopt_btn)
        ll.addLayout(add_row)
        self.projects_notice = elastic(QLabel(left))
        self.projects_notice.setObjectName("projectsNotice")
        theme.set_flag(self.projects_notice, "role", "muted")
        ll.addWidget(self.projects_notice)
        self.table = EnterTable(left)
        self.table.setAccessibleName("Projects")
        ll.addWidget(self.table, 1)
        self.table_model = ViewModelTable(ProjectListModel.COLUMNS, lambda: len(self.list_vm.rows),
                                          self.list_vm.cell, self)
        self.table.setModel(self.table_model)
        configure_table(self.table, ProjectListModel.COLUMNS)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        keys = [c.key for c in ProjectListModel.COLUMNS]
        for key in ("customer", "mode", "rel_path", "status"):  # all shown in Project details; keep the list narrow
            self.table.setColumnHidden(keys.index(key), True)
        self.table.setColumnWidth(keys.index("number"), 80)
        self.table.setMinimumWidth(180)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.table.doubleClicked.connect(lambda _: self.open_folder())
        self.table.enter_pressed.connect(self.open_folder)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Previous", left)
        self.prev_btn.clicked.connect(lambda *_: self.list_vm.prev_page())
        self.next_btn = QPushButton("Next ▶", left)
        self.next_btn.clicked.connect(lambda *_: self.list_vm.next_page())
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        ll.addLayout(nav)
        self.help_btn = QPushButton("Help and shortcuts  (F1)", left)
        theme.set_flag(self.help_btn, "flat", True)
        self.help_btn.clicked.connect(lambda *_: self.show_shortcuts())
        ll.addWidget(self.help_btn)
        self.alpha_banner = QLabel(self.status_vm.notice, left)
        self.alpha_banner.setObjectName("alphaBanner")
        self.alpha_banner.setWordWrap(True)
        self.alpha_banner.setAccessibleName("Scope notice")
        ll.addWidget(self.alpha_banner)

        # ---- main area: project header, warning, tabs
        detail = QWidget(split)
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(20, 16, 16, 8)
        dl.setSpacing(6)
        buttons = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self.project_heading = elastic(QLabel(detail))
        self.project_heading.setObjectName("projectHeading")
        self.project_heading.setTextFormat(Qt.TextFormat.PlainText)
        self.project_sub = elastic(QLabel(detail))
        self.project_sub.setTextFormat(Qt.TextFormat.PlainText)
        theme.set_flag(self.project_sub, "role", "muted")
        title_col.addWidget(self.project_heading)
        title_col.addWidget(self.project_sub)
        self.open_btn = QPushButton("Open folder", detail)
        self.open_btn.setToolTip("Open the project folder (Enter on the project list)")
        self.open_btn.clicked.connect(lambda *_: self.open_folder())
        self.archive_btn = QPushButton("Archive", detail)
        self.archive_btn.clicked.connect(lambda *_: self.toggle_archive())
        buttons.addLayout(title_col, 1)
        buttons.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignTop)
        buttons.addWidget(self.archive_btn, 0, Qt.AlignmentFlag.AlignTop)
        dl.addLayout(buttons)
        self.warning_label = elastic(QLabel(detail))  # above the tabs: must not hide on another tab
        self.warning_label.setObjectName("projectWarning")
        dl.addWidget(self.warning_label)
        tabs = QTabWidget(detail)
        tabs.setAccessibleName("Project contents")
        tabs.setDocumentMode(True)
        self.tabs = tabs
        self.documents_tab = DocumentsTab(ctl, configure_table, tabs)
        tabs.addTab(self.documents_tab, "Documents")
        files_page = QWidget(tabs)
        fl = QVBoxLayout(files_page)
        fl.setContentsMargins(0, 8, 0, 0)
        self.file_filter = QLineEdit(files_page)
        self.file_filter.setPlaceholderText("Filter files…")
        self.file_filter.setAccessibleName("Filter baseline files")
        self.file_filter.setClearButtonEnabled(True)
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
        self.detail_form.setContentsMargins(4, 12, 4, 4)
        tabs.addTab(details_page, "Project details")
        dl.addWidget(tabs, 1)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([270, 930])
        outer.addWidget(split, 1)

        QWidget.setTabOrder(self.table, self.prev_btn)  # they sit under the list, so they follow it
        QWidget.setTabOrder(self.prev_btn, self.next_btn)
        QWidget.setTabOrder(self.next_btn, self.help_btn)
        QWidget.setTabOrder(self.help_btn, self.open_btn)
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
        self.checkouts_label = QLabel(self)
        self.checkouts_label.setAccessibleName("Your checkouts")
        self.team_label = QLabel(self)
        self.team_label.setAccessibleName("Who is logged in")
        self.statusBar().addPermanentWidget(self.team_label)
        self.statusBar().addPermanentWidget(self.checkouts_label)
        self.statusBar().addPermanentWidget(self.busy_label)
        self._was_busy = False
        self._team_head: int | None = None
        self._team_ticks = 0
        self._team_probe: threading.Thread | None = None
        self._team_seen: dict | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        for name in ("search", "archived", "adopt_btn", "new_btn", "table", "open_btn", "archive_btn",
                     "prev_btn", "next_btn", "file_filter", "files", "stack", "busy_label", "help_btn"):
            getattr(self, name).setObjectName("main_" + name)
        self._restore_layout()

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
        team = self.menuBar().addMenu("Team")
        self.login_act = QAction("Log in…", self)
        self.login_act.triggered.connect(lambda *_: self.ask_login())
        self.logout_act = QAction("Log out", self)
        self.logout_act.triggered.connect(lambda *_: self.team_log_out())
        self.password_act = QAction("Change my password…", self)
        self.password_act.triggered.connect(lambda *_: self.change_password())
        self.accounts_act = QAction("Accounts…", self)
        self.accounts_act.triggered.connect(lambda *_: self.show_accounts())
        self.set_up_team_act = QAction("Set this workspace up for a team…", self)
        self.set_up_team_act.triggered.connect(lambda *_: self.set_up_team())
        self.check_folder_act = QAction("Check this folder…", self)
        self.check_folder_act.triggered.connect(lambda *_: self.ctl.location is not None and show_share_check(self.ctl, self))
        for act in (self.login_act, self.logout_act, self.password_act):
            team.addAction(act)
        team.addSeparator()
        team.addAction(self.accounts_act)
        team.addSeparator()
        team.addAction(self.set_up_team_act)
        team.addAction(self.check_folder_act)
        team.aboutToShow.connect(self.update_team_state)
        view = self.menuBar().addMenu("&View")
        goto = QAction("Go to anything…", self)
        goto.setShortcut(QKeySequence("Ctrl+P"))
        goto.triggered.connect(lambda *_: self.open_palette())
        view.addAction(goto)
        mine = QAction("My checkouts…", self)
        mine.setShortcut(QKeySequence("Ctrl+Shift+M"))
        mine.triggered.connect(lambda *_: self.show_my_checkouts())
        view.addAction(mine)
        view.addSeparator()
        theme_menu = view.addMenu("Colour theme")
        self.theme_actions = QActionGroup(self)
        for key, spec in theme.THEMES.items():
            act = QAction(spec["label"], self, checkable=True)
            act.setData(key)
            act.setChecked(key == self.theme_name)
            act.triggered.connect(lambda *_, k=key: self.set_theme(k))
            self.theme_actions.addAction(act)
            theme_menu.addAction(act)
        help_menu = self.menuBar().addMenu("&Help")
        guide_act = QAction("User guide", self)
        guide_act.triggered.connect(lambda *_: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        help_menu.addAction(guide_act)
        keys_act = QAction("Keyboard shortcuts", self)
        keys_act.setShortcut(QKeySequence("F1"))
        keys_act.triggered.connect(lambda *_: self.show_shortcuts())
        help_menu.addAction(keys_act)
        help_menu.addSeparator()
        about_act = QAction("About RevisionWorx", self)
        about_act.triggered.connect(lambda *_: self.show_about())
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
            theme.set_tone(self.health_banner, "danger" if (st.blocked or not st.health.get("ok")) else "info")
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
        self.project_heading.setText(p.get("name") or p.get("number") or "" if p else "Select a project")
        self.project_heading.setToolTip(self.project_heading.text())
        sub = []
        if p:
            sub = [x for x in (p.get("number"), p.get("customer")) if x]
            if p.get("mode") != "managed":
                sub.append("indexed (search only)")
            if p.get("status") != "active":
                sub.append(p.get("status", ""))
        self.project_sub.setText("  ·  ".join(sub))
        self.project_sub.setVisible(bool(sub))
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
            self._save_layout()
            event.accept()

    def open_location(self, location: Path, quiet: bool = False, select_project: str | None = None,
                      restore_selection: bool = False) -> bool:
        """Open a workspace; on failure stay on (or return to) the start page with the reason."""
        if self.refuse_while_busy(quiet):
            return False
        previous = self.ctl.location
        self._read_team_history(location)
        try:
            info = self.ctl.open_workspace(location)
        except Exception as exc:
            if previous is not None:  # still open, and what every action targets: it stays on screen
                if not quiet:
                    QMessageBox.critical(self, "Cannot open workspace",
                                         f"Cannot open {location}:\n{describe_error(exc)}\n\n"
                                         f"{previous} is still open.")
                return False
            self.show_start(f"Cannot open {location}:\n{describe_error(exc)}")
            if not quiet:
                self.error("Cannot open workspace", exc)
            return False
        if previous != Path(location):
            self.close_workspace_windows()
        self.setWindowTitle(f"RevisionWorx {__version__} — {info['name']}")
        self.alpha_banner.setText(self.status_vm.notice)
        self.ws_name.setText(info["name"])
        self.ws_sub.setText(str(location))
        self.ws_sub.setToolTip(str(location))
        if select_project is None and restore_selection:  # app start: back where you left off
            select_project = (self.ctl.preference("last_project", {}) or {}).get(info["id"])
        self._fill_recent()
        self.stack.setCurrentWidget(self.workspace_page)
        self.status_vm.check()
        self.list_vm.set_query(self.search.text(), self.archived.isChecked())
        self.select_project(select_project)
        if self.ctl.preferences_warning:
            self.statusBar().showMessage(self.ctl.preferences_warning, 15000)
        self.update_checkouts_label()
        self.update_team_state()
        if self.ctl.team_mode and self.ctl.my_account() is None:
            QTimer.singleShot(0, self.ask_login)
        return True

    # ------------------------------------------------------------------ a team working from a shared folder (ADR-024)
    def _read_team_history(self, location: Path) -> None:
        """A computer reads and checks a team workspace's whole journal the first time it opens it. After a year of use, on a network, that
        takes a while, so it happens on another thread with the window alive. Whatever goes wrong, the real open says so straight after."""
        if not Workspace._is_shared_folder(Workspace.meta_dir_for(location)):
            return
        done = threading.Event()

        def read() -> None:
            try:
                with Workspace.open(location, state_dir=self.ctl.state_dir):
                    pass
            except Exception:  # noqa: BLE001 - see above
                pass
            finally:
                done.set()
        threading.Thread(target=read, name="revisionworx-team-history", daemon=True).start()
        if done.wait(0.3):
            return
        progress = QProgressDialog("Reading this workspace's history. It's only slow the first time on each computer.", "", 0, 0, self)
        progress.setWindowTitle("Opening workspace")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        while not done.wait(0.03):
            QApplication.processEvents()
        progress.close()

    def update_team_state(self) -> None:
        """Who's logged in, in the status bar, and what the Team menu offers."""
        try:
            team = self.ctl.team()
        except Exception:  # noqa: BLE001 - a status hint must never raise
            team = {"team": False, "account": None, "role": None, "must_change": False, "head": None}
        is_team, account = team["team"], team["account"]
        self._team_head = team.get("head")
        self.login_act.setEnabled(is_team and account is None)
        self.logout_act.setEnabled(is_team and account is not None)
        self.password_act.setEnabled(is_team and account is not None)
        self.accounts_act.setEnabled(is_team)
        self.set_up_team_act.setEnabled(self.ctl.location is not None and not is_team)
        self.check_folder_act.setEnabled(self.ctl.location is not None)
        if not is_team:
            self.team_label.setText("")
        elif account is None:
            self.team_label.setText("Not logged in: you can look, but not change anything")
        else:
            role = "administrator" if team["role"] == "admin" else "user"
            owed = ": choose a password (Team menu) before you do anything else" if team["must_change"] else ""
            self.team_label.setText(f"{team['name'] or account} ({role}){owed}")

    def ask_login(self) -> None:
        if self.ctl.location is None or not self.ctl.team_mode or self.ctl.my_account() is not None:
            return
        dialog = LoginDialog(self.ctl, self, str(self.ctl.preference("team_user", "") or ""))
        if not dialog.exec():
            self.update_team_state()
            return
        if self.ctl.my_account():
            self.ctl.set_preference("team_user", self.ctl.my_account())
        if dialog.outcome and dialog.outcome.get("must_change"):
            ChangePasswordDialog(self.ctl, self, current=dialog.typed_password, first_time=True).exec()
        self.refresh_all()
        self.update_checkouts_label()
        self.update_team_state()

    def team_log_out(self) -> None:
        self.ctl.log_out()
        self.update_checkouts_label()
        self.update_team_state()

    def change_password(self) -> None:
        if ChangePasswordDialog(self.ctl, self).exec():
            self.statusBar().showMessage("Your password was changed.", 8000)
        self.update_team_state()

    def show_accounts(self) -> None:
        AccountsDialog(self.ctl, self).exec()
        self.update_team_state()

    def set_up_team(self) -> None:
        if self.refuse_while_busy():
            return
        dialog = SetUpTeamDialog(self.ctl, self)
        if not dialog.exec() or not dialog.made:
            return
        RecoveryCodeDialog(dialog.made["recovery_code"], self).exec()
        self.open_location(self.ctl.location, select_project=self.selected_project_id())

    def _watch_team(self) -> None:
        """Others' changes appear without anyone pressing F5. A share can stall for seconds, so the look goes on another thread."""
        if not self.ctl.team_mode:
            return
        seen = self._team_seen
        if seen is not None and not self.ctl.busy and QApplication.activeModalWidget() is None:
            self._team_seen = None
            if seen["head"] != self._team_head:
                self._team_head = seen["head"]
                if seen["others"]:                   # one's own changes have already been shown
                    self.refresh_all()
                    self.update_checkouts_label()
                    self.update_team_state()
                    names = ", ".join(seen["others"][:3])
                    self.statusBar().showMessage(f"Updated: {names} made a change.", 5000)
        self._team_ticks += 1
        if self._team_ticks < TEAM_WATCH_TICKS or (self._team_probe is not None and self._team_probe.is_alive()):
            return
        self._team_ticks = 0
        head = self._team_head

        def look() -> None:
            try:
                self._team_seen = self.ctl.changes_since(head)
            except Exception:  # noqa: BLE001 - it looks again in five seconds, and F5 shows anything wrong
                pass
        self._team_probe = threading.Thread(target=look, name="revisionworx-team-watch", daemon=True)
        self._team_probe.start()

    def close_workspace_windows(self) -> None:
        """Dialogs that are not modal belong to the workspace they were opened for. Left open, their
        next action would write to whichever workspace is open by then."""
        windows = [*self.adopt_dialogs, *self.documents_tab.dialogs]
        self.adopt_dialogs.clear()
        self.documents_tab.dialogs.clear()
        for dlg in windows:
            dlg.reject()

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
        self.close_workspace_windows()
        self.ctl.close_workspace()
        self.setWindowTitle(f"RevisionWorx {__version__}")
        self.alpha_banner.setText(self.status_vm.notice)
        self.ws_name.setText("RevisionWorx")
        self.ws_sub.setText("")
        self.checkouts_label.setText("")
        self.update_team_state()
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
        if self.ctl.location is None:
            return
        if not self.ctl.start_rebuild_index(on_done=lambda r: self.error("Rebuild failed", r.error) if r.error
                                            else self.list_vm.refresh()):
            QMessageBox.information(self, "Busy", "Another task is still running; try again in a moment.")

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
        if pid and self.ctl.workspace_id:
            last = dict(self.ctl.preference("last_project", {}) or {})
            if last.get(self.ctl.workspace_id) != pid:
                last[self.ctl.workspace_id] = pid
                self.ctl.set_preference("last_project", last)

    def filter_files(self) -> None:
        self.detail_vm.file_filter = self.file_filter.text()
        if self.detail_vm.project:
            try:
                self.detail_vm.load(self.detail_vm.project["id"])
            except Exception as exc:
                self.error("Cannot filter files", exc)

    def open_folder(self) -> None:
        if self.detail_vm.can_open_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.detail_vm.project["folder"]))

    def toggle_archive(self) -> None:
        try:
            self.detail_vm.toggle_archive()
        except Exception as exc:
            self.error("Cannot change status", exc)
            if isinstance(exc, (CommittedNotApplied, SavedWithWarning)):  # saved: show the new status
                self.selection_changed()
        self.list_vm.refresh()

    def new_project(self) -> None:
        if self.ctl.location is None:
            self.show_start()
            self.start_page.name_edit.setFocus()
            return
        if self.ctl.read_only:
            return
        try:
            dlg = NewProjectDialog(self.ctl, self)
        except Exception as exc:
            self.error("Cannot create a project", exc)
            return
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
            if not busy:
                self.update_checkouts_label()
        for ev in events:
            if isinstance(ev, Progress):
                for d in self.adopt_dialogs:
                    d.flow.on_progress(ev)
        self._watch_team()

    # ------------------------------------------------------------------ redesign: themes, palette, about, layout
    def set_theme(self, name: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        self.theme_name = theme.apply(app, name)
        self.ctl.set_preference("theme", self.theme_name)
        for act in self.theme_actions.actions():
            act.setChecked(act.data() == self.theme_name)
        for view in (self.table, self.documents_tab.table, self.documents_tab.history, self.files):
            view.viewport().update()
        self.documents_tab.render_panel()
        self.render_health()

    def show_shortcuts(self) -> None:
        QMessageBox.information(self, "Keyboard shortcuts", SHORTCUTS_TEXT)

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def palette_actions(self) -> list:
        tab, p = self.documents_tab, self.documents_tab.panel
        has_ws = lambda: self.ctl.location is not None  # noqa: E731
        return [
            ("Check out the selected document", "edit reserve lock", tab.checkout, lambda: p.can_checkout),
            ("Check in the selected document…", "save revision commit", tab.checkin, lambda: p.can_checkin),
            ("Import a file into this project…", "add upload copy", tab.import_file, lambda: tab.list_vm.can_add),
            ("Open the selected document's file", "launch cad", tab.open_file, lambda: p.can_open_file),
            ("Show the selected document in its folder", "explorer finder", tab.open_folder, lambda: bool(p.detail)),
            ("My checkouts…", "mine checked out", self.show_my_checkouts, has_ws),
            ("New project…", "create", self.new_project, lambda: has_ws() and not self.ctl.read_only),
            ("Adopt an existing project…", "register folder", self.adopt, lambda: has_ws() and not self.ctl.read_only),
            ("Open the project folder", "explorer", self.open_folder, lambda: self.detail_vm.can_open_folder),
            ("Check workspace health", "doctor verify", lambda: self.status_vm.check(), has_ws),
            ("Refresh", "reload f5", self.refresh_all, has_ws),
            ("Open workspace…", "switch", self.open_ws, lambda: True),
            *[(f"Colour theme: {spec['label']}", "theme dark light colour color", (lambda k=k: self.set_theme(k)),
               lambda: True) for k, spec in theme.THEMES.items()],
            ("Keyboard shortcuts", "help keys", self.show_shortcuts, lambda: True),
            ("About RevisionWorx", "version licence", self.show_about, lambda: True),
        ]

    def open_palette(self) -> PaletteDialog:
        model = CommandPalette(self.ctl, self.palette_actions())
        dlg = PaletteDialog(model, self._palette_chose, self)
        dlg.show()
        dlg.search.setFocus()
        self._palette = dlg
        return dlg

    def _palette_chose(self, kind: str, payload) -> None:
        if kind == "action":
            payload()
        elif kind == "project":
            self.reveal_project(payload)
        elif kind == "document":
            self.go_to_document(*payload)

    def reveal_project(self, project_id: str) -> None:
        """Select a project even if the current search or page hides it."""
        if self.list_vm.index_of(project_id) is None:
            self.search.clear()
            self.archived.setChecked(True)
            self.list_vm.set_query("", True)
        self.select_project(project_id)
        self.tabs.setCurrentWidget(self.documents_tab)

    def go_to_document(self, project_id: str | None, doc_id: str) -> bool:
        if project_id is None:
            try:
                project_id = self.ctl.document_detail(doc_id)["document"]["project_id"]
            except Exception as exc:
                self.error("Cannot show the document", exc)
                return False
        self.reveal_project(project_id)
        self.documents_tab.filter.clear()
        if self.documents_tab.list_vm.text:
            self.documents_tab.apply_filter()
        found = self.documents_tab.select_document(doc_id)
        if found:
            self.documents_tab.table.setFocus()
        return found

    def show_my_checkouts(self) -> None:
        if self.ctl.location is None:
            return
        try:
            mine = self.ctl.my_checkouts()
        except Exception as exc:
            self.error("Cannot list your checkouts", exc)
            return
        if not mine:
            QMessageBox.information(self, "My checkouts", "You have no documents checked out in this workspace.")
            return
        model = CommandPalette(self.ctl, [])
        model.items = [{"kind": "document", "title": " ".join(x for x in (d["number"], d["title"]) if x) or d["rel_path"],
                        "subtitle": f"Checked out by you · {d['rel_path']}", "payload": (d["project_id"], d["id"])}
                       for d in mine]
        dlg = PaletteDialog(model, self._palette_chose, self)
        dlg.setWindowTitle("My checkouts")
        dlg.search.hide()
        dlg.render()
        dlg.show()
        dlg.list.setFocus()
        self._palette = dlg

    def update_checkouts_label(self) -> None:
        if self.ctl.location is None:
            self.checkouts_label.setText("")
            return
        try:
            n = len(self.ctl.my_checkouts())
        except Exception:  # noqa: BLE001 - a status hint must never raise
            n = 0
        self.checkouts_label.setText(f"{n} checked out by you  (Ctrl+Shift+M)" if n else "")

    # drag and drop: files dropped on the window are imported into the selected project
    def _dropped_files(self, event) -> list[Path]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile() and Path(u.toLocalFile()).is_file()]

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._dropped_files(event) and self.documents_tab.list_vm.can_add:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        files = self._dropped_files(event)
        if files:
            event.acceptProposedAction()
            self.import_dropped(files)

    def import_dropped(self, files: list[Path]) -> None:
        if not self.documents_tab.list_vm.can_add:
            return
        if len(files) > 10:
            QMessageBox.information(self, "Too many files", "Drop up to 10 files at a time; each gets its own "
                                    "import window so you can give it a number and title.")
            return
        self.tabs.setCurrentWidget(self.documents_tab)
        for f in files:
            self.documents_tab.import_file(f)

    # remembered window layout
    def _save_layout(self) -> None:
        try:
            self.ctl.set_preference("window", {
                "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii"),
                "main_split": self.main_split.sizes(), "doc_split": self.documents_tab.split.sizes()})
        except Exception:  # noqa: BLE001 - never stop the window closing
            pass

    def _restore_layout(self) -> None:
        saved = self.ctl.preference("window", {}) or {}
        try:
            if saved.get("geometry"):
                self.restoreGeometry(QByteArray.fromBase64(saved["geometry"].encode("ascii")))
            if isinstance(saved.get("main_split"), list) and len(saved["main_split"]) == 2:
                self.main_split.setSizes([int(x) for x in saved["main_split"]])
            if isinstance(saved.get("doc_split"), list) and len(saved["doc_split"]) == 2:
                self.documents_tab.split.setSizes([int(x) for x in saved["doc_split"]])
        except (TypeError, ValueError, AttributeError):
            pass

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
