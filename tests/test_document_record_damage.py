# SPDX-License-Identifier: GPL-3.0-or-later
"""Malformed document, revision, checkout, project and root records, and broken revision chains.

A journal entry's digest says it was not altered by accident, not that its record has the right shape, so an entry can be
well formed and still hold nonsense. Each field of each record is replaced or removed, with a correct digest and no record
files to fall back on. Every operation must then either work or refuse with a controlled error, and a refused operation
must leave the journal and the working file exactly as they were."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from revisionworx import atomicio, documents as D, projects as P
from revisionworx.index import LocalIndex
from revisionworx.lifecycle import LifecycleError
from revisionworx.paths import PathValidationError
from revisionworx.workspace import (RecordPut, Workspace, WorkspaceCorruptError, WorkspaceError, entry_digest,
                                    strip_rev)

REMOVED = object()
VALUES = [REMOVED, None, [], {}, 5, "x", True, "../x"]
WRITE_VALUES = [REMOVED, None, 5, "../x"]
CONTROLLED = (WorkspaceError, PathValidationError, LifecycleError)


@pytest.fixture
def setup(tmp_path):
    location = tmp_path / "w"
    ws = Workspace.create(location, "w")
    root = P.add_root(ws, "r", location)
    pid = P.create_project(ws, P.NewProject(name="p", number="1", root_id=root, mode="managed", rel_path="proj"))["id"]
    working = P.project_folder(ws, ws.get("project", pid)) / "a.ipt"
    working.write_bytes(b"one")
    doc, first = D.register_document(ws, pid, working, number="N1")
    co = D.checkout(ws, doc["id"])
    working.write_bytes(b"two")
    second = D.checkin(ws, doc["id"], co["token"], "second")
    D.transition(ws, second["id"], "in_review")
    co = D.checkout(ws, doc["id"])
    ws.close()
    return {"location": location, "working": working, "pid": pid, "doc": doc["id"], "first": first["id"],
            "second": second["id"], "token": co["token"], "dump": tmp_path / "dump"}


def meta(location: Path) -> Path:
    return Workspace.meta_dir_for(location)


def field_paths(value, prefix=()):
    if prefix:
        yield prefix
    if isinstance(value, dict):
        for key, child in value.items():
            yield from field_paths(child, prefix + (key,))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from field_paths(child, prefix + (i,))


def changed(entry: dict, index: int, path: tuple, new) -> dict:
    out = copy.deepcopy(entry)
    target = out["records"][index]["data"]
    for step in path[:-1]:
        target = target[step]
    if new is REMOVED:
        del target[path[-1]]
    else:
        target[path[-1]] = new
    out["digest"] = entry_digest(out)
    return out


def mutations(location: Path, values):
    """Each change to each field of each record, in the last entry to write that record. Record files are gone, so the
    journal is the only answer. The original entry is back when the generator finishes."""
    entries = {}
    for file in sorted((meta(location) / "journal").glob("*.json")):
        entry = json.loads(file.read_bytes())
        for i, item in enumerate(entry["records"]):
            if item["kind"] in ("root", "project", "document", "revision", "checkout") and not item.get("delete"):
                entries[item["kind"], item["id"]] = file, i
    shutil.rmtree(meta(location) / "records")
    (meta(location) / "records").mkdir()
    for (kind, _), (file, i) in entries.items():
        original = file.read_bytes()
        entry = json.loads(original)
        for path in field_paths(entry["records"][i]["data"]):
            for new in values:
                file.write_bytes(atomicio.dumps_json(changed(entry, i, path, new)))
                yield kind, path, new, file
        file.write_bytes(original)


def read_operations(ws: Workspace, s: dict) -> list:
    return [lambda: D.list_documents(ws), lambda: D.history(ws, s["doc"]), lambda: D.document_status(ws, s["doc"], verify_hash=True),
            lambda: D.verify_revisions(ws), lambda: D.audit_history(ws), lambda: D.drawing_register(ws, s["pid"]),
            lambda: D.export_revision(ws, s["second"], s["dump"], overwrite=True),
            lambda: D.read_revision_bytes(ws, s["second"], 100), lambda: D.compare_revisions(ws, s["first"], s["second"])]


def test_read_operations_work_or_refuse_for_every_malformed_record(setup):
    tried = 0
    for kind, path, new, _ in mutations(setup["location"], VALUES):
        with Workspace.open(setup["location"]) as ro:
            for operation in read_operations(ro, setup):
                try:
                    operation()
                except CONTROLLED:
                    pass
                except Exception as exc:
                    pytest.fail(f"{kind} {path} = {new!r}: {type(exc).__name__}: {exc}")
                tried += 1
    assert tried > 3000


def write_operations(s: dict) -> list:
    return [lambda ws: D.checkin(ws, s["doc"], s["token"], "x", allow_unchanged=True),
            lambda ws: D.cancel_checkout(ws, s["doc"], s["token"]),
            lambda ws: D.transition(ws, s["second"], "approved"),
            lambda ws: D.restore_to_working(ws, s["doc"], s["token"], s["first"]),
            lambda ws: D.update_document(ws, s["doc"], title="t"),
            lambda ws: D.gc_content(ws, apply=True, min_age_seconds=0)]


def test_a_refused_write_changes_nothing_for_every_malformed_record(setup, tmp_path):
    saved = tmp_path / "saved"
    shutil.copytree(meta(setup["location"]), saved)
    working = setup["working"].read_bytes()
    content = sorted(p.name for p in (meta(setup["location"]) / "content" / "sha256").rglob("*") if p.is_file())
    for kind, path, new, file in mutations(setup["location"], WRITE_VALUES):
        mutated = file.read_bytes()
        for operation in write_operations(setup):
            head = len(list((meta(setup["location"]) / "journal").glob("*.json")))
            try:
                with Workspace.open(setup["location"], writable=True) as ws:
                    operation(ws)
            except CONTROLLED:
                now = meta(setup["location"]) / "journal"
                assert len(list(now.glob("*.json"))) == head, f"{kind} {path} = {new!r}"
                assert setup["working"].read_bytes() == working, f"{kind} {path} = {new!r}"
                assert sorted(p.name for p in (meta(setup["location"]) / "content" / "sha256").rglob("*")
                              if p.is_file()) == content, f"{kind} {path} = {new!r}"
                continue
            except Exception as exc:
                pytest.fail(f"{kind} {path} = {new!r}: {type(exc).__name__}: {exc}")
            # It worked, so this mutation did not matter to it. Put everything back for the next operation.
            shutil.rmtree(meta(setup["location"]))
            shutil.copytree(saved, meta(setup["location"]))
            shutil.rmtree(meta(setup["location"]) / "records")
            (meta(setup["location"]) / "records").mkdir()
            file.write_bytes(mutated)
            setup["working"].write_bytes(working)


def test_index_reports_a_malformed_record_and_keeps_its_old_state(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for kind, path, new, _ in mutations(setup["location"], VALUES):
        with Workspace.open(setup["location"]) as ro:
            try:
                index = LocalIndex(ro, tmp_path / "index-cache")
            except CONTROLLED:
                continue
            except Exception as exc:
                pytest.fail(f"{kind} {path} = {new!r}: {type(exc).__name__}: {exc}")
            before = index.cursor_seq()
            try:
                index.rebuild()
            except CONTROLLED:
                assert index.cursor_seq() == before
            except Exception as exc:
                pytest.fail(f"{kind} {path} = {new!r}: {type(exc).__name__}: {exc}")
            finally:
                index.db.close()


# ---------------------------------------------------------------------- revision chains
def rewrite(location: Path, kind: str, rid: str, **fields) -> None:
    """Publish a change to a record, as a bug in other code or a damaged restore would leave it."""
    with Workspace.open(location, writable=True) as ws:
        ws.commit("test.damage", [RecordPut(kind, rid, {**strip_rev(ws.get(kind, rid)), **fields})])


CHAINS = {
    "out-of-order": ("revision", "second", {"number": 7}),
    "loop": ("revision", "first", {"previous_rev_id": "@second"}),
    "wrong-document": ("revision", "second", {"document_id": "doc_" + "0" * 32}),
    "missing-link": ("revision", "second", {"previous_rev_id": "rev_" + "0" * 32}),
    "short-count": ("document", "doc", {"rev_count": 1}),
    "dangling-latest": ("document", "doc", {"latest_rev_id": "rev_" + "0" * 32}),
}


@pytest.mark.parametrize("damage", CHAINS.values(), ids=CHAINS)
def test_broken_chain_is_refused_for_history_check_in_and_release(setup, damage):
    kind, who, fields = damage
    fields = {k: setup[v[1:]] if isinstance(v, str) and v.startswith("@") else v for k, v in fields.items()}
    rewrite(setup["location"], kind, setup[who], **fields)
    with Workspace.open(setup["location"], writable=True) as ws:
        head = ws.head_seq()
        with pytest.raises(WorkspaceError):
            D.history(ws, setup["doc"])
        with pytest.raises(WorkspaceError):
            D.checkin(ws, setup["doc"], setup["token"], "x", allow_unchanged=True)
        assert ws.head_seq() == head and ws.get("checkout", setup["doc"]) is not None


def test_a_revision_in_a_broken_chain_cannot_be_released(setup):
    with Workspace.open(setup["location"], writable=True) as ws:
        D.cancel_checkout(ws, setup["doc"], setup["token"])
        D.transition(ws, setup["second"], "approved")
    rewrite(setup["location"], "revision", setup["first"], number=9)
    with Workspace.open(setup["location"], writable=True) as ws:
        head = ws.head_seq()
        with pytest.raises(WorkspaceError, match="chain"):
            D.transition(ws, setup["second"], "released")
        assert ws.head_seq() == head and ws.get("document", setup["doc"])["released_rev_id"] is None


def test_gc_deletes_nothing_when_a_revision_cannot_be_read(setup):
    with Workspace.open(setup["location"], writable=True) as ws:
        ws.commit("test.damage", [RecordPut("revision", setup["first"], {"id": setup["first"]})])
        objects = sorted(p for p in (ws.meta_dir / "content" / "sha256").rglob("*") if p.is_file())
        with pytest.raises(WorkspaceCorruptError):
            D.gc_content(ws, apply=True, min_age_seconds=0)
        assert sorted(p for p in (ws.meta_dir / "content" / "sha256").rglob("*") if p.is_file()) == objects


def test_a_corrected_record_clears_the_refusal(setup):
    with Workspace.open(setup["location"], writable=True) as ws:
        good = strip_rev(ws.get("document", setup["doc"]))
        ws.commit("test.damage", [RecordPut("document", setup["doc"], {"id": setup["doc"]})])
        with pytest.raises(WorkspaceCorruptError, match="malformed"):
            D.history(ws, setup["doc"])
        ws.commit("test.correct", [RecordPut("document", setup["doc"], good)])
        assert len(D.history(ws, setup["doc"])) == 2
