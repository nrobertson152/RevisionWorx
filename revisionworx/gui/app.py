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


def self_test(report: Path) -> int:
    """``--self-test REPORT``: start the real window offscreen-capable, create a project in a temporary
    folder, import a file, reopen the workspace, close, and write the result to REPORT. Used to prove that
    an installed or packaged copy really starts (a windowed Windows executable has no console). Touches
    only a fresh temporary folder and a temporary profile. Exit code 0 means every step passed."""
    import os
    import shutil
    import tempfile
    lines: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="rwx-selftest-"))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        os.environ[var] = str(tmp / "profile" / var)
    ok = False
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from .. import __version__
        from .controller import Controller
        from .qt.mainwindow import MainWindow
        from .qt.branding import Splash, app_icon, set_platform_identity
        app = QApplication.instance() or QApplication([sys.argv[0]])
        set_platform_identity(app)
        splash = Splash(__version__)
        splash.show()
        window = MainWindow(Controller())
        window.show()
        splash.finish(window)
        result: dict = {"icon sizes": sorted(s.width() for s in app_icon().availableSizes()),
                        "splash": not splash.pixmap().isNull()}

        def drive() -> None:
            try:
                home = tmp / "SelfTest"
                project = window.ctl.quick_start(home, "Self test", number="ST-1")
                src = tmp / "part.txt"
                src.write_bytes(b"RevisionWorx self-test")
                window.ctl.import_document(project["id"], src)
                result["opened"] = window.open_location(home, quiet=True, select_project=project["id"])
                result["documents"] = len(window.ctl.documents(project["id"]))
                result["title"] = window.windowTitle()
            except BaseException as exc:  # noqa: BLE001 - reported below
                result["error"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            finally:
                window.close()
                app.quit()
        QTimer.singleShot(0, drive)
        app.exec()
        lines.append(f"version {__version__}")
        lines += [f"{k}: {v}" for k, v in result.items()]
        ok = (not result.get("error") and result.get("opened") is True and result.get("documents") == 1
              and __version__ in str(result.get("title", "")) and result.get("splash") is True
              and {16, 32, 48, 256} <= set(result.get("icon sizes", [])))
    except BaseException as exc:  # noqa: BLE001
        lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines.append("self-test " + ("PASSED" if ok else "FAILED"))
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--self-test":
        return self_test(Path(argv[1]))
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(MISSING_QT, file=sys.stderr)
        return 3
    from .controller import Controller
    from .qt.mainwindow import MainWindow

    from .. import __version__
    from .qt.branding import Splash, set_platform_identity

    sys.excepthook = exception_reporter()
    app = QApplication.instance() or QApplication([sys.argv[0], *argv])
    app.setApplicationName("RevisionWorx")
    app.setOrganizationName("RevisionWorx")
    app.setApplicationVersion(__version__)
    set_platform_identity(app)
    # The splash covers the real start-up work (building the window, opening the last workspace) and closes as
    # soon as the window is ready: it never adds a delay of its own.
    splash = Splash(__version__)
    splash.show()
    splash.say("Starting…")
    location, document = _parse(argv)
    window = MainWindow(Controller())
    recent = window.ctl.recent_workspaces()
    target = Path(location) if location else (Path(recent[0]) if recent and Path(recent[0]).exists() else None)
    if target is not None:
        splash.say(f"Opening {target.name or target}…")
    window.show()
    if location:
        if window.open_location(Path(location), restore_selection=document is None) and document:
            window.go_to_document(None, document)
    elif target is not None:
        window.open_location(target, quiet=True, restore_selection=True)  # failure -> start page shows why
    splash.finish(window)
    return app.exec()


def _parse(argv: list[str]) -> tuple[str | None, str | None]:
    """``[WORKSPACE] [--document DOC_ID]`` (``revisionworx link open`` starts the window this way)."""
    location = document = None
    i = 0
    while i < len(argv):
        if argv[i] == "--document" and i + 1 < len(argv):
            document = argv[i + 1]
            i += 2
            continue
        if not argv[i].startswith("-") and location is None:
            location = argv[i]
        i += 1
    return location, document


if __name__ == "__main__":
    sys.exit(main())
