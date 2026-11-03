# SPDX-License-Identifier: GPL-3.0-or-later
"""Alpha acceptance workflows (Issue #13), exercised headless through the GUI controller and
view-models against the REAL core and a real temporary filesystem. No mocks, no fake persistence.

Workflows covered: quick-start a local project, import a document, check out / check in, view
history, reopen after "restart" (a fresh Controller), and actionable error text for missing files,
permission denial, conflicting edits and recovery-blocked state.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

from revisionworx import documents
from revisionworx.gui import controller as ctl_mod
from revisionworx.gui.controller import ALPHA_NOTICE, Controller, describe_error
from revisionworx.gui.viewmodels import (AppStatus, CheckinForm, DocumentListModel, DocumentPanel,
                                         ImportFlow, QuickStartForm)
from revisionworx.workspace import CommittedNotApplied, Workspace, WorkspaceCorruptError


def make_ctl(tmp_path: Path) -> Controller:
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")


def pump(c: Controller, until, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        c.drain()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


@pytest.fixture
def started(tmp_path):
    """A quick-started local project plus an outside file to import."""
    c = make_ctl(tmp_path)
    home = tmp_path / "My Projects"
    project = c.quick_start(home, "Bracket", number="P-100")
    src = tmp_path / "outside" / "bracket.ipt"
    src.parent.mkdir()
    src.write_bytes(b"inventor part v1")
    return c, home, project, src


# ---------------------------------------------------------------------------- workflow 1
def test_quick_start_creates_workspace_root_and_managed_project(tmp_path):
    c = make_ctl(tmp_path)
    home = tmp_path / "My Projects"
    p = c.quick_start(home, "Bracket", number="P-100", customer="ACME")
    assert (home / ".revisionworx" / "workspace.json").is_file()
    assert p["mode"] == "managed" and p["status"] == "active"
    assert (home / "Projects" / "P-100 Bracket").is_dir()
    assert c.location == home and c.recent_workspaces()[0] == str(home)
    assert c.health()["ok"]


def test_quick_start_reuses_existing_workspace_and_root(tmp_path):
    c = make_ctl(tmp_path)
    home = tmp_path / "ws"
    c.quick_start(home, "One")
    ws_id = c.workspace_id
    c.quick_start(home, "Two")
    assert c.workspace_id == ws_id
    assert len(c.roots()) == 1
    assert {r["name"] for r in c.project_page()["rows"]} == {"One", "Two"}


def test_quick_start_refuses_existing_folder_and_bad_names(tmp_path):
    c = make_ctl(tmp_path)
    home = tmp_path / "ws"
    c.quick_start(home, "One")
    with pytest.raises(Exception, match="already registered"):
        c.quick_start(home, "One")
    (home / "Projects" / "Unregistered").mkdir()
    with pytest.raises(Exception, match="already exists"):
        c.quick_start(home, "Unregistered")  # never merges into an existing folder
    with pytest.raises(ValueError):
        c.quick_start(home, "bad:name")
    f = QuickStartForm(folder=str(home), name="CON")
    assert not f.validate() and "name" in f.errors
    f = QuickStartForm(folder="", name="")
    assert not f.validate() and set(f.errors) == {"folder", "name"}
    f = QuickStartForm(folder=str(home), name="Gear", number="7")
    assert f.project_path_preview.endswith(os.path.join("Projects", "7 Gear"))
    assert f.submit(c)["name"] == "Gear"


# ---------------------------------------------------------------------------- workflow 2
def test_import_copies_registers_and_leaves_source_untouched(started):
    c, home, project, src = started
    before = (src.read_bytes(), os.stat(src).st_mtime_ns)
    doc, rev = c.import_document(project["id"], src, "", number="D-1", description="First issue")
    dest = home / "Projects" / "P-100 Bracket" / "bracket.ipt"
    assert dest.read_bytes() == b"inventor part v1"
    assert (src.read_bytes(), os.stat(src).st_mtime_ns) == before
    assert rev["label"] and rev["description"] == "First issue" and doc["number"] == "D-1"
    rows = c.documents(project["id"])
    assert [r["rel_path"] for r in rows] == ["bracket.ipt"]


def test_import_into_template_subfolder(started):
    c, home, project, src = started
    subs = c.project_subfolders(project["id"])
    assert "" in subs and len(subs) > 1  # template created folders
    sub = subs[1]
    doc, _ = c.import_document(project["id"], src, sub)
    assert doc["rel_path"] == f"{sub}/bracket.ipt"


def test_import_never_overwrites_and_cleans_up_on_validation_failure(started):
    c, home, project, src = started
    c.import_document(project["id"], src, "")
    with pytest.raises(ctl_mod.ImportError_, match="already exists"):
        c.import_document(project["id"], src, "")
    assert (home / "Projects" / "P-100 Bracket" / "bracket.ipt").read_bytes() == b"inventor part v1"
    # Duplicate document number is refused by core validation BEFORE commit: our copy is removed.
    other = src.with_name("other.ipt")
    other.write_bytes(b"x")
    c.import_document(project["id"], src.with_name("bracket.ipt"), c.project_subfolders(project["id"])[1],
                      number="N-1")
    with pytest.raises(documents.DocumentError, match="already used"):
        c.import_document(project["id"], other, "", number="N-1")
    assert not (home / "Projects" / "P-100 Bracket" / "other.ipt").exists()


def test_import_keeps_copy_when_commit_outcome_is_committed_not_applied(started, monkeypatch):
    """An ambiguous commit failure must never delete a file that may already be registered."""
    c, home, project, src = started

    def boom(*a, **k):
        raise CommittedNotApplied(1, "tx", RuntimeError("simulated"))
    monkeypatch.setattr(documents, "register_document", boom)
    with pytest.raises(CommittedNotApplied):
        c.import_document(project["id"], src, "")
    assert (home / "Projects" / "P-100 Bracket" / "bracket.ipt").exists()


def test_import_refuses_missing_source_symlink_and_indexed_project(started, tmp_path):
    c, home, project, src = started
    with pytest.raises(ctl_mod.ImportError_, match="does not exist"):
        c.import_document(project["id"], tmp_path / "nope.ipt", "")
    if hasattr(os, "symlink"):
        link = tmp_path / "link.ipt"
        try:
            os.symlink(src, link)
        except OSError:
            pass
        else:
            with pytest.raises(ctl_mod.ImportError_, match="not a regular file"):
                c.import_document(project["id"], link, "")
    with pytest.raises(Exception):
        c.import_document(project["id"], src, "../escape")


def test_import_flow_viewmodel(started):
    c, home, project, src = started
    flow = ImportFlow(c, project["id"])
    assert not flow.can_import
    assert not flow.start() and "source" in flow.errors
    flow.set_source(str(src))
    assert flow.title == "bracket" and flow.destination_preview == "bracket.ipt"
    assert flow.start()
    pump(c, lambda: flow.state in ("done", "error"))
    assert flow.state == "done", flow.message
    assert "revision" in flow.message


def test_document_list_notice_for_indexed_and_empty_projects(started, tmp_path):
    c, home, project, src = started
    vm = DocumentListModel(c)
    vm.load(project["id"])
    assert vm.can_add and "Import" in vm.notice
    legacy = home / "Projects" / "Legacy"
    legacy.mkdir()
    (legacy / "a.ipt").write_bytes(b"a")
    done = {}
    c.start_adopt(legacy, "Legacy", "indexed", on_done=lambda r: done.setdefault("r", r))
    pump(c, lambda: "r" in done)
    vm.load(done["r"].value["id"])
    assert not vm.can_add and "indexed" in vm.notice


# ---------------------------------------------------------------------------- workflows 3 + 4
def test_checkout_checkin_history_and_restart(started, tmp_path):
    c, home, project, src = started
    doc, rev1 = c.import_document(project["id"], src, "")
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    assert panel.can_checkout and not panel.can_checkin
    assert Path(panel.working_path).name == "bracket.ipt" and panel.can_open_file
    panel.checkout()
    assert panel.can_checkin and "you" in panel.status_text
    Path(panel.working_path).write_bytes(b"inventor part v2")

    form = CheckinForm(description="  ")
    assert not form.validate() and "description" in form.errors
    form = CheckinForm(description="ok", label="")
    assert not form.validate()  # too short to be meaningful
    form = CheckinForm(description="Widened mounting slot", label="bad label!")
    assert not form.validate() and "label" in form.errors
    form = CheckinForm(description="Widened mounting slot to 8 mm")
    assert form.validate()
    assert panel.start_checkin(form.description)
    pump(c, lambda: not panel.message.startswith("Checking in"))
    assert panel.message.startswith("Checked in revision"), panel.message
    assert [r["description"] for r in panel.history] == ["Widened mounting slot to 8 mm", "Initial revision"]
    assert "Widened mounting slot" in panel.revision_text(0)
    assert dict(panel.summary_fields)["Revisions"] == "2"

    # "Restart": a brand-new controller with the same per-user dirs reopens the last workspace.
    c2 = make_ctl(tmp_path)
    assert c2.recent_workspaces()[0] == str(home)
    c2.open_workspace(Path(c2.recent_workspaces()[0]))
    detail = c2.document_detail(doc["id"])
    assert [r["label"] for r in detail["history"]] == [rev1["label"], panel.history[0]["label"]]
    assert detail["checkout"] is None


def test_checkout_survives_restart_and_can_be_checked_in(started, tmp_path):
    c, home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    c.checkout(doc["id"])
    wf = c.working_file(doc["id"])
    wf.write_bytes(b"edited after restart")
    c2 = make_ctl(tmp_path)
    c2.open_workspace(home)
    panel = DocumentPanel(c2)
    panel.load(doc["id"])
    assert panel.can_checkin  # token persisted in per-user state
    assert panel.start_checkin("Edit made across an application restart")
    pump(c2, lambda: not panel.message.startswith("Checking in"))
    assert panel.detail["document"]["rev_count"] == 2


# ---------------------------------------------------------------------------- workflow 5: errors
def test_missing_working_file_is_reported_actionably(started):
    c, home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    c.checkout(doc["id"])
    c.working_file(doc["id"]).unlink()
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    assert panel.file_missing and panel.severity == "error" and not panel.can_open_file
    assert "missing" in panel.status_text.lower()
    assert panel.start_checkin("Should fail: file is gone")
    pump(c, lambda: not panel.message.startswith("Checking in"))
    assert "Missing file" in panel.message and "Discard edits" in panel.message


def test_conflicting_edit_and_foreign_checkout_messages(started, tmp_path):
    c, home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    c.checkout(doc["id"])
    other = Controller(cfg_dir=tmp_path / "cfg2", cache_dir=tmp_path / "cache2", state_dir=tmp_path / "state2")
    other.open_workspace(home)
    with pytest.raises(documents.CheckoutConflict) as ei:
        other.checkout(doc["id"])
    assert "check it in" in describe_error(ei.value)
    panel = DocumentPanel(other)
    panel.load(doc["id"])
    assert not panel.can_checkin and not panel.can_checkout and panel.severity == "warning"
    msg = describe_error(documents.StaleCheckout("a newer revision was committed"))
    assert "Conflicting edit" in msg and "NOT changed" in msg
    assert "Only the session" in describe_error(documents.TokenMismatch("belongs to another session"))


def test_externally_modified_file_is_flagged(started):
    c, home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    c.working_file(doc["id"]).write_bytes(b"edited without checkout!")
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    assert panel.severity == "warning" and "WITHOUT a checkout" in panel.status_text


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permission bits are not enforced on Windows or for root")
def test_permission_denied_is_actionable(started, tmp_path):
    c, home, project, src = started
    folder = home / "Projects" / "P-100 Bracket"
    os.chmod(folder, 0o555)
    try:
        with pytest.raises(PermissionError) as ei:
            c.import_document(project["id"], src, "")
    finally:
        os.chmod(folder, 0o755)
    assert "Permission denied" in describe_error(ei.value)


def test_permission_error_text():
    assert "Permission denied" in describe_error(PermissionError(13, "denied", "/x/y"))


def test_recovery_blocked_workspace_is_reported_and_refuses_writes(started):
    c, home, project, src = started
    journal = home / ".revisionworx" / "journal"
    entry = sorted(journal.glob("*.json"))[-1]
    data = json.loads(entry.read_text())
    data["op"] = data["op"] + "-tampered"
    entry.write_text(json.dumps(data))
    status = AppStatus(c)
    status.check()
    assert status.blocked and "RECOVERY BLOCKED" in status.health_text
    with pytest.raises(WorkspaceCorruptError) as ei:
        c.import_document(project["id"], src, "")
    assert "Nothing was written" in describe_error(ei.value)
    assert not (home / "Projects" / "P-100 Bracket" / "bracket.ipt").exists()


def test_pending_recovery_is_reported_without_repairing(started):
    c, home, project, src = started
    state = home / ".revisionworx" / "state.json"
    applied = json.loads(state.read_text())["applied_seq"]
    with Workspace.open(home) as ws:
        head = ws.head_seq()
    assert head == applied
    state.write_text(json.dumps({"applied_seq": applied - 1}))
    h = c.health()
    assert h["ok"] and h["pending_recovery"] == 1 and "automatically" in h["message"]
    assert json.loads(state.read_text())["applied_seq"] == applied - 1  # health check is read-only


# ---------------------------------------------------------------------------- workflow 6
def test_alpha_notice_is_explicit():
    s = AppStatus.__init__.__code__  # noqa: F841 - ensure class importable
    assert "single user" in ALPHA_NOTICE and "local" in ALPHA_NOTICE
    assert "irreplaceable production data" in ALPHA_NOTICE


# ---------------------------------------------------------------------------- checkpoint 2
def test_document_list_shows_working_file_state(started):
    c, home, project, src = started
    a, _ = c.import_document(project["id"], src, "")
    other = src.with_name("b.ipt")
    other.write_bytes(b"b")
    b, _ = c.import_document(project["id"], other, "")
    vm = DocumentListModel(c)
    vm.load(project["id"])
    col = [col.key for col in DocumentListModel.COLUMNS].index("file_state")
    assert [vm.cell(i, col) for i in range(2)] == ["OK", "OK"] and vm.problem_count == 0
    c.working_file(a["id"]).unlink()
    c.working_file(b["id"]).write_bytes(b"changed outside")
    vm.refresh()
    states = {r["rel_path"]: r["file_state"] for r in vm.rows}
    assert states == {"bracket.ipt": "missing", "b.ipt": "modified-externally"}
    assert {vm.row_severity(i) for i in range(2)} == {"error", "warning"}
    assert vm.problem_count == 2 and "need attention" in vm.notice


def _two_revisions(c, project, src):
    doc, _ = c.import_document(project["id"], src, "")
    c.checkout(doc["id"])
    c.working_file(doc["id"]).write_bytes(b"inventor part v2 - longer")
    done = {}
    c.start_checkin(doc["id"], "Second revision", on_done=lambda r: done.setdefault("r", r))
    pump(c, lambda: "r" in done)
    assert done["r"].error is None, done["r"].error
    return doc


def test_restore_earlier_revision_records_new_revision_without_rewriting_history(started):
    c, home, project, src = started
    doc = _two_revisions(c, project, src)
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    assert not panel.can_restore(1)  # not checked out
    with pytest.raises(ValueError):
        panel.restore(1)
    panel.checkout()
    assert not panel.can_restore(0) and panel.can_restore(1)  # only OLDER revisions
    old_history = [dict(r) for r in panel.history]
    panel.restore(1)
    assert c.working_file(doc["id"]).read_bytes() == b"inventor part v1"
    assert "not rewritten" in panel.message
    assert [r["id"] for r in panel.history] == [r["id"] for r in old_history]  # unchanged history
    assert panel.start_checkin("Back to v1 geometry")
    pump(c, lambda: not panel.message.startswith("Checking in"))
    newest = panel.history[0]
    assert newest["number"] == 3 and newest["restored_from"] == old_history[1]["id"]
    assert "Restored from" in panel.revision_text(0)


def test_compare_with_previous_revision(started):
    c, home, project, src = started
    doc = _two_revisions(c, project, src)
    panel = DocumentPanel(c)
    panel.load(doc["id"])
    assert panel.can_compare(0) and not panel.can_compare(1)
    text = panel.compare_text(0)
    assert "different" in text and "Second revision" in text and "not diffed" in text
