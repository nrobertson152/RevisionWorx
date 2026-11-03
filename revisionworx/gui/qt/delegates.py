# SPDX-License-Identifier: GPL-3.0-or-later
"""Painted list rows. Each list shows one stretched column painted as a whole row, so selection is one
rounded band with no cell seams. The item models keep all their columns for tests and accessibility."""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHeaderView, QStyle, QStyledItemDelegate, QTableView

from . import theme

PAD, GAP, NUMBER_W, REV_W, STATE_W = 12, 12, 96, 30, 124
TONES = {"neutral": ("neutralBg", "neutralFg"), "info": ("infoBg", "infoFg"), "ok": ("okBg", "okFg"),
         "warn": ("warnBg", "warnFg"), "danger": ("dangerBg", "dangerFg")}


def flat_list(view: QTableView, columns, visible: str, row_height: int, delegate) -> None:
    """Turn a table view into a borderless list of painted rows."""
    keys = [c.key for c in columns]
    for i, key in enumerate(keys):
        view.setColumnHidden(i, key != visible)
    header = view.horizontalHeader()
    header.setSectionResizeMode(keys.index(visible), QHeaderView.ResizeMode.Stretch)
    header.hide()
    view.verticalHeader().setDefaultSectionSize(row_height)
    view.setShowGrid(False)
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setAlternatingRowColors(False)
    view.setMouseTracking(True)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setItemDelegate(delegate)


