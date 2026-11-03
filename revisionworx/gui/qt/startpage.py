# SPDX-License-Identifier: GPL-3.0-or-later
"""Start page shown when no workspace is open: quick-start a local project, open an existing
workspace, or reopen a recent one. Validation lives in ``QuickStartForm``; this only binds widgets."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..controller import ALPHA_NOTICE, Controller, describe_error
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
        page = QHBoxLayout(self)
        page.addStretch(1)
        column = QWidget(self)
        column.setMaximumWidth(480)
        column.setMinimumWidth(380)
        outer = QVBoxLayout(column)
        outer.setContentsMargins(0, 20, 0, 20)
        outer.setSpacing(14)
        page.addWidget(column, 4)
        page.addStretch(1)
        outer.addStretch(1)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo = QLabel("logo", column)  # placeholder until the Director's artwork is in the repository
        logo.setProperty("role", "logo")
        logo.setFixedSize(44, 44)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setAccessibleName("RevisionWorx logo placeholder")
        name = QLabel("RevisionWorx", column)
        name.setStyleSheet("font-size: 20px; font-weight: 600")
        brand.addWidget(logo)
        brand.addWidget(name, 1)
        outer.addLayout(brand)
        title = QLabel("Start a new project", column)
        title.setProperty("role", "welcome")
        outer.addWidget(title)
        tip = QLabel("Every revision of your engineering files, kept safe on this computer. "
                     "No account or network needed.", column)
        tip.setProperty("role", "muted")
        tip.setWordWrap(True)
        outer.addWidget(tip)

        box = QFrame(column)
        box.setObjectName("card")
        form = QVBoxLayout(box)
        form.setContentsMargins(18, 16, 18, 18)
        form.setSpacing(6)

        def field(label_text: str, widget) -> None:
            label = QLabel(label_text, box)
            label.setStyleSheet("font-weight: 500")
            label.setBuddy(widget)
            form.addWidget(label)
            form.addWidget(widget)
            form.addSpacing(6)

        self.name_edit = QLineEdit(box)
        self.name_edit.setPlaceholderText("e.g. Hydraulic Manifold")
        self.name_edit.setAccessibleName("Project name")
        self.number_edit = QLineEdit(box)
        self.number_edit.setPlaceholderText("e.g. P-1042")
        self.number_edit.setAccessibleName("Project number")
        self.customer_edit = QLineEdit(box)  # set later, in the project's details
        self.customer_edit.setVisible(False)
        field("Project &name", self.name_edit)
        field("Project n&umber (optional)", self.number_edit)
        row = QWidget(box)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit(self.form.folder, row)
        self.folder_edit.setAccessibleName("Projects folder")
        browse = QPushButton("&Browse…", row)
        browse.clicked.connect(lambda *_: self.browse())
        rl.addWidget(self.folder_edit, 1)
        rl.addWidget(browse)
        field("Saved in &folder", row)
        row.setFocusProxy(self.folder_edit)
        self.preview = elastic(QLabel(box))
        self.preview.setProperty("role", "small")
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addWidget(self.preview)
        self.error_label = elastic(QLabel(box))
        self.error_label.setObjectName("formError")
        form.addWidget(self.error_label)
        self.create_btn = QPushButton("Create &project", box)
        self.create_btn.setProperty("role", "primary")
        self.create_btn.setDefault(True)
        self.create_btn.clicked.connect(lambda *_: self.create())
        form.addSpacing(4)
        form.addWidget(self.create_btn)
        outer.addWidget(box)

        other = QHBoxLayout()
        already = QLabel("Already use RevisionWorx?", column)
        already.setProperty("role", "muted")
        self.open_btn = QPushButton("&Open an existing workspace", column)
        self.open_btn.setProperty("role", "link")
        self.open_btn.clicked.connect(lambda *_: self.open_requested.emit())
        other.addWidget(already, 1)
        other.addWidget(self.open_btn)
        outer.addLayout(other)
        self.recent_label = QLabel("Recent", column)
        self.recent_label.setProperty("role", "section")
        outer.addWidget(self.recent_label)
        self.recent = QListWidget(column)
        self.recent.setAccessibleName("Recent workspaces")
        self.recent.setMaximumHeight(110)
        self.recent.itemActivated.connect(lambda item: self.opened.emit(item.data(Qt.ItemDataRole.UserRole)))
        outer.addWidget(self.recent)
        notice = QLabel(ALPHA_NOTICE, column)
        notice.setObjectName("startAlphaNotice")
        notice.setProperty("role", "alpha")
        notice.setWordWrap(True)
        outer.addWidget(notice)
        outer.addStretch(1)

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
        self.recent.setVisible(self.recent.count() > 0)
        self.recent_label.setVisible(self.recent.count() > 0)

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
