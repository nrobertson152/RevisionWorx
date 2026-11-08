# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-029: a fresh read-only handle starts from the view this computer last checked, and reads only what came after it.

Each command-line call is a fresh handle, and other programs ask RevisionWorx through the command line, so each of their
questions read every journal entry. A checkpoint is a cache, kept per computer and per folder: when it doesn't
describe the journal exactly, it is ignored, and the view is built from the journal as before. Writes never start from one.
"""
import json
import os
import shutil

import pytest

from revisionworx import workspace as workspace_module
from revisionworx.workspace import (CHECKPOINT_EVERY, LockLostError, RecordDelete, RecordPut, Workspace,
                                    view_checkpoint_path)


def rid(i: int) -> str:
    return "root_" + f"{i:032x}"


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def root(tmp_path, state):
    root = tmp_path / "w"
    with Workspace.create(root, "t"):
        pass
    return root


def grow(root, state, n, start=0):
    with Workspace.open(root, writable=True, state_dir=state) as w:
        for i in range(start, start + n):
            w.commit("t.a", [RecordPut("root", rid(i), {"name": str(i)}, expect_rev=0)])


def checkpoint_of(root, state):
    meta = root / ".revisionworx"
    with Workspace.open(root) as ws:
        path = view_checkpoint_path(meta, ws.id, state)
    return path, (json.loads(path.read_text("utf-8")) if path.exists() else None)


def counting_reads(monkeypatch, ws):
    seen = []
    real = ws.read_entry
    monkeypatch.setattr(ws, "read_entry", lambda s: (seen.append(s), real(s))[1])
    return seen


def comparable(ws):
    v = ws._journal_view()
    return v["seen"], v["anchor"], v["latest"], sorted(v["revocations"]), v["attempts"]


def from_scratch(root):
    """The same workspace, its view built from the journal alone."""
    with Workspace.open(root) as ws:
        ws._checkpoint, ws._view = None, None
        return comparable(ws)


def test_a_fresh_read_only_handle_reads_only_what_came_after_its_checkpoint(root, state, monkeypatch):
    """Was: every fresh handle read every entry, so each command-line call did (154 ms at 5,000 entries on a local disk,
    1.3 s after an import of 20,000 units, and one file read per entry on a share)."""
    grow(root, state, CHECKPOINT_EVERY + 20)
    grow(root, state, 5, start=CHECKPOINT_EVERY + 20)
    _path, data = checkpoint_of(root, state)
    assert data is not None and data["seen"] >= CHECKPOINT_EVERY
    with Workspace.open(root, state_dir=state) as reader:
        reads = counting_reads(monkeypatch, reader)
        assert reader.get("root", rid(0))["name"] == "0"
        assert reader.get("root", rid(CHECKPOINT_EVERY + 24))["name"] == str(CHECKPOINT_EVERY + 24)
        head = reader.head_seq()
        assert data["seen"] < head
        assert sorted(set(reads)) == list(range(data["seen"] + 1, head + 1))
    assert comparable_after(root, state) == from_scratch(root)


def comparable_after(root, state):
    with Workspace.open(root, state_dir=state) as ws:
        return comparable(ws)


def test_the_view_from_a_checkpoint_is_the_view_from_the_journal(root, state, monkeypatch):
    """Deletes, a retried transaction, a writer lock break and the fenced entry of the writer it revoked: whatever the
    checkpoint holds and wherever it ends, the view is the one the journal gives."""
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    grow(root, state, 3)
    with Workspace.open(root, writable=True, state_dir=state) as w:
        w.commit("t.d", [RecordDelete("root", rid(1), expect_rev=w.get("root", rid(1))["_rev"])])
        seq = w.commit("t.x", [RecordPut("root", rid(10), {"name": "once"}, expect_rev=0)], txid="7" * 32)
        assert w.commit("t.x", [RecordPut("root", rid(10), {"name": "once"}, expect_rev=0)], txid="7" * 32) == seq
    b = Workspace.open(root, writable=True, state_dir=state)
    real = b._current_rev

    def revoked_just_after_preflight(kind, rid_):
        Workspace.break_writer_lock(root, b._lock_token, "B unresponsive")
        b._current_rev = real
        return real(kind, rid_)
    b._current_rev = revoked_just_after_preflight
    with pytest.raises(LockLostError):
        b.commit("t.b", [RecordPut("root", rid(20), {"name": "fenced"}, expect_rev=0)])
    b.close()
    grow(root, state, 2, start=30)
    truth = from_scratch(root)
    assert truth[3], "the lock break is in the journal"
    assert rid(20) not in {r for _k, r in truth[2]}, "the fenced entry took no effect"
    with Workspace.open(root) as probe:
        head = probe.head_seq()
    for end in range(2, head + 1):                   # a checkpoint ending at every entry in turn
        cut_checkpoint_at(root, state, end)
        with Workspace.open(root, state_dir=state) as reader:
            assert comparable(reader) == truth, f"a checkpoint ending at entry {end}"


def cut_checkpoint_at(root, state, end):
    """Write the checkpoint a handle would have written had the journal ended at ``end``."""
    meta = root / ".revisionworx"
    with Workspace.open(root, state_dir=state) as ws:
        real_seqs = ws.journal_seqs
        ws.journal_seqs = lambda: [s for s in real_seqs() if s <= end]
        ws._checkpoint = None                        # nothing loaded, nothing saved while it is cut short
        ws._view = None
        view = ws._journal_view()
        ws._checkpoint = view_checkpoint_path(meta, ws.id, state)
        ws._save_checkpoint(view)
    assert json.loads(ws._checkpoint.read_text("utf-8"))["seen"] == end


@pytest.mark.parametrize("damage", ["bytes", "check", "version", "workspace", "folder", "beyond-the-journal", "anchor"])
def test_a_checkpoint_that_doesnt_describe_this_journal_is_ignored(root, state, monkeypatch, damage):
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    grow(root, state, 4)
    path, data = checkpoint_of(root, state)
    if damage == "bytes":
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 0x20
        path.write_bytes(bytes(raw))
    else:
        if damage == "check":
            data["latest"] = [row for row in data["latest"] if row[1] != rid(2)]   # a record dropped, the check kept
        else:
            if damage == "version":
                data["version"] += 1
            elif damage == "workspace":
                data["workspace"] = "ws_" + "0" * 32
            elif damage == "folder":
                data["folder"] = data["folder"] + "-elsewhere"
            elif damage == "beyond-the-journal":
                data["seen"] += 5
            elif damage == "anchor":
                data["anchor"] = "0" * 64
                data["anchor_stat"] = None
            body = {k: v for k, v in data.items() if k != "check"}
            data["check"] = workspace_module._checkpoint_check(body)
        path.write_text(json.dumps(data), "utf-8")
    with Workspace.open(root, state_dir=state) as reader:
        reads = counting_reads(monkeypatch, reader)
        assert reader.get("root", rid(2))["name"] == "2"
        assert 1 in reads, "built from the journal, from its first entry"
    assert comparable_after(root, state) == from_scratch(root)


def test_a_restore_to_the_same_length_isnt_answered_from_the_checkpoint(root, state, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    grow(root, state, 2)
    backup = tmp_path / "backup"
    shutil.copytree(root / ".revisionworx", backup)
    with Workspace.open(root, writable=True, state_dir=state) as w:
        w.commit("t.a", [RecordPut("root", rid(0), {"name": "before the restore"}, expect_rev=w.get("root", rid(0))["_rev"])])
    with Workspace.open(root, state_dir=state) as reader:
        assert reader.get("root", rid(0))["name"] == "before the restore"
        head = reader.head_seq()
    _path, data = checkpoint_of(root, state)
    assert data["seen"] == head                       # the checkpoint ends at the entry the restore will replace
    shutil.rmtree(root / ".revisionworx")
    shutil.copytree(backup, root / ".revisionworx")
    with Workspace.open(root, writable=True, state_dir=root.parent / "elsewhere") as w:
        w.commit("t.a", [RecordPut("root", rid(1), {"name": "after the restore, longer"},
                                   expect_rev=w.get("root", rid(1))["_rev"])])
        assert w.head_seq() == data["seen"]           # the checkpoint's last entry number exists again, with another entry
    with Workspace.open(root, state_dir=state) as reader:
        assert reader.get("root", rid(0))["name"] == "0"
        assert reader.get("root", rid(1))["name"] == "after the restore, longer"


def test_a_writable_handle_builds_its_view_from_the_journal(root, state, monkeypatch):
    grow(root, state, CHECKPOINT_EVERY + 2)
    assert checkpoint_of(root, state)[1] is not None
    with Workspace.open(root, writable=True, state_dir=state) as w:
        reads = counting_reads(monkeypatch, w)
        w._view = None
        w.get("root", rid(0))
        assert sorted(set(reads)) == list(range(1, w.head_seq() + 1))


def test_doctor_and_other_direct_handles_never_use_one(root, state):
    grow(root, state, CHECKPOINT_EVERY + 2)
    meta = root / ".revisionworx"
    from revisionworx import atomicio
    ws = Workspace(meta, atomicio.read_json(meta / "workspace.json"), False, None)
    assert ws._checkpoint is None


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps it private by the profile's permissions")
def test_a_checkpoint_is_written_for_this_user_only(root, state):
    grow(root, state, CHECKPOINT_EVERY + 2)
    path, _data = checkpoint_of(root, state)
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_copy_of_the_workspace_in_another_folder_has_its_own(root, state, tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    grow(root, state, 3)
    copy = tmp_path / "copy"
    shutil.copytree(root, copy)
    with Workspace.open(copy, writable=True, state_dir=state) as w:
        w.commit("t.a", [RecordPut("root", rid(0), {"name": "the copy's"}, expect_rev=w.get("root", rid(0))["_rev"])])
    with Workspace.open(root, state_dir=state) as original, Workspace.open(copy, state_dir=state) as other:
        assert original.get("root", rid(0))["name"] == "0"
        assert other.get("root", rid(0))["name"] == "the copy's"
        assert view_checkpoint_path(root / ".revisionworx", original.id, state) != \
            view_checkpoint_path(copy / ".revisionworx", other.id, state)


def test_a_checkpoint_that_cant_be_written_changes_nothing(root, state, monkeypatch):
    """A profile that can't be written to (read-only, full): reads are what they were, and the handle stops trying."""
    from revisionworx import clientstate
    tries = []

    def refuse(*_args, **_kwargs):
        tries.append(1)
        raise PermissionError("read-only profile")
    monkeypatch.setattr(clientstate, "write_private", refuse)
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    with Workspace.open(root, writable=True, state_dir=state) as w:
        for i in range(5):
            w.commit("t.a", [RecordPut("root", rid(i), {"name": str(i)}, expect_rev=0)])
            assert w.get("root", rid(i))["name"] == str(i)
    assert len(tries) == 1
    assert not view_checkpoint_path(root / ".revisionworx", w.id, state).exists()


