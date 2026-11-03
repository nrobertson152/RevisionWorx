# SPDX-License-Identifier: GPL-3.0-or-later
"""Design tokens, fonts, icons and the application stylesheet (docs/UI_REDESIGN_PROPOSAL.md).

Everything visual lives here, so the rest of the GUI only says what a thing is (a primary button, a
status block), never what colour it is."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QImage, QPainter, QPalette,
                           QPixmap)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

LIGHT = {
    "ground": "#F2F4F7", "surface": "#FFFFFF", "hover": "#E8EBF0", "tint": "#E6EFFC", "tintHover": "#DCE8FA",
    "line": "#E1E5EA", "lineStrong": "#C6CCD5", "ink": "#14181F", "sub": "#56606C", "pill": "#E1E5EA",
    "accent": "#1D5FC9", "accentHover": "#184FA8", "onAccent": "#FFFFFF", "accentInk": "#174FA8",
    "offBg": "#E4E7EC", "offFg": "#5F6974", "neutralBg": "#EEF1F5", "neutralFg": "#3D4651",
    "infoBg": "#E6EFFC", "infoFg": "#174FA8", "okBg": "#E2F4E9", "okFg": "#14613A",
    "warnBg": "#FFF3D6", "warnFg": "#6F4700", "dangerBg": "#FDECEA", "dangerFg": "#A31F17",
}
DARK = {
    "ground": "#12161B", "surface": "#191E25", "hover": "#262C34", "tint": "#1E2C42", "tintHover": "#25354E",
    "line": "#2A313A", "lineStrong": "#404955", "ink": "#E9ECF0", "sub": "#A2ACB8", "pill": "#2F3640",
    "accent": "#2F6FDB", "accentHover": "#3C7BE6", "onAccent": "#FFFFFF", "accentInk": "#8DB8F7",
    "offBg": "#262C34", "offFg": "#98A2AE", "neutralBg": "#262C34", "neutralFg": "#C3CAD3",
    "infoBg": "#1E2C42", "infoFg": "#9BC1F8", "okBg": "#16352A", "okFg": "#7AD6A2",
    "warnBg": "#3A2E12", "warnFg": "#EDC36B", "dangerBg": "#3E1F1D", "dangerFg": "#F39A90",
}
THEMES = {"light": LIGHT, "dark": DARK}

ICONS = {
    "part": "M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9",
    "assembly": "M8 3l5 3v5.5l-5 3-5-3V6z M3 6l5 3 5-3 M8 9v5.5 M16 9.5l5 3V18l-5 3-5-3v-3.5 M16 15v6 M11 15l5 0 5-2.5",
    "drawing": "M4 4h16v16H4z M4 15h16 M13 15v5 M8 8h8 M8 11h5",
    "doc": "M7 3h7l5 5v13H7z M14 3v5h5 M10 13h6 M10 17h6",
    "check": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M8.5 12l2.5 2.5 4.5-5",
    "pencil": "M4 20l4-1L19 8a2.1 2.1 0 0 0-3-3L5 16z",
    "alert": "M12 4l9 16H3z M12 10v4 M12 17.5h.01",
    "lock": "M6 11h12v9H6z M8.5 11V8a3.5 3.5 0 0 1 7 0v3",
    "info": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v5 M12 8h.01",
    "plus": "M12 5v14 M5 12h14",
    "more": "M5 12h.01 M12 12h.01 M19 12h.01",
    "chevron": "M6 9l6 6 6-6",
    "search": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.3-4.3",
    "help": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1.9-1.1 1.8 M12 17h.01",
    "upload": "M12 16V5 M7 10l5-5 5 5 M5 19h14",
    "back": "M15 6l-6 6 6 6",
}

# Which icon a document gets, from its core document-type category and file name.
KIND_BY_SUFFIX = {".iam": "assembly", ".sldasm": "assembly", ".asm": "assembly",
                  ".idw": "drawing", ".dwg": "drawing", ".dxf": "drawing", ".slddrw": "drawing", ".drw": "drawing",
                  ".ipt": "part", ".sldprt": "part", ".step": "part", ".stp": "part", ".iges": "part", ".igs": "part"}

SANS_FALLBACK = ["IBM Plex Sans", "Segoe UI", "Noto Sans", "DejaVu Sans", "Sans Serif"]
MONO_FALLBACK = ["IBM Plex Mono", "Consolas", "DejaVu Sans Mono", "Monospace"]
BASE_PX = 14

current: dict[str, str] = LIGHT
current_name = "light"
_fonts_loaded = False


def fonts_dir() -> Path:
    return Path(__file__).parent / "assets" / "fonts"


def load_fonts() -> None:
    """Bundled fonts are a nicety: if a packaged build leaves them out, Qt's fallbacks are used."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    _fonts_loaded = True
    for path in sorted(fonts_dir().glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def mono_font(pixel_size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FALLBACK)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def ui_font(pixel_size: int = BASE_PX, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(SANS_FALLBACK)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def chosen_theme() -> str:
    """REVISIONWORX_THEME=light|dark overrides; otherwise follow the system (D3 in the proposal)."""
    forced = os.environ.get("REVISIONWORX_THEME", "").strip().lower()
    if forced in THEMES:
        return forced
    app = QGuiApplication.instance()
    try:
        if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
            return "dark"
    except AttributeError:  # Qt older than 6.5
        pass
    return "light"


def install(name: str | None = None) -> dict[str, str]:
    """Apply the theme to the running application. Safe to call more than once."""
    global current, current_name
    current_name = name if name in THEMES else chosen_theme()
    current = THEMES[current_name]
    app = QApplication.instance()
    if app is None or app.property("revisionworxTheme") == current_name:
        return current  # restyling is costly: it repolishes every widget in the application
    app.setProperty("revisionworxTheme", current_name)
    load_fonts()
    app.setStyle("Fusion")
    app.setFont(ui_font())
    app.setPalette(palette(current))
    app.setStyleSheet(stylesheet(current))
    return current


def palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "surface"), (QPalette.ColorRole.Base, "surface"),
                      (QPalette.ColorRole.AlternateBase, "ground"), (QPalette.ColorRole.WindowText, "ink"),
                      (QPalette.ColorRole.Text, "ink"), (QPalette.ColorRole.Button, "surface"),
                      (QPalette.ColorRole.ButtonText, "ink"), (QPalette.ColorRole.ToolTipBase, "surface"),
                      (QPalette.ColorRole.ToolTipText, "ink"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "onAccent"), (QPalette.ColorRole.PlaceholderText, "sub"),
                      (QPalette.ColorRole.Link, "accentInk")):
        p.setColor(role, QColor(t[key]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["offFg"]))
    return p


