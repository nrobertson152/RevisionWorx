# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent Phase 1 data integrity and fault injection regressions."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
from revisionworx import atomicio, projects
from revisionworx.workspace import RecordPut, Workspace, WorkspaceCorruptError, WorkspaceLockedError
from .fixtures import STRUCTURES, fingerprint, populate

@pytest.mark.parametrize("layout", sorted(STRUCTURES))
@pytest.mark.parametrize("mode", ["indexed", "managed"])
def test_adoption_never_changes_original_tree(tmp_path, layout, mode):
    root = tmp_path / "Engineering"
    fixture = populate(root, layout)
    with Workspace.create(tmp_path / "store", "QA") as ws:
        projects.add_root(ws, "Engineering", root)
        for folder in fixture["projects"]:
            preview = projects.preview_adoption(ws, folder)
            assert not preview["conflicts"]
            assert fingerprint(root) == fixture["before"]
            record = projects.adopt_project(ws, folder, name=folder.name, mode=mode)
            baseline = ws.read_blob(record["baseline"]["blob"])
            assert "No historical revisions are asserted." in baseline["note"]
            assert len(baseline["files"]) == 7
            assert all(len(item["sha256"]) == 64 for item in baseline["files"])
            assert record["rel_path"] == folder.relative_to(root).as_posix()
            assert fingerprint(root) == fixture["before"]
    assert fingerprint(root) == fixture["before"]

def test_disk_full_before_publication_is_not_commit(tmp_path, monkeypatch):
    store = tmp_path / "store"
    rid = "prj_" + "f" * 32
    with Workspace.create(store, "QA") as ws:
        before = ws.head_seq()
        real = atomicio.publish_exclusive
        def fail(path, data):
            if Path(path).parent.name == "journal":
                raise OSError(28, "synthetic ENOSPC")
            return real(path, data)
        with monkeypatch.context() as m:
            m.setattr(atomicio, "publish_exclusive", fail)
            with pytest.raises(OSError):
                ws.commit("qa.enospc", [RecordPut("project", rid, {"name": "never"})])
        assert ws.head_seq() == before
        assert ws.get("project", rid) is None
    with Workspace.open(store) as reader:
        assert reader.get("project", rid) is None

def test_journal_publish_then_apply_failure_is_recovered(tmp_path, monkeypatch):
    store = tmp_path / "store"
    rid = "prj_" + "a" * 32
    writer = Workspace.create(store, "QA")
    before = writer.head_seq()
    try:
        with monkeypatch.context() as m:
            def crash(_entry):
                raise OSError("simulated crash")
            m.setattr(writer, "_apply", crash)
            with pytest.raises(OSError):
                writer.commit("qa.crash", [RecordPut("project", rid, {"name": "durable"})])
        assert writer.head_seq() == before + 1
        with Workspace.open(store) as reader:
            assert reader.get("project", rid) == {"name": "durable"}
    finally:
        writer.close()
    with Workspace.open(store, writable=True) as recovered:
        assert recovered.get("project", rid) == {"name": "durable"}
        assert recovered.applied_seq() == recovered.head_seq()

def test_journal_gap_fails_closed(tmp_path):
    store = tmp_path / "store"
    with Workspace.create(store, "QA") as writer:
        writer.commit("qa.one", [RecordPut("settings", "workspace", {"a": 1})])
        writer.commit("qa.two", [RecordPut("settings", "workspace", {"a": 2})])
    journal = store / ".revisionworx" / "journal"
    missing = journal / "00000000000000000002.json"
    missing.unlink()
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(store, writable=True)
    assert not missing.exists()

def test_exclusive_writer_rejected(tmp_path):
    store = tmp_path / "store"
    with Workspace.create(store, "QA"):
        original = Workspace.read_writer_lock(store)
        with pytest.raises(WorkspaceLockedError):
            Workspace.open(store, writable=True)
        assert Workspace.read_writer_lock(store) == original

def test_unmanaged_archive_not_enumerated_during_open(tmp_path, monkeypatch):
    archive = tmp_path / "Engineering"
    populate(archive, "customer")
    store = tmp_path / "store"
    with Workspace.create(store, "QA") as ws:
        projects.add_root(ws, "Legacy", archive)
    real_scandir = os.scandir
    real_listdir = os.listdir
    def reject(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            resolved = Path(path).resolve()
            if resolved == archive or archive in resolved.parents:
                raise AssertionError("startup enumerated unmanaged folder")
    def checked_scandir(path):
        reject(path)
        return real_scandir(path)
    def checked_listdir(path):
        reject(path)
        return real_listdir(path)
    with monkeypatch.context() as m:
        m.setattr(os, "scandir", checked_scandir)
        m.setattr(os, "listdir", checked_listdir)
        with Workspace.open(store) as ws:
            assert ws.list("project") == {}
