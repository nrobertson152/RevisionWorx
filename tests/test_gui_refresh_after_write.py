# SPDX-License-Identifier: GPL-3.0-or-later
"""A saved change stays reported as saved when re-reading the document straight afterwards fails.

Windows CI run 41711104288 showed "Cannot read this document: Permission denied: ...state.json" in place
of "Checked in." after a check-in that had been committed: the core had just replaced state.json and the
next read was refused. The message told the user the opposite of what had happened, inviting a repeat.

Real core on a real temporary filesystem. Only the panel's re-read is made to fail.
"""
from pathlib import Path

import pytest

from revisionworx.gui.controller import Controller
from revisionworx.gui.viewmodels import DocumentPanel

DENIED = PermissionError(13, "Permission denied", "state.json")


@pytest.fixture
def world(tmp_path: Path):
    c = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    project = c.quick_start(tmp_path / "home", "Bracket", number="P-100")
    src = tmp_path / "bracket.ipt"
    src.write_bytes(b"v1")
    doc, _ = c.import_document(project["id"], src, "")
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    return c, doc["id"], panel, project


@pytest.fixture
def reads_denied(world, monkeypatch):
    """Switch that makes the panel's document re-read fail, leaving the write itself alone."""
    c = world[0]
    real = c.document_detail
    state = {"on": False}

    def detail(*a, **k):
        if state["on"]:
            raise DENIED
        return real(*a, **k)
    monkeypatch.setattr(c, "document_detail", detail)
    return state


def _assert_saved_but_unread(panel, did, words):
    assert panel.message.startswith(words), panel.message
    assert "Cannot read" not in panel.message
    assert "Press F5" in panel.message and "Permission denied" in panel.message
    assert panel.message_level != "error"
    # What the panel knows is now out of date, so nothing may act on it.
    assert panel.detail is None and panel.failed_id == did
    assert not (panel.can_checkout or panel.can_checkin or panel.can_cancel)


def test_checkout_read_failure_is_not_reported_as_failed(world, reads_denied):
    c, did, panel, _ = world
    reads_denied["on"] = True
    panel.checkout()  # must not raise: the checkout happened
    _assert_saved_but_unread(panel, did, "Checked out to you")
    reads_denied["on"] = False
    panel.load_or_report(did)
    assert panel.can_checkin and panel.message.startswith("Checked out to you")
    assert "Press F5" not in panel.message and panel.message_level == "ok"


def test_cancel_read_failure_is_not_reported_as_failed(world, reads_denied):
    c, did, panel, _ = world
    panel.checkout()
    reads_denied["on"] = True
    panel.cancel(revert=False)
    _assert_saved_but_unread(panel, did, "Checkout released")


def test_restore_read_failure_is_not_reported_as_failed(world, reads_denied):
    c, did, panel, _ = world
    panel.checkout()
    c.working_file(did).write_bytes(b"v2")
    assert panel.start_checkin("second")
    assert c.wait_idle(30)
    panel.checkout()
    reads_denied["on"] = True
    panel.restore(1)
    _assert_saved_but_unread(panel, did, "Working file replaced with revision")
    reads_denied["on"] = False
    assert c.working_file(did).read_bytes() == b"v1"


def test_state_change_read_failure_is_not_reported_as_failed(world, reads_denied):
    c, did, panel, _ = world
    state, name = panel.transition_choices[0]
    reads_denied["on"] = True
    rev = panel.transition_latest(state)
    assert rev["state"] == state
    _assert_saved_but_unread(panel, did, "State changed to")
    reads_denied["on"] = False
    panel.load_or_report(did)
    assert panel.detail["latest"]["state"] == state


def test_checkin_read_failure_keeps_success_and_blocks_stale_actions(world, reads_denied):
    c, did, panel, _ = world
    panel.checkout()
    c.working_file(did).write_bytes(b"v2")
    reads_denied["on"] = True
    assert panel.start_checkin("second")
    assert c.wait_idle(30)
    _assert_saved_but_unread(panel, did, "Checked in revision")
    assert panel.message_level == "warning"  # amber until the document can be read again
    panel.load_or_report(did)  # a second failed read, as the list refresh does, keeps it too
    _assert_saved_but_unread(panel, did, "Checked in revision")
    assert panel.message.count("Press F5") == 1
    reads_denied["on"] = False
    panel.load_or_report(did)
    assert panel.message.startswith("Checked in revision") and len(panel.history) == 2
    assert panel.message_level == "ok" and "Press F5" not in panel.message


def test_failed_write_is_still_a_failure(world, reads_denied):
    """Only a write that happened is protected. A refused one reports the refusal, and no stale outcome."""
    c, did, panel, _ = world
    panel.checkout()
    assert panel.start_checkin("  ") is False
    assert panel.message.startswith("Describe the change")
    reads_denied["on"] = True
    panel.load_or_report(did)
    assert panel.message.startswith("⚠ Cannot read this document") and panel.message_level == "error"


def test_outcome_does_not_follow_to_another_document(world, reads_denied, tmp_path):
    c, did, panel, project = world
    src = tmp_path / "other.ipt"
    src.write_bytes(b"other")
    other, _ = c.import_document(project["id"], src, "")
    panel.checkout()
    assert panel.message.startswith("Checked out to you")
    reads_denied["on"] = True
    panel.load_or_report(other["id"])
    assert panel.message.startswith("⚠ Cannot read this document") and panel.message_level == "error"
    assert "Checked out" not in panel.message
    reads_denied["on"] = False
    panel.load_or_report(did)
    assert not panel.message.startswith("Checked out")  # it was superseded, so it does not come back


def test_unreadable_document_with_no_outcome_is_still_an_error(world, reads_denied):
    c, did, panel, _ = world
    reads_denied["on"] = True
    panel.load_or_report(did)
    assert panel.message.startswith("⚠ Cannot read this document") and panel.message_level == "error"
    assert panel.detail is None
