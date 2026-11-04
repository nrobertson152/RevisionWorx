# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-019 rev. 3: an administrative orphan clear must bind the audited bytes to the evidence it
archives (DS P1, run 41393650443) and must be a single serialized transition per orphan (DS P2).

Every test here reproduces a counterexample against rev. 2 (366d435) before asserting the fix."""
import hashlib
import json
import os

import pytest

import revisionworx.workspace as wsmod
from revisionworx.workspace import OrphanClearConflict, RecordPut, Workspace, WorkspaceError

NAME = ".releasing-deadbeef"
ORIGINAL = b"{ torn"
RID = "prj_" + "9" * 32


def _orphan(share, data=ORIGINAL):
    w = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"
    os.replace(locks / "writer.lock", locks / NAME)
    (locks / NAME).write_bytes(data)
    w._lock_token = None
    w.close()
    return locks


def _events(share, event):
    meta = share / ".revisionworx"
    probe = Workspace(meta, {"workspace_id": "t"}, False, None)
    out = []
    for s in probe.journal_seqs():
        for item in probe.read_entry(s)["records"]:
            data = item.get("data") or {}
            if data.get("event") == event:
                out.append(data)
    return out


def _auths(share):
    return _events(share, "orphaned-release-cleared")


def _results(share):
    return _events(share, "orphaned-release-clear-result")


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _evidence_matches_audit(locks, share):
    """The safety invariant: every evidence file named by an authorisation holds exactly the
    audited bytes (or does not exist)."""
    for a in _auths(share):
        p = locks / a["evidence"]
        if p.exists() and _sha(p.read_bytes()) != a["file_sha256"]:
            return False
    return True


def _hook_replace(monkeypatch, on_src_suffix, action):
    real = wsmod.os.replace

    def hooked(src, dst, *a, **kw):
        if str(src).endswith(on_src_suffix):
            action(src, dst)
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", hooked)


# ----------------------------------------------------------------------------------- P1 (TOCTOU)
def test_ds_p1_bytes_replaced_between_verification_and_move(share, monkeypatch):
    """DS counterexample: the orphan's bytes are replaced after the verifying hash and before the
    pathname move. Rev. 2 archived the replacement under the original audit and removed the
    acquisition barrier. Now the replacement must never be covered by that audit and must keep
    blocking writers, with its bytes preserved."""
    locks = _orphan(share)
    swapped = b'{"user": "mallory", "token": null, "garbage": 1}'
    fired = []

    def swap(src, dst):
        if not fired:
            fired.append(1)
            tmp = locks / "swap.tmp"
            tmp.write_bytes(swapped)
            os.rename(tmp, src) if os.name != "nt" else os.replace(tmp, src)
    _hook_replace(monkeypatch, NAME, swap)
    try:
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    except WorkspaceError:
        pass
    monkeypatch.undo()
    assert fired
    assert _evidence_matches_audit(locks, share), "evidence differs from the audited SHA-256"
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)                    # barrier still in force
    preserved = [p.read_bytes() for p in locks.iterdir() if p.is_file()]
    assert swapped in preserved                                 # unauthorised bytes kept, not lost
    res = _results(share)
    assert len(res) == 1 and res[0]["outcome"] == "refused"     # recorded, not silent
    assert res[0]["observed_sha256"] == _sha(swapped)


def test_ds_p1_swap_during_acquisition_resume(share, monkeypatch):
    """Same race on the automatic path: a crash left an audited-but-unmoved clear, and the bytes are
    swapped while an acquirer is completing it. The acquirer must not become a writer."""
    locks = _orphan(share)
    # crash after the authorisation is durable, before custody
    _hook_replace(monkeypatch, NAME, lambda s, d: (_ for _ in ()).throw(SystemExit("died")))
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    assert len(_auths(share)) == 1
    fired = []

    def swap(src, dst):
        if not fired:
            fired.append(1)
            (locks / "swap.tmp").write_bytes(b"{ other torn")
            os.replace(locks / "swap.tmp", src) if os.name == "nt" else os.rename(locks / "swap.tmp", src)
    _hook_replace(monkeypatch, NAME, swap)
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)
    monkeypatch.undo()
    assert fired and _evidence_matches_audit(locks, share)
    assert Workspace.read_writer_lock(share) is None
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)


def test_custody_bytes_mutated_after_verification_never_reach_the_evidence(share, monkeypatch):
    """Races AFTER the check: the custody file is rewritten after its bytes were read and verified.
    The evidence is written from the verified buffer, so it still equals the audited digest, and
    the mutated custody file is quarantined as a blocking orphan rather than discarded."""
    locks = _orphan(share)
    real_publish = wsmod.atomicio.publish_exclusive
    fired = []

    def mutate_then_publish(path, data):
        if str(path).endswith(tuple(a["evidence"] for a in _auths(share))) and not fired:
            fired.append(1)
            custody = [p for p in locks.iterdir() if p.name.startswith(".clearing-")]
            assert len(custody) == 1
            custody[0].write_bytes(b"{ mutated after verification")
        return real_publish(path, data)
    monkeypatch.setattr(wsmod.atomicio, "publish_exclusive", mutate_then_publish)
    Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    assert fired
    auth = _auths(share)[0]
    assert (locks / auth["evidence"]).read_bytes() == ORIGINAL
    assert _evidence_matches_audit(locks, share)
    quarantined = [p for p in locks.iterdir() if p.name.startswith(".releasing-")]
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == b"{ mutated after verification"
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)


def test_swapped_in_live_lock_is_fenced_not_silently_archived(share, monkeypatch):
    """The replacement is a readable lock carrying a real token. It must be revoked in the journal
    (fail closed) instead of being archived under the administrator's authorisation."""
    locks = _orphan(share)
    victim = {"user": "v", "host": "h", "pid": 1, "token": "f" * 32, "acquired": "x"}

    def swap(src, dst):
        if not getattr(swap, "done", False):
            swap.done = True
            (locks / "swap.tmp").write_text(json.dumps(victim))
            os.replace(locks / "swap.tmp", src) if os.name == "nt" else os.rename(locks / "swap.tmp", src)
    _hook_replace(monkeypatch, NAME, swap)
    try:
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    except WorkspaceError:
        pass
    monkeypatch.undo()
    assert _evidence_matches_audit(locks, share)
    with Workspace.open(share, writable=True) as c:   # the scanner fences the token, then acquires
        c.commit("c.ok", [RecordPut("project", RID, {"by": "C"})])
    assert victim["token"] in Workspace._revoked_tokens(share / ".revisionworx")


