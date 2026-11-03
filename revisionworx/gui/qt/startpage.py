# SPDX-License-Identifier: GPL-3.0-or-later
"""Start page shown when no workspace is open: quick-start a local project, open an existing
workspace, or reopen a recent one. Validation lives in ``QuickStartForm``; this only binds widgets."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from ..controller import Controller, describe_error
from ...workspace import CommittedNotApplied
from ..viewmodels import QuickStartForm
from .models import elastic


def default_projects_folder() -> str:
    docs = Path.home() / "Documents"
    return str((docs if docs.is_dir() else Path.home()) / "RevisionWorx Projects")


class StartPage(QWidget):
    """Emits ``opened`` with the workspace location after a successful create/open."""
    opened = Signal(str)
    open_requested = Signal()

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.form = QuickStartForm(folder=default_projects_folder())
        self.last_created_id: str | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        title = QLabel("<h2>Welcome to RevisionWorx</h2>"
                       "<p>Revision control for your engineering files, entirely on this computer. "
                       "No account or network connection is needed.</p>", self)
        title.setWordWrap(True)
        outer.addWidget(title)

        box = QGroupBox("Create a new local project", self)
        form = QFormLayout(box)
        row = QWidget(box)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit(self.form.folder, row)
        self.folder_edit.setAccessibleName("Projects folder")
        browse = QPushButton("&Browse…", row)
        browse.clicked.connect(lambda *_: self.browse())
        rl.addWidget(self.folder_edit, 1)
        rl.addWidget(browse)
        form.addRow("Projects &folder", row)
        self.name_edit = QLineEdit(box)
        self.name_edit.setPlaceholderText("e.g. Conveyor Bracket")
        self.name_edit.setAccessibleName("Project name")
        self.number_edit = QLineEdit(box)
        self.number_edit.setPlaceholderText("optional, e.g. P-1001")
        self.customer_edit = QLineEdit(box)
        self.customer_edit.setPlaceholderText("optional")
        form.addRow("Project &name *", self.name_edit)
        form.addRow("Project n&umber", self.number_edit)
        form.addRow("&Customer", self.customer_edit)
        self.preview = elastic(QLabel(box))
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Will create", self.preview)
        self.error_label = elastic(QLabel(box))
        self.error_label.setStyleSheet("color: #b00020")
        form.addRow(self.error_label)
        self.create_btn = QPushButton("Create &project", box)
        self.create_btn.setDefault(True)
        self.create_btn.clicked.connect(lambda *_: self.create())
        form.addRow(self.create_btn)
        outer.addWidget(box)

        other = QGroupBox("Open an existing workspace", self)
        ol = QVBoxLayout(other)
        self.open_btn = QPushButton("&Open workspace folder…", other)
        self.open_btn.clicked.connect(lambda *_: self.open_requested.emit())
        ol.addWidget(self.open_btn)
        ol.addWidget(QLabel("Recent:", other))
        self.recent = QListWidget(other)
        self.recent.setAccessibleName("Recent workspaces")
        self.recent.itemActivated.connect(lambda item: self.opened.emit(item.data(Qt.ItemDataRole.UserRole)))
        ol.addWidget(self.recent, 1)
        outer.addWidget(other, 1)

        for edit in (self.folder_edit, self.name_edit, self.number_edit, self.customer_edit):
            edit.textChanged.connect(lambda *_: self.sync())
            edit.returnPressed.connect(lambda *_: self.create())
        for name in ("folder_edit", "name_edit", "number_edit", "customer_edit", "preview", "error_label",
                     "create_btn", "open_btn", "recent"):
            getattr(self, name).setObjectName("start_" + name)
        self.sync()
        self.fill_recent()

    def fill_recent(self) -> None:
        self.recent.clear()
        for loc in self.ctl.recent_workspaces():
            missing = not Path(loc).exists()
            item = QListWidgetItem(loc + ("   (not found)" if missing else ""))
            item.setData(Qt.ItemDataRole.UserRole, loc)
            self.recent.addItem(item)

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.setToolTip(text)

    def sync(self) -> None:
        self.form.folder = self.folder_edit.text()
        self.form.name = self.name_edit.text()
        self.form.number = self.number_edit.text()
        self.form.customer = self.customer_edit.text()
        self.preview.setText(self.form.project_path_preview or "—")
        self.preview.setToolTip(self.form.project_path_preview)

    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder for your RevisionWorx projects", self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)

    def create(self) -> bool:
        self.sync()
        if not self.form.validate():
            self.show_error("\n".join(self.form.errors.values()))
            return False
        try:
            self.last_created_id = self.form.submit(self.ctl)["id"]
        except CommittedNotApplied as exc:  # saved (CORE_API §4): open it, never invite a second try
            self.last_created_id = None
            QMessageBox.warning(self, "Saved with a warning", describe_error(exc))
        except Exception as exc:
            self.show_error(describe_error(exc))
            return False
        self.error_label.clear()
        self.name_edit.clear()
        self.number_edit.clear()
        self.customer_edit.clear()
        self.opened.emit(self.form.folder)
        return True
