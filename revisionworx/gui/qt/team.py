# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialogs for a team working from a shared folder (ADR-024): logging in, passwords, accounts and setting it up."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..controller import Controller, describe_error
from .dialogs import error_label

ROLE_NAMES = {"admin": "Administrator", "user": "Regular user"}
REMEMBER_HOURS = 12.0


def _password_field(parent, name: str) -> QLineEdit:
    field = QLineEdit(parent)
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setAccessibleName(name)
    return field


def _note(text: str, parent) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _say(exc: Exception) -> str:
    """An error as a sentence: messages from the core read well in the middle of one, and these stand alone."""
    text = describe_error(exc).strip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def _fit(dialog: QDialog, width: int) -> None:
    """Exactly tall enough for its wrapped text at this width, whatever the fonts on this computer are: a fixed height clips it."""
    layout = dialog.layout()
    dialog.resize(width, max(layout.totalHeightForWidth(width), layout.totalMinimumSize().height()) + 12)


def _buttons(dialog, ok_text: str) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    buttons.accepted.connect(dialog.submit)
    buttons.rejected.connect(dialog.reject)
    return buttons


class LoginDialog(QDialog):
    """``outcome`` holds what logging in returned, once it's accepted."""

    def __init__(self, ctl: Controller, parent=None, account: str = ""):
        super().__init__(parent)
        self.ctl = ctl
        self.outcome: dict | None = None
        self.setWindowTitle("Log in")
        self.setObjectName("loginDialog")
        form = QFormLayout(self)
        form.addRow(_note("This workspace is shared by a team. Log in to make changes. Without logging in you can still look.", self))
        self.account = QLineEdit(account, self)
        self.account.setAccessibleName("User name")
        form.addRow("&User name", self.account)
        self.password = _password_field(self, "Password")
        form.addRow("&Password", self.password)
        self.remember = QCheckBox(f"&Stay logged in on this computer for {REMEMBER_HOURS:g} hours", self)
        self.remember.setToolTip("Other programs, such as OpenSchematix, can then save revisions in your name. Don't use this on a "
                                 "computer you share.")
        form.addRow(self.remember)
        self.error = error_label(self)
        form.addRow(self.error)
        buttons = _buttons(self, "&Log in")
        self.recover_btn = buttons.addButton("Use &recovery code…", QDialogButtonBox.ButtonRole.ActionRole)
        self.recover_btn.clicked.connect(lambda *_: self.recover())
        form.addRow(buttons)
        _fit(self, 440)
        (self.password if account else self.account).setFocus()

    def submit(self) -> None:
        self.error.setText("")
        try:
            self.outcome = self.ctl.log_in(self.account.text(), self.password.text(),
                                           remember_hours=REMEMBER_HOURS if self.remember.isChecked() else None)
        except Exception as exc:
            self.error.setText(_say(exc))
            self.password.selectAll()
            self.password.setFocus()
            return
        self.typed_password = self.password.text()
        self.accept()

    def recover(self) -> None:
        dialog = RecoverDialog(self.ctl, self, self.account.text())
        if dialog.exec():
            self.outcome = {"session": None, "must_change": False, "role": "admin"}
            self.typed_password = ""
            self.accept()


class ChangePasswordDialog(QDialog):
    def __init__(self, ctl: Controller, parent=None, current: str | None = None, first_time: bool = False):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Choose your password" if first_time else "Change my password")
        self.setObjectName("changePasswordDialog")
        form = QFormLayout(self)
        if first_time:
            form.addRow(_note("You were given this password to get started. Choose your own before doing anything else. "
                              "Nobody else will know it, and it isn't stored anywhere.", self))
        self.current = _password_field(self, "Current password")
        if current is None:
            form.addRow("&Current password", self.current)
        else:
            self.current.setText(current)            # just typed at the login: not asked for again, and not on show
            self.current.hide()
        self.new = _password_field(self, "New password")
        form.addRow("&New password", self.new)
        self.again = _password_field(self, "New password again")
        form.addRow("&Again", self.again)
        form.addRow(_note("Anyone who can read the shared folder could try to guess it on their own computer, so choose a long one: "
                          "a few words is better than a few symbols.", self))
        self.error = error_label(self)
        form.addRow(self.error)
        form.addRow(_buttons(self, "&Change password"))
        _fit(self, 480)
        (self.current if current is None else self.new).setFocus()

    def submit(self) -> None:
        self.error.setText("")
        if self.new.text() != self.again.text():
            self.error.setText("Those don't match.")
            return
        try:
            self.ctl.change_password(self.current.text(), self.new.text())
        except Exception as exc:
            self.error.setText(_say(exc))
            return
        self.accept()


