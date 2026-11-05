# SPDX-License-Identifier: GPL-3.0-or-later
"""Go to anything (Ctrl+P): one box to find any project, document or action and jump to it.
Binds viewmodels.CommandPalette; no business logic."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..viewmodels import CommandPalette
from . import theme

KIND_MARK = {"action": "›", "project": "▣", "document": "▤"}


class _Search(QLineEdit):
    """Up/Down move through the results while typing; Enter chooses."""

    def __init__(self, dialog: "PaletteDialog"):
        super().__init__(dialog)
        self.dialog = dialog

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        lst = self.dialog.list
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            if lst.count():
                lst.setCurrentRow(max(0, min(lst.count() - 1, lst.currentRow() + step)))
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.dialog.choose(lst.currentRow())
            event.accept()
        else:
            super().keyPressEvent(event)


class PaletteDialog(QDialog):
    """``on_choose(kind, payload)`` is called with the chosen item; the dialog then closes."""

    def __init__(self, model: CommandPalette, on_choose, parent=None):
        super().__init__(parent)
        self.model = model
        self.on_choose = on_choose
        self.setWindowTitle("Go to anything")
        self.setObjectName("paletteDialog")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.search = _Search(self)
        self.search.setPlaceholderText("Type a project, document number, title or action…")
        self.search.setAccessibleName("Go to anything")
        self.search.textChanged.connect(lambda text: self.model.search(text))
        lay.addWidget(self.search)
        self.list = QListWidget(self)
        self.list.setAccessibleName("Results")
        self.list.itemActivated.connect(lambda item: self.choose(self.list.row(item)))
        lay.addWidget(self.list, 1)
        self.hint = QLabel("↑ ↓ to move · Enter to open · Esc to close", self)
        theme.set_flag(self.hint, "role", "muted")
        lay.addWidget(self.hint)
        self.model.subscribe(self.render)
        self.render()
        self.resize(620, 420)

    def render(self) -> None:
        self.list.clear()
        for item in self.model.items:
            entry = QListWidgetItem(f"{KIND_MARK.get(item['kind'], '')}  {item['title']}\n      {item['subtitle']}")
            entry.setToolTip(item["subtitle"])
            self.list.addItem(entry)
        if self.model.items:
            self.list.setCurrentRow(0)
        if self.model.error:
            self.hint.setText(self.model.error)
        elif self.model.text and not self.model.items:
            self.hint.setText("Nothing found. Unmanaged folders are never searched.")
        else:
            self.hint.setText("↑ ↓ to move · Enter to open · Esc to close")

    def choose(self, row: int) -> None:
        chosen = self.model.choose(row)
        if chosen is None:
            return
        self.accept()
        self.on_choose(*chosen)
