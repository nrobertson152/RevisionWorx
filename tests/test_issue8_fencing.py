# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #8 (stale writer after admin lock break), #9 (index snapshot), #10 (Windows lock release)."""
import pytest

from revisionworx import atomicio
from revisionworx.workspace import LockLostError, RecordPut, Workspace, WorkspaceError

RID = "prj_" + "5" * 32


def _token(share):
    return Workspace.read_writer_lock(share)["token"]


def test_revoked_before_commit_is_refused_and_publishes_nothing(share, monkeypatch):
    stale = Workspace.create(share, "W")
    real_check = stale._check_lock_still_held

    def check_then_break():
        real_check()
        Workspace.break_writer_lock(share, stale._lock_token, reason="writer unresponsive")
    monkeypatch.setattr(stale, "_check_lock_still_held", check_then_break)
    with pytest.raises(LockLostError):
        stale.commit("stale", [RecordPut("project", RID, {"name": "stale"})], txid="d" * 32)
    monkeypatch.undo()
    assert stale.transaction_outcome("d" * 32)["state"] == "not-found"
    with Workspace.open(share, writable=True) as admin:
        assert admin.get("project", RID) is None and admin.verify()["problems"] == []
    stale._lock_token = None
    stale.close()


def test_revocation_taking_the_same_sequence_wins_and_stale_publishes_nothing(share, monkeypatch):
    stale = Workspace.create(share, "W")
    token = stale._lock_token
    real_publish, fired = atomicio.publish_exclusive, []

    def publish(path, data):
        if "journal" in str(path) and b"stale" in data and not fired:
            fired.append(1)
            Workspace.break_writer_lock(share, token, reason="writer unresponsive")  # takes this seq
        return real_publish(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", publish)
    with pytest.raises(LockLostError, match="before its change was published"):
        stale.commit("stale", [RecordPut("project", RID, {"name": "stale"})], txid="d" * 32)
    monkeypatch.undo()
    assert stale.transaction_outcome("d" * 32)["state"] == "not-found"
    with Workspace.open(share, writable=True) as admin:
        assert admin.get("project", RID) is None and admin.verify()["problems"] == []
    stale._lock_token = None
    stale.close()


def test_revocation_after_preflight_but_before_seq_choice_fences_entry(share, monkeypatch):
    stale = Workspace.create(share, "W")
    token = stale._lock_token
    real_rev, fired = stale._current_rev, []

    def current_rev(kind, rid):  # runs after the lock and revocation checks, before seq is chosen
        if not fired:
            fired.append(1)
            Workspace.break_writer_lock(share, token, reason="writer unresponsive")
        return real_rev(kind, rid)
    monkeypatch.setattr(stale, "_current_rev", current_rev)
    with pytest.raises(LockLostError, match="fenced"):
        stale.commit("stale", [RecordPut("project", RID, {"name": "stale"}, expect_rev=0)], txid="d" * 32)
    monkeypatch.undo()
    assert stale.transaction_outcome("d" * 32)["state"] == "fenced"
    with Workspace.open(share) as reader:
        assert reader.get("project", RID) is None
    with Workspace.open(share, writable=True) as admin:
        assert admin.get("project", RID) is None
        assert [p["problem"] for p in admin.verify()["problems"]] == ["fenced-entry"]
    stale._lock_token = None
    stale.close()


def test_former_pre_audit_gap_admin_paused_before_lock_move(share, monkeypatch):
    """Admin publishes the revocation, then pauses before moving writer.lock; the stale writer, whose
    lock file is still present, must not take effect."""
    stale = Workspace.create(share, "W")
    token = stale._lock_token
    real_replace = __import__("os").replace
    import revisionworx.workspace as wsmod

    def replace(src, dst):
        if str(src).endswith("writer.lock"):
            with pytest.raises(LockLostError):  # stale writer acts during the admin's pause
                stale.commit("stale", [RecordPut("project", RID, {"name": "stale"})])
        return real_replace(src, dst)
    monkeypatch.setattr(wsmod.os, "replace", replace)
    Workspace.break_writer_lock(share, token, reason="writer unresponsive")
    monkeypatch.undo()
    with Workspace.open(share, writable=True) as admin:
        assert admin.get("project", RID) is None and admin.verify()["problems"] == []
    stale._lock_token = None
    stale.close()


def test_lock_file_left_behind_by_revoked_writer_does_not_block(share, monkeypatch):
    stale = Workspace.create(share, "W")
    token = stale._lock_token
    import revisionworx.workspace as wsmod
    monkeypatch.setattr(wsmod.Workspace, "read_writer_lock", classmethod(lambda cls, loc: None))
    Workspace.break_writer_lock.__func__  # noqa: B018
    monkeypatch.undo()
    # Simulate an admin crash after publishing the revocation but before moving the lock file.
    real_replace = wsmod.os.replace
    monkeypatch.setattr(wsmod.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("crash"))
                        if str(s).endswith("writer.lock") else real_replace(s, d))
    Workspace.break_writer_lock(share, token, reason="x")
    monkeypatch.undo()
    assert _token(share) == token  # stale lock file still there...
    with Workspace.open(share, writable=True) as admin:  # ...but acquisition treats it as revoked
        assert admin._lock_token != token
    stale._lock_token = None
    stale.close()


