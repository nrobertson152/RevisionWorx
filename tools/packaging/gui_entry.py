# SPDX-License-Identifier: GPL-3.0-or-later
"""Bundler-only PySide6 entry point; no production application changes.

Import QtWidgets explicitly at module scope so PyInstaller treats the GUI
dependency as mandatory instead of following the guarded optional import
inside revisionworx.gui.app.main(). Import failures now retain their cause.
"""
from PySide6.QtWidgets import QApplication  # noqa: F401  (force Qt bundle)
from revisionworx.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
