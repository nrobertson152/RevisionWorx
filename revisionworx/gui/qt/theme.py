# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour themes for the desktop window: tokens, the application style sheet and the palette.

Same model and theme names as OpenSchematix, so the two programs look like one family. Every colour the window
uses comes from the token tables below; widgets never hard-code colours. They take a role from their object
name or from a dynamic property (``tone``, ``role``, ``chip``), and the style sheet colours that role.
Switching themes restyles the whole application in place.

Light and Dark follow docs/UI_REDESIGN_PROPOSAL.md. Every text/background pair used for text was checked at
4.5:1 or better (tests/test_gui_theme.py).
"""
from __future__ import annotations

THEMES: dict[str, dict] = {
    "light": {
        "label": "Modern Light", "dark": False,
        "t": {"ground": "#F2F4F7", "surface": "#FFFFFF", "surface2": "#F7F8FA", "tint": "#E6EFFC",
              "line": "#E1E5EA", "line_strong": "#C6CCD5", "ink": "#14181F", "muted": "#56606C",
              "faint": "#6B7480", "accent": "#1D5FC9", "accent_hover": "#174FA8", "accent_ink": "#FFFFFF",
              "accent_text": "#174FA8", "hover": "#ECEFF3", "pressed": "#E1E5EA", "input": "#FFFFFF",
              "neutral_bg": "#EEF1F5", "neutral_fg": "#3D4651", "ok_bg": "#E2F4E9", "ok_fg": "#14613A",
              "warn_bg": "#FFF3D6", "warn_fg": "#6F4700", "danger_bg": "#FDECEA", "danger_fg": "#A31F17",
              "info_bg": "#E6EFFC", "info_fg": "#174FA8", "tooltip_bg": "#1F2329", "tooltip_fg": "#F5F6F8",
              "focus": "#1D5FC9"},
    },
    "dark": {
        "label": "Modern Dark", "dark": True,
        "t": {"ground": "#12161B", "surface": "#191E25", "surface2": "#1E242C", "tint": "#1E2C42",
              "line": "#2A313A", "line_strong": "#404955", "ink": "#E9ECF0", "muted": "#A2ACB8",
              "faint": "#8C96A3", "accent": "#2F6FDB", "accent_hover": "#4A82E3", "accent_ink": "#FFFFFF",
              "accent_text": "#8DB8F7", "hover": "#232A33", "pressed": "#2C343F", "input": "#12161B",
              "neutral_bg": "#262C34", "neutral_fg": "#C3CAD3", "ok_bg": "#16352A", "ok_fg": "#7AD6A2",
              "warn_bg": "#3A2E12", "warn_fg": "#EDC36B", "danger_bg": "#3E1F1D", "danger_fg": "#F39A90",
              "info_bg": "#1E2C42", "info_fg": "#8DB8F7", "tooltip_bg": "#F1F3F5", "tooltip_fg": "#1B1F24",
              "focus": "#4A82E3"},
    },
    "graphite": {
        "label": "Graphite", "dark": True,
        "t": {"ground": "#2B2D30", "surface": "#323538", "surface2": "#383B3F", "tint": "#4A3F2C",
              "line": "#45494E", "line_strong": "#55595F", "ink": "#E8E8E8", "muted": "#B3B7BC",
              "faint": "#9A9EA4", "accent": "#E8A33D", "accent_hover": "#F0B45A", "accent_ink": "#1C1C1C",
              "accent_text": "#F0B45A", "hover": "#3D4044", "pressed": "#474A4F", "input": "#26282B",
              "neutral_bg": "#3D4044", "neutral_fg": "#D5D7DA", "ok_bg": "#25402C", "ok_fg": "#8EE59B",
              "warn_bg": "#4A3F2C", "warn_fg": "#F0C374", "danger_bg": "#4D2A28", "danger_fg": "#FF9C94",
              "info_bg": "#2A3B4F", "info_fg": "#9CCBFF", "tooltip_bg": "#F1F1F1", "tooltip_fg": "#1C1C1C",
              "focus": "#E8A33D"},
    },
    "blueprint": {
        "label": "Blueprint", "dark": True,
        "t": {"ground": "#0B2140", "surface": "#0F2A52", "surface2": "#13325F", "tint": "#173F72",
              "line": "#1F4478", "line_strong": "#2A5591", "ink": "#E6F0FF", "muted": "#A9C2E3",
              "faint": "#8FAAD0", "accent": "#5AB0FF", "accent_hover": "#7CC0FF", "accent_ink": "#06203F",
              "accent_text": "#8FCBFF", "hover": "#16386A", "pressed": "#1D4580", "input": "#0A1D38",
              "neutral_bg": "#173F72", "neutral_fg": "#D3E3FA", "ok_bg": "#124436", "ok_fg": "#8CEBB6",
              "warn_bg": "#3F3A1C", "warn_fg": "#FFD98A", "danger_bg": "#4A2035", "danger_fg": "#FFA6A6",
              "info_bg": "#173F72", "info_fg": "#A8D5FF", "tooltip_bg": "#E6F0FF", "tooltip_fg": "#06203F",
              "focus": "#5AB0FF"},
    },
    "high_contrast": {
        "label": "High Contrast", "dark": True,
        "t": {"ground": "#000000", "surface": "#000000", "surface2": "#0D0D0D", "tint": "#333300",
              "line": "#FFFFFF", "line_strong": "#FFFFFF", "ink": "#FFFFFF", "muted": "#FFFFFF",
              "faint": "#E0E0E0", "accent": "#FFFF00", "accent_hover": "#FFFF80", "accent_ink": "#000000",
              "accent_text": "#FFFF00", "hover": "#1F1F1F", "pressed": "#333333", "input": "#000000",
              "neutral_bg": "#000000", "neutral_fg": "#FFFFFF", "ok_bg": "#000000", "ok_fg": "#00FF00",
              "warn_bg": "#000000", "warn_fg": "#FFFF00", "danger_bg": "#000000", "danger_fg": "#FF6060",
              "info_bg": "#000000", "info_fg": "#00FFFF", "tooltip_bg": "#FFFF00", "tooltip_fg": "#000000",
              "focus": "#FFFF00"},
    },
}
DEFAULT_THEME = "light"

# Lifecycle state -> chip tone (unknown custom states read as neutral).
STATE_TONES = {"wip": "neutral", "in_review": "warn", "approved": "info", "released": "ok",
               "superseded": "neutral", "obsolete": "danger"}
# Working-file state -> row tone.
FILE_TONES = {"missing": "danger", "unavailable": "danger", "modified-externally": "warn", "modified": "info"}


def tokens(name: str) -> dict:
    return THEMES.get(name, THEMES[DEFAULT_THEME])["t"]


def tone_colors(name: str, tone: str) -> tuple[str, str]:
    """(background, text) for a tone: ok, warn, danger, info, neutral."""
    t = tokens(name)
    return t.get(f"{tone}_bg", t["neutral_bg"]), t.get(f"{tone}_fg", t["neutral_fg"])


def stylesheet(name: str) -> str:
    t = tokens(name)
    tone_rules = []
    for tone in ("ok", "warn", "danger", "info", "neutral"):
        bg, fg = t[f"{tone}_bg"], t[f"{tone}_fg"]
        tone_rules.append(f"""
