# SPDX-License-Identifier: GPL-3.0-or-later
"""The Units window (ADR-025): find a unit, see the drawings that apply to it, the engineering changes due on it, what is fitted
in it and its history. Read-only: units are recorded with ``revisionworx unit`` (docs/USER_GUIDE.md)."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout)

from ..controller import Controller, describe_error
from ..viewmodels import UNIT_COLUMNS, unit_row, unit_sections

EMPTY = ("No units are registered in this workspace yet. Register them with 'revisionworx unit new', or import a "
         "register with 'revisionworx unit import'.")


class UnitsWindow(QDialog):
    """Not modal: it belongs to the workspace it was opened for and is closed with it."""

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.rows: list[dict] = []
        self.setWindowTitle("Units")
        self.setObjectName("unitsWindow")
        self.setModal(False)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find a unit: serial, tag, model, customer, site…")
        self.search.setAccessibleName("Find a unit")
        self.search.setClearButtonEnabled(True)
        self.due = QCheckBox("Only units with engineering changes due", self)
        self.table = QTableWidget(0, len(UNIT_COLUMNS), self)
        self.table.setAccessibleName("Units")
        self.table.setHorizontalHeaderLabels([c.title for c in UNIT_COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for i, col in enumerate(UNIT_COLUMNS):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if col.stretch else QHeaderView.ResizeMode.Interactive)
            if not col.stretch:
                self.table.setColumnWidth(i, col.width)
        self.detail = QTextBrowser(self)
        self.detail.setAccessibleName("Unit details")
        self.detail.setOpenLinks(False)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.due)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([520, 560])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        self.resize(1100, 640)
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(200)
        self._typing.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda *_: self._typing.start())
        self.due.toggled.connect(lambda *_: self.refresh())
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.refresh()

    def refresh(self) -> None:
        """Read the register again (the workspace may have changed) and keep the selected unit if it is still listed."""
        keep = self.selected_id()
        try:
            found = self.ctl.units_page(self.search.text().strip(), self.due.isChecked())
        except Exception as exc:  # noqa: BLE001 - shown, never raised into Qt
            found = []
            self.status.setText(describe_error(exc))
        else:
            if found:
                due = sum(1 for u in found if u.get("outstanding"))
                self.status.setText(f"{len(found)} unit{'s' if len(found) != 1 else ''}"
                                    + (f", {due} with engineering changes due" if due else ""))
            elif self.search.text().strip() or self.due.isChecked():
                self.status.setText("No units match.")
            else:
                self.status.setText(EMPTY)
        self.rows = [unit_row(u) for u in found]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            for c, col in enumerate(UNIT_COLUMNS):
                item = QTableWidgetItem(row[col.key])
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                self.table.setItem(r, c, item)
        target = next((i for i, row in enumerate(self.rows) if row["id"] == keep), 0 if self.rows else -1)
        if target >= 0:
            self.table.selectRow(target)
        else:
            self.detail.clear()
        self.show_selected()

    def selected_id(self) -> str | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def select_unit(self, label: str) -> bool:
        for i, row in enumerate(self.rows):
            if row["label"] == label:
                self.table.selectRow(i)
                return True
        return False

    def show_selected(self) -> None:
        unit_id = self.selected_id()
        if unit_id is None:
            self.detail.clear()
            return
        try:
            detail = self.ctl.unit_detail(unit_id)
        except Exception as exc:  # noqa: BLE001 - shown, never raised into Qt
            self.detail.setPlainText(describe_error(exc))
            return
        parts = []
        for i, (heading, lines) in enumerate(unit_sections(detail)):
            if heading:
                parts.append(f"<h{2 if i == 0 else 3}>{html.escape(heading)}</h{2 if i == 0 else 3}>")
            body = "<br>".join(html.escape(line) for line in lines)
            parts.append(f"<p><b>{body}</b></p>" if not heading else f"<p>{body}</p>")
        self.detail.setHtml("".join(parts))

    def detail_text(self) -> str:
        return self.detail.toPlainText()
