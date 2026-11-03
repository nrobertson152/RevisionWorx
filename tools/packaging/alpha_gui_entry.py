# SPDX-License-Identifier: GPL-3.0-or-later
"""TEST-ONLY wrapper for an exact-SHA frozen desktop-alpha build.

Normal invocation launches the actual RevisionWorx GUI. The --alpha-probe
mode drives real GUI widgets for disposable acceptance, and MUST NOT ship.
No production source code is modified for this harness.
"""
import sys

from PySide6.QtWidgets import QApplication  # noqa: F401 (force Qt bundle)
from revisionworx.gui.app import main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--alpha-probe":
        from alpha_frozen_probe import main as run_probe
        raise SystemExit(run_probe(sys.argv[2:]))
    raise SystemExit(main())
