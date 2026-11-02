# SPDX-License-Identifier: GPL-3.0-or-later
"""Successor-lock deletion race (Distributed Systems finding, run 41387552435).

Writer A verifies its token, pauses before removing writer.lock; an administrator revokes A and
writer B acquires; A resumes. A must never remove or invalidate B's lock."""
import os
from pathlib import Path

import pytest

from revisionworx import atomicio
from revisionworx.workspace import LockLostError, RecordPut, Workspace

RID = "prj_" + "7" * 32


def _inject_before_first_lock_removal(monkeypatch, action):
    """Run ``action`` exactly when A first tries to remove/move writer.lock (unlink or replace)."""
    fired = []
    real_unlink, real_replace = Path.unlink, os.replace

    def trigger():
        if not fired:
            fired.append(1)
            action()

    def unlink(self, *a, **kw):
        if self.name == "writer.lock":
            trigger()
        return real_unlink(self, *a, **kw)

    def replace(src, dst, *a, **kw):
        if str(src).endswith(os.sep + "writer.lock") and ".releasing" in str(dst):
            trigger()
        return real_replace(src, dst, *a, **kw)
    monkeypatch.setattr(Path, "unlink", unlink)
    import revisionworx.workspace as wsmod
    monkeypatch.setattr(wsmod.os, "replace", replace)
    return fired


def test_paused_releaser_never_deletes_successor_lock(share, monkeypatch):
    a = Workspace.create(share, "W")
    holders = {}

    def admin_revokes_a_and_b_acquires():
        Workspace.break_writer_lock(share, a._lock_token, reason="A unresponsive")
        holders["b"] = Workspace.open(share, writable=True)

    fired = _inject_before_first_lock_removal(monkeypatch, admin_revokes_a_and_b_acquires)
    a.close()  # A resumes its release after B acquired
    monkeypatch.undo()
    b = holders["b"]
    assert fired
    lock = Workspace.read_writer_lock(share)
    assert lock is not None and lock["token"] == b._lock_token  # successor keeps its lock
    seq = b.commit("b.works", [RecordPut("project", RID, {"by": "B"})])  # ...and its authority
    assert b.get("project", RID)["_rev"] == seq
    b.close()
    assert Workspace.read_writer_lock(share) is None


def test_revoked_writer_does_not_touch_lock_at_all(share, monkeypatch):
    a = Workspace.create(share, "W")
    Workspace.break_writer_lock(share, a._lock_token, reason="A unresponsive")
    b = Workspace.open(share, writable=True)
    touched = _inject_before_first_lock_removal(monkeypatch, lambda: None)
    a.close()
    monkeypatch.undo()
    assert not touched and Workspace.read_writer_lock(share)["token"] == b._lock_token
    b.close()


def test_third_writer_in_restore_window_fails_closed(share, monkeypatch):
    """A moves B's lock aside; before A can restore it, C creates writer.lock. B must lose authority
    via a journaled revocation (never two effective writers); C keeps its lock."""
    import revisionworx.workspace as wsmod
    a = Workspace.create(share, "W")
    holders = {}

    def admin_revokes_a_and_b_acquires():
        Workspace.break_writer_lock(share, a._lock_token, reason="A unresponsive")
        holders["b"] = Workspace.open(share, writable=True)
    _inject_before_first_lock_removal(monkeypatch, admin_revokes_a_and_b_acquires)
    real_link, real_rename = os.link, os.rename

    def c_wins_window(src, dst):
        if ".releasing-" in str(src) and "c" not in holders:  # only A's restore of B's lock
            holders["c"] = None  # guard: C's own acquisition also publishes via link/rename
            holders["c"] = Workspace._acquire_writer_lock(share / ".revisionworx")  # C in the gap
        return (real_rename if os.name == "nt" else real_link)(src, dst)
    monkeypatch.setattr(wsmod.os, "rename" if os.name == "nt" else "link", c_wins_window)
    a.close()
    monkeypatch.undo()
    b = holders["b"]
    assert Workspace.read_writer_lock(share)["token"] == holders["c"]  # C keeps its lock
    with pytest.raises(LockLostError):  # B can no longer take effect
        b.commit("b.late", [RecordPut("project", RID, {"by": "B"})])
    locks = share / ".revisionworx" / "locks"
    # evidence kept: by the releaser (displaced) or by C's acquisition, which took custody (orphaned)
    assert any(p.name.startswith(("writer.lock.displaced-", "writer.lock.orphaned-")) for p in locks.iterdir())
    assert not any(p.name.startswith(".releasing") for p in locks.iterdir())
    with Workspace.open(share) as r:
        assert r.get("project", RID) is None
        ev = [x for x in r.list("audit").values() if x["previous_holder"].get("token") == b._lock_token]
        assert len(ev) == 1  # fenced exactly once, whichever party noticed first
    b._lock_token = None
    b.close()


def test_normal_and_repeated_release(share):
    ws = Workspace.create(share, "W")
    ws.close()
    ws.close()  # idempotent
    assert Workspace.read_writer_lock(share) is None
    assert not [p for p in (share / ".revisionworx" / "locks").iterdir()]
    w2 = Workspace.open(share, writable=True)  # reacquire after release
    w2.close()


def test_crash_then_admin_break_then_new_writer(share):
    crashed = Workspace.create(share, "W")  # never closed: simulates a crash
    token = crashed._lock_token
    Workspace.break_writer_lock(share, token, reason="client crashed")
    b = Workspace.open(share, writable=True)
    crashed.close()  # the crashed process "returns": revoked, must not touch B's lock
    assert Workspace.read_writer_lock(share)["token"] == b._lock_token
    b.commit("ok", [RecordPut("project", RID, {"v": 1})])
    b.close()


def test_crash_after_rename_of_successor_lock_fails_closed(share, monkeypatch):
    """A moves B's lock aside and crashes before restoring it. The next acquirer C must revoke the
    orphaned token before writing, so B and C can never both take effect."""
    import revisionworx.workspace as wsmod
    a = Workspace.create(share, "W")
    holders = {}

    def admin_revokes_a_and_b_acquires():
        Workspace.break_writer_lock(share, a._lock_token, reason="A unresponsive")
        holders["b"] = Workspace.open(share, writable=True)
    _inject_before_first_lock_removal(monkeypatch, admin_revokes_a_and_b_acquires)
    monkeypatch.setattr(wsmod.Workspace, "_restore_foreign_lock",
                        lambda self, *a_: (_ for _ in ()).throw(SystemExit("A crashed")))
    with pytest.raises(SystemExit):
        a.close()
    monkeypatch.undo()
    b = holders["b"]
    assert Workspace.read_writer_lock(share) is None  # B's lock is sitting in A's private file
    c = Workspace.open(share, writable=True)          # C acquires the vacant name
    assert b._lock_token in Workspace._revoked_tokens(share / ".revisionworx")  # journal fence, not luck
    with pytest.raises(LockLostError):                # ...and B must be fenced
        b.commit("b.late", [RecordPut("project", RID, {"by": "B"})])
    c.commit("c.ok", [RecordPut("project", RID, {"by": "C"})])
    assert c.get("project", RID)["by"] == "C"
    locks = share / ".revisionworx" / "locks"
    assert not any(p.name.startswith(".releasing") for p in locks.iterdir())
    assert any(p.name.startswith("writer.lock.orphaned-") for p in locks.iterdir())
    b._lock_token = None
    b.close()
    c.close()
