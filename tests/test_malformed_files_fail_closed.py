# SPDX-License-Identifier: GPL-3.0-or-later
"""Damaged files around the journal: every one ends in a controlled answer, never a raw AttributeError or RecursionError.

Lock files name who may write, so damage there fails closed and an administrator has a way out. State and record files are
caches of the journal, so damage there costs nothing: the journal answers. An orphan clear acts on names read from its
journal entry, and a digest is not a signature, so those names are checked before anything moves."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from revisionworx import atomicio, clientstate, doctor
from revisionworx.workspace import (MAX_RECORD_NESTING, LockLostError, RecordPut, Workspace, WorkspaceCorruptError,
                                    WorkspaceError, WorkspaceLockedError, entry_digest)

NOT_OBJECTS = {"list": b"[]", "null": b"null", "string": b'"x"', "number": b"5", "true": b"true"}
TOO_DEEP = b"[" * 200_000
DAMAGE = {**NOT_OBJECTS, "deep": TOO_DEEP, "not-utf8": b"\xff\xfe", "not-json": b"{", "empty": b""}
BAD_TOKENS = {"no-token": b'{"user": "u"}', "null-token": b'{"token": null}', "list-token": b'{"token": []}',
              "number-token": b'{"token": 5}', "empty-token": b'{"token": ""}'}
RID = "root_" + "a" * 32


@pytest.fixture
def loc(tmp_path):
    location = tmp_path / "w"
    with Workspace.create(location, "w") as ws:
        ws.commit("t", [RecordPut("root", RID, {"id": RID, "name": "r"}, expect_rev=0)])
    return location


def meta(loc: Path) -> Path:
    return Workspace.meta_dir_for(loc)


def head_files(loc: Path) -> list[str]:
    return sorted(p.name for p in (meta(loc) / "journal").glob("*.json"))


@pytest.mark.parametrize("damage", {**DAMAGE, **BAD_TOKENS}.values(), ids=[*DAMAGE, *BAD_TOKENS])
def test_damaged_writer_lock_refuses_writers_keeps_its_bytes_and_has_a_way_out(loc, damage):
    lock = meta(loc) / "locks" / "writer.lock"
    lock.write_bytes(damage)
    before = head_files(loc)
    with pytest.raises(WorkspaceLockedError):
        Workspace.open(loc, writable=True)
    assert lock.read_bytes() == damage and head_files(loc) == before
    shown = Workspace.read_writer_lock(loc)
    assert shown["unreadable"] is True and shown["token"] == "?"
    with Workspace.open(loc) as ro:              # reading is not refused
        assert ro.get("root", RID)["name"] == "r"

    # The documented way out: break, which makes it an orphan, then the audited clear.
    with pytest.raises(WorkspaceCorruptError, match="clear-orphan"):
        Workspace.break_writer_lock(loc, "?", "writer confirmed stopped")
    assert not (meta(loc) / "locks" / "writer.lock").exists()
    with pytest.raises(WorkspaceCorruptError, match="clear-orphan"):
        Workspace.open(loc, writable=True)
    orphan = next((meta(loc) / "locks").glob(".releasing-*"))
    assert orphan.read_bytes() == damage
    assert Workspace.clear_orphaned_release(loc, orphan.name, "writer confirmed stopped")["outcome"] == "completed"
    with Workspace.open(loc, writable=True) as ws:
        ws.commit("t", [RecordPut("root", "root_" + "b" * 32, {"id": "x"}, expect_rev=0)])


@pytest.mark.parametrize("damage", {**DAMAGE, **BAD_TOKENS}.values(), ids=[*DAMAGE, *BAD_TOKENS])
def test_damaged_release_file_fails_closed_and_gives_the_lock_back(loc, damage):
    orphan = meta(loc) / "locks" / ".releasing-abc"
    orphan.write_bytes(damage)
    before = head_files(loc)
    with pytest.raises(WorkspaceCorruptError, match="clear-orphan"):
        Workspace.open(loc, writable=True)
    assert orphan.read_bytes() == damage and head_files(loc) == before
    assert not (meta(loc) / "locks" / "writer.lock").exists()


@pytest.mark.parametrize("damage", {**DAMAGE, **BAD_TOKENS}.values(), ids=[*DAMAGE, *BAD_TOKENS])
def test_own_lock_damaged_mid_session_means_lock_lost_not_a_crash(loc, damage):
    ws = Workspace.open(loc, writable=True)
    (meta(loc) / "locks" / "writer.lock").write_bytes(damage)
    with pytest.raises(LockLostError):
        ws.commit("t", [RecordPut("root", "root_" + "b" * 32, {"id": "x"}, expect_rev=0)])
    ws.close()                                   # not ours as far as anyone can tell, so left in place, without raising
    assert (meta(loc) / "locks" / "writer.lock").read_bytes() == damage


def test_token_that_cannot_be_hashed_is_not_mistaken_for_revocable_by_doctor(loc):
    (meta(loc) / "locks" / "writer.lock").write_bytes(b'{"token": []}')
    (meta(loc) / "locks" / ".releasing-abc").write_bytes(b'{"token": {}}')
    (meta(loc) / "locks" / "writer.lock.broken-abc").write_bytes(b'{"token": []}')
    report = doctor.examine(loc)
    codes = {f["code"] for f in report["findings"]}
    assert {"L003", "L005"} <= codes and "X001" not in codes and "L007" not in codes


STATE_DAMAGE = {**DAMAGE, "inf": b'{"applied_seq": 1e999}', "negative": b'{"applied_seq": -5}', "true": b'{"applied_seq": true}',
                "float": b'{"applied_seq": 1.5}', "text": b'{"applied_seq": "2"}', "absent": b"{}"}


@pytest.mark.parametrize("damage", STATE_DAMAGE.values(), ids=STATE_DAMAGE)
def test_damaged_state_file_is_only_a_cache(loc, damage):
    head = len(head_files(loc))
    (meta(loc) / "state.json").write_bytes(damage)
    with Workspace.open(loc) as ro:
        assert ro.applied_seq() == 0 and ro.get("root", RID)["name"] == "r"
    assert doctor.examine(loc)["findings"] is not None
    with Workspace.open(loc, writable=True) as ws:
        assert ws.applied_seq() == head and ws.get("root", RID)["name"] == "r"


def test_folder_where_a_file_should_be_is_damage_not_a_raw_error(loc):
    journal = sorted((meta(loc) / "journal").glob("*.json"))[-1]
    saved = journal.read_bytes()
    journal.unlink()
    journal.mkdir()
    with Workspace.open(loc) as ro:
        assert ro.health()["ok"] is False
        with pytest.raises(WorkspaceCorruptError):
            ro.read_entry(int(journal.stem))
    journal.rmdir()
    journal.write_bytes(saved)

    record = meta(loc) / "records" / "root" / f"{RID}.json"
    record.unlink()
    record.mkdir()
    with Workspace.open(loc) as ro:
        assert ro.get("root", RID)["name"] == "r"
        assert ro.list("root")[RID]["name"] == "r"


RECORD_DAMAGE = {**DAMAGE, "rev-text": b'{"id": "x", "_rev": "1"}', "rev-null": b'{"id": "x", "_rev": null}',
                 "rev-list": b'{"id": "x", "_rev": []}', "rev-true": b'{"id": "x", "_rev": true}',
                 "rev-inf": b'{"id": "x", "_rev": 1e999}', "rev-negative": b'{"id": "x", "_rev": -1}'}


@pytest.mark.parametrize("damage", RECORD_DAMAGE.values(), ids=RECORD_DAMAGE)
def test_damaged_record_file_is_answered_from_the_journal_and_repairable(loc, damage):
    record = meta(loc) / "records" / "root" / f"{RID}.json"
    good = record.read_bytes()
    record.write_bytes(damage)
    with Workspace.open(loc) as ro:
        assert ro.get("root", RID)["name"] == "r"
        assert ro.list("root")[RID]["name"] == "r"
        assert ro.verify()["problems"]
    with Workspace.open(loc, writable=True) as ws:
        assert ws.verify(repair=True)["repaired"] is True
    assert json.loads(record.read_bytes()) == json.loads(good)


@pytest.mark.parametrize("damage", RECORD_DAMAGE.values(), ids=RECORD_DAMAGE)
def test_damaged_record_with_a_damaged_journal_is_a_controlled_refusal(loc, damage):
    (meta(loc) / "records" / "root" / f"{RID}.json").write_bytes(damage)
    (sorted((meta(loc) / "journal").glob("*.json"))[0]).write_bytes(b"[]")
    with Workspace.open(loc) as ro:
        with pytest.raises(WorkspaceCorruptError):
            ro.get("root", RID)
        with pytest.raises(WorkspaceCorruptError):
            ro.list("root")


def test_too_deep_journal_entry_refuses_writes_and_is_reported(loc):
    (sorted((meta(loc) / "journal").glob("*.json"))[-1]).write_bytes(TOO_DEEP)
    with Workspace.open(loc) as ro:
        assert ro.health()["ok"] is False
    assert "J002" in {f["code"] for f in doctor.examine(loc)["findings"]}
    with Workspace.open(loc, writable=True) as ws:
        with pytest.raises(WorkspaceCorruptError):
            ws.commit("t", [RecordPut("root", "root_" + "b" * 32, {"id": "x"}, expect_rev=0)])


def nested(levels: int):
    value: object = {}
    for _ in range(levels - 1):
        value = {"a": value}
    return value


def test_commit_refuses_records_too_deep_for_every_reader_to_parse(loc):
    with Workspace.open(loc, writable=True) as ws:
        before = ws.head_seq()
        with pytest.raises(ValueError, match="nested"):
            ws.commit("t", [RecordPut("root", "root_" + "b" * 32, nested(MAX_RECORD_NESTING + 1), expect_rev=0)])
        assert ws.head_seq() == before
        ws.commit("t", [RecordPut("root", "root_" + "c" * 32, nested(MAX_RECORD_NESTING), expect_rev=0)])
    with Workspace.open(loc) as ro:
        assert ro.get("root", "root_" + "c" * 32) is not None


# ---------------------------------------------------------------------- orphan clear names come from the journal
def publish_entry(loc: Path, op: str, data: dict) -> None:
    """A well-formed, correctly digested entry, as anyone able to write to the share could make."""
    seq = len(head_files(loc)) + 1
    entry = {"seq": seq, "txid": "c" * 32, "time": "t", "actor": {}, "op": op, "blobs": [], "writer_token": None,
             "records": [{"kind": "audit", "id": "aud_" + "d" * 32, "expect_rev": 0, "data": data}]}
    entry["digest"] = entry_digest(entry)
    (meta(loc) / "journal" / f"{seq:020d}.json").write_bytes(atomicio.dumps_json(entry))


TRAVERSAL = {
    "file": {"file": "../records/root/" + RID + ".json", "evidence": "writer.lock.orphaned-abc"},
    "evidence": {"file": ".releasing-abc", "evidence": "../../../outside.txt"},
    "clear-id": {"file": ".releasing-abc", "evidence": "writer.lock.orphaned-abc", "clear_id": "../../x"},
}


@pytest.mark.parametrize("names", TRAVERSAL.values(), ids=TRAVERSAL)
def test_orphan_clear_entry_cannot_name_files_outside_the_lock_folder(loc, names):
    orphan_bytes = b"damaged"
    (meta(loc) / "locks" / ".releasing-abc").write_bytes(orphan_bytes)
    record = meta(loc) / "records" / "root" / f"{RID}.json"
    good = record.read_bytes()
    publish_entry(loc, "workspace.orphan.clear", {
        "event": "orphaned-release-cleared", "clear_id": "abc", "file_sha256": hashlib.sha256(orphan_bytes).hexdigest(),
        "reason": "r", "time": "t", "by": {}, **names})
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(loc, writable=True)
    assert record.read_bytes() == good
    assert not (loc.parent / "outside.txt").exists()
    assert (meta(loc) / "locks" / ".releasing-abc").read_bytes() == orphan_bytes


# ---------------------------------------------------------------------- every field of every entry
VALUES = [None, [], {}, 5, "x", True, "../x", [[]], {"a": 1}]
RAW_ERROR = re.compile(r"^(AttributeError|TypeError|KeyError|IndexError|RecursionError|OverflowError)")


def paths_of(value, prefix=()):
    yield prefix
    if isinstance(value, dict):
        for k, v in value.items():
            yield from paths_of(v, prefix + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from paths_of(v, prefix + (i,))


def assign(value, path, new):
    for step in path[:-1]:
        value = value[step]
    value[path[-1]] = new


def read_everything(loc: Path) -> None:
    with Workspace.open(loc) as ro:
        ro.health()
        ro.transaction_outcome("c" * 32)
        for call in (lambda: ro.get("root", RID), lambda: ro.list("root"), lambda: ro.list("audit"), ro.verify,
                     lambda: Workspace._orphan_clear_state(meta(loc), ro)):
            try:
                call()
            except WorkspaceError:
                pass
    for finding in doctor.examine(loc)["findings"]:
        assert not (finding["code"] == "X001" and RAW_ERROR.search(finding["message"])), finding


def test_no_change_to_any_field_of_any_entry_escapes_as_a_raw_error(loc):
    """The journal below holds every kind of entry readers treat specially: a change, a lock break, an orphan clear and
    its result. Each field is replaced by each value with a correct digest, since a digest says nothing about shape."""
    with Workspace.open(loc, writable=True) as ws:
        ws.commit("t", [RecordPut("root", "root_" + "b" * 32, {"id": "x"}, expect_rev=0)], txid="c" * 32)
        token = Workspace.read_writer_lock(loc)["token"]
        Workspace.break_writer_lock(loc, token, "test")
        ws._closed = True
        ws._lock_token = None
    orphan = meta(loc) / "locks" / ".releasing-abc"
    orphan.write_bytes(b"damaged")
    Workspace.clear_orphaned_release(loc, orphan.name, "test")

    files = sorted((meta(loc) / "journal").glob("*.json"))
    assert {"workspace.lock.break", "workspace.orphan.clear", "workspace.orphan.clear.result"} <= {
        json.loads(f.read_bytes())["op"] for f in files}
    tried = 0
    for file in files:
        original = file.read_bytes()
        entry = json.loads(original)
        for path in paths_of(entry):
            if not path or path == ("seq",) or path == ("digest",):
                continue
            for new in VALUES:
                mutated = copy.deepcopy(entry)
                assign(mutated, path, new)
                mutated["digest"] = entry_digest(mutated)
                file.write_bytes(atomicio.dumps_json(mutated))
                try:
                    read_everything(loc)
                except WorkspaceError:
                    pass
                tried += 1
        file.write_bytes(original)
    assert tried > 500
    read_everything(loc)


# ---------------------------------------------------------------------- local client state
@pytest.mark.parametrize("damage", [*DAMAGE.values(), b'{"token": []}', b'{"token": 5}'],
                         ids=[*DAMAGE, "list-token", "number-token"])
def test_damaged_client_state_means_no_token(tmp_path, damage):
    state = clientstate.CheckoutTokens(tmp_path)
    state.put("wks", "doc", "t")
    state._file("wks", "doc").write_bytes(damage)
    assert state.get("wks", "doc") is None
    state.path.write_bytes(damage)
    assert state.get("wks", "doc") is None
    state.path.write_bytes(b'{"wks": []}')
    assert state.get("wks", "doc") is None
    state.path.write_bytes(b'{"wks": {"doc": "old"}}')
    assert state.get("wks", "doc") == "old"
