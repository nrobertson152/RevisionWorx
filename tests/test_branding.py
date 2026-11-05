# SPDX-License-Identifier: GPL-3.0-or-later
"""The official logo (docs/REVISIONWORX_BRANDING_DIRECTIVE.md): the derived icon files exist with the right sizes
and transparency, are packaged, and are what the window, About, start page and splash show."""
import os
import re
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "revisionworx" / "gui" / "resources"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def png_header(path: Path) -> tuple[int, int, int, int]:
    """(width, height, bit depth, colour type) from the PNG IHDR chunk."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    length, kind = struct.unpack(">I4s", data[8:16])
    assert kind == b"IHDR" and length == 13
    w, h, depth, colour = struct.unpack(">IIBB", data[16:26])
    return w, h, depth, colour


def png_corner_alpha(path: Path) -> int:
    """Alpha of the top-left pixel of an 8-bit RGBA PNG (the corner must be transparent). On the first row every
    PNG filter leaves the first pixel's bytes as stored, so no unfiltering is needed."""
    data = path.read_bytes()
    pos, idat = 8, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)                # row 0: filter byte, then R G B A of the first pixel
    return raw[4]


def test_original_and_master_are_committed():
    original = ROOT / "assets" / "branding" / "revisionworx-logo-original.png"
    master = ROOT / "assets" / "branding" / "revisionworx-icon.png"
    w, h, *_ = png_header(original)
    assert (w, h) == (2560, 1429)             # the artwork exactly as the Director supplied it
    mw, mh, depth, colour = png_header(master)
    assert mw == mh and mw >= 900 and depth == 8 and colour == 6   # square RGBA, full resolution


@pytest.mark.parametrize("size", SIZES)
def test_icon_png_sizes_are_square_rgba_with_transparent_corners(size):
    path = RES / f"revisionworx-{size}.png"
    assert png_header(path) == (size, size, 8, 6)
    # the dark presentation background was left out: with it the corner would be opaque (255); resampling
    # the curve can spill a trace into the corner pixel of small sizes
    assert png_corner_alpha(path) < 16


def test_windows_icon_has_every_size():
    data = (RES / "revisionworx.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = set()
    for i in range(count):
        w, h = data[6 + 16 * i], data[7 + 16 * i]
        sizes.add(256 if w == 0 else w)       # 0 means 256 in the ICO directory
    assert {16, 24, 32, 48, 64, 128, 256} <= sizes


def test_icons_are_packaged_for_wheels_and_the_standalone_build():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"revisionworx\.gui"\s*=\s*\[[^\]]*resources/\*\.png[^\]]*resources/\*\.ico', pyproject)
    spec = (ROOT / "packaging" / "revisionworx.spec").read_text(encoding="utf-8")
    assert "datas=datas" in spec and spec.count("icon=icon") == 2
    desktop = (ROOT / "packaging" / "linux" / "revisionworx.desktop.in").read_text(encoding="utf-8")
    assert "Icon=revisionworx" in desktop and "Exec=\"@EXEC@\" %f" in desktop
    script = ROOT / "packaging" / "linux" / "install-desktop-entry.sh"
    assert os.name == "nt" or os.access(script, os.X_OK)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script")