# ----------------------------------------------------------------------------- crash windows
def _die_on(monkeypatch, predicate):
    real = wsmod.os.replace

    def hooked(src, dst, *a, **kw):
        if predicate(str(src), str(dst)):
            raise SystemExit("process died")
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", hooked)


def test_crash_after_custody_move_blocks_then_resumes(share, monkeypatch):
    """Window: the orphan is in custody (.clearing-*) but not yet verified/archived. The custody
    file must keep blocking until a completer finishes the authorised disposition exactly once."""
    locks = _orphan(share)
    real_publish = wsmod.atomicio.publish_exclusive

    def die_before_evidence(path, data):
        if "orphaned-" in str(path):
            raise SystemExit("died after custody")
        return real_publish(path, data)
    monkeypatch.setattr(wsmod.atomicio, "publish_exclusive", die_before_evidence)
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    assert not (locks / NAME).exists()
    assert [p for p in locks.iterdir() if p.name.startswith(".clearing-")]
    with Workspace.open(share, writable=True) as c:            # completes, then acquires
        c.commit("c.ok", [RecordPut("project", RID, {"by": "C"})])
    auth = _auths(share)[0]
    assert (locks / auth["evidence"]).read_bytes() == ORIGINAL
    assert not [p for p in locks.iterdir() if p.name.startswith((".clearing-", ".releasing-"))]
    res = _results(share)
    assert len(res) == 1 and res[0]["outcome"] == "completed"


