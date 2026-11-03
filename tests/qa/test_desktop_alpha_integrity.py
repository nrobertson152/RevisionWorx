# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent GUI-controller data integrity and restart tests for draft PR #14.

Tests use the real Phase 2 journal and document store; Qt is not required.
The GUI branch is deliberately checked out separately in fixed-source CI.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import time

import pytest

if importlib.util.find_spec("revisionworx.documents") is None or importlib.util.find_spec("revisionworx.gui.controller") is None:
    pytest.skip("Desktop alpha and Phase 2 documents unavailable on this base", allow_module_level=True)

from revisionworx import documents
from revisionworx.gui.controller import Controller
from revisionworx.workspace import CommittedNotApplied, Workspace


def controller(tmp_path):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache",
                      state_dir=tmp_path / "tokens")


def start(tmp_path):
    c = controller(tmp_path)
    home = tmp_path / "workspace"
    project = c.quick_start(home, "Test bracket", number="Q-7")
    src = tmp_path / "external.stp"
    content = b"independent QA CAD fixture content"
    src.write_bytes(content)
    return c, home, project["id"], src, content


def results_until_done(c):
    for _ in range(400):
        events = c.drain()
        matches = [e for e in events if getattr(e, "name", None) in ("register", "checkin")]
        if matches:
            return matches[-1]
        time.sleep(.02)
    raise AssertionError("GUI background job did not complete")


def test_failed_copy_leaves_no_partial_working_file_or_journal_record(tmp_path, monkeypatch):
    c, home, pid, src, original = start(tmp_path)
    def partial_then_error(source, target, *args, **kwargs):
        target.write(b"partially copied bytes")
        raise OSError("injected copy read failure")
    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copyfileobj", partial_then_error)
        with pytest.raises(OSError, match="injected copy"):
            c.import_document(pid, src)
    with Workspace.open(home) as reader:
        assert reader.list("document") == {} and reader.list("revision") == {}
    with Workspace.open(home) as reader:
        project = reader.get("project", pid)
        from revisionworx.projects import project_folder
        dest = project_folder(reader, project) / src.name
    assert not dest.exists(), "incomplete copy must not appear as a legitimate document"
    assert src.read_bytes() == original


def test_interrupted_import_before_registration_is_resumable_without_overwrite(tmp_path, monkeypatch):
    c, home, pid, src, original = start(tmp_path)
    def stop_after_copy(*args, **kwargs):
        raise SystemExit("simulated interpreter termination between copy and register")
    with monkeypatch.context() as patch:
        patch.setattr(documents, "register_document", stop_after_copy)
        with pytest.raises(SystemExit, match="between copy and register"):
            c.import_document(pid, src)
    with Workspace.open(home) as reader:
        assert reader.list("document") == {}
        from revisionworx.projects import project_folder
        dest = project_folder(reader, reader.get("project", pid)) / src.name
        assert dest.read_bytes() == original
    # A new GUI controller cannot import over the already copied file; registering
    # the unchanged existing copy is the deliberately documented recovery route.
    fresh = controller(tmp_path)
    fresh.open_workspace(home)
    with pytest.raises(Exception, match="already exists"):
        fresh.import_document(pid, src)
    assert fresh.start_register(pid, dest)
    result = results_until_done(fresh)
    assert result.error is None, result.error
    with Workspace.open(home) as reader:
        docs = reader.list("document")
        assert len(docs) == 1 and len(reader.list("revision")) == 1
    assert dest.read_bytes() == original


def test_real_postjournal_import_failure_keeps_copy_and_restart_recovers(tmp_path, monkeypatch):
    c, home, pid, src, original = start(tmp_path)
    def fail_materialize(self, entry):
        raise OSError("injected disk failure after durable journal publication")
    with monkeypatch.context() as patch:
        patch.setattr(Workspace, "_apply", fail_materialize)
        with pytest.raises(CommittedNotApplied):
            c.import_document(pid, src)
    # Recover from the real journal entry on a NEW writable workspace instance.
    with Workspace.open(home, writable=True) as recovered:
        docs = recovered.list("document")
        revisions = recovered.list("revision")
        assert len(docs) == 1 and len(revisions) == 1
        doc = next(iter(docs.values()))
        from revisionworx import projects
        dest = projects.project_folder(recovered, recovered.get("project", pid)) / src.name
        assert dest.read_bytes() == original
        assert next(iter(revisions.values()))["sha256"] == hashlib.sha256(original).hexdigest()
    fresh = controller(tmp_path)
    fresh.open_workspace(home)
    assert len(fresh.documents(pid)) == 1


def test_real_postjournal_checkin_failure_survives_restart_once(tmp_path, monkeypatch):
    c, home, pid, src, original = start(tmp_path)
    doc, _rev = c.import_document(pid, src)
    did = doc["id"]
    c.checkout(did)
    working = c.working_file(did)
    updated = original + b" - checked in"
    working.write_bytes(updated)
    def fail_materialize(self, entry):
        raise OSError("injected disk failure during checkin apply")
    with monkeypatch.context() as patch:
        patch.setattr(Workspace, "_apply", fail_materialize)
        assert c.start_checkin(did, "Independent engineering revision 2")
        result = results_until_done(c)
        assert isinstance(result.error, CommittedNotApplied), result.error
    fresh = controller(tmp_path)
    fresh.open_workspace(home)
    with Workspace.open(home, writable=True) as recovered:
        doc_after = recovered.get("document", did)
        assert doc_after["rev_count"] == 2
        assert recovered.get("checkout", did) is None
        from revisionworx.documents import history
        revs = history(recovered, did)
        assert len(revs) == 2
        assert revs[-1]["sha256"] == hashlib.sha256(updated).hexdigest()
    detail = fresh.document_detail(did)
    assert len(detail["history"]) == 2 and detail["checkout"] is None
    assert working.read_bytes() == updated
