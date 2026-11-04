# SPDX-License-Identifier: GPL-3.0-or-later
"""A change that committed must never be reported as failed because releasing the writer lock failed.

``Workspace.close()`` can raise after the commit point: on Windows the lock-file rename is retried and
then gives up with WorkspaceError while another process (antivirus, backup, indexer) holds the file.
The GUI showed that as the operation failing although the change was saved, inviting a retry of an
import, checkout or check-in that had already happened. A body error is also no longer replaced by a
release error, so CommittedNotApplied ("saved") cannot turn into a failure that way either.

Real core on a real temporary filesystem; only the lock release is made to fail.
"""
from pathlib import Path

import pytest

from revisionworx import documents
from revisionworx.gui.controller import Controller, SavedWithWarning, describe_error
from revisionworx.workspace import CommittedNotApplied, ConflictError, Workspace, WorkspaceError


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
    return c, home, project, src


@pytest.fixture
def release_fails(monkeypatch):
    """Release really happens (so later steps can write), then reports failure, as the Windows path does
    when its final attempt is ambiguous. ``on`` switches it."""
    real = Workspace._unlink_own_lock
    state = {"on": False, "calls": 0}

    def flaky(self, lock):
        real(self, lock)
        if state["on"]:
            state["calls"] += 1
            raise WorkspaceError("could not release the writer lock after 50 attempts (WinError 32)")
    monkeypatch.setattr(Workspace, "_unlink_own_lock", flaky)
    return state


def _drain(c: Controller):
    assert c.wait_idle(30)


def test_checkout_saved_but_release_failed_is_saved_with_warning(started, release_fails):
    c, _home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    release_fails["on"] = True
    with pytest.raises(SavedWithWarning) as ei:
        c.checkout(doc["id"])
    assert "writer lock" in ei.value.warning and "saved" in ei.value.warning.lower()
    release_fails["on"] = False
    detail = c.document_detail(doc["id"])
    assert detail["checkout"] is not None and detail["mine"]  # really checked out, by this session


def test_import_job_saved_but_release_failed_is_not_reported_as_failed(started, release_fails):
    c, home, project, src = started
    results = []
    release_fails["on"] = True
    assert c.start_import(project["id"], src, "", on_done=results.append)
    _drain(c)
    release_fails["on"] = False
    (res,) = results
    assert res.saved and res.warning and "writer lock" in res.warning
    assert [d["rel_path"] for d in c.documents(project["id"])] == ["bracket.ipt"]
    assert (home / "Projects" / "P-100 Bracket" / "bracket.ipt").read_bytes() == b"inventor part v1"


def test_checkin_saved_but_release_failed_forgets_the_token(started, release_fails):
    c, _home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    c.checkout(doc["id"])
    c.working_file(doc["id"]).write_bytes(b"v2")
    results = []
    release_fails["on"] = True
    assert c.start_checkin(doc["id"], "second", on_done=results.append)
    _drain(c)
    release_fails["on"] = False
    (res,) = results
    assert res.saved and res.warning and "Failed" not in res.warning
    d = c.document_detail(doc["id"])
    assert d["checkout"] is None and len(c.document_detail(doc["id"])["history"]) == 2
    assert c.tokens.get(c.workspace_id, doc["id"]) is None


def test_refused_change_keeps_its_own_error_when_release_also_fails(started, release_fails):
    c, _home, project, _src = started
    release_fails["on"] = True
    with pytest.raises(ConflictError):  # nothing was written: still a plain refusal, not "saved"
        c.update_project(project["id"], 999, customer="ACME")
    assert release_fails["calls"] == 1


def test_committed_not_applied_survives_a_release_failure(started, release_fails, monkeypatch):
    c, _home, project, src = started
    doc, _ = c.import_document(project["id"], src, "")
    c.checkout(doc["id"])
    c.working_file(doc["id"]).write_bytes(b"v2")
    real = documents.checkin

    def committed_then_fail(ws, *a, **k):
        rev = real(ws, *a, **k)
        raise CommittedNotApplied(ws.head_seq(), k.get("txid") or "0" * 32, OSError("disk"))
    monkeypatch.setattr(documents, "checkin", committed_then_fail)
    release_fails["on"] = True
    with pytest.raises(SavedWithWarning):
        c.checkin_now(doc["id"], "second")


def test_release_failure_without_a_commit_is_still_an_error(started, release_fails):
    c, _home, _project, _src = started
    release_fails["on"] = True
    with pytest.raises(WorkspaceError) as ei:
        with c._write():
            pass  # opened, nothing committed
    assert not isinstance(ei.value, SavedWithWarning)
    assert "writer lock" in describe_error(ei.value)