# ---------------------------------------------------------------- issue #9
def test_rebuild_is_a_consistent_snapshot_under_concurrent_commit(ws, cache_dir, monkeypatch):
    from revisionworx import projects
    from revisionworx.index import open_index
    from revisionworx.projects import NewProject
    with open_index(ws, cache_dir) as idx:
        real_list, fired = ws.list, []

        def list_then_commit(kind):
            out = real_list(kind)
            if kind == "root" and not fired:
                fired.append(1)
                projects.create_project(ws, NewProject(name="Racer"))  # commits N+1 mid-snapshot
            return out
        monkeypatch.setattr(ws, "list", list_then_commit)
        head = idx.rebuild()
        monkeypatch.undo()
        assert head == ws.head_seq() == idx.cursor_seq()
        assert [p["name"] for p in idx.search_projects("racer")] == ["Racer"]
        assert idx.sync()["mode"] == "current"


def test_rebuild_and_sync_agree_across_insert_delete_restart(ws, cache_dir):
    from revisionworx import projects
    from revisionworx.index import open_index
    from revisionworx.projects import NewProject
    from revisionworx.workspace import RecordDelete
    a = projects.create_project(ws, NewProject(name="A", rel_path="a"))
    with open_index(ws, cache_dir) as idx:
        projects.create_project(ws, NewProject(name="B", rel_path="b"))
        ws.commit("t", [RecordDelete("project", a["id"], expect_rev=a["_rev"])])
        idx.sync()
        synced = idx.search_projects("")
    with open_index(ws, cache_dir) as idx2:  # restart
        assert idx2.search_projects("") == synced
        idx2.rebuild()
        assert idx2.search_projects("") == synced == [r for r in synced if r["name"] == "B"]


# ---------------------------------------------------------------- issue #10
# Release moves writer.lock aside with os.replace (ADR-019), so Windows sharing violations are injected
# there: the operation that actually touches the shared lock file.
import os  # noqa: E402

import revisionworx.workspace as wsmod  # noqa: E402

REAL_REPLACE = os.replace


def _sharing_violation(times):
    calls = {"n": 0}

    def replace(src, dst, *a, **kw):
        if str(src).endswith(os.sep + "writer.lock") and calls["n"] < times:
            calls["n"] += 1
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return REAL_REPLACE(src, dst, *a, **kw)
    return replace, calls


def test_close_retries_transient_sharing_violation(share, monkeypatch):
    ws = Workspace.create(share, "W")
    replace, calls = _sharing_violation(5)
    monkeypatch.setattr(wsmod.os, "replace", replace)
    ws.close()
    monkeypatch.undo()
    assert calls["n"] == 5 and Workspace.read_writer_lock(share) is None


def test_close_never_deletes_another_writers_lock_while_retrying(share, monkeypatch):
    ws = Workspace.create(share, "W")
    lock = share / ".revisionworx" / "locks" / "writer.lock"
    calls = {"n": 0}

    def replace(src, dst, *a, **kw):
        if str(src).endswith(os.sep + "writer.lock") and calls["n"] == 0:
            calls["n"] += 1
            # while we retry, our lock is replaced by someone else's
            atomicio.atomic_write_json(lock, {**atomicio.read_json(lock), "token": "new-owner"})
            raise PermissionError(32, "sharing violation")
        return REAL_REPLACE(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", replace)
    ws.close()
    monkeypatch.undo()
    assert calls["n"] == 1 and atomicio.read_json(lock)["token"] == "new-owner"
    assert not [p for p in lock.parent.iterdir() if p.name.startswith(".releasing")]


def test_close_reports_persistent_failure_and_keeps_lock(share, monkeypatch):
    ws = Workspace.create(share, "W")
    ws.LOCK_RELEASE_ATTEMPTS, ws.LOCK_RELEASE_DELAY = 3, 0
    replace, _ = _sharing_violation(10**6)
    monkeypatch.setattr(wsmod.os, "replace", replace)
    with pytest.raises(WorkspaceError, match="still held"):
        ws.close()
    monkeypatch.undo()
    assert Workspace.read_writer_lock(share) is not None
