# SPDX-License-Identifier: GPL-3.0-or-later
"""Smoke for a normal (non-editable) install, run by CI from outside the source tree.

Proves the install steps in docs/GUI_ACCEPTANCE.md: the package, both entry points and the Qt modules are
in the installed copy, and the real ``main()`` opens a window that can create a project and import a file.
Not collected by pytest. Exit code 0 means every check passed.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        sys.exit(1)


def main() -> int:
    import revisionworx
    where = Path(revisionworx.__file__).resolve()
    source = Path(__file__).resolve().parent.parent
    check(source not in where.parents, f"imported from the install, not the source tree: {where}")
    bin_dir = Path(sys.executable).parent
    for tool in ("revisionworx", "revisionworx-gui"):
        check(shutil.which(tool, path=str(bin_dir)) is not None, f"entry point installed: {tool}")

    tmp = Path(tempfile.mkdtemp(prefix="rwx-smoke-"))
    # A clean user: nothing left from an earlier run, nothing written to the real profile.
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        os.environ[var] = str(tmp / "profile" / var)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from revisionworx.gui import app as gui_app
    from revisionworx.gui.qt.mainwindow import MainWindow

    qapp = QApplication.instance() or QApplication([sys.argv[0]])
    seen: dict = {}

    def drive() -> None:
        try:
            window = next(w for w in qapp.topLevelWidgets() if isinstance(w, MainWindow))
            seen["first"] = window.observable_state()
            home = tmp / "Acceptance"
            project = window.ctl.quick_start(home, "Smoke", number="S-1")
            source_file = tmp / "part.txt"
            source_file.write_bytes(b"installed smoke")
            window.ctl.import_document(project["id"], source_file)
            seen["opened"] = window.open_location(home, quiet=True, select_project=project["id"])
            seen["second"] = window.observable_state()
            window.close()
        finally:
            qapp.quit()

    QTimer.singleShot(500, drive)
    code = gui_app.main([])
    print("platform", qapp.platformName(), flush=True)
    check(code == 0, f"main() exit code {code}")
    first, second = seen.get("first", {}), seen.get("second", {})
    check(first.get("page") == "start", "a clean user sees the start page")
    check("single user" in first.get("alpha_banner", ""), "the alpha notice is shown")
    check(bool(seen.get("opened")) and second.get("page") == "workspace", "the new workspace opened")
    check(len(second.get("documents", [])) == 1 and len(second.get("history", [])) == 1,
          "the imported document and its first revision are shown")
    print("INSTALLED GUI SMOKE PASSED", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
