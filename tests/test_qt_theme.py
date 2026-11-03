# SPDX-License-Identifier: GPL-3.0-or-later
"""The design tokens stay readable and the theme survives missing assets (docs/UI_REDESIGN_PROPOSAL.md)."""
import os

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx.gui.qt import theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PAIRS = [("ink", "surface"), ("sub", "surface"), ("sub", "ground"), ("sub", "tint"), ("accentInk", "tint"),
         ("onAccent", "accent"), ("neutralFg", "neutralBg"), ("neutralFg", "surface"), ("infoFg", "infoBg"),
         ("infoFg", "surface"), ("okFg", "okBg"), ("warnFg", "warnBg"), ("dangerFg", "dangerBg"), ("ink", "dangerBg"),
         ("ink", "infoBg"), ("ink", "okBg"), ("ink", "warnBg"), ("sub", "neutralBg"), ("offFg", "offBg"),
         ("accentInk", "surface"), ("accentInk", "ground"), ("dangerFg", "surface")]


@pytest.mark.parametrize("name", sorted(theme.THEMES))
def test_text_is_readable_in_both_themes(name):
    t = theme.THEMES[name]
    low = [(a, b, round(_contrast(t[a], t[b]), 2)) for a, b in PAIRS if _contrast(t[a], t[b]) < 4.5]
    assert not low


def test_both_themes_define_the_same_tokens():
    assert set(theme.LIGHT) == set(theme.DARK)


def test_env_overrides_the_system_theme(qapp, monkeypatch):
    monkeypatch.setenv("REVISIONWORX_THEME", "dark")
    assert theme.chosen_theme() == "dark"
    monkeypatch.setenv("REVISIONWORX_THEME", "light")
    assert theme.chosen_theme() == "light"


def test_install_applies_tokens_and_can_switch(qapp):
    assert theme.install("dark")["surface"] == theme.DARK["surface"] and theme.current_name == "dark"
    assert theme.DARK["accent"] in qapp.styleSheet()
    assert theme.install("light") is theme.LIGHT and theme.LIGHT["accent"] in qapp.styleSheet()


def test_bundled_fonts_load_and_carry_their_licence(qapp):
    from PySide6.QtGui import QFontDatabase
    theme.load_fonts()
    assert (theme.fonts_dir() / "OFL-IBM-Plex.txt").is_file()
    assert "IBM Plex Sans" in QFontDatabase.families() and "IBM Plex Mono" in QFontDatabase.families()


def test_missing_fonts_are_not_fatal(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(theme, "_fonts_loaded", False)
    monkeypatch.setattr(theme, "fonts_dir", lambda: tmp_path / "nothing-here")
    theme.load_fonts()  # a packaged build that leaves them out uses the fallbacks
    assert theme.ui_font().families()[0] == "IBM Plex Sans"


@pytest.mark.parametrize("name", sorted(theme.ICONS))
def test_every_icon_draws_something(qapp, name):
    image = theme.icon_pixmap(name, "#000000", 24).toImage()
    painted = any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height()))
    assert painted, name


def test_document_kind_comes_from_the_file_type():
    assert theme.document_kind("x/a.IAM") == "assembly" and theme.document_kind("a.ipt") == "part"
    assert theme.document_kind("a.idw") == "drawing" and theme.document_kind("a.pdf") == "doc"
