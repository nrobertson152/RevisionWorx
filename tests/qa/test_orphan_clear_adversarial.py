# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent ADR-019 custody and administrative-concurrency acceptance.

The two tests named "unsafe_reproducer" assert an OBSERVED DEFECT, NOT safety.
Their paired tests are strict expected-failure safety requirements on 366d435.
No production implementation is changed by this module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import os
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 absent from Phase 1 QA base", allow_module_level=True)

from revisionworx.workspace import RecordPut, Workspace, WorkspaceCorruptError, WorkspaceError

NAME = ".releasing-" + "d" * 16
ORIGINAL = b"{qa-operator-observed-orphan-X"
SWAPPED = b"{qa-different-lock-evidence-Y"


def _vulnerable_move():
    if not hasattr(Workspace, "_complete_orphan_clear"):
        return False
    source = inspect.getsource(Workspace._complete_orphan_clear)
    return "hashlib.sha256(src.read_bytes())" in source and "os.replace(src," in source


def _vulnerable_admin_lookup():
    source = inspect.getsource(Workspace.clear_orphaned_release)
    return ("if audit is None:" in source
            and 'cls._publish_admin_entry(meta, "workspace.orphan.clear", audit)' in source)


def _setup(tmp_path):
    root = tmp_path / "workspace"
    with Workspace.create(root, "QA adversarial custody"):
        pass
    locks = root / ".revisionworx" / "locks"
    orphan = locks / NAME
    orphan.write_bytes(ORIGINAL)
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(root, writable=True)
    return root, locks, orphan


def _audits(root):
    with Workspace.open(root) as reader:
        return [v for v in reader.list("audit").values()
                if v.get("event") == "orphaned-release-cleared" and v.get("file") == NAME]


def _probe_effective_write(root):
    """Assess effective journal state, not only whether a lock was acquired."""
    rid = "root_" + "d" * 32
    rejected = False
    try:
        with Workspace.open(root, writable=True) as writer:
            writer.commit("qa.orphan-clear.writer-probe", [
                RecordPut("root", rid, {"name": "new-writer"}, expect_rev=0)])
    except (WorkspaceError, OSError):
        rejected = True
    with Workspace.open(root) as reader:
        effective = reader.get("root", rid) is not None
    return rejected, effective


