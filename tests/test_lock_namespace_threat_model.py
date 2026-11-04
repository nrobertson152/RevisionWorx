# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-019 threat-model boundary (Codex review of e5b38a6, threads on workspace.py:693 and :472).

Both Codex counterexamples are reproduced here, deterministically, exactly as reported: an actor
renames a *live* lock file onto a custody name at the instant before the protocol disposes of or
archives that name, and a writer paused after its preflight then commits beside the new holder.

The third test is the baseline. The *same capability*, one rename of a live writer.lock by a
process that does not follow the protocol, produces the same two effective writers with no orphan
recovery code involved at all. A file-only authority cannot defend against that actor: POSIX has
no unlink/rename bound to an inspected object, and the actor can simply vacate writer.lock. ADR-019
rev. 3 therefore scopes writer exclusivity to protocol-following processes (with crashes at any
point and arbitrary interleaving), and separately guarantees evidence/audit integrity against
arbitrary renames (tests/test_orphan_clear_custody.py). These tests pin that boundary: if one
ever stops reproducing, the boundary has moved and ADR-019 must be revisited.
"""
import json
import os

import revisionworx.workspace as wsmod
from revisionworx.workspace import RecordPut, Workspace

RID_B = "prj_" + "b" * 32
RID_C = "prj_" + "c" * 32


def _paused_after_preflight(b, during_pause):
    """Run ``during_pause`` while B is inside commit(), past every lock/revocation check."""
    real, fired = b._current_rev, []

    def current_rev(kind, rid):
        if not fired:
            fired.append(1)
            during_pause()
        return real(kind, rid)
    b._current_rev = current_rev


def _effective_writers(share):
    with Workspace.open(share) as r:
        return {r.get("project", RID_B) is not None, r.get("project", RID_C) is not None}


def _c_acquires_and_commits(share):
    c = Workspace.open(share, writable=True)
    c.commit("c", [RecordPut("project", RID_C, {"by": "C"})])
    c.close()


def test_boundary_baseline_rename_of_live_lock_needs_no_orphan_code(share):
    b = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"

    def adversary():
        os.replace(locks / "writer.lock", share / "stash")   # vacate B's lock: not a protocol move
        _c_acquires_and_commits(share)
    _paused_after_preflight(b, adversary)
    b.commit("b", [RecordPut("project", RID_B, {"by": "B"}, expect_rev=0)])
    assert _effective_writers(share) == {True}               # both B and C took effect
    b._lock_token = None
    b.close()


def test_boundary_codex_custody_disposal_substitution(share, monkeypatch):
    """Codex workspace.py:693: B's live lock renamed onto .clearing-* just before disposal."""
    b = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"
    (locks / ".releasing-deadbeef").write_bytes(b"{ torn")
    real_unlink = wsmod.Path.unlink

    def unlink(self, *a, **kw):
        if self.name.startswith(".clearing-") and (locks / "writer.lock").exists():
            os.replace(locks / "writer.lock", self)           # the out-of-protocol rename
        return real_unlink(self, *a, **kw)

    def admin_then_c():
        monkeypatch.setattr(wsmod.Path, "unlink", unlink)
        Workspace.clear_orphaned_release(share, ".releasing-deadbeef", reason="dead writer")
        monkeypatch.setattr(wsmod.Path, "unlink", real_unlink)
        _c_acquires_and_commits(share)
    _paused_after_preflight(b, admin_then_c)
    b.commit("b", [RecordPut("project", RID_B, {"by": "B"}, expect_rev=0)])
    assert _effective_writers(share) == {True}               # same outcome as the baseline
    b._lock_token = None
    b.close()


def test_boundary_codex_fencing_custody_substitution(share, monkeypatch):
    """Codex workspace.py:472, as reported: two readable orphans, normal acquisition. B's lock was
    displaced to .releasing-bbbb by a releaser (B paused after preflight). While C fences A's orphan,
    .releasing-bbbb is renamed onto A's .fencing-* name just before archival, so only A is revoked."""
    b = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"
    (locks / ".releasing-aaaa").write_text(json.dumps({"token": "a" * 32, "user": "A"}))
    real_replace = wsmod.os.replace

    def replace(src, dst, *a, **kw):
        if os.path.basename(str(src)).startswith(".fencing-") and "orphaned" in str(dst) \
                and (locks / ".releasing-bbbb").exists():
            real_replace(locks / ".releasing-bbbb", src)       # the out-of-protocol rename
        return real_replace(src, dst, *a, **kw)

    def displaced_then_c():
        os.replace(locks / "writer.lock", locks / ".releasing-bbbb")   # protocol: release step 2
        monkeypatch.setattr(wsmod.os, "replace", replace)
        try:
            _c_acquires_and_commits(share)
        finally:
            monkeypatch.setattr(wsmod.os, "replace", real_replace)
    _paused_after_preflight(b, displaced_then_c)
    b.commit("b", [RecordPut("project", RID_B, {"by": "B"}, expect_rev=0)])
    assert b._lock_token not in Workspace._revoked_tokens(share / ".revisionworx")
    assert _effective_writers(share) == {True}
    b._lock_token = None
    b.close()


def test_without_the_substitution_the_same_interleaving_is_safe(share):
    """Control for the test above: identical protocol moves and timing, no out-of-protocol rename.
    C's acquisition fences B's displaced lock, and B's paused commit is fenced."""
    import pytest
    from revisionworx.workspace import LockLostError
    b = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"
    (locks / ".releasing-aaaa").write_text(json.dumps({"token": "a" * 32, "user": "A"}))

    def displaced_then_c():
        os.replace(locks / "writer.lock", locks / ".releasing-bbbb")
        _c_acquires_and_commits(share)
    _paused_after_preflight(b, displaced_then_c)
    with pytest.raises(LockLostError):
        b.commit("b", [RecordPut("project", RID_B, {"by": "B"}, expect_rev=0)])
    assert b._lock_token in Workspace._revoked_tokens(share / ".revisionworx")
    with Workspace.open(share) as r:
        assert r.get("project", RID_B) is None and r.get("project", RID_C) is not None
    b._lock_token = None
    b.close()
