# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt item models: thin adapters exposing toolkit-free view-models to QTableView."""
from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..viewmodels import Column


class ViewModelTable(QAbstractTableModel):
    """Read-only table over any view-model exposing ``rows``-like data through callbacks."""

    def __init__(self, columns: Sequence[Column], count: Callable[[], int],
                 cell: Callable[[int, int], str], parent=None):
        super().__init__(parent)
        self._columns, self._count, self._cell = tuple(columns), count, cell

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
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._columns[section].title
        return None
