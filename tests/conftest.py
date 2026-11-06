# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # make helper modules importable in spawned processes

from revisionworx import projects  # noqa: E402
from revisionworx.workspace import Workspace  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def tree_snapshot(root: Path) -> dict:
    """Every path under root with type, size, mtime and content hash - for non-destruction checks."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = os.lstat(p)
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                snap[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                snap[rel] = ("dir", st.st_mtime_ns)
            else:
                snap[rel] = ("file", st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    snap["."] = ("dir", os.lstat(root).st_mtime_ns)
    return snap


@pytest.fixture
def share(tmp_path):
    """A fake engineering share: metadata folder and an engineering root side by side."""
    s = tmp_path / "share"
    (s / "Engineering").mkdir(parents=True)
    return s


@pytest.fixture
def ws(share):
    w = Workspace.create(share, "Test workspace")
    w.root_id = projects.add_root(w, "Engineering", share / "Engineering")
    yield w
    w.close()


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"



@contextlib.contextmanager
def qt_window_cleanup():
    """Delete, on exit, the hidden top-level windows created inside the block. Windows that are still shown, that
    have a parent, or that existed before the block are left alone. Does nothing without PySide6 or an application."""
    try:
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        yield
        return

    def alive() -> dict:
        app = QApplication.instance()
        return {shiboken6.getCppPointer(w)[0]: w for w in app.topLevelWidgets()} if app is not None else {}

    before = set(alive())
    try:
        yield
    finally:
        for pointer, widget in alive().items():
            if pointer not in before and widget.parent() is None and not widget.isVisible():
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _delete_closed_qt_windows(request):
    """Qt tests close their windows but nothing deletes them, and every new main window applies the colour theme to
    the whole application, which re-styles every widget still alive: by the later Qt modules a fixture took ~13 s to
    set up and as long to tear down (main 098a805). Once a Qt test is over, delete the hidden top-level windows it
    created (qt_window_cleanup)."""
    if request.node.get_closest_marker("qt") is None:
        yield
        return
    with qt_window_cleanup():
        yield


@pytest.fixture(autouse=True)
def _team_tests_stretch_passwords_less(monkeypatch, tmp_path):
    """Real accounts stretch a password for about a third of a second on purpose. Tests that log in hundreds of times don't need
    to. tests/test_accounts.py checks what real accounts use. And no test's local state ends up in the real user's profile."""
    from revisionworx import accounts
    monkeypatch.setattr(accounts, "KDF", {"name": "scrypt", "n": 2 ** 4, "r": 8, "p": 1})
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / ".local-state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / ".local-state"))
    yield