class RecoverDialog(QDialog):
    """An administrator account on this computer, with the recovery code."""

    def __init__(self, ctl: Controller, parent=None, account: str = ""):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Use recovery code")
        self.setObjectName("recoverDialog")
        form = QFormLayout(self)
        form.addRow(_note("The recovery code was shown when this workspace was set up for a team. It makes this computer's copy of the "
                          "account an administrator's, with a new password. Any other computer's login for that account stops working.", self))
        self.code = QLineEdit(self)
        self.code.setAccessibleName("Recovery code")
        form.addRow("Recovery &code", self.code)
        self.account = QLineEdit(account, self)
        self.account.setAccessibleName("Administrator's user name")
        form.addRow("&User name", self.account)
        self.new = _password_field(self, "New password")
        form.addRow("&New password", self.new)
        self.again = _password_field(self, "New password again")
        form.addRow("&Again", self.again)
        self.error = error_label(self)
        form.addRow(self.error)
        form.addRow(_buttons(self, "&Recover"))
        _fit(self, 520)
        self.code.setFocus()

    def submit(self) -> None:
        self.error.setText("")
        if self.new.text() != self.again.text():
            self.error.setText("Those don't match.")
            return
        try:
            self.ctl.recover_admin(self.code.text(), self.account.text(), self.new.text())
        except Exception as exc:
            self.error.setText(_say(exc))
            return
        self.accept()


class SetUpTeamDialog(QDialog):
    """Turn team use on. ``made`` holds the recovery code and the login, once it's accepted."""

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.made: dict | None = None
        self.setWindowTitle("Set up for a team")
        self.setObjectName("setUpTeamDialog")
        form = QFormLayout(self)
        form.addRow(_note(
            "This lets several people work from this folder, each with their own login, with no server. You become its administrator, "
            "on this computer, and you create everyone else's accounts.\n\n"
            "• Everyone else has to close RevisionWorx first.\n"
            "• It can't be undone, and older versions of RevisionWorx can't open the workspace afterwards. Make a backup first.\n"
            "• Nothing already recorded is changed.\n\n"
            "What it can't do: a shared folder can't stop someone who can write to it from deleting or damaging files. RevisionWorx will "
            "notice and stop accepting changes, but you still need folder permissions and backups.\n\n"
            "Not verified: whether your network share behaves correctly between computers. This checks it from this computer. To find "
            "out, run 'revisionworx share test' on two computers at the same time.", self))
        self.account = QLineEdit(self)
        self.account.setAccessibleName("Your user name")
        form.addRow("Your &user name", self.account)
        self.name = QLineEdit(self)
        self.name.setAccessibleName("Your full name")
        form.addRow("Your &full name", self.name)
        self.new = _password_field(self, "Password")
        form.addRow("&Password", self.new)
        self.again = _password_field(self, "Password again")
        form.addRow("&Again", self.again)
        self.error = error_label(self)
        form.addRow(self.error)
        form.addRow(_buttons(self, "&Set up"))
        _fit(self, 620)
        self.account.setFocus()

    def submit(self) -> None:
        self.error.setText("")
        if self.new.text() != self.again.text():
            self.error.setText("Those don't match.")
            return
        try:
            self.made = self.ctl.enable_team(self.account.text(), self.name.text(), self.new.text())
        except Exception as exc:
            self.error.setText(_say(exc))
            return
        self.accept()


class RecoveryCodeDialog(QDialog):
    """Shown once. It can't be closed by accident, because this is the only time anyone sees this code."""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Your recovery code")
        self.setObjectName("recoveryCodeDialog")
        layout = QVBoxLayout(self)
        layout.addWidget(_note("Write this down and keep it somewhere safe, away from the shared folder. It's shown only this once. It's the "
                               "only way to get an administrator account back if this computer is lost or the password is forgotten.", self))
        self.code = QLineEdit(code, self)
        self.code.setReadOnly(True)
        self.code.setAccessibleName("Recovery code")
        layout.addWidget(self.code)
        row = QHBoxLayout()
        copy = QPushButton("&Copy", self)
        copy.clicked.connect(lambda *_: QGuiApplication.clipboard().setText(code))
        row.addWidget(copy)
        row.addStretch(1)
        layout.addLayout(row)
        self.done_box = QCheckBox("I've &written it down", self)
        layout.addWidget(self.done_box)
        self.close_btn = QPushButton("&Close", self)
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(lambda *_: self.accept())
        self.done_box.toggled.connect(self.close_btn.setEnabled)
        layout.addWidget(self.close_btn)
        _fit(self, 540)

    def reject(self) -> None:
        if self.done_box.isChecked():
            super().reject()


