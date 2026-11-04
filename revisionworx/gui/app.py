# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop entry point (PySide6 / Qt 6).

The previous Tkinter view was removed in favour of PySide6 (ADR-009). Business logic stays in the
toolkit-free ``controller`` and ``viewmodels`` modules.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

MISSING_QT = ("RevisionWorx's desktop window needs PySide6 (Qt 6).\n"
              "Install it with:  pip install 'PySide6-Essentials>=6.5'\n"
              "The command-line interface works without it:  python -m revisionworx --help")


def exception_reporter(log=None):
    """A last resort for a defect in a Qt slot. PySide6 only prints those, and the installed gui-script has
    no console, so the user would see nothing happen. Says plainly that the outcome is not known."""
    def report(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if log is not None:
            log(text)
        elif sys.stderr is not None:
            sys.stderr.write(text)
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            return
        try:
            QMessageBox.critical(None, "RevisionWorx: unexpected error",
                                 f"Something unexpected went wrong: {exc}\n\nIf you were making a change, it "
                                 "may or may not have been saved. Press F5 and check the revision history "
                                 "before repeating it.")
        except Exception:  # a report must never raise out of the hook
            pass
    return report


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(MISSING_QT, file=sys.stderr)
        return 3
    from .controller import Controller
    from .qt.mainwindow import MainWindow

    sys.excepthook = exception_reporter()
    app = QApplication.instance() or QApplication([sys.argv[0], *argv])
    app.setApplicationName("RevisionWorx")
    app.setOrganizationName("RevisionWorx")
    window = MainWindow(Controller())
    window.show()
    recent = window.ctl.recent_workspaces()
    if argv:
        window.open_location(Path(argv[0]))
    elif recent and Path(recent[0]).exists():
        window.open_location(Path(recent[0]), quiet=True)  # failure -> start page shows why
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
