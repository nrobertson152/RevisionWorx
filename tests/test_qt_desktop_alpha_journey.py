# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end GUI acceptance through the real window (backlog #28 item 04), offscreen on Linux and Windows.

The journey of ``tests/test_gui_journey.py`` driven through ``MainWindow``: the real buttons, the real import
and check-in dialogs with typed input, F5, the restart of the app with a new window, and the error box a user
gets. A real child process dies between commit and apply. What the window shows is read through the launch
contract (``observable_state``); what was actually stored is checked independently through the CLI and the
bytes on disk.

Keyboard: input inside the dialogs is typed with QTest. The window-level shortcuts (Ctrl+I, Ctrl+K, F5) are
asserted to be bound, but are triggered through the actions they call, because window focus is not
guaranteed offscreen. Real keystrokes on a real display are covered by ``tests/e2e_x11_walkthrough.py``
(Linux only).
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from revisionworx.workspace import Workspace  # noqa: E402
from journey_helpers import exported, make_ctl, run_crashing_checkin, sha  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def boxes(monkeypatch):
    """Record instead of blocking on modal message boxes."""
    shown = []
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[1:])) or 0))
    return shown


def pump(qapp, ctl, until, timeout=30):
    import time
    end = time.time() + timeout
    while time.time() < end:
        ctl.drain()
        qapp.processEvents()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def open_window(qapp, ctl, home, project_id):
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(ctl)
    w.show()
    assert w.open_location(home, quiet=True, select_project=project_id)
    qapp.processEvents()
    return w


def labels(w) -> list[str]:
    return [r["label"] for r in w.observable_state()["history"]]


def check_in_through_the_dialog(qapp, ctl, w, description: str) -> None:
    """The real Check in button opens the real modal dialog; the description is typed into it."""
    from revisionworx.gui.qt.dialogs import CheckinDialog
    seen = []

    def fill():
        modal = qapp.activeModalWidget()
        seen.append(type(modal).__name__)
        if isinstance(modal, CheckinDialog):
            QTest.keyClicks(modal.description, description)
            modal.submit()
        elif modal is not None:
            modal.reject()  # never hang the suite on an unexpected dialog
    QTimer.singleShot(0, fill)
    QTest.mouseClick(w.documents_tab.checkin_btn, Qt.MouseButton.LeftButton)
    assert seen == ["CheckinDialog"], seen
    pump(qapp, ctl, lambda: not ctl.busy and not w.documents_tab.panel.message.startswith("Checking in"))