def _die_on_result(monkeypatch):
    real = Workspace._publish_admin_entry.__func__

    def die_on_result(cls, meta, op, *a, **kw):
        if op == "workspace.orphan.clear.result":
            raise SystemExit("died before the terminal audit")
        return real(cls, meta, op, *a, **kw)
    monkeypatch.setattr(Workspace, "_publish_admin_entry", classmethod(die_on_result))


@pytest.mark.parametrize("resume", ["acquisition", "operator-retry"])
def test_crash_after_evidence_before_terminal_result(share, monkeypatch, resume):
    """Window: evidence written from verified bytes, custody file still present, no terminal audit.
    The custody file keeps blocking; resumption publishes exactly one terminal result."""
    locks = _orphan(share)
    _die_on_result(monkeypatch)
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    with pytest.raises(SystemExit):        # an acquirer that also dies at that step never writes
        Workspace.open(share, writable=True)
    monkeypatch.undo()
    assert Workspace.read_writer_lock(share) is None
    auth = _auths(share)[0]
    assert (locks / auth["evidence"]).read_bytes() == ORIGINAL
    assert [p for p in locks.iterdir() if p.name.startswith(".clearing-")]   # still blocking
    if resume == "operator-retry":
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    with Workspace.open(share, writable=True):
        pass
    res = _results(share)
    assert len(res) == 1 and res[0]["outcome"] == "completed" and len(_auths(share)) == 1
    assert not [p for p in locks.iterdir() if p.name.startswith((".clearing-", ".releasing-"))]
    assert (locks / auth["evidence"]).read_bytes() == ORIGINAL


def test_crash_after_refusal_audit_before_quarantine(share, monkeypatch):
    locks = _orphan(share)
    real = wsmod.os.replace
    swapped = []

    def hooked(src, dst, *a, **kw):
        s, d = str(src), str(dst)
        if s.endswith(NAME) and not swapped:
            swapped.append(1)
            (locks / NAME).write_bytes(b"{ swapped")          # replaced just before custody
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(wsmod.os, "replace", hooked)
    monkeypatch.setattr(Workspace, "_quarantine",
                        staticmethod(lambda *a: (_ for _ in ()).throw(SystemExit("died before quarantine"))))
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="writer confirmed stopped")
    monkeypatch.undo()
    res = _results(share)
    assert swapped and len(res) == 1 and res[0]["outcome"] == "refused"
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)                # resumed: quarantined, still blocked
    q = _results(share)[0]["quarantine"]
    assert (locks / q).read_bytes() == b"{ swapped" and len(_results(share)) == 1
    assert _evidence_matches_audit(locks, share)
    Workspace.clear_orphaned_release(share, q, reason="inspected the swapped file; dead writer")
    with Workspace.open(share, writable=True):
        pass


def test_refused_name_cannot_be_reauthorised_by_old_name(share, monkeypatch):
    locks = _orphan(share)
    _hook_replace(monkeypatch, NAME, lambda s, d: (locks / NAME).write_bytes(b"{ x"))
    try:
        Workspace.clear_orphaned_release(share, NAME, reason="r")
    except WorkspaceError:
        pass
    monkeypatch.undo()
    with pytest.raises(WorkspaceError, match="quarantined"):
        Workspace.clear_orphaned_release(share, NAME, reason="r again")