QLabel[tone="{tone}"] {{ background: {bg}; color: {fg}; border-radius: 8px; padding: 8px 10px; }}""")
    return f"""
* {{ outline: none; }}
QWidget {{ color: {t['ink']}; }}
QMainWindow, QDialog, QStackedWidget, QWidget#workspacePage, QWidget#startPage {{ background: {t['surface']}; }}
QWidget#sidebar {{ background: {t['ground']}; border-right: 1px solid {t['line']}; }}
QWidget#sidebar QTableView {{ background: {t['ground']}; border: none; }}
QWidget#sidebar QTableView::item:selected {{ background: {t['tint']}; color: {t['accent_text']}; }}
QLabel {{ background: transparent; }}
QLabel#workspaceName {{ font-size: 15px; font-weight: 600; }}
QLabel#workspaceSub, QLabel[role="muted"] {{ color: {t['muted']}; }}
QLabel[role="error"] {{ color: {t['danger_fg']}; font-weight: 600; }}
QLabel[role="section"] {{ color: {t['muted']}; font-weight: 600; font-size: 11px; }}
QLabel#projectHeading {{ font-size: 20px; font-weight: 600; }}
QLabel#documentTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#alphaBanner {{ background: {t['warn_bg']}; color: {t['warn_fg']}; border-radius: 8px; padding: 8px 10px;
                      font-size: 11px; }}
QLabel#healthBanner {{ padding: 8px 14px; }}
QLabel#healthBanner[tone="danger"] {{ background: {t['danger_bg']}; color: {t['danger_fg']}; font-weight: 600;
                                       border-radius: 0; }}
QLabel#healthBanner[tone="info"] {{ background: {t['info_bg']}; color: {t['info_fg']}; border-radius: 0; }}
QLabel#projectWarning {{ color: {t['warn_fg']}; background: {t['warn_bg']}; border-radius: 8px; padding: 6px 10px; }}
{''.join(tone_rules)}
QPushButton {{ background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['line_strong']};
               border-radius: 8px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {t['hover']}; }}
QPushButton:pressed {{ background: {t['pressed']}; }}
QPushButton:focus {{ border: 2px solid {t['focus']}; padding: 5px 13px; }}
QPushButton:disabled {{ color: {t['faint']}; background: {t['surface2']}; border-color: {t['line']}; }}
QPushButton[primary="true"] {{ background: {t['accent']}; color: {t['accent_ink']}; border: 1px solid {t['accent']};
                               font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t['accent_hover']}; }}
QPushButton[primary="true"]:disabled {{ background: {t['surface2']}; color: {t['faint']}; border-color: {t['line']};
                                        font-weight: 400; }}