def _swap_on_custody_move(root, orphan, monkeypatch):
    """Deliberately change path content AFTER the audited hash and BEFORE rename."""
    actual_replace = os.replace
    moves = []

    def replace_after_swap(src, dst, *args, **kwargs):
        if Path(src) == orphan:
            moves.append(Path(dst))
            orphan.write_bytes(SWAPPED)
        return actual_replace(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", replace_after_swap)
        Workspace.clear_orphaned_release(root, NAME, "approved original evidence")

    assert moves, "the actual orphan custody transition was not intercepted"
    audits = _audits(root)
    evidence = list(orphan.parent.glob("writer.lock.orphaned-*"))
    rejected, effective = _probe_effective_write(root)
    return audits, evidence, rejected, effective


def _overlap_admin_requests(root, monkeypatch):
    """Nest B after A's lookup but before A publishes, without test scheduler races."""
    publish = Workspace._publish_admin_entry
    entered = False

    def interleaved(cls, meta, op, data, revokes=None, **kwargs):
        nonlocal entered
        if op == "workspace.orphan.clear" and not entered:
            entered = True
            Workspace.clear_orphaned_release(root, NAME, "administrator B")
        return publish(meta, op, data, revokes=revokes, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Workspace, "_publish_admin_entry", classmethod(interleaved))
        Workspace.clear_orphaned_release(root, NAME, "administrator A")
    assert entered, "overlapping operation was not injected"
    return _audits(root), list((root / ".revisionworx" / "locks").glob("writer.lock.orphaned-*"))


def test_p1_unsafe_reproducer_original_hash_does_not_bind_moved_bytes(tmp_path, monkeypatch):
    """PASS = demonstrated unsafe behavior, explicitly NOT an acceptance pass."""
    if not _vulnerable_move():
        pytest.skip("original unsafe verify-then-rename mechanism absent")
    root, _, orphan = _setup(tmp_path)
    audits, evidence, rejected, effective = _swap_on_custody_move(root, orphan, monkeypatch)
    assert len(audits) == 1
    assert audits[0]["file_sha256"] == hashlib.sha256(ORIGINAL).hexdigest()
    assert len(evidence) == 1 and evidence[0].read_bytes() == SWAPPED
    assert not orphan.exists()
    assert effective and not rejected  # unsafe admission of a new effective writer


def test_p1_safety_moved_evidence_must_match_audit_or_refuse_writer(
        tmp_path, monkeypatch, request):
    """Strict safety requirement: new writer cannot benefit from unauthorised move."""
    if _vulnerable_move():
        request.node.add_marker(pytest.mark.xfail(
            strict=True, raises=AssertionError,
            reason="366d435 verify-path-before-move TOCTOU, audit bound to wrong bytes"))
    root, _, orphan = _setup(tmp_path)
    audits, evidence, rejected, effective = _swap_on_custody_move(root, orphan, monkeypatch)
    assert audits, "no durable audit available to authorize the disposition"
    blocking = list(orphan.parent.glob(".releasing-*")) + list(orphan.parent.glob(".clearing-*"))
    with Workspace.open(root) as reader:
        results = [v for v in reader.list("audit").values()
                   if v.get("event") == "orphaned-release-clear-result"]
    for audit in audits:
        matches = any(p.name == audit["evidence"]
                      and hashlib.sha256(p.read_bytes()).hexdigest() == audit["file_sha256"]
                      for p in evidence)
        refused = (any(r.get("outcome") == "refused" for r in results)
                   and any(p.read_bytes() == SWAPPED for p in blocking)
                   and rejected and not effective)
        assert matches or refused, (
            "moved bytes were neither preserved under their exact audit nor "
            "quarantined under an audited refusal with writer access blocked")
    mismatch = any(p.name == a["evidence"] and
                   hashlib.sha256(p.read_bytes()).hexdigest() != a["file_sha256"]
                   for a in audits for p in evidence)
    if mismatch:
        assert rejected and not effective, "new effective writer admitted after corrupt custody"


def test_p2_unsafe_reproducer_two_admins_publish_same_orphan_twice(tmp_path, monkeypatch):
    """PASS = demonstrated duplicate/contradictory audits, NOT a safety pass."""
    if not _vulnerable_admin_lookup():
        pytest.skip("original unserialized audit lookup absent")
    root, _, orphan = _setup(tmp_path)
    audits, evidence = _overlap_admin_requests(root, monkeypatch)
    assert len(audits) == 2
    assert {a["reason"] for a in audits} == {"administrator A", "administrator B"}
    assert len({a["file_sha256"] for a in audits}) == 1
    assert len({a["evidence"] for a in audits}) == 2
    assert len(evidence) == 1 and not orphan.exists()


def test_p2_safety_overlapping_admins_have_unique_consistent_audit(
        tmp_path, monkeypatch, request):
    """Strict acceptance requirement: at most one disposition for same orphan/hash."""
    if _vulnerable_admin_lookup():
        request.node.add_marker(pytest.mark.xfail(
            strict=True, raises=AssertionError,
            reason="366d435 concurrent admins publish divergent audits for same orphan"))
    root, _, orphan = _setup(tmp_path)
    audits, evidence = _overlap_admin_requests(root, monkeypatch)
    assert len(audits) <= 1, "duplicate durable dispositions for the same orphan and digest"
    for audit in audits:
        custody = orphan.parent / audit["evidence"]
        assert custody.exists()
        assert hashlib.sha256(custody.read_bytes()).hexdigest() == audit["file_sha256"]
    if not audits:
        with pytest.raises(WorkspaceError):
            Workspace.open(root, writable=True)


def test_crash_just_after_custody_rename_retains_audit_and_exact_bytes(
        tmp_path, monkeypatch):
    """Process death immediately after rename must not lose audited forensic custody."""
    root, _, orphan = _setup(tmp_path)
    actual_replace = os.replace
    moves = []

    def die_after_rename(src, dst, *args, **kwargs):
        result = actual_replace(src, dst, *args, **kwargs)
        if Path(src) == orphan:
            moves.append(Path(dst))
            raise SystemExit("injected death after atomic evidence move")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", die_after_rename)
        with pytest.raises(SystemExit, match="after atomic evidence move"):
            Workspace.clear_orphaned_release(root, NAME, "former writer verified stopped")

    assert len(moves) == 1 and moves[0].read_bytes() == ORIGINAL
    audits = _audits(root)
    assert len(audits) == 1
    assert audits[0]["file_sha256"] == hashlib.sha256(ORIGINAL).hexdigest()
    # Rev. 2 moves directly to evidence; rev. 3 moves first to blocking custody.
    assert moves[0].name == audits[0]["evidence"] or moves[0].name.startswith(".clearing-")
    Workspace.clear_orphaned_release(root, NAME, "idempotent restart retry")
    assert len(_audits(root)) == 1
    evidence = orphan.parent / audits[0]["evidence"]
    assert evidence.read_bytes() == ORIGINAL
    assert not list(orphan.parent.glob(".clearing-*"))
    rejected, effective = _probe_effective_write(root)
    assert effective and not rejected
