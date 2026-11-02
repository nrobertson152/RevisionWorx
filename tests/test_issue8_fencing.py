# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #8: a writer paused after its lock check must not take effect after an audited lock break."""
import pytest

from revisionworx.workspace import LockLostError, RecordPut, Workspace


def _paused_stale_commit(share, monkeypatch):
    stale = Workspace.create(share, "W")
    token = Workspace.read_writer_lock(share)["token"]
    real_check = stale._check_lock_still_held

    def check_then_pause():
        real_check()  # passes with the old token...
        Workspace.break_writer_lock(share, token, reason="writer unresponsive")  # ...then admin breaks it
    monkeypatch.setattr(stale, "_check_lock_still_held", check_then_pause)
    return stale


def test_stale_writer_entry_never_takes_effect(share, monkeypatch):
    stale = _paused_stale_commit(share, monkeypatch)
    rid = "prj_" + "5" * 32
    with pytest.raises(LockLostError, match="fenced"):
        stale.commit("test.stale.after.break", [RecordPut("project", rid, {"name": "stale"})], txid="d" * 32)
    monkeypatch.undo()
    assert stale.transaction_outcome("d" * 32)["state"] == "fenced"
    with Workspace.open(share) as reader:
        assert reader.get("project", rid) is None and rid not in reader.list("project")
    with Workspace.open(share, writable=True) as admin:
        assert admin.get("project", rid) is None  # replay skips it too
        rep = admin.verify()
        assert [p["problem"] for p in rep["problems"]] == ["fenced-entry"]
    stale._lock_token = None
    stale.close()


def test_index_ignores_fenced_entries(share, monkeypatch, tmp_path):
    from revisionworx.index import open_index
    stale = _paused_stale_commit(share, monkeypatch)
    with pytest.raises(LockLostError):
        stale.commit("x", [RecordPut("root", "root_" + "6" * 32, {"name": "stale", "paths": {}})])
    monkeypatch.undo()
    with Workspace.open(share) as r, open_index(r, tmp_path / "c") as idx:
        assert idx.db.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 0
    stale._lock_token = None
    stale.close()