QPushButton[chip="true"] {{ border: none; border-radius: 14px; padding: 4px 12px; background: transparent;
                            color: {t['muted']}; }}
QPushButton[chip="true"]:checked {{ background: {t['tint']}; color: {t['accent_text']}; font-weight: 600; }}
QPushButton[chip="true"]:hover {{ background: {t['hover']}; }}
QPushButton[flat="true"], QToolButton {{ border: none; background: transparent; color: {t['muted']};
                                         padding: 4px 8px; text-align: left; }}
QPushButton[flat="true"]:hover, QToolButton:hover {{ background: {t['hover']}; color: {t['ink']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{ background: {t['input']}; color: {t['ink']};
               border: 1px solid {t['line_strong']}; border-radius: 8px; padding: 5px 8px;
               selection-background-color: {t['tint']}; selection-color: {t['ink']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 2px solid {t['focus']};
               padding: 4px 7px; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t['faint']}; background: {t['surface2']}; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; color: {t['ink']}; selection-background-color: {t['tint']};
               selection-color: {t['ink']}; border: 1px solid {t['line']}; }}
QCheckBox {{ color: {t['ink']}; spacing: 6px; }}
QTableView, QListView, QListWidget, QTreeView {{ background: {t['surface']}; alternate-background-color: {t['surface2']};
               color: {t['ink']}; border: 1px solid {t['line']}; border-radius: 8px; gridline-color: transparent;
               selection-background-color: {t['tint']}; selection-color: {t['ink']}; }}
QTableView::item {{ padding: 4px 6px; border: none; }}
QTableView::item:selected, QListView::item:selected, QListWidget::item:selected {{ background: {t['tint']};
               color: {t['ink']}; }}
QTableView:focus, QListView:focus, QListWidget:focus {{ border: 2px solid {t['focus']}; }}
QHeaderView::section {{ background: {t['surface']}; color: {t['muted']}; border: none;
               border-bottom: 1px solid {t['line']}; padding: 6px; font-weight: 600; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {t['line']}; }}
QTabBar::tab {{ background: transparent; color: {t['muted']}; padding: 8px 14px; border: none;
               border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t['ink']}; border-bottom: 2px solid {t['accent']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t['ink']}; }}
QMenuBar {{ background: {t['surface']}; color: {t['ink']}; border-bottom: 1px solid {t['line']}; }}
QMenuBar::item {{ padding: 6px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['hover']}; border-radius: 6px; }}
QMenu {{ background: {t['surface']}; color: {t['ink']}; border: 1px solid {t['line_strong']}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t['tint']}; color: {t['ink']}; }}
QMenu::item:disabled {{ color: {t['faint']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 8px; }}
QStatusBar {{ background: {t['ground']}; color: {t['muted']}; border-top: 1px solid {t['line']}; }}
QStatusBar QLabel {{ color: {t['muted']}; }}
QToolTip {{ background: {t['tooltip_bg']}; color: {t['tooltip_fg']}; border: none; padding: 6px 8px; }}
QGroupBox {{ border: 1px solid {t['line']}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 10px 10px;
             background: {t['surface']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {t['ink']}; font-weight: 600; }}
QSplitter::handle {{ background: {t['line']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {t['line_strong']}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QWidget#documentPanel, QScrollArea#documentPanelScroll {{ background: {t['surface']}; }}
QWidget#paletteBox {{ background: {t['surface']}; border: 1px solid {t['line_strong']}; border-radius: 12px; }}
QMessageBox {{ messagebox-text-interaction-flags: 3; }}
"""


def apply(app, name: str) -> str:
    """Apply a theme to the whole application. Returns the theme actually applied."""
    from PySide6.QtGui import QColor, QPalette
    if name not in THEMES:
        name = DEFAULT_THEME
    t = tokens(name)
    pal = QPalette()
    roles = {QPalette.ColorRole.Window: t["surface"], QPalette.ColorRole.WindowText: t["ink"],
             QPalette.ColorRole.Base: t["input"], QPalette.ColorRole.AlternateBase: t["surface2"],
             QPalette.ColorRole.Text: t["ink"], QPalette.ColorRole.Button: t["surface"],
             QPalette.ColorRole.ButtonText: t["ink"], QPalette.ColorRole.Highlight: t["tint"],
             QPalette.ColorRole.HighlightedText: t["ink"], QPalette.ColorRole.ToolTipBase: t["tooltip_bg"],
             QPalette.ColorRole.ToolTipText: t["tooltip_fg"], QPalette.ColorRole.PlaceholderText: t["faint"],
             QPalette.ColorRole.Link: t["accent_text"]}
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["faint"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(name))
    app.setProperty("rwx_theme", name)
    return name


def current(app) -> str:
    name = app.property("rwx_theme") if app is not None else None
    return name if name in THEMES else DEFAULT_THEME


def set_tone(widget, tone: str | None) -> None:
    """Set a widget's ``tone`` property and re-apply the style sheet to it."""
    widget.setProperty("tone", tone or "")
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_flag(widget, name: str, value=True) -> None:
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
