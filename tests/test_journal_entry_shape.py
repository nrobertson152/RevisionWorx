# SPDX-License-Identifier: GPL-3.0-or-later
"""Journal entries are validated structurally before any field is used (P0, issue #29).

Complements the independent gates in tests/test_core_independent_journal_shape.py (PR #34): those
cover six field-type cases; these add top-level non-objects, every dereferenced field, the paths that
consume entries (replay, verify, transaction_outcome, writable open), legacy compatibility, and that
inspection writes nothing. Each corrupt case fails on f01b37d with AttributeError/TypeError or a false
healthy report. Only disposable closed workspaces are altered.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from revisionworx.workspace import RecordPut, Workspace, WorkspaceCorruptError, entry_digest


def _make(tmp_path: Path) -> tuple[Path, Path, dict]:
    loc = tmp_path / "ws"
    with Workspace.create(loc, "shape"):
        pass
    journal = loc / ".revisionworx" / "journal" / "00000000000000000001.json"
    return loc, journal, json.loads(journal.read_text(encoding="utf-8"))


def _write(journal: Path, value, redigest: bool = False) -> None:
    if isinstance(value, dict):
        value.pop("digest", None)
        if redigest:
            value["digest"] = entry_digest(value)
    journal.write_text(json.dumps(value), encoding="utf-8")


def _tree(meta: Path) -> dict[str, str]:
    return {str(p.relative_to(meta)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(meta.rglob("*")) if p.is_file()}


TOP_LEVEL = [pytest.param(v, id=i) for v, i in
             [(None, "null"), ([], "empty-list"), ([1], "list"), ("x", "string"), (7, "number"), (True, "true")]]


def _set(path, value):
    def corrupt(e):
        target = e
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return corrupt


FIELDS = [
    pytest.param(_set(["seq"], 1.0), id="float-seq"),
    pytest.param(_set(["seq"], "1"), id="string-seq"),
    pytest.param(_set(["records"], None), id="null-records"),
    pytest.param(_set(["records"], {}), id="object-records"),
    pytest.param(_set(["records", 0], None), id="null-record"),
    pytest.param(_set(["records", 0, "kind"], ["root"]), id="list-kind"),
    pytest.param(_set(["records", 0, "kind"], "../x"), id="traversal-kind"),
    pytest.param(_set(["records", 0, "id"], 5), id="number-id"),
    pytest.param(_set(["records", 0, "id"], "../../evil"), id="traversal-id"),
    pytest.param(_set(["records", 0, "data"], None), id="null-put-data"),
    pytest.param(_set(["records", 0, "delete"], 1), id="integer-delete"),
    pytest.param(_set(["records", 0, "expect_rev"], "0"), id="string-expect-rev"),
    pytest.param(_set(["blobs"], "a/b"), id="string-blobs"),
    pytest.param(_set(["blobs"], ["no-slash"]), id="blob-without-kind"),
    pytest.param(_set(["blobs"], ["../x/blb_" + "0" * 32]), id="traversal-blob"),
    pytest.param(_set(["checks"], [None]), id="null-check"),
    pytest.param(_set(["txid"], 12), id="number-txid"),
    pytest.param(_set(["writer_token"], {"t": 1}), id="object-writer-token"),
    pytest.param(_set(["op"], ["x"]), id="list-op"),
    pytest.param(_set(["request"], "v1"), id="string-request"),
]


def _assert_rejected_everywhere(loc: Path, journal: Path) -> None:
    before = _tree(loc / ".revisionworx")
    with Workspace.open(loc, writable=False) as ws:
        with pytest.raises(WorkspaceCorruptError):
            ws.read_entry(1)
        report = ws.health()
        assert report["ok"] is False and isinstance(report["reason"], str) and report["reason"]
        with pytest.raises(WorkspaceCorruptError):
            ws.verify()
        with pytest.raises(WorkspaceCorruptError):
            ws.transaction_outcome("0" * 32)
        with pytest.raises(WorkspaceCorruptError):
            list(ws.entries_after(0))
    assert _tree(loc / ".revisionworx") == before, "read-only inspection modified authoritative files"


@pytest.mark.parametrize("value", TOP_LEVEL)
def test_top_level_non_object_is_corrupt_not_a_crash(tmp_path, value):
    loc, journal, _ = _make(tmp_path)
    _write(journal, value)
    _assert_rejected_everywhere(loc, journal)


@pytest.mark.parametrize("corrupt", FIELDS)
@pytest.mark.parametrize("redigest", [False, True], ids=["digestless", "valid-digest"])
def test_malformed_field_is_corrupt_not_a_crash(tmp_path, corrupt, redigest):
    # valid-digest: a consistent digest must not vouch for an invalid structure.
    loc, journal, entry = _make(tmp_path)
    corrupt(entry)
    _write(journal, entry, redigest=redigest)
    _assert_rejected_everywhere(loc, journal)


def test_non_string_digest_is_corrupt(tmp_path):
    loc, journal, entry = _make(tmp_path)
    entry["digest"] = 5
    journal.write_text(json.dumps(entry), encoding="utf-8")
    _assert_rejected_everywhere(loc, journal)


def test_malformed_revocation_holder_is_corrupt(tmp_path):
    loc, journal, entry = _make(tmp_path)
    entry["op"] = "workspace.lock.break"
    entry["records"][0]["data"]["previous_holder"] = ["not", "an", "object"]
    _write(journal, entry, redigest=True)
    _assert_rejected_everywhere(loc, journal)


def test_writable_open_refuses_a_malformed_unapplied_entry(tmp_path):
    loc, _journal, _ = _make(tmp_path)
    with Workspace.open(loc, writable=True) as ws:
        ws.commit("t", [RecordPut("settings", "extra", {"a": 1})])
    meta = loc / ".revisionworx"
    second = meta / "journal" / "00000000000000000002.json"
    entry = json.loads(second.read_text(encoding="utf-8"))
    entry["records"][0]["data"] = []
    _write(second, entry, redigest=True)
    (meta / "state.json").write_text(json.dumps({"applied_seq": 1}), encoding="utf-8")  # force replay
    with pytest.raises(WorkspaceCorruptError):
        with Workspace.open(loc, writable=True) as ws:
            ws.recover()
    assert not (meta / "records" / "settings" / "extra.json").exists() or \
        json.loads((meta / "records" / "settings" / "extra.json").read_text())["a"] == 1


def test_legacy_minimal_entry_is_still_accepted(tmp_path):
    """Pre-digest, pre-ADR-021 shape: only seq and records (put without txid/request/blobs/checks)."""
    loc, journal, entry = _make(tmp_path)
    legacy = {"seq": 1, "records": entry["records"]}
    _write(journal, legacy)
    with Workspace.open(loc, writable=False) as ws:
        assert ws.read_entry(1)["seq"] == 1
        assert ws.health()["ok"] is True
        assert [p["problem"] for p in ws.verify()["problems"]] == ["entry-without-digest"]


def test_legacy_null_optional_fields_are_accepted(tmp_path):
    loc, journal, entry = _make(tmp_path)
    for key in ("txid", "writer_token", "request", "revokes"):
        entry[key] = None
    _write(journal, entry, redigest=True)
    with Workspace.open(loc, writable=False) as ws:
        assert ws.health()["ok"] is True


def test_real_workspace_with_deletes_blobs_and_breaks_stays_healthy(tmp_path):
    """Positive acceptance: every shape the product itself writes passes validation."""
    loc, _journal, _ = _make(tmp_path)
    with Workspace.open(loc, writable=True) as ws:
        ref = ws.write_blob("baseline", {"x": 1})
        ws.commit("put", [RecordPut("settings", "s1", {"v": 1}, expect_rev=0)], blob_refs=[ref],
                  txid="a" * 32)
        from revisionworx.workspace import RecordDelete
        ws.commit("del", [RecordDelete("settings", "s1")])
        token = Workspace.read_writer_lock(loc)["token"]
    Workspace.break_writer_lock(loc, token, "test") if Workspace.read_writer_lock(loc) else None
    with Workspace.open(loc, writable=False) as ws:
        for s in ws.journal_seqs():
            ws.read_entry(s)
        assert ws.health()["ok"] is True
        assert ws.transaction_outcome("a" * 32)["state"] == "committed"
