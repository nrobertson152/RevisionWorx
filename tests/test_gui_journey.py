# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end GUI acceptance journey through the controller and view-models (backlog #28 item 04).

import -> check out -> edit -> check in -> the app is killed between commit and apply of the next check-in
-> restart (a brand-new Controller on the same per-user folders) -> the left-behind lock and the pending
change are shown -> an administrator breaks the lock with the command the GUI gives -> F5 -> the next change
completes the pending one -> everything is checked independently of the GUI, through the CLI and the bytes.

Real core, real temporary filesystem, and a real second process that really dies (``os._exit``) after its
journal entry is committed. Nothing about the core is mocked in this process. Qt is not needed; the same
journey through the real window is ``tests/test_qt_desktop_alpha_journey.py``.
"""
from pathlib import Path

import pytest

from journey_helpers import exported, make_ctl, run_crashing_checkin, sha
from revisionworx.cli import main as cli
from revisionworx.gui.controller import Controller, describe_error
from revisionworx.gui.viewmodels import AppStatus, CheckinForm, DocumentPanel, ImportFlow
from revisionworx.workspace import Workspace, WorkspaceLockedError


def pump(c: Controller, until, timeout=30):
    import time
    end = time.time() + timeout
    while time.time() < end:
        c.drain()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


@pytest.fixture
def journey(tmp_path):
    """Steps 1-3: quick start, import, check out, edit, check in (all through view-models)."""
    root = tmp_path / "user"
    c = make_ctl(root)
    home = tmp_path / "My Projects"
    project = c.quick_start(home, "Bracket", number="P-100")
    src = tmp_path / "outside" / "bracket.ipt"
    src.parent.mkdir()
    src.write_bytes(b"inventor part v1")

    flow = ImportFlow(c, project["id"])
    flow.set_source(str(src))
    flow.description = "Initial release"
    assert flow.start()
    pump(c, lambda: flow.state in ("done", "error"))
    assert flow.state == "done" and flow.result, flow.message
    doc, rev_a = flow.result

    panel = DocumentPanel(c)
    panel.load(doc["id"])
    panel.checkout()
    assert panel.can_checkin, panel.message
    Path(panel.working_path).write_bytes(b"inventor part v2")
    form = CheckinForm(description="Widened mounting slot to 8 mm")
    assert form.validate()
    assert panel.start_checkin(form.description)
    pump(c, lambda: not panel.message.startswith("Checking in"))
    assert panel.message.startswith("Checked in revision"), panel.message
    rev_b = panel.history[0]
    return dict(root=root, c=c, home=home, project=project, src=src, doc=doc, rev_a=rev_a, rev_b=rev_b,
                tmp=tmp_path)


def test_import_checkout_checkin_crash_restart_recover(journey):
    j = journey
    root, home, doc = j["root"], j["home"], j["doc"]
    c = j["c"]
    working = Path(c.working_file(doc["id"]))
    # Independent of the GUI: the stored revisions hold exactly the bytes that were imported and checked in.
    assert exported(home, j["rev_a"]["id"], j["tmp"] / "a.ipt") == b"inventor part v1"
    assert exported(home, j["rev_b"]["id"], j["tmp"] / "b.ipt") == b"inventor part v2"
    assert j["src"].read_bytes() == b"inventor part v1"  # the outside original is never touched

    # 4. Check out again, edit, and the app dies between commit and apply of the check-in.
    c.checkout(doc["id"])
    working.write_bytes(b"inventor part v3")
    del c  # this process lets go of the app; the "previous run" is the child below
    assert run_crashing_checkin(root, doc["id"], "Thicker flange for load case B") == 9
    holder = Workspace.read_writer_lock(home)
    assert holder is not None  # the crash left its lock behind

    # 5. Restart: a brand-new controller on the same per-user folders reopens the last workspace.
    c2 = make_ctl(root)
    assert c2.recent_workspaces()[0] == str(home)
    c2.open_workspace(Path(c2.recent_workspaces()[0]))
    status = AppStatus(c2)
    status.check()  # what the window shows on open and on F5
    h = c2.health()
    assert not h["blocked"] and not c2.read_only and h["problems"] == []
    assert h["pending"] == 1 and "inconsisten" not in h["message"].lower()
    # The committed check-in is not lost: it is already part of the history after the restart...
    panel = DocumentPanel(c2)
    panel.load(doc["id"])
    assert [r["label"] for r in panel.history] == ["C", "B", "A"]
    assert panel.history[0]["sha256"] == sha(b"inventor part v3")
    assert panel.detail["checkout"] is None and not panel.can_checkin  # no phantom "checked out by you"
    assert working.read_bytes() == b"inventor part v3"  # the user's file is untouched

    # 6. The next change is refused because of the crash lock, and the refusal says how to recover.
    with pytest.raises(WorkspaceLockedError) as ei:
        c2.checkout(doc["id"])
    advice = describe_error(ei.value)
    assert holder["token"] in advice and "lock break" in advice and "crash" in advice
    assert c2.health()["pending"] == 1  # the refusal wrote nothing

    # 7. The administrator follows that advice; F5; the next change applies the pending entries first.
    assert cli(["-w", str(home), "lock", "break", "--token", holder["token"],
                "--reason", "test: the session crashed"]) in (0, None)
    status.check()
    assert not c2.read_only
    panel.checkout()
    assert panel.can_checkin, panel.message
    assert c2.health() == {"ok": True, "blocked": False, "problems": [], "pending": 0, "message": ""}

    # 8. Independently of the GUI: three revisions with the right bytes, the journal verifies clean.
    assert exported(home, panel.history[0]["id"], j["tmp"] / "c.ipt") == b"inventor part v3"
    assert exported(home, j["rev_b"]["id"], j["tmp"] / "b2.ipt") == b"inventor part v2"
    assert cli(["-w", str(home), "verify"]) in (0, None)
    assert cli(["-w", str(home), "doc", "verify", "--id", doc["id"]]) in (0, None)  # re-hashes stored content

    # 9. And it keeps working: check in after the recovery, then another restart sees all four revisions.
    working.write_bytes(b"inventor part v4")
    assert panel.start_checkin("Added lightening holes after recovery")
    pump(c2, lambda: not panel.message.startswith("Checking in"))
    assert panel.message.startswith("Checked in revision"), panel.message
    c3 = make_ctl(root)
    c3.open_workspace(home)
    detail = c3.document_detail(doc["id"])
    assert [r["label"] for r in detail["history"]] == ["A", "B", "C", "D"]  # oldest first here
    assert detail["checkout"] is None and c3.health()["pending"] == 0


def test_pending_notice_does_not_promise_the_next_change_while_a_lock_is_held(journey):
    """Regression (found by this journey): after a crash between commit and apply, the notice said "your
    next change applies it first", but every change is refused until the crash's writer lock is released or
    broken. The notice now says a lock is held, by whom, and that changes wait for it."""
    j = journey
    root, home, doc = j["root"], j["home"], j["doc"]
    j["c"].checkout(doc["id"])
    Path(j["c"].working_file(doc["id"])).write_bytes(b"inventor part v3")
    assert run_crashing_checkin(root, doc["id"], "Crashes after commit") == 9
    holder = Workspace.read_writer_lock(home)
    c2 = make_ctl(root)
    c2.open_workspace(home)
    h = c2.health()
    assert h["pending"] == 1 and not h["blocked"]
    msg = h["message"]
    assert "your next change applies" not in msg.lower()
    assert "writer lock" in msg and f"pid {holder['pid']}" in msg and "refused" in msg
    # Once the lock is gone, the original promise is true again and is what is shown.
    assert cli(["-w", str(home), "lock", "break", "--token", holder["token"], "--reason", "test"]) in (0, None)
    msg = c2.health()["message"]
    assert "next change applies" in msg and "writer lock" not in msg