def stylesheet(t: dict[str, str]) -> str:
    """Widgets choose a look with the ``role`` and ``tone`` properties, not with colours."""
    tones = "".join(
        f'QFrame[tone="{tone}"] {{ background: {t[bg]}; border-radius: 10px; }}\n'
        f'QLabel[tone="{tone}"] {{ color: {t[fg]}; }}\n'
        for tone, bg, fg in (("neutral", "neutralBg", "ink"), ("info", "infoBg", "infoFg"), ("ok", "okBg", "okFg"),
                             ("warn", "warnBg", "warnFg"), ("danger", "dangerBg", "dangerFg")))
    return f"""
QWidget {{ color: {t['ink']}; }}
QMainWindow, QDialog, QMessageBox, QStackedWidget, QSplitter {{ background: {t['surface']}; }}
QMessageBox {{ messagebox-text-interaction-flags: 3; }}
QFrame#sidebar {{ background: {t['ground']}; border-right: 1px solid {t['line']}; }}
QFrame#sidebar QLabel {{ background: transparent; }}
QFrame#card {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QFrame#dropZone {{ border: 2px dashed {t['lineStrong']}; border-radius: 12px; }}
QFrame#revisionCard {{ background: {t['ground']}; border-radius: 8px; }}
QFrame#hairline {{ background: {t['line']}; }}
QLabel[role="logo"] {{ border: 2px dashed {t['lineStrong']}; border-radius: 8px; color: {t['sub']}; font-size: 10px; }}
QLabel[role="muted"] {{ color: {t['sub']}; }}
QLabel[role="small"] {{ color: {t['sub']}; font-size: 12px; }}
QLabel[role="section"] {{ color: {t['sub']}; font-size: 12px; font-weight: 600; }}
QLabel[role="columnHead"] {{ color: {t['sub']}; font-size: 12px; font-weight: 500; }}
QLabel[role="h1"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="welcome"] {{ font-size: 26px; font-weight: 600; }}
QLabel[role="alpha"] {{ background: {t['warnBg']}; color: {t['warnFg']}; border-radius: 8px; padding: 10px; font-size: 12px; }}
QLabel[role="statusTitle"] {{ font-weight: 600; }}
QLabel#formError {{ color: {t['dangerFg']}; }}
QFrame#healthBox[tone="danger"], QFrame#healthBox[tone="info"] {{ border-radius: 10px; }}
{tones}
QLineEdit, QPlainTextEdit, QComboBox {{
  background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['lineStrong']}; border-radius: 8px;
  padding: 6px 10px; selection-background-color: {t['accent']}; selection-color: {t['onAccent']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit[role="quiet"] {{ border: 1px solid {t['line']}; }}
QPlainTextEdit[role="plain"] {{ background: transparent; border: 0; padding: 0; }}
QPushButton, QToolButton {{
  background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['lineStrong']}; border-radius: 8px;
  padding: 8px 14px; font-weight: 500;
}}
QPushButton:hover, QToolButton:hover {{ background: {t['hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t['hover']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['offFg']}; border-color: {t['line']}; background: transparent; }}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {t['accent']}; }}
QPushButton[role="primary"] {{ background: {t['accent']}; color: {t['onAccent']}; border: 1px solid {t['accent']}; font-weight: 600; padding: 10px 16px; }}
QPushButton[role="primary"]:hover {{ background: {t['accentHover']}; border-color: {t['accentHover']}; }}
QPushButton[role="primary"]:focus {{ border: 2px solid {t['accentInk']}; }}
QPushButton[role="primary"]:disabled {{ background: {t['offBg']}; color: {t['offFg']}; border-color: {t['offBg']}; }}
QPushButton[role="link"] {{ background: transparent; border: 0; color: {t['accentInk']}; padding: 4px 0; font-weight: 500; }}
QPushButton[role="link"]:hover {{ text-decoration: underline; background: transparent; }}
QPushButton[role="link"]:focus {{ text-decoration: underline; border: 0; }}
QPushButton[role="segment"] {{ background: transparent; border: 0; border-radius: 6px; color: {t['sub']}; padding: 6px 10px; }}
QPushButton[role="segment"]:hover {{ background: {t['hover']}; }}
QPushButton[role="segment"]:checked {{ background: {t['tint']}; color: {t['accentInk']}; }}
QPushButton[role="segment"]:focus {{ border: 1px solid {t['accent']}; padding: 5px 9px; }}
QPushButton[role="nav"] {{ background: transparent; border: 0; text-align: left; padding: 8px 10px; }}
QPushButton[role="nav"]:hover {{ background: {t['hover']}; }}
QPushButton[role="nav"]:focus {{ border: 1px solid {t['accent']}; padding: 7px 9px; }}
QPushButton[role="brand"] {{ background: transparent; border: 0; text-align: left; padding: 4px 6px; font-weight: 600; font-size: 15px; }}
QPushButton[role="brand"]:hover {{ background: {t['hover']}; }}
QPushButton[role="brand"]:focus {{ border: 1px solid {t['accent']}; padding: 3px 5px; }}
QPushButton::menu-indicator, QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton[role="iconPlain"], QToolButton[role="iconPlain"] {{ background: transparent; border: 0; padding: 4px; }}
QPushButton[role="iconPlain"]:hover, QToolButton[role="iconPlain"]:hover {{ background: {t['hover']}; }}
QToolButton[role="icon"] {{ padding: 8px; }}
QToolButton[role="chip"] {{ border: 0; border-radius: 5px; padding: 3px 8px; font-size: 12px; font-weight: 500; }}
QToolButton[role="chip"][tone="neutral"] {{ background: {t['neutralBg']}; color: {t['neutralFg']}; }}
QToolButton[role="chip"][tone="info"] {{ background: {t['infoBg']}; color: {t['infoFg']}; }}
QToolButton[role="chip"][tone="ok"] {{ background: {t['okBg']}; color: {t['okFg']}; }}
QToolButton[role="chip"][tone="warn"] {{ background: {t['warnBg']}; color: {t['warnFg']}; }}
QToolButton[role="chip"]:focus {{ border: 1px solid {t['accent']}; }}
QCheckBox {{ spacing: 8px; }}
QTableView, QListWidget {{ background: transparent; border: 0; outline: 0; gridline-color: transparent; selection-background-color: {t['tint']}; selection-color: {t['ink']}; }}
QListWidget {{ border: 1px solid {t['line']}; border-radius: 8px; padding: 4px; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {t['tint']}; color: {t['accentInk']}; }}
QHeaderView::section {{ background: transparent; border: 0; color: {t['sub']}; font-weight: 500; padding: 4px 8px; }}
QTabWidget::pane {{ border: 0; }}
QSplitter::handle {{ background: {t['line']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QMenu {{ background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['line']}; padding: 4px; }}
QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['hover']}; }}
QMenu::item:disabled {{ color: {t['offFg']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 6px; }}
QMenuBar {{ background: {t['surface']}; }}
QToolTip {{ background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['lineStrong']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['lineStrong']}; border-radius: 3px; min-height: 28px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['lineStrong']}; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; width: 0; height: 0; }}
QStatusBar {{ background: {t['surface']}; color: {t['sub']}; }}
"""


def icon_pixmap(name: str, color: str, size: int = 16, stroke: float = 1.8) -> QPixmap:
    """A crisp stroke icon from ``ICONS``, in any colour, at the screen's scale."""
    ratio = 1.0
    app = QGuiApplication.instance()
    if app is not None and QGuiApplication.primaryScreen() is not None:
        ratio = QGuiApplication.primaryScreen().devicePixelRatio()
    if name == "more":
        stroke = 2.4
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS[name]}"/></svg>')
    px = round(size * ratio)
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, px, px))
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    return QIcon(icon_pixmap(name, color or current["ink"], size))


def icon_size(size: int = 16) -> QSize:
    return QSize(size, size)


def document_kind(rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    return KIND_BY_SUFFIX.get(suffix, "doc")


def restyle(widget, **properties) -> None:
    """Change ``role``/``tone`` at run time; Qt only re-reads style rules when asked to."""
    for key, value in properties.items():
        widget.setProperty(key, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
