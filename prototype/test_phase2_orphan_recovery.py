"""ADR-019 independent orphan/restart safety probes against actual Phase 2 source.

D656884 adds abandoned .releasing-* custody detection on each writer acquire.
Tests focus on durability, malformed evidence and interrupted revocation.
These are LOCAL filename simulations, NOT actual SMB crash/power-loss tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PHASE2 = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(PHASE2, "actual Phase 2 source required")
class Phase2OrphanedReleaseSafety(unittest.TestCase):
    def _load(self):
        source = Path(PHASE2).resolve()
        sys.path.insert(0, str(source))
        from revisionworx.workspace import (LockLostError, RecordPut,
                                            Workspace, WorkspaceLockedError)
        self.assertEqual(Path(sys.modules[Workspace.__module__].__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        return Workspace, RecordPut, LockLostError, WorkspaceLockedError

    def _displaced_b(self, Workspace, root):
        a = Workspace.create(root / "workspace", "InterruptedRelease")
        meta = a.meta_dir
        Workspace.break_writer_lock(meta, a._lock_token, "admin overrides A")
        b = Workspace.open(meta, writable=True)
        token_b = b._lock_token
        lock = meta / "locks" / "writer.lock"
        orphan = lock.with_name(".releasing-test-interrupted")
        os.replace(lock, orphan)  # precise phase after A moved B's lock, before restore
        self.assertIsNone(Workspace.read_writer_lock(meta))
        return a, b, meta, orphan, token_b

    def test_orphan_owner_revoked_once_before_successor_can_commit(self):
        Workspace, RecordPut, LockLostError, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            a, b, meta, orphan, token_b = self._displaced_b(Workspace, Path(directory))
            try:
                with Workspace.open(meta, writable=True) as c:
                    self.assertNotEqual(c._lock_token, token_b)
                    with self.assertRaises(LockLostError):
                        b.commit("b.revision", [
                            RecordPut("project", "prj_" + "a" * 32, {"writer": "B"})])
                    seq = c.commit("c.revision", [
                        RecordPut("project", "prj_" + "c" * 32, {"writer": "C"})])
                    self.assertEqual(c.get("project", "prj_" + "c" * 32)["_rev"], seq)
                with Workspace.open(meta) as reader:
                    revocations = [reader.read_entry(n)
                                   for n in reader.journal_seqs()
                                   if reader.read_entry(n).get("revokes") == token_b]
                    self.assertEqual(len(revocations), 1)
                    self.assertIn("orphaned", revocations[0]["records"][0]["data"]["reason"])
                    self.assertIsNone(reader.get("project", "prj_" + "a" * 32))
                self.assertFalse(orphan.exists())
                self.assertTrue(any(f.name.startswith("writer.lock.orphaned-")
                                    for f in (meta / "locks").iterdir()))
                # A later writer must not duplicate the revocation or reinstate B.
                with Workspace.open(meta, writable=True) as d:
                    d.commit("d.revision", [
                        RecordPut("project", "prj_" + "d" * 32, {"writer": "D"})])
                with Workspace.open(meta) as reader:
                    self.assertEqual(sum(reader.read_entry(n).get("revokes") == token_b
                                         for n in reader.journal_seqs()), 1)
            finally:
                b._lock_token = None
                b.close()
                a._lock_token = None
                a.close()

    def test_malformed_orphan_archived_without_token_fence_UNSAFE_control(self):
        """UNSAFE control only: the current implementation grants authority
        without identifying/revoking the displaced owner. The separate
        preflighted-writer proof shows both writers can commit. Do not treat
        this successful test as safety acceptance.
        """
        Workspace, RecordPut, _, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            first = Workspace.create(Path(directory) / "workspace", "CorruptEvidence")
            meta = first.meta_dir
            first.close()
            malformed = meta / "locks" / ".releasing-unreadable"
            malformed.write_text("{not-json", encoding="utf-8")
            with Workspace.open(meta, writable=True) as new:
                new.commit("new.owner", [
                    RecordPut("project", "prj_" + "e" * 32, {"valid": True})])
            self.assertFalse(malformed.exists())
            evidence = list((meta / "locks").glob("writer.lock.orphaned-*"))
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].read_text(encoding="utf-8"), "{not-json")
            with Workspace.open(meta) as r:
                self.assertTrue(r.get("project", "prj_" + "e" * 32)["valid"])


    @unittest.expectedFailure
    def test_unreadable_orphan_must_not_permit_preflighted_old_writer(self):
        """UNSAFE hypothesis: unreadable displaced token means no journal fence.

        B has already passed the final token check. A displaces its lock
        into an unreadable .releasing-*; C acquires a fresh writer.lock.
        If C only archives unreadable evidence, B's in-flight publication
        can still land afterwards and take effect without revocation.
        The safety oracle demands that B be fenced, not merely that B's
        next preflight would fail.
        """
        Workspace, RecordPut, LockLostError, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            a = Workspace.create(Path(directory) / "workspace", "UnreadableOrphan")
            meta = a.meta_dir
            Workspace.break_writer_lock(meta, a._lock_token, "admin replaces A")
            b = Workspace.open(meta, writable=True)
            b_token = b._lock_token
            original_check = b._check_lock_still_held
            successor = []
            events = []
            try:
                def after_successful_preflight():
                    original_check()
                    events.append("B-checked")
                    lock = meta / "locks" / "writer.lock"
                    orphan = lock.with_name(".releasing-corrupted-holder")
                    os.replace(lock, orphan)
                    orphan.write_text("{unreadable-token", encoding="utf-8")
                    with Workspace.open(meta, writable=True) as c:
                        events.append("C-acquired")
                        c.commit("C.earlier", [
                            RecordPut("project", "prj_" + "c" * 32,
                                      {"author": "C"})])
                    events.append("C-committed")
                b._check_lock_still_held = after_successful_preflight
                with self.assertRaises(LockLostError):
                    b.commit("B.after-preflight", [
                        RecordPut("project", "prj_" + "b" * 32,
                                  {"author": "B"})], txid="b" * 32)
                self.assertEqual(events, ["B-checked", "C-acquired", "C-committed"])
                with Workspace.open(meta) as reader:
                    self.assertIsNone(reader.get("project", "prj_" + "b" * 32))
                    self.assertIsNotNone(reader.get("project", "prj_" + "c" * 32))
                    self.assertIn(b_token, Workspace._revoked_tokens(meta))
            finally:
                b._lock_token = None
                b.close()
                a._lock_token = None
                a.close()


    def test_unreadable_orphan_permits_two_effective_writers_UNSAFE_reproduction(self):
        """UNSAFE demonstration: a malformed orphan is archived without revoking B.

        Confirm distinct *effective* journal entries from both B (preflighted
        before displacement) and C (freshly acquired), not merely an xfail.
        This test PASSES because it proves the unsafe state currently exists.
        """
        Workspace, RecordPut, _, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            a = Workspace.create(Path(directory) / "ws", "UnreadableOrphanProof")
            meta = a.meta_dir
            Workspace.break_writer_lock(meta, a._lock_token, "admin revokes A")
            b = Workspace.open(meta, writable=True)
            token_b = b._lock_token
            check = b._check_lock_still_held
            events = []
            try:
                def displace_and_commit_c():
                    check()
                    events.append("B-preflight")
                    lock = meta / "locks" / "writer.lock"
                    evidence = meta / "locks" / ".releasing-corrupt"
                    os.replace(lock, evidence)
                    evidence.write_text("{not-valid-json", encoding="utf-8")
                    with Workspace.open(meta, writable=True) as c:
                        events.append("C-acquired")
                        c.commit("C.document", [
                            RecordPut("project", "prj_" + "c" * 32,
                                      {"writer": "C"})], txid="c" * 32)
                    events.append("C-committed")
                b._check_lock_still_held = displace_and_commit_c
                b_seq = b.commit("B.document.after-C", [
                    RecordPut("project", "prj_" + "b" * 32, {"writer": "B"})],
                    txid="b" * 32)
                self.assertEqual(events, ["B-preflight", "C-acquired", "C-committed"])
                with Workspace.open(meta) as reader:
                    self.assertEqual(reader.get("project", "prj_" + "b" * 32)["writer"], "B")
                    self.assertEqual(reader.get("project", "prj_" + "c" * 32)["writer"], "C")
                    self.assertEqual(reader.transaction_outcome("b" * 32),
                                     {"state": "committed", "seq": b_seq})
                    self.assertNotIn(token_b, Workspace._revoked_tokens(meta))
            finally:
                b._lock_token = None
                b.close()
                a._lock_token = None
                a.close()

    def test_concurrent_successor_acquirers_single_winner_after_orphan(self):
        """Two local clients start together while abandoned B custody is pending."""
        import queue
        import threading
        Workspace, RecordPut, LockLostError, WorkspaceLockedError = self._load()
        with tempfile.TemporaryDirectory() as directory:
            a, b, meta, orphan, token_b = self._displaced_b(
                Workspace, Path(directory))
            start = threading.Barrier(3)
            release = threading.Event()
            outcomes = queue.Queue()
            workers = []
            def acquire_and_hold(identifier):
                try:
                    start.wait(timeout=15)
                    try:
                        with Workspace.open(meta, writable=True) as writer:
                            outcomes.put(("won", identifier, writer._lock_token))
                            if not release.wait(15):
                                raise AssertionError("concurrent test release timed out")
                            writer.commit("concurrent.owner", [
                                RecordPut("project", "prj_" + identifier * 32,
                                          {"winner": identifier})])
                    except WorkspaceLockedError:
                        outcomes.put(("conflict", identifier, None))
                except BaseException as exc:
                    outcomes.put(("error", identifier, repr(exc)))
            try:
                for identifier in ("d", "e"):
                    t = threading.Thread(target=acquire_and_hold,
                                         args=(identifier,), daemon=True)
                    workers.append(t)
                    t.start()
                start.wait(timeout=15)
                observed = [outcomes.get(timeout=20) for _ in workers]
                self.assertCountEqual([o[0] for o in observed], ["won", "conflict"])
                release.set()
                for t in workers:
                    t.join(15)
                    self.assertFalse(t.is_alive())
                with Workspace.open(meta) as reader:
                    revocations = [reader.read_entry(i)
                                   for i in reader.journal_seqs()
                                   if reader.read_entry(i).get("revokes") == token_b]
                    self.assertEqual(len(revocations), 1)
                    winner = next(o[1] for o in observed if o[0] == "won")
                    self.assertIsNotNone(reader.get("project", "prj_" + winner * 32))
                    self.assertIsNone(reader.get("project", "prj_" +
                                                ({"d", "e"} - {winner}).pop() * 32))
                with self.assertRaises(LockLostError):
                    b.commit("B.late", [
                        RecordPut("project", "prj_" + "b" * 32, {"wrong": True})])
                self.assertFalse(orphan.exists())
            finally:
                release.set()
                for t in workers:
                    t.join(5)
                b._lock_token = None
                b.close()
                a._lock_token = None
                a.close()

    def test_crash_after_durable_revocation_before_orphan_custody(self):
        """Restart must not create a second revocation after a file move crashes."""
        Workspace, RecordPut, LockLostError, _ = self._load()
        import revisionworx.workspace as wsmod
        with tempfile.TemporaryDirectory() as directory:
            a, b, meta, orphan, token_b = self._displaced_b(
                Workspace, Path(directory))
            real_replace = wsmod.os.replace
            try:
                def die_before_evidence_move(src, dst, *args, **kwargs):
                    if (Path(src).name.startswith(".releasing-")
                            and Path(dst).name.startswith("writer.lock.orphaned-")):
                        raise SystemExit("crash after journal fence")
                    return real_replace(src, dst, *args, **kwargs)
                with patch.object(wsmod.os, "replace",
                                  side_effect=die_before_evidence_move):
                    with self.assertRaises(SystemExit):
                        Workspace.open(meta, writable=True)
                self.assertIn(token_b, Workspace._revoked_tokens(meta))
                leaked = Workspace.read_writer_lock(meta)
                self.assertIsNotNone(leaked)
                self.assertTrue(orphan.exists())
                Workspace.break_writer_lock(meta, leaked["token"],
                                            "recover from process death after durable revocation")
                with Workspace.open(meta, writable=True) as d:
                    d.commit("safe.post-recovery", [
                        RecordPut("project", "prj_" + "f" * 32, {"safe": True})])
                with Workspace.open(meta) as reader:
                    self.assertEqual(sum(
                        reader.read_entry(i).get("revokes") == token_b
                        for i in reader.journal_seqs()), 1)
                    self.assertIsNotNone(reader.get("project", "prj_" + "f" * 32))
                self.assertFalse(orphan.exists())
                with self.assertRaises(LockLostError):
                    b.commit("B.after-recovery", [
                        RecordPut("project", "prj_" + "b" * 32, {"unsafe": True})])
            finally:
                b._lock_token = None
                b.close()
                a._lock_token = None
                a.close()

    def test_interrupted_orphan_revocation_fails_closed_until_admin_recovery(self):
        Workspace, RecordPut, LockLostError, WorkspaceLockedError = self._load()
        with tempfile.TemporaryDirectory() as directory:
            a, b, meta, orphan, token_b = self._displaced_b(Workspace, Path(directory))
            try:
                # C publishes its own writer.lock, then dies while trying to
                # attest B's orphan. No usable client handle was returned.
                with patch.object(Workspace, "_publish_revocation",
                                  side_effect=SystemExit("simulated process death")):
                    with self.assertRaises(SystemExit):
                        Workspace.open(meta, writable=True)
                leaked = Workspace.read_writer_lock(meta)
                self.assertIsNotNone(leaked)
                self.assertTrue(orphan.exists())
                with self.assertRaises(WorkspaceLockedError):
                    Workspace.open(meta, writable=True)
                with self.assertRaises(LockLostError):
                    b.commit("b.late", [
                        RecordPut("project", "prj_" + "b" * 32, {"unsafe": True})])
                Workspace.break_writer_lock(meta, leaked["token"],
                                            "recover interrupted orphan scan")
                with Workspace.open(meta, writable=True) as recovered:
                    seq = recovered.commit("recovered.owner", [
                        RecordPut("project", "prj_" + "f" * 32, {"safe": True})])
                    self.assertEqual(recovered.get("project", "prj_" + "f" * 32)["_rev"], seq)
                with Workspace.open(meta) as r:
                    self.assertEqual(sum(r.read_entry(n).get("revokes") == token_b
                                         for n in r.journal_seqs()), 1)
                    self.assertIsNone(r.get("project", "prj_" + "b" * 32))
                self.assertFalse(orphan.exists())
            finally:
                b._lock_token = None
                b.close()
                a._lock_token = None
                a.close()


if __name__ == "__main__":
    unittest.main()