def test_linux_desktop_entry_installer(tmp_path):
    """The installer from the Linux build, run against a fake build folder and a private data folder."""
    import shutil
    import subprocess
    build = tmp_path / "RevisionWorx"
    (build / "icons").mkdir(parents=True)
    for s in SIZES:
        shutil.copy(RES / f"revisionworx-{s}.png", build / "icons")
    for name in ("install-desktop-entry.sh", "revisionworx.desktop.in"):
        shutil.copy(ROOT / "packaging" / "linux" / name, build)
    gui = build / "revisionworx-gui"
    gui.write_text("#!/bin/sh\n")
    gui.chmod(0o755)
    data = tmp_path / "data"
    env = {**os.environ, "XDG_DATA_HOME": str(data)}
    subprocess.run(["sh", str(build / "install-desktop-entry.sh")], check=True, env=env, capture_output=True)
    entry = (data / "applications" / "revisionworx.desktop").read_text(encoding="utf-8")
    assert f'Exec="{gui}" %f' in entry and "Icon=revisionworx" in entry
    assert (data / "icons" / "hicolor" / "48x48" / "apps" / "revisionworx.png").read_bytes() == \
        (RES / "revisionworx-48.png").read_bytes()
    subprocess.run(["sh", str(build / "install-desktop-entry.sh"), "--remove"], check=True, env=env,
                   capture_output=True)
    assert not (data / "applications" / "revisionworx.desktop").exists()


# ------------------------------------------------------------------------------------------------ Qt
qt = pytest.mark.qt


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    return widgets.QApplication.instance() or widgets.QApplication([])


@qt
def test_icon_loads_every_size(qapp):
    from revisionworx.gui.qt import branding
    icon = branding.app_icon()
    assert not icon.isNull()
    assert {16, 24, 32, 48, 64, 128, 256, 512} <= {s.width() for s in icon.availableSizes()}
    pm = branding.logo_pixmap(64, ratio=2.0)
    assert pm.width() == 128 and pm.devicePixelRatio() == 2.0


@qt
def test_window_about_and_start_page_show_the_logo(qapp, tmp_path):
    from revisionworx.gui.controller import Controller
    from revisionworx.gui.qt.about import AboutDialog
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "st"))
    try:
        assert not w.windowIcon().isNull()
        assert not w.start_page.logo.pixmap().isNull()
        about = AboutDialog(w)
        assert not about.logo.pixmap().isNull() and about.logo.accessibleName() == "RevisionWorx logo"
        about.close()
    finally:
        w.close()


@qt
def test_splash_shows_logo_name_and_real_version(qapp):
    from revisionworx import __version__
    from revisionworx.gui.qt import branding
    pm = branding.splash_pixmap(__version__)
    assert not pm.isNull() and round(pm.width() / pm.devicePixelRatio()) == 460
    img = pm.toImage()
    # the logo is drawn centred at the top (x 166-294, y 34-162): its blue left of the R, a white card around it
    tile = img.pixelColor(round(185 * pm.devicePixelRatio()), round(98 * pm.devicePixelRatio()))
    assert tile.blue() > 150 and tile.red() < 100, tile.name()
    assert img.pixelColor(10, 10).name() == "#ffffff"
    splash = branding.Splash(__version__)
    assert splash.version == __version__ and not splash.pixmap().isNull()
    splash.say("Opening…")
    assert splash.message() == "Opening…"
    splash.close()


@qt
def test_startup_shows_the_splash_then_the_window(qapp, tmp_path, monkeypatch):
    """main() shows the splash before building the window and closes it once the window is up."""
    import sys
    from PySide6.QtCore import QTimer
    from revisionworx.gui import app as gui_app
    from revisionworx.gui.qt import branding
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / var))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)   # main() installs its own; restore it afterwards
    seen = []
    real_finish = branding.Splash.finish

    def finish(self, window):
        seen.append((self.isVisible(), window.isVisible()))
        real_finish(self, window)
    monkeypatch.setattr(branding.Splash, "finish", finish)
    def quit_once_running():
        # main() processes events before exec() (the splash), so a plain quit() could land before the loop runs
        # and be lost; quit only from inside the event loop.
        if qapp.thread().loopLevel() >= 1:
            qapp.quit()
        else:
            QTimer.singleShot(50, quit_once_running)
    QTimer.singleShot(0, quit_once_running)
    assert gui_app.main([]) == 0
    assert seen == [(True, True)]                            # splash up until the window was shown
    assert not qapp.windowIcon().isNull()
    for w in qapp.topLevelWidgets():
        w.close()