def _band(painter: QPainter, option) -> bool:
    t = theme.current
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    rect = QRectF(option.rect).adjusted(0, 1, 0, -1)
    painter.setPen(Qt.PenStyle.NoPen)
    if selected:
        painter.setBrush(QColor(t["tint"]))
        painter.drawRoundedRect(rect, 8, 8)
    elif option.state & QStyle.StateFlag.State_MouseOver:
        painter.setBrush(QColor(t["hover"]))
        painter.drawRoundedRect(rect, 8, 8)
    if option.state & QStyle.StateFlag.State_HasFocus:  # keyboard users must see where they are
        painter.setPen(QPen(QColor(t["accent"]), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 8, 8)
    return selected


def _text(painter: QPainter, rect: QRect, text: str, font: QFont, colour: str,
          align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) -> int:
    metrics = QFontMetrics(font)
    shown = metrics.elidedText(text or "", Qt.TextElideMode.ElideRight, max(0, rect.width()))
    painter.setFont(font)
    painter.setPen(QColor(colour))
    painter.drawText(rect, align, shown)
    return metrics.horizontalAdvance(shown)


def _chip(painter: QPainter, x: int, cy: int, text: str, tone: str, font: QFont, on_selected: bool, height: int = 22) -> int:
    t = theme.current
    bg_key, fg_key = TONES[tone]
    background = t["surface"] if on_selected and tone in ("neutral", "info") else t[bg_key]
    width = QFontMetrics(font).horizontalAdvance(text) + 14
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(QRectF(x, cy - height / 2, width, height), 5, 5)
    _text(painter, QRect(x, cy - height // 2, width, height), text, font, t[fg_key], Qt.AlignmentFlag.AlignCenter)
    return width


class _RowDelegate(QStyledItemDelegate):
    height = 56

    def sizeHint(self, option, index):  # noqa: N802 - Qt API
        return QSize(120, self.height)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        try:
            self.paint_row(painter, option, index.row())
        finally:
            painter.restore()


class ProjectDelegate(_RowDelegate):
    height = 50

    def __init__(self, rows, checkouts, parent=None):
        super().__init__(parent)
        self.rows, self.checkouts = rows, checkouts

    def paint_row(self, painter, option, row):
        rows = self.rows()
        if row >= len(rows):
            return
        t, r = theme.current, rows[row]
        selected = _band(painter, option)
        rect = option.rect.adjusted(10, 0, -10, 0)
        ink, sub = (t["accentInk"], t["accentInk"]) if selected else (t["ink"], t["sub"])
        count = self.checkouts().get(r["id"], 0)
        name_width = rect.width() - (34 if count else 0)
        name = r.get("name") or ""
        if r.get("status") == "archived":
            name += " (archived)"
        _text(painter, QRect(rect.left(), rect.top() + 6, name_width, 20), name,
              theme.ui_font(14, QFont.Weight.DemiBold if selected else QFont.Weight.Medium), ink)
        _text(painter, QRect(rect.left(), rect.top() + 26, rect.width(), 18), r.get("number") or "",
              theme.mono_font(12), sub)
        if count:
            painter.drawPixmap(rect.right() - 28, rect.top() + 9, theme.icon_pixmap("pencil", sub, 13, 2))
            _text(painter, QRect(rect.right() - 13, rect.top() + 6, 14, 20), str(count),
                  theme.ui_font(12, QFont.Weight.Medium), sub)


class DocumentDelegate(_RowDelegate):
    height = 56

    def __init__(self, list_vm, parent=None):
        super().__init__(parent)
        self.vm = list_vm

    def paint_row(self, painter, option, row):
        rows = self.vm.rows
        if row >= len(rows):
            return
        t, r = theme.current, rows[row]
        selected = _band(painter, option)
        rect = option.rect.adjusted(PAD, 0, -PAD, 0)
        cy = rect.center().y()
        right = rect.right() + 1
        state_x = right - STATE_W
        rev_x = state_x - GAP - REV_W
        number_x = rev_x - GAP - NUMBER_W

        tile = QRectF(rect.left(), cy - 17, 34, 34)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t["surface"] if selected else t["neutralBg"]))
        painter.drawRoundedRect(tile, 8, 8)
        painter.drawPixmap(rect.left() + 8, cy - 9,
                           theme.icon_pixmap(theme.document_kind(r.get("rel_path", "")),
                                             t["accentInk"] if selected else t["neutralFg"], 18, 1.6))

        text_x = rect.left() + 44
        text_w = number_x - GAP - text_x
        title_font = theme.ui_font(14, QFont.Weight.Medium)
        tag_font = theme.ui_font(12, QFont.Weight.Medium)
        tag = self.vm.row_tag(row)
        tag_w = QFontMetrics(tag_font).horizontalAdvance(tag[0]) + 14 if tag else 0
        title = r.get("title") or r.get("rel_path") or ""
        title_w = min(QFontMetrics(title_font).horizontalAdvance(title), max(40, text_w - (tag_w + 8 if tag else 0)))
        drawn = _text(painter, QRect(text_x, cy - 20, title_w + 1, 20), title, title_font, t["ink"])
        if tag:
            _chip(painter, text_x + drawn + 8, cy - 10, tag[0], tag[1], tag_font, selected, 19)
        _text(painter, QRect(text_x, cy + 1, text_w, 18), r.get("rel_path", ""), theme.ui_font(12), t["sub"])

        _text(painter, QRect(number_x, cy - 10, NUMBER_W, 20), r.get("number") or "—", theme.mono_font(12), t["sub"])
        _text(painter, QRect(rev_x, cy - 10, REV_W, 20), str(r.get("latest_label") or "—"),
              theme.mono_font(13, QFont.Weight.Medium), t["ink"])
        name = r.get("state_name") or ""
        if name:
            _chip(painter, state_x, cy, name, self.vm.state_tone(r.get("latest_state")),
                  theme.ui_font(12, QFont.Weight.Medium), selected)


class HistoryDelegate(_RowDelegate):
    height = 54

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel

    def paint_row(self, painter, option, row):
        history = self.panel.history
        if row >= len(history):
            return
        t, r = theme.current, history[row]
        _band(painter, option)
        rect = option.rect.adjusted(4, 0, -6, 0)
        cx = rect.left() + 14
        if row < len(history) - 1:  # the line joining this revision to the older one
            painter.setPen(QPen(QColor(t["line"]), 1))
            painter.drawLine(cx, rect.top() + 36, cx, option.rect.bottom() + 1)
        latest = row == 0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t["accent"] if latest else t["neutralBg"]))
        painter.drawEllipse(QRectF(rect.left(), rect.top() + 7, 28, 28))
        _text(painter, QRect(rect.left(), rect.top() + 7, 28, 28), str(r.get("label", "")),
              theme.mono_font(12, QFont.Weight.Medium), t["onAccent"] if latest else t["neutralFg"], Qt.AlignmentFlag.AlignCenter)
        x = rect.left() + 38
        _text(painter, QRect(x, rect.top() + 6, rect.right() - x, 20), r.get("description", ""),
              theme.ui_font(14, QFont.Weight.Medium), t["ink"])
        _text(painter, QRect(x, rect.top() + 26, rect.right() - x, 18), self.panel.history_meta(row),
              theme.ui_font(12), t["sub"])
