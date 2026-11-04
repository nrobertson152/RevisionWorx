# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen-build entry point for the command line."""
import sys

from revisionworx.cli import main

if __name__ == "__main__":
    sys.exit(main())
