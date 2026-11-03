# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-019 supervised recovery: `clear_orphaned_release` must be audit-first and crash-resumable
(DS finding, run 41391444478)."""
import os

import pytest

from revisionworx.workspace import RecordPut, Workspace, WorkspaceError

NAME = ".releasing-deadbeef"
RID = "prj_" + "8" * 32


def _orphan(share):
    w = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"
    os.replace(locks / "writer.lock", locks / NAME)
    (locks / NAME).write_text("{ torn")
    w._lock_token = None
    w.close()
    return locks


def _audits(share):
    with Workspace.open(share) as r:
        return [a for a in r.list("audit").values() if a["event"] == "orphaned-release-cleared"]


def test_crash_at_audit_publication_leaves_system_fail_closed(share, monkeypatch):
    """Old order moved evidence first: dying at the audit step left no orphan and no audit, and the
    next writer could acquire. Audit-first: the orphan is still there and acquisition is refused."""
    locks = _orphan(share)
    head = Workspace.open(share).head_seq()

    def die(*a, **kw):
        raise SystemExit("process died while publishing the audit")
    monkeypatch.setattr(Workspace, "_publish_admin_entry", classmethod(lambda cls, *a, **kw: die()))
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    assert (locks / NAME).exists() and Workspace.open(share).head_seq() == head and not _audits(share)
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)


def test_crash_after_audit_before_move_is_resumed_by_acquisition(share, monkeypatch):
    locks = _orphan(share)
    import revisionworx.workspace as wsmod
    real = wsmod.os.replace

    def die_on_evidence_move(src, dst, *a, **kw):
        if str(src).endswith(NAME):
            raise SystemExit("process died before moving evidence")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", die_on_evidence_move)
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    assert len(_audits(share)) == 1 and (locks / NAME).exists()
    with Workspace.open(share, writable=True) as c:           # resumes the audited disposition
        c.commit("c.ok", [RecordPut("project", RID, {"by": "C"})])
    assert not (locks / NAME).exists()
    ev = [p for p in locks.iterdir() if p.name.startswith("writer.lock.orphaned-")]
    assert len(ev) == 1 and ev[0].read_text() == "{ torn"      # forensic bytes preserved
    assert len(_audits(share)) == 1                            # exactly once


def test_retry_after_crash_is_idempotent(share, monkeypatch):
    locks = _orphan(share)
    import revisionworx.workspace as wsmod
    real = wsmod.os.replace

    def die_on_evidence_move(src, dst, *a, **kw):
        if str(src).endswith(NAME):
            raise SystemExit("died")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", die_on_evidence_move)
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")  # operator retries
    Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")  # and again
    assert len(_audits(share)) == 1 and not (locks / NAME).exists()
    assert len([p for p in locks.iterdir() if p.name.startswith("writer.lock.orphaned-")]) == 1


def test_complete_clear_then_acquire(share):
    locks = _orphan(share)
    Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    audit = _audits(share)[0]
    assert audit["file_sha256"] and audit["evidence"].startswith("writer.lock.orphaned-")
    assert (locks / audit["evidence"]).read_text() == "{ torn"
    with Workspace.open(share, writable=True):
        pass


def test_orphan_changed_after_audit_is_refused(share, monkeypatch):
    locks = _orphan(share)
    import revisionworx.workspace as wsmod
    real = wsmod.os.replace

    def die(src, dst, *a, **kw):
        if str(src).endswith(NAME):
            raise SystemExit("died")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", die)
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    (locks / NAME).write_text("{ different torn bytes")  # not what the administrator authorised
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)
    # rev. 3: the old authorisation does not cover new bytes; they were quarantined under a fresh
    # name (a new orphan instance), which needs a fresh, separately audited decision
    with pytest.raises(WorkspaceError, match="quarantined as (\\.releasing-[0-9a-f]+)") as ei:
        Workspace.clear_orphaned_release(share, NAME, reason="re-inspected")
    import re
    q = re.search(r"quarantined as (\.releasing-[0-9a-f]+)", str(ei.value)).group(1)
    assert (locks / q).read_text() == "{ different torn bytes"
    Workspace.clear_orphaned_release(share, q, reason="re-inspected: still a dead writer's file")
    audits = _audits(share)
    assert len(audits) == 2 and audits[0]["file_sha256"] != audits[1]["file_sha256"]
    with Workspace.open(share, writable=True):
        pass
