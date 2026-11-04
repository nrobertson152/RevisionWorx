# SPDX-License-Identifier: GPL-3.0-or-later
"""Workspace health re-check in the desktop GUI (controller + view-model, no Qt needed).

Two defects, both against the REAL core on a real temporary filesystem (no mocks of the core):

1. A recovery block that only a refused write can see (an unreadable orphaned ``.releasing-*``
   lock file) was cleared by F5, because the read-only ``verify()`` cannot see the lock namespace.
   The GUI went back to looking writable although nothing had changed, and invited the same refused
   change again. The block must persist until something proves the cause is gone.

2. (A8) Entries that are committed to the journal but not yet applied were reported as
   "inconsistencies" by the read-only check. That happens after an ordinary administrator command
   or a crash between commit and apply, and is completed by the next change, so calling it an
   inconsistency was a false alarm. It is now reported as pending; real inconsistencies still are.
"""
import json
from pathlib import Path

import pytest

from revisionworx import projects
from revisionworx.gui.controller import Controller, describe_error
from revisionworx.gui.viewmodels import AppStatus, DocumentPanel
from revisionworx.workspace import Workspace, WorkspaceCorruptError, WorkspaceLockedError


def make_ctl(tmp_path: Path) -> Controller:
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")


@pytest.fixture
def started(tmp_path):
    c = make_ctl(tmp_path)
    home = tmp_path / "My Projects"
    project = c.quick_start(home, "Bracket", number="P-100")
    src = tmp_path / "outside" / "bracket.ipt"
    src.parent.mkdir()
    src.write_bytes(b"inventor part v1")
    doc, _ = c.import_document(project["id"], src, "")
    return c, home, project, doc


def _orphan(home: Path) -> Path:
    path = home / ".revisionworx" / "locks" / ".releasing-deadbeef"
    path.write_text("{}")  # unreadable as a release record: its writer cannot be fenced
    return path


def _refused_checkout(c: Controller, doc_id: str) -> None:
    with pytest.raises(WorkspaceCorruptError):
        c.checkout(doc_id)
    assert c.read_only


# ---------------------------------------------------------------------------- 1. write-found block
def test_f5_keeps_a_block_that_only_a_write_can_see(started):
    c, home, _project, doc = started
    _orphan(home)
    assert c.health()["ok"] and not c.read_only  # the read-only check really cannot see it
    _refused_checkout(c, doc["id"])
    status = AppStatus(c)
    status.check()  # F5
    assert c.read_only, "F5 made the GUI writable although the cause of the refusal is still there"
    assert status.blocked and "READ-ONLY" in status.health_text and "clear-orphan" in status.health_text
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    assert panel.history and not panel.can_checkout  # browsing works, changes stay off
    status.check()  # and again: still blocked, still no change written
    assert c.read_only and status.blocked


def test_f5_clears_the_block_once_the_administrator_has_resolved_it(started):
    from revisionworx.cli import main
    c, home, _project, doc = started
    orphan = _orphan(home)
    _refused_checkout(c, doc["id"])
    assert main(["-w", str(home), "lock", "clear-orphan", orphan.name,
                 "--reason", "test: writer confirmed stopped"]) in (0, None)
    h = c.health()  # F5
    assert not h["blocked"] and not c.read_only
    c.checkout(doc["id"])  # the GUI is usable again without reopening the workspace
    assert c.document_detail(doc["id"])["checkout"] is not None


def test_recheck_writes_no_journal_entry(started):
    c, home, _project, doc = started
    _orphan(home)
    _refused_checkout(c, doc["id"])
    journal = home / ".revisionworx" / "journal"
    before = sorted(p.name for p in journal.iterdir())
    c.health()
    c.health()
    assert sorted(p.name for p in journal.iterdir()) == before


def test_recheck_is_not_attempted_while_a_job_holds_the_workspace(started, monkeypatch):
    c, home, _project, doc = started
    _orphan(home)
    _refused_checkout(c, doc["id"])
    (home / ".revisionworx" / "locks" / ".releasing-deadbeef").unlink()  # cause gone...
    opened = []
    real_open = Workspace.open
    monkeypatch.setattr(Workspace, "open", classmethod(
        lambda cls, loc, writable=False: (opened.append(writable), real_open(loc, writable))[1]))
    assert c._busy.acquire(blocking=False)  # ...but a background job holds the slot
    try:
        h = c.health()
    finally:
        c._busy.release()
    assert h["blocked"] and c.read_only and True not in opened
    assert "finish" in h["message"].lower()
    assert not c.health()["blocked"] and not c.read_only  # once idle, the re-check runs and clears


