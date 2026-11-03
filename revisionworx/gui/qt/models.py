# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt item models: thin adapters exposing toolkit-free view-models to QTableView."""
from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QSizePolicy, QTableView

from ..viewmodels import Column


def elastic(label: QLabel) -> QLabel:
    """For labels holding paths or other data: wrap, and never let one long unbreakable word set the
    window's minimum width. Such a word is clipped, so also put the full text in the tooltip."""
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


class EnterTable(QTableView):
    """Enter on a row does what a double-click does. Qt's ``activated`` signal is not used because on some
    desktops it also fires on a single click."""
    enter_pressed = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.enter_pressed.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class ViewModelTable(QAbstractTableModel):
    """Read-only table over any view-model exposing ``rows``-like data through callbacks."""

    SEVERITY_COLORS = {"warning": "#8a5300", "error": "#b00020"}

    def __init__(self, columns: Sequence[Column], count: Callable[[], int],
                 cell: Callable[[int, int], str], parent=None,
                 severity: Callable[[int], str] | None = None):
        super().__init__(parent)
        self._columns, self._count, self._cell = tuple(columns), count, cell
        self._severity = severity

    def reload(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else self._count()

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return self._cell(index.row(), index.column())
        if role == Qt.ItemDataRole.ForegroundRole and self._severity is not None:
            color = self.SEVERITY_COLORS.get(self._severity(index.row()))
            return QColor(color) if color else None
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._columns[section].title
        return None
