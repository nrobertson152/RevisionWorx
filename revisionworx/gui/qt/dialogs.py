# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt dialogs. Validation and workflow state live in ``viewmodels``; these only bind widgets."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ...workspace import CommittedNotApplied
from ..controller import Controller, describe_error
from ..viewmodels import AdoptFlow, CheckinForm, ImportFlow, NewProjectForm


def error_label(parent) -> QLabel:
    label = QLabel(parent)
    label.setObjectName("formError")
    label.setStyleSheet("color: #b00020")
    label.setWordWrap(True)
    return label


class CheckinDialog(QDialog):
    """Collects a meaningful change description (required), optional label and the explicit
    "check in unchanged" override. ``form`` holds the accepted values."""

    def __init__(self, doc_title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Check in — {doc_title}")
        self.resize(520, 320)
        self.form = CheckinForm()
        layout = QFormLayout(self)
        self.description = QPlainTextEdit(self)
        self.description.setPlaceholderText("What changed and why? e.g. “Widened mounting slot to 8 mm per ECR-12”")
        self.description.setAccessibleName("Change description")
        self.description.setTabChangesFocus(True)
        layout.addRow("&Description *", self.description)
        self.label = QLineEdit(self)
        self.label.setPlaceholderText("automatic")
        layout.addRow("Revision &label", self.label)
        self.unchanged = QCheckBox("Check in even if the file is &unchanged", self)
        layout.addRow(self.unchanged)
        self.error = error_label(self)
        layout.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Check &in")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.description.setFocus()

    def submit(self) -> None:
        self.form.description = self.description.toPlainText()
        self.form.label = self.label.text()
        self.form.allow_unchanged = self.unchanged.isChecked()
        if not self.form.validate():
            self.error.setText("\n".join(self.form.errors.values()))
            return
        self.accept()


class ImportDialog(QDialog):
    """Copy an outside file into the selected managed project and record revision 1."""

    def __init__(self, ctl: Controller, project_id: str, project_name: str, parent=None, on_done=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import file into {project_name}")
        self.resize(620, 340)
        self.flow = ImportFlow(ctl, project_id)
        self._on_done = on_done
        layout = QFormLayout(self)
        row = QWidget(self)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.source = QLineEdit(row)
        self.source.setAccessibleName("File to import")
        browse = QPushButton("&Browse…", row)
        browse.clicked.connect(lambda *_: self.browse())
        rl.addWidget(self.source, 1)
        rl.addWidget(browse)
        layout.addRow("&File *", row)
        self.folder = QComboBox(self)
        for sub in self.flow.subfolders:
            self.folder.addItem(sub or "(project folder)", sub)
        layout.addRow("Into f&older", self.folder)
        self.dest = QLabel(self)
        self.dest.setWordWrap(True)
        layout.addRow("Saved as", self.dest)
        self.number, self.title = QLineEdit(self), QLineEdit(self)
        self.number.setPlaceholderText("optional company document number")
        layout.addRow("Document &number", self.number)
        layout.addRow("&Title", self.title)
        self.description = QLineEdit(self.flow.description, self)
        layout.addRow("Revision &description *", self.description)
        note = QLabel("The original file is copied, never moved or changed. An existing file in the "
                      "project is never overwritten.", self)
        note.setWordWrap(True)
        layout.addRow(note)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        layout.addRow(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                        QDialogButtonBox.StandardButton.Close, parent=self)
        self.import_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_btn.setText("&Import")
        self.buttons.accepted.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        self.source.textChanged.connect(lambda *_: self._sync())
        self.folder.currentIndexChanged.connect(lambda *_: self._sync())
        self.flow.subscribe(self.render)
        self.render()

    def _sync(self) -> None:
        self.flow.subfolder = self.folder.currentData() or ""
        if self.source.text() != self.flow.source:
            self.flow.title = self.title.text()
            self.flow.set_source(self.source.text())
            self.title.setText(self.flow.title)
        self.render()

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "File to import (it is copied, not moved)")
        if path:
            self.source.setText(path)

    def start(self) -> bool:
        self._sync()
        self.flow.number = self.number.text()
        self.flow.title = self.title.text()
        self.flow.description = self.description.text()
        return self.flow.start(on_done=self._done)

    def _done(self, res) -> None:
        if self._on_done:
            self._on_done(res)

    def render(self) -> None:
        self.dest.setText(self.flow.destination_preview or "—")
        self.import_btn.setEnabled(self.flow.can_import)
        error = self.flow.state == "error" or bool(self.flow.errors)
        self.message.setStyleSheet("color: #b00020" if error else "")
        self.message.setText(self.flow.message)


class NewProjectDialog(QDialog):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New project")
        self.ctl = ctl
        self.form = NewProjectForm(roots=ctl.roots(), templates=ctl.templates())
        self.result_record: dict | None = None
        layout = QFormLayout(self)
        self.edits = {}
        for key, label in (("name", "Project name *"), ("number", "Project number"),
                           ("customer", "Customer"), ("description", "Description"),
                           ("rel_path", "Folder inside root (optional)")):
            edit = QLineEdit(self)
            self.edits[key] = edit
            layout.addRow(label, edit)
        self.edits["rel_path"].setPlaceholderText("default: workspace path pattern")
        self.root_box = QComboBox(self)
        for r in self.form.roots:
            self.root_box.addItem(r["name"], r["id"])
        layout.addRow("Engineering root", self.root_box)
        self.tpl_box = QComboBox(self)
        for tid, t in self.form.templates.items():
            self.tpl_box.addItem(t["name"], tid)
        self.tpl_box.setCurrentIndex(max(0, self.tpl_box.findData("standard")))
        layout.addRow("Folder template", self.tpl_box)
        self.error_label = QLabel(self)
        self.error_label.setStyleSheet("color: #b00020")
        self.error_label.setWordWrap(True)
        layout.addRow(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel, parent=self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def submit(self) -> None:
        for key, edit in self.edits.items():
            setattr(self.form, key, edit.text())
        self.form.root_id = self.root_box.currentData()
        self.form.template_id = self.tpl_box.currentData()
        if not self.form.validate():
            self.error_label.setText("\n".join(self.form.errors.values()))
            return
        try:
            self.result_record = self.form.submit(self.ctl)
        except CommittedNotApplied as exc:  # saved (CORE_API §4): never invite a second try
            QMessageBox.warning(self, "Saved with a warning", describe_error(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Could not create project", describe_error(exc))
            return
        self.accept()


class AdoptDialog(QDialog):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adopt existing project")
        self.resize(760, 560)
        self.flow = AdoptFlow(ctl)
        self.flow.subscribe(self.render)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        row = QWidget(self)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.folder_edit = QLineEdit(row)
        self.folder_edit.setReadOnly(True)
        browse = QPushButton("Browse…", row)
        browse.clicked.connect(lambda *_: self.browse())
        rl.addWidget(self.folder_edit)
        rl.addWidget(browse)
        form.addRow("Folder", row)
        self.name_edit, self.number_edit, self.customer_edit = QLineEdit(self), QLineEdit(self), QLineEdit(self)
        form.addRow("Project name *", self.name_edit)
        form.addRow("Project number", self.number_edit)
        form.addRow("Customer", self.customer_edit)
        modes = QWidget(self)
        ml = QHBoxLayout(modes)
        ml.setContentsMargins(0, 0, 0, 0)
        self.indexed_radio = QRadioButton("Indexed (search only)", modes)
        self.managed_radio = QRadioButton("Managed", modes)
        self.indexed_radio.setChecked(True)
        ml.addWidget(self.indexed_radio)
        ml.addWidget(self.managed_radio)
        ml.addStretch(1)
        form.addRow("Mode", modes)
        outer.addLayout(form)
        self.report = QPlainTextEdit(self)
        self.report.setReadOnly(True)
        outer.addWidget(self.report, 1)
        self.progress = QLabel(self)
        outer.addWidget(self.progress)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.preview_btn = QPushButton("Preview", self)
        self.cancel_btn = QPushButton("Cancel scan", self)
        self.adopt_btn = QPushButton("Adopt", self)
        close = QPushButton("Close", self)
        for b in (self.preview_btn, self.cancel_btn, self.adopt_btn, close):
            buttons.addWidget(b)
        outer.addLayout(buttons)
        self.preview_btn.clicked.connect(lambda *_: self.flow.preview())
        self.cancel_btn.clicked.connect(lambda *_: self.flow.cancel())
        self.adopt_btn.clicked.connect(lambda *_: self.adopt())
        close.clicked.connect(lambda *_: self.reject())
        self.name_edit.textChanged.connect(self._sync_fields)
        self.render()

    def _sync_fields(self, *_args) -> None:
        self.flow.name = self.name_edit.text()
        self.adopt_btn.setEnabled(self.flow.can_adopt)

    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Project folder to adopt")
        if d:
            self.flow.set_folder(d)
            self.folder_edit.setText(d)
            if not self.name_edit.text():
                self.name_edit.setText(self.flow.name)

    def adopt(self) -> None:
        self.flow.name = self.name_edit.text()
        self.flow.number = self.number_edit.text()
        self.flow.customer = self.customer_edit.text()
        self.flow.mode = "managed" if self.managed_radio.isChecked() else "indexed"
        answer = QMessageBox.question(self, "Adopt project", "Register this folder? Files are not "
                                      "modified; a baseline inventory with SHA-256 hashes is recorded.")
        if answer == QMessageBox.StandardButton.Yes:
            self.flow.adopt()

    def render(self) -> None:
        self.report.setPlainText(self.flow.report_text)
        self.progress.setText(self.flow.progress_text)
        self.preview_btn.setEnabled(self.flow.can_preview)
        self.cancel_btn.setEnabled(self.flow.busy)
        self.adopt_btn.setEnabled(self.flow.can_adopt)

    def reject(self) -> None:
        self.flow.cancel()
        super().reject()
