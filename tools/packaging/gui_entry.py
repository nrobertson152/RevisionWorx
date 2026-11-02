# SPDX-License-Identifier: GPL-3.0-or-later
"""Bundler-only PySide6 entry point; no production application changes."""
from revisionworx.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