class AddAccountDialog(QDialog):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("Add an account")
        self.setObjectName("addAccountDialog")
        form = QFormLayout(self)
        self.account = QLineEdit(self)
        self.account.setAccessibleName("User name")
        form.addRow("&User name", self.account)
        self.name = QLineEdit(self)
        self.name.setAccessibleName("Full name")
        form.addRow("&Full name", self.name)
        self.role = QComboBox(self)
        for key, text in ROLE_NAMES.items():
            self.role.addItem(text, key)
        self.role.setCurrentIndex(1)
        form.addRow("&Role", self.role)
        self.password = _password_field(self, "Starting password")
        form.addRow("Starting &password", self.password)
        form.addRow(_note("Give them this yourself. They choose their own when they first log in, and it isn't stored anywhere.", self))
        self.error = error_label(self)
        form.addRow(self.error)
        form.addRow(_buttons(self, "&Add"))
        _fit(self, 480)
        self.account.setFocus()

    def submit(self) -> None:
        self.error.setText("")
        try:
            self.ctl.create_account(self.account.text(), self.password.text(), name=self.name.text(), role=self.role.currentData())
        except Exception as exc:
            self.error.setText(_say(exc))
            return
        self.accept()


class AccountsDialog(QDialog):
    COLUMNS = ("User name", "Name", "Role", "State")

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.rows: list[dict] = []
        self.setWindowTitle("Accounts")
        self.setObjectName("accountsDialog")
        self.resize(640, 400)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setAccessibleName("Accounts")
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        layout.addWidget(self.table, 1)
        self.error = error_label(self)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.add_btn = QPushButton("&Add…", self)
        self.add_btn.clicked.connect(lambda *_: self.add())
        self.active_btn = QPushButton("&Disable", self)
        self.active_btn.setToolTip("What they did before stands. From now on, every computer ignores changes in their name.")
        self.active_btn.clicked.connect(lambda *_: self.toggle_active())
        self.role_btn = QPushButton("Make &administrator", self)
        self.role_btn.clicked.connect(lambda *_: self.toggle_role())
        self.reset_btn = QPushButton("&Reset password…", self)
        self.reset_btn.clicked.connect(lambda *_: self.reset())
        close = QPushButton("&Close", self)
        close.clicked.connect(lambda *_: self.accept())
        for button in (self.add_btn, self.active_btn, self.role_btn, self.reset_btn):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows and rows[0].row() < len(self.rows) else None

    def refresh(self, select: str | None = None) -> None:
        select = select or (self.selected() or {}).get("id")
        try:
            self.rows = self.ctl.accounts()
        except Exception as exc:
            self.rows = []
            self.error.setText(_say(exc))
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            state = "Disabled" if not row["active"] else ("Hasn't chosen a password yet" if row.get("must_change") else "Active")
            for j, text in enumerate((row["id"], row.get("name", ""), ROLE_NAMES.get(row["role"], row["role"]), state)):
                self.table.setItem(i, j, QTableWidgetItem(text))
            if row["id"] == select:
                self.table.selectRow(i)
        self.update_buttons()

    def update_buttons(self) -> None:
        row = self.selected()
        for button in (self.active_btn, self.role_btn, self.reset_btn):
            button.setEnabled(row is not None)
        if row is not None:
            self.active_btn.setText("&Disable" if row["active"] else "&Enable")
            self.role_btn.setText("Make regular &user" if row["role"] == "admin" else "Make &administrator")

    def _do(self, call) -> None:
        self.error.setText("")
        try:
            call()
        except Exception as exc:
            self.error.setText(_say(exc))
        self.refresh()

    def add(self) -> None:
        dialog = AddAccountDialog(self.ctl, self)
        if dialog.exec():
            self.refresh(dialog.account.text().strip().casefold())

    def toggle_active(self) -> None:
        row = self.selected()
        if row:
            self._do(lambda: self.ctl.set_account_active(row["id"], not row["active"]))

    def toggle_role(self) -> None:
        row = self.selected()
        if row:
            self._do(lambda: self.ctl.set_account_role(row["id"], "user" if row["role"] == "admin" else "admin"))

    def reset(self) -> None:
        row = self.selected()
        if not row:
            return
        text, ok = QInputDialog.getText(self, "Reset password", f"A new starting password for {row['id']}. They choose their own when they "
                                        "next log in:", QLineEdit.EchoMode.Password)
        if ok:
            self._do(lambda: self.ctl.reset_account_password(row["id"], text))


def show_share_check(ctl: Controller, parent=None) -> None:
    try:
        report = ctl.check_share()
    except Exception as exc:
        QMessageBox.critical(parent, "Check this folder", describe_error(exc))
        return
    lines = [f"{'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}" for c in report["checks"]]
    verdict = "This folder passed" if report["ok"] else "This folder FAILED"
    lines.append(f"\n{verdict}, {report['scope']}. To find out, run 'revisionworx share test' on two computers at the same time.")
    box = QMessageBox.information if report["ok"] else QMessageBox.warning
    box(parent, "Check this folder", "\n".join(lines))
