# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop entry point (PySide6 / Qt 6).

The previous Tkinter view was removed in favour of PySide6 (ADR-009). Business logic stays in the
toolkit-free ``controller`` and ``viewmodels`` modules.
"""
from __future__ import annotations

import sys
from pathlib import Path

MISSING_QT = ("RevisionWorx's desktop window needs PySide6 (Qt 6).\n"
              "Install it with:  pip install 'PySide6-Essentials>=6.5'\n"
              "The command-line interface works without it:  python -m revisionworx --help")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(MISSING_QT, file=sys.stderr)
        return 3
    from .controller import Controller
    from .qt.mainwindow import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0], *argv])
    app.setApplicationName("RevisionWorx")
    app.setOrganizationName("RevisionWorx")
    window = MainWindow(Controller())
    window.show()
    recent = window.ctl.recent_workspaces()
    if argv:
        window.open_location(Path(argv[0]))
    elif recent:
        window.open_location(Path(recent[0]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