# ------------------------------------------------------------------------ P2 (serialization)
def test_ds_p2_overlapping_admin_clears_publish_one_authorisation(share, monkeypatch):
    """DS counterexample: admin B runs entirely inside admin A's window between A's lookup and A's
    publication. Rev. 2 published two authorisations with different reasons/evidence names for one
    orphan. Now exactly one authorisation and one terminal result exist; B adopts A's."""
    locks = _orphan(share)
    real = Workspace._publish_admin_entry.__func__
    state = {"nested": False}

    def reentrant(cls, meta, op, *a, **kw):
        if op == "workspace.orphan.clear" and not state["nested"]:
            state["nested"] = True
            state["b"] = Workspace.clear_orphaned_release(share, NAME, reason="admin A")
        return real(cls, meta, op, *a, **kw)
    monkeypatch.setattr(Workspace, "_publish_admin_entry", classmethod(reentrant))
    a = Workspace.clear_orphaned_release(share, NAME, reason="admin A")
    monkeypatch.undo()
    auths, res = _auths(share), _results(share)
    assert len(auths) == 1, auths
    assert len(res) == 1 and res[0]["outcome"] == "completed"
    assert a["clear_id"] == state["b"]["clear_id"] == auths[0]["clear_id"]
    assert a["adopted"] and not state["b"]["adopted"]       # the inner request won the CAS
    assert sum(1 for p in locks.iterdir() if p.name.startswith("writer.lock.orphaned-")) == 1
    assert _evidence_matches_audit(locks, share)