def test_one_large_entry_is_enough_for_a_checkpoint(root, state, monkeypatch):
    """An import of thousands of units is one entry: counting entries alone, it would never be kept."""
    with Workspace.open(root, writable=True, state_dir=state) as w:
        w.commit("t.big", [RecordPut("root", rid(i), {"name": str(i)}, expect_rev=0)
                           for i in range(workspace_module.CHECKPOINT_RECORDS)])
        head = w.head_seq()
    with Workspace.open(root, state_dir=state) as reader:
        assert reader.get("root", rid(7))["name"] == "7"
    _path, data = checkpoint_of(root, state)
    assert data["seen"] == head
    with Workspace.open(root, state_dir=state) as reader:
        reads = counting_reads(monkeypatch, reader)
        assert reader.get("root", rid(7))["name"] == "7"
        assert reads == []                       # the large entry isn't read again: its file is the one checked


def test_integrity_checks_build_their_view_from_the_journal(root, state, monkeypatch):
    """`verify`, `unit verify`, `doc verify` (revision content and PDFs) and `change verify` report on the journal, never on
    what this computer checked before: a checkpoint that has lost a record (here, written so on purpose) doesn't change
    what they see. `doctor` builds its own handle, which has no checkpoint."""
    from revisionworx import derivatives, documents
    from revisionworx.units import query
    from revisionworx.units.service import KIND, service_for
    UNIT_KIND = KIND["unit"]
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    with Workspace.open(root, writable=True, state_dir=state) as w:
        made = service_for(w).register_many([{"identifiers": [{"scheme": "serial", "issuer": "", "value": f"SN-{i}"}]}
                                             for i in range(2)])
        w.commit("t.a", [RecordPut("root", rid(1), {"name": "1"}, expect_rev=0)])
    with Workspace.open(root, state_dir=state) as reader:
        assert len(reader.list(UNIT_KIND)) == 2
    path, data = checkpoint_of(root, state)
    hidden = made[0]["id"]
    data["latest"] = [row for row in data["latest"] if row[1] not in (hidden, rid(1))]
    data["check"] = workspace_module._checkpoint_check({k: v for k, v in data.items() if k != "check"})
    path.write_text(json.dumps(data), "utf-8")
    checks = [("verify", lambda ws: ws.verify()),
              ("unit verify", lambda ws: query.verify(service_for(ws))),
              ("revision content", lambda ws: documents.verify_revisions(ws)),
              ("PDFs", lambda ws: derivatives.verify(ws))]
    for name, check in checks:
        with Workspace.open(root, state_dir=state) as ws:
            check(ws)
            assert ws._checkpoint is None, name
            assert set(ws.list(UNIT_KIND)) == {u["id"] for u in made}, name
            assert ws.get("root", rid(1))["name"] == "1", name


def test_the_window_keeps_its_checkpoints_in_its_own_state_folder(root, tmp_path, monkeypatch):
    """GUI_LAUNCH_CONTRACT: the per-user folders can be given to the Controller, and the checkpoint is kept in its state
    folder, for a workspace not set up for a team as for one that is."""
    from revisionworx.gui.controller import Controller
    monkeypatch.setattr(workspace_module, "CHECKPOINT_EVERY", 1)
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "its-state")
    ctl.open_workspace(root)
    ctl.units_page()
    assert list((tmp_path / "its-state" / "views").rglob("view-*.json"))