def test_window_journey_import_checkin_crash_restart_recover(qapp, tmp_path, boxes):
    root = tmp_path / "user"
    c = make_ctl(root)
    home = tmp_path / "My Projects"
    project = c.quick_start(home, "Bracket", number="P-100")
    src = tmp_path / "outside" / "bracket.ipt"
    src.parent.mkdir()
    src.write_bytes(b"inventor part v1")
    w = open_window(qapp, c, home, project["id"])
    tab = w.documents_tab

    # The documented shortcuts are bound on the window (pressed for real in the X11 walkthrough).
    bound = {sc.key().toString(QKeySequence.SequenceFormat.PortableText) for sc in tab.findChildren(QShortcut)}
    assert {"Ctrl+I", "Ctrl+K", "Ctrl+Shift+K"} <= bound

    # 1. Import: the real dialog, the path typed, the title follows the file name.
    tab.import_file()
    dlg = tab.dialogs[-1]
    QTest.keyClicks(dlg.source, str(src))
    assert dlg.title.text() == "bracket"
    QTest.mouseClick(dlg.import_btn, Qt.MouseButton.LeftButton)
    pump(qapp, c, lambda: dlg.flow.state in ("done", "error") and not c.busy)
    assert dlg.flow.result is not None, f"import ended {dlg.flow.state!r}: {dlg.flow.message}"
    doc = dlg.flow.result[0]
    dlg.close()
    pump(qapp, c, lambda: w.observable_state()["selected_document"] == doc["id"])
    assert labels(w) == ["A"]

    # 2. Check out with the real button, edit, check in through the dialog.
    QTest.mouseClick(tab.checkout_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    st = w.observable_state()
    assert st["actions"]["checkin"] and not st["actions"]["checkout"]
    working = Path(c.working_file(doc["id"]))
    working.write_bytes(b"inventor part v2")
    check_in_through_the_dialog(qapp, c, w, "Widened mounting slot to 8 mm")
    assert labels(w) == ["B", "A"] and boxes == []
    assert w.observable_state()["history"][0]["sha256"] == sha(b"inventor part v2")

    # 3. Check out again and edit; the app is then killed between commit and apply of its check-in.
    QTest.mouseClick(tab.checkout_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    working.write_bytes(b"inventor part v3")
    w.close()
    qapp.processEvents()
    del w, tab, c
    assert run_crashing_checkin(root, doc["id"], "Thicker flange for load case B") == 9
    holder = Workspace.read_writer_lock(home)
    assert holder is not None

    # 4. Restart: a new controller and a new window reopen the last workspace.
    c2 = make_ctl(root)
    assert c2.recent_workspaces()[0] == str(home)
    w2 = open_window(qapp, c2, Path(c2.recent_workspaces()[0]), project["id"])
    tab2 = w2.documents_tab
    tab2.select_document(doc["id"])
    qapp.processEvents()
    st = w2.observable_state()
    assert not st["read_only"] and "RECOVERY BLOCKED" not in st["health_banner"]
    assert "waiting to be applied" in st["health_banner"] and "inconsisten" not in st["health_banner"].lower()
    assert "writer lock" in st["health_banner"] and f"pid {holder['pid']}" in st["health_banner"]
    assert labels(w2) == ["C", "B", "A"]  # the committed check-in survived the crash
    assert st["history"][0]["sha256"] == sha(b"inventor part v3")
    assert st["actions"]["checkout"] and not st["actions"]["checkin"]  # no phantom checkout
    assert working.read_bytes() == b"inventor part v3"

    # 5. The user tries to check out: refused, and the error box gives the way out.
    QTest.mouseClick(tab2.checkout_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert [b[0] for b in boxes] == ["critical"]
    text = boxes[0][1][1]
    assert holder["token"] in text and "lock break" in text
    assert not w2.observable_state()["actions"]["checkin"]

    # 6. The administrator follows it; F5; the next checkout applies the pending changes and works.
    from revisionworx.cli import main as cli
    assert cli(["-w", str(home), "lock", "break", "--token", holder["token"],
                "--reason", "test: the session crashed"]) in (0, None)
    w2.refresh_all()
    qapp.processEvents()
    assert "writer lock" not in w2.observable_state()["health_banner"]
    QTest.mouseClick(tab2.checkout_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    st = w2.observable_state()
    assert st["actions"]["checkin"] and len(boxes) == 1
    w2.refresh_all()
    qapp.processEvents()
    assert w2.observable_state()["health_banner"] == ""

    # 7. And it keeps working after the recovery.
    working.write_bytes(b"inventor part v4")
    check_in_through_the_dialog(qapp, c2, w2, "Added lightening holes after recovery")
    assert labels(w2) == ["D", "C", "B", "A"] and len(boxes) == 1
    w2.close()

    # 8. Independently of the GUI: the stored bytes, the journal and the outside original.
    c3 = make_ctl(root)
    c3.open_workspace(home)
    revs = {r["label"]: r["id"] for r in c3.document_detail(doc["id"])["history"]}
    for label, data in (("A", b"inventor part v1"), ("B", b"inventor part v2"),
                        ("C", b"inventor part v3"), ("D", b"inventor part v4")):
        assert exported(home, revs[label], tmp_path / f"rev{label}.ipt") == data
    assert cli(["-w", str(home), "verify"]) in (0, None)
    assert cli(["-w", str(home), "doc", "verify", "--id", doc["id"]]) in (0, None)
    assert src.read_bytes() == b"inventor part v1"
    assert c3.health()["pending"] == 0