def test_recheck_that_cannot_get_the_lock_stays_blocked(started, monkeypatch):
    c, home, _project, doc = started
    _orphan(home)
    _refused_checkout(c, doc["id"])
    (home / ".revisionworx" / "locks" / ".releasing-deadbeef").unlink()

    def locked(meta):
        raise WorkspaceLockedError("held by another session")
    monkeypatch.setattr(Workspace, "_acquire_writer_lock", staticmethod(locked))
    h = c.health()
    assert h["blocked"] and c.read_only  # not proven gone: fail closed
    assert "could not be re-checked" in h["message"]


def test_block_found_by_the_read_only_check_is_still_cleared_by_it(started):
    """Unchanged behaviour: a journal problem verify() can see is cleared as soon as it is gone."""
    c, home, _project, _doc = started
    entry = sorted((home / ".revisionworx" / "journal").glob("*.json"))[-1]
    original = entry.read_bytes()
    data = json.loads(original)
    data["op"] += "-tampered"
    entry.write_text(json.dumps(data))
    assert c.health()["blocked"] and c.read_only
    entry.write_bytes(original)
    assert not c.health()["blocked"] and not c.read_only


def test_reopening_or_switching_workspace_forgets_the_old_block(started, tmp_path):
    c, home, _project, doc = started
    _orphan(home)
    _refused_checkout(c, doc["id"])
    other = make_ctl(tmp_path / "x")
    other.quick_start(tmp_path / "Other", "Other")
    c.open_workspace(tmp_path / "Other")
    assert not c.read_only and c.health()["ok"]


# ---------------------------------------------------------------------------- 2. A8 pending entries
def _rewind_last_apply(home: Path, record: Path) -> None:
    """Make the last journal entry committed-but-not-applied, as a crash between commit and apply
    leaves it: put back the record as it was and step ``applied_seq`` back by one."""
    meta = home / ".revisionworx"
    state = json.loads((meta / "state.json").read_text())
    state["applied_seq"] -= 1
    (meta / "state.json").write_text(json.dumps(state))


def test_committed_but_unapplied_change_is_pending_not_an_inconsistency(started):
    c, home, project, _doc = started
    rec = home / ".revisionworx" / "records" / "project" / f"{project['id']}.json"
    before = rec.read_bytes()
    c.update_project(project["id"], project["_rev"] if "_rev" in project else None, customer="ACME")
    rec.write_bytes(before)
    _rewind_last_apply(home, rec)
    h = c.health()
    assert h["ok"] and not h["blocked"] and not c.read_only
    assert h["problems"] == [] and h["pending"] == 1
    assert "inconsisten" not in h["message"] and "waiting to be applied" in h["message"]
    assert rec.read_bytes() == before  # health never applies or repairs anything


def test_real_inconsistency_is_still_reported_next_to_pending_entries(started):
    c, home, project, doc = started
    meta = home / ".revisionworx"
    rec = meta / "records" / "project" / f"{project['id']}.json"
    before = rec.read_bytes()
    c.update_project(project["id"], project.get("_rev"), customer="ACME")
    rec.write_bytes(before)
    _rewind_last_apply(home, rec)
    (meta / "records" / "document" / f"{doc['id']}.json").unlink()  # not explained by the pending entry
    h = c.health()
    assert h["pending"] == 1 and len(h["problems"]) == 1 and h["problems"][0]["id"] == doc["id"]
    assert "inconsistency" in h["message"] and "waiting to be applied" in h["message"]


def test_pending_change_is_completed_by_the_next_change_and_the_notice_goes(started):
    c, home, project, doc = started
    rec = home / ".revisionworx" / "records" / "project" / f"{project['id']}.json"
    before = rec.read_bytes()
    c.update_project(project["id"], project.get("_rev"), customer="ACME")
    rec.write_bytes(before)
    _rewind_last_apply(home, rec)
    assert c.health()["pending"] == 1
    c.checkout(doc["id"])  # any change rolls the journal forward first (core recover())
    h = c.health()
    assert h["pending"] == 0 and h["message"] == "" and c.project(project["id"])["customer"] == "ACME"


def test_administrator_clear_is_not_shown_as_an_inconsistency(started):
    """The scenario A8 was filed for: after 'lock clear-orphan' the read-only check said
    'inconsistency' until the next write."""
    from revisionworx.cli import main
    c, home, _project, doc = started
    orphan = _orphan(home)
    _refused_checkout(c, doc["id"])
    assert main(["-w", str(home), "lock", "clear-orphan", orphan.name,
                 "--reason", "test: writer confirmed stopped"]) in (0, None)
    status = AppStatus(c)
    status.check()
    assert not status.blocked and "inconsisten" not in status.health_text.lower()
