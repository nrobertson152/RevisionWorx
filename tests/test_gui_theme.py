# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour themes: every text colour on every background it is used on reaches WCAG AA (4.5:1).

Toolkit-free: reads the token tables only, so it runs everywhere (also without PySide6)."""
import pytest

from revisionworx.gui.qt import theme

TEXT_PAIRS = [("ink", "surface"), ("ink", "surface2"), ("ink", "ground"), ("ink", "tint"), ("ink", "input"),
              ("ink", "hover"), ("muted", "surface"), ("muted", "ground"), ("muted", "surface2"),
              ("accent_ink", "accent"), ("accent_text", "tint"), ("tooltip_fg", "tooltip_bg"),
              ("ok_fg", "ok_bg"), ("warn_fg", "warn_bg"), ("danger_fg", "danger_bg"), ("info_fg", "info_bg"),
              ("neutral_fg", "neutral_bg"), ("danger_fg", "surface"), ("ok_fg", "surface")]


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("name", sorted(theme.THEMES))
def test_text_contrast_meets_wcag_aa(name):
    t = theme.tokens(name)
    low = [(fg, bg, round(contrast(t[fg], t[bg]), 2)) for fg, bg in TEXT_PAIRS if contrast(t[fg], t[bg]) < 4.5]
    assert not low, low


@pytest.mark.parametrize("name", sorted(theme.THEMES))
def test_every_theme_defines_every_token(name):
    assert set(theme.tokens(name)) == set(theme.tokens(theme.DEFAULT_THEME))


def test_stylesheet_uses_only_the_theme_colours():
    import re
    for name in theme.THEMES:
        sheet = theme.stylesheet(name)
        used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", sheet)}
        assert used <= {v.upper() for v in theme.tokens(name).values()}, name


def test_names_match_openschematix_family():
    assert [theme.THEMES[k]["label"] for k in ("light", "dark", "graphite", "blueprint", "high_contrast")] == [
        "Modern Light", "Modern Dark", "Graphite", "Blueprint", "High Contrast"]
    assert theme.tokens("no-such-theme") == theme.tokens("light")
    assert theme.STATE_TONES["released"] == "ok" and theme.tone_colors("light", "danger")[1] == "#A31F17"
