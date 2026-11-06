# SPDX-License-Identifier: GPL-3.0-or-later
"""About RevisionWorx: the same shape as OpenSchematix's About dialog (name, version, purpose, licence, what it
does and does not claim, links, and the exact versions to quote in a bug report)."""
from __future__ import annotations

import platform

from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ... import __version__
from . import branding, theme

APP_NAME = "RevisionWorx"
TAGLINE = "Offline engineering document and revision control"
COPYRIGHT = "Copyright © 2026 Nate Robertson and RevisionWorx contributors."
LICENSE_NAME = "GNU General Public License v3.0 or later"
REPO_URL = "https://github.com/nrobertson152/RevisionWorx"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
DOCS_URL = REPO_URL + "/blob/main/docs/USER_GUIDE.md"


def system_info() -> str:
    try:
        import PySide6
        pyside = PySide6.__version__
    except Exception:  # noqa: BLE001
        pyside = "?"
    return (f"{APP_NAME} {__version__} · Python {platform.python_version()} · PySide6 {pyside} · Qt {qVersion()} · "
            f"{platform.system()} {platform.release()} ({platform.machine()})")


BODY = (f"{COPYRIGHT}\n\n"
        f"Free and open-source software, licensed under the {LICENSE_NAME}. You may use, study, share and "
        "modify it under the terms of that licence. It comes with ABSOLUTELY NO WARRANTY.\n\n"
        "RevisionWorx keeps your project folders where they are and records every revision, checkout and "
        "approval in a crash-safe journal next to them. It is made for one computer's local disk, or for a team "
        "working from a shared folder, with no server. How well a shared folder works between real computers has "
        "not been verified (see Known limitations in the user guide). Keep regular backups of your engineering data.\n\n"
        "RevisionWorx has no ads, no telemetry, no online accounts, no subscriptions and no feature limits, and never "
        "connects to the internet. It works with OpenSchematix, OpenSchematix Viewer, TraceMarx, Caldera and "
        "Ledgerbook through its integration interface.")


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setObjectName("aboutDialog")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.logo = QLabel(self)
        self.logo.setObjectName("aboutLogo")
        self.logo.setPixmap(branding.logo_pixmap(72))
        self.logo.setFixedSize(72, 72)
        self.logo.setAccessibleName("RevisionWorx logo")
        top.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(2)
        name = QLabel(APP_NAME, self)
        name.setObjectName("projectHeading")
        version = QLabel(f"Version {__version__}", self)
        tag = QLabel(TAGLINE, self)
        theme.set_flag(tag, "role", "muted")
        for w in (name, version, tag):
            names.addWidget(w)
        names.addStretch(1)
        top.addLayout(names, 1)
        lay.addLayout(top)
        self.body = QLabel(BODY, self)
        self.body.setWordWrap(True)
        self.body.setMinimumWidth(460)
        self.body.setAccessibleName("About RevisionWorx")
        lay.addSpacing(10)
        lay.addWidget(self.body)
        links = QHBoxLayout()
        for text, url in (("Project page", REPO_URL), ("User guide", DOCS_URL), ("Licence text", LICENSE_URL)):
            b = QPushButton(text, self)
            theme.set_flag(b, "flat", True)
            b.setToolTip(url)
            b.clicked.connect(lambda *_, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.addWidget(b)
        links.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(links)
        self.info = QLabel(system_info(), self)
        self.info.setWordWrap(True)
        theme.set_flag(self.info, "role", "muted")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.info)
        buttons = QDialogButtonBox(self)
        self.copy_btn = buttons.addButton("Copy version info", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_btn.clicked.connect(lambda *_: QGuiApplication.clipboard().setText(system_info()))
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close.clicked.connect(self.accept)
        lay.addSpacing(8)
        lay.addWidget(buttons)
