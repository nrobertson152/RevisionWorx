# SPDX-License-Identifier: GPL-3.0-or-later
"""Bundler-only CLI entry point; no production application changes."""
from revisionworx.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