def test_concurrent_clears_in_processes(share):
    """Real processes racing clear_orphaned_release on one orphan: one authorisation, one result."""
    import multiprocessing as mp
    locks = _orphan(share)
    ctx = mp.get_context("spawn")
    import proc_helpers
    procs = [ctx.Process(target=proc_helpers.clear_orphan, args=(str(share), NAME, f"admin {i}"))
             for i in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    codes = sorted(p.exitcode for p in procs)
    assert codes == [0, 3, 3, 3], codes                       # one clear; three explicit conflicts
    assert len(_auths(share)) == 1 and len(_results(share)) == 1
    assert _results(share)[0]["outcome"] == "completed"
    assert sum(1 for p in locks.iterdir() if p.name.startswith("writer.lock.orphaned-")) == 1
    assert _evidence_matches_audit(locks, share)
    with Workspace.open(share, writable=True):
        pass


def test_different_reason_is_a_conflict_identical_retry_resumes(share, monkeypatch):
    """DS P2: a different reason (or administrator) for the same incarnation is rejected, not merged;
    the identical request resumes after a crash or lost acknowledgement, exactly once."""
    locks = _orphan(share)
    _die_on(monkeypatch, lambda s, d: s.endswith(NAME))
    with pytest.raises(SystemExit):
        Workspace.clear_orphaned_release(share, NAME, reason="first reason")
    monkeypatch.undo()
    with pytest.raises(OrphanClearConflict, match="first reason") as ei:
        Workspace.clear_orphaned_release(share, NAME, reason="a different reason")
    assert ei.value.outcome == "pending" and (locks / NAME).exists()   # nothing done for it
    monkeypatch.setattr(wsmod, "current_actor", lambda: {"user": "other", "host": "elsewhere", "pid": 1})
    with pytest.raises(OrphanClearConflict):                            # different administrator
        Workspace.clear_orphaned_release(share, NAME, reason="first reason")
    monkeypatch.undo()
    r = Workspace.clear_orphaned_release(share, NAME, reason="first reason")
    assert r["adopted"] is True and r["outcome"] == "completed"
    again = Workspace.clear_orphaned_release(share, NAME, reason="first reason")  # lost ACK replay
    assert again == r
    assert [a["reason"] for a in _auths(share)] == ["first reason"] and len(_results(share)) == 1
    with pytest.raises(OrphanClearConflict, match="completed"):
        Workspace.clear_orphaned_release(share, NAME, reason="late, different")
    assert not (locks / NAME).exists()


def test_recreated_identical_bytes_are_a_new_incarnation(share):
    """DS: name + digest cannot identify an incarnation. After a terminal result, identical bytes
    reappearing at the same name are a new orphan that needs its own authorisation."""
    locks = _orphan(share)
    first = Workspace.clear_orphaned_release(share, NAME, reason="r")
    (locks / NAME).write_bytes(ORIGINAL)
    with pytest.raises(WorkspaceError, match="orphaned"):
        Workspace.open(share, writable=True)
    second = Workspace.clear_orphaned_release(share, NAME, reason="r")
    assert second["clear_id"] != first["clear_id"] and not second["adopted"]
    assert len(_auths(share)) == 2 and len(_results(share)) == 2


def test_verify_detects_altered_or_missing_evidence(share):
    locks = _orphan(share)
    r = Workspace.clear_orphaned_release(share, NAME, reason="r")
    with Workspace.open(share) as ws:
        assert not [p for p in ws.verify()["problems"] if p["problem"].startswith("orphan-evidence")]
    (locks / r["evidence"]).write_bytes(b"tampered")
    with Workspace.open(share) as ws:
        assert {"problem": "orphan-evidence-altered", "clear_id": r["clear_id"],
                "evidence": r["evidence"]} in ws.verify()["problems"]
    (locks / r["evidence"]).unlink()
    with Workspace.open(share) as ws:
        assert any(p["problem"] == "orphan-evidence-missing" for p in ws.verify()["problems"])


def test_cli_reports_refusal_and_exits_nonzero(share, monkeypatch, capsys):
    from revisionworx import cli
    locks = _orphan(share)
    _hook_replace(monkeypatch, NAME, lambda s, d: (locks / NAME).write_bytes(b"{ changed"))
    code = cli.main(["-w", str(share), "lock", "clear-orphan", NAME, "--reason", "dead writer"])
    monkeypatch.undo()
    err = capsys.readouterr().err
    assert code == 2 and "NOT cleared" in err and ".releasing-" in err
    q = _results(share)[0]["quarantine"]
    code = cli.main(["-w", str(share), "lock", "clear-orphan", q, "--reason", "inspected"])
    assert code == 0 and "verified against the audited SHA-256" in capsys.readouterr().out


def test_rev2_authorisation_in_journal_is_completed_with_binding(share):
    """A rev. 2 authorisation (no clear_id) published before a crash on an older build is completed
    by rev. 3 under the same custody/verification rules, at its originally planned evidence name."""
    locks = _orphan(share)
    meta = share / ".revisionworx"
    legacy = {"event": "orphaned-release-cleared", "file": NAME, "file_sha256": _sha(ORIGINAL),
              "evidence": "writer.lock.orphaned-0123456789ab", "reason": "rev2", "time": "t", "by": {}}
    Workspace._publish_admin_entry(meta, "workspace.orphan.clear", legacy)
    with Workspace.open(share, writable=True):
        pass
    assert (locks / "writer.lock.orphaned-0123456789ab").read_bytes() == ORIGINAL
    res = _results(share)
    assert len(res) == 1 and res[0]["clear_id"] == "0123456789ab" and res[0]["outcome"] == "completed"


def test_fencing_revocation_names_the_archived_evidence(share):
    w = Workspace.create(share, "W")
    locks = share / ".revisionworx" / "locks"
    tok = w._lock_token
    os.replace(locks / "writer.lock", locks / ".releasing-abc")     # crash after release step 2
    w._lock_token = None
    with Workspace.open(share, writable=True) as c:
        audits = [a for a in c.list("audit").values() if a.get("event") == "writer-lock-broken"]
    assert audits and audits[-1]["previous_holder"]["token"] == tok
    assert (locks / audits[-1]["evidence"]).exists()
    w.close()


def test_stale_view_missing_file_is_redecided_not_reported_absent(share, monkeypatch):
    """Found under load: admin B decides against head H, admin A then authorises at H+1 and takes
    custody, and B finds the file gone. B must re-decide (adopt A's clear), not report 'missing'."""
    _orphan(share)
    real_read = wsmod.Path.read_bytes
    state = {"done": False}

    def read_bytes(self):
        if self.name == NAME and not state["done"]:
            state["done"] = True
            state["a"] = Workspace.clear_orphaned_release(share, NAME, reason="admin A")
        return real_read(self)
    monkeypatch.setattr(wsmod.Path, "read_bytes", read_bytes)
    b = Workspace.clear_orphaned_release(share, NAME, reason="admin A")   # same request, retried
    monkeypatch.undo()
    assert b["adopted"] and b["clear_id"] == state["a"]["clear_id"] and b["outcome"] == "completed"
    assert len(_auths(share)) == 1 and len(_results(share)) == 1
