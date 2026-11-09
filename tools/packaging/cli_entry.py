# SPDX-License-Identifier: GPL-3.0-or-later
"""Bundler-only UTF-8 CLI entry point; no changes to production cli.py.

The default Windows console codepage may be cp1252 even when a valid PDM path
contains non-ASCII characters. A frozen CLI redirected to a subprocess pipe
must be able to emit machine-readable JSON without an encoding failure.
"""
import sys
from revisionworx.cli import main


def configure_utf8_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


if __name__ == "__main__":
    configure_utf8_console()
    raise SystemExit(main())
