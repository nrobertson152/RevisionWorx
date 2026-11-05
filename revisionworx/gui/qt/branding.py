# SPDX-License-Identifier: GPL-3.0-or-later
"""The official RevisionWorx logo (docs/REVISIONWORX_BRANDING_DIRECTIVE.md): application/window/taskbar icon,
the About and start-page logo, and the startup splash.

The image files are derived from the Director-supplied artwork by ``packaging/make_icons.py`` and ship inside
the package (``revisionworx/gui/resources``), so the same lookup works from a source checkout, an installed wheel
and the PyInstaller build (which keeps the package layout under its bundle folder)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSplashScreen

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
APP_USER_MODEL_ID = "RevisionWorx.RevisionWorx"   # Windows: group the taskbar button under our own icon


def resource(name: str) -> Path:
    return RESOURCES / name


def icon_files() -> list[Path]:
    return [resource(f"revisionworx-{s}.png") for s in ICON_SIZES]


def app_icon() -> QIcon:
    """Every size, so each platform picks a crisp one (16 px title bar, 32/48 taskbar, 256 task switcher)."""
    icon = QIcon()
    for size, path in zip(ICON_SIZES, icon_files()):
        if path.is_file():
            icon.addFile(str(path), QSize(size, size))
    return icon


def logo_pixmap(size: int, ratio: float | None = None) -> QPixmap:
    """The logo at ``size`` logical pixels, sharp on high-DPI screens (an empty pixmap if the files are missing)."""
    if ratio is None:
        app = QGuiApplication.instance()
        ratio = app.devicePixelRatio() if app is not None else 1.0
    want = max(1, round(size * ratio))
    source = next((p for s, p in zip(ICON_SIZES, icon_files()) if s >= want and p.is_file()), None)
    if source is None:
        source = next((p for p in reversed(icon_files()) if p.is_file()), None)
    if source is None:
        return QPixmap()
    pm = QPixmap(str(source))
    if pm.width() != want:
        pm = pm.scaled(want, want, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pm.setDevicePixelRatio(ratio)
    return pm


def set_platform_identity(app) -> None:
    """Make the taskbar/dock show RevisionWorx, not the Python interpreter that runs it."""
    app.setWindowIcon(app_icon())
    app.setDesktopFileName("revisionworx")           # Wayland/X11: matches revisionworx.desktop
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:  # noqa: BLE001 - cosmetic; never stop the program starting
            pass


def splash_pixmap(version: str, ratio: float = 1.0) -> QPixmap:
    """The splash image: logo, name and the real version, on a plain card (no decorative animation)."""
    w, h = 460, 300
    pm = QPixmap(round(w * ratio), round(h * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(QColor("#FFFFFF"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(QColor("#C6CCD5"))
    p.drawRect(QRect(0, 0, w - 1, h - 1))
    logo = logo_pixmap(128, ratio)
    if not logo.isNull():
        p.drawPixmap(QRect((w - 128) // 2, 34, 128, 128), logo)
    title = QFont()
    title.setPointSizeF(20)
    title.setBold(True)
    p.setFont(title)
    p.setPen(QColor("#14181F"))
    p.drawText(QRect(0, 176, w, 40), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, "RevisionWorx")
    small = QFont()
    small.setPointSizeF(10)
    p.setFont(small)
    p.setPen(QColor("#56606C"))
    p.drawText(QRect(0, 216, w, 24), Qt.AlignmentFlag.AlignHCenter, f"Version {version}")
    p.end()
    return pm


class Splash(QSplashScreen):
    """Shown while the window is built and the last workspace opens; closed by ``finish(window)``. Status lines
    go along the bottom. It never waits: it is up only as long as real start-up work takes."""

    def __init__(self, version: str):
        app = QGuiApplication.instance()
        ratio = app.devicePixelRatio() if app is not None else 1.0
        super().__init__(splash_pixmap(version, ratio))
        self.version = version
        self.setObjectName("revisionworxSplash")
        self.setWindowIcon(app_icon())

    def say(self, text: str) -> None:
        self.showMessage(text, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, QColor("#56606C"))
        QGuiApplication.processEvents()
