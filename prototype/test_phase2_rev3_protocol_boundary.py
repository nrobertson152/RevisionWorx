"""Independent ADR-019 rev.3 protocol-only crash and authority-boundary regression.

Runs the actual Phase 2 source pinned by CI; not the implementation owner's test
module. Fixtures model durable orphan files from interrupted normal releases.
No test performs a surprise rename of a live writer.lock onto an existing private
custody pathname: that requires an out-of-protocol actor and is a distinct threat.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
ID_B = "prj_" + "b" * 32
ID_C = "prj_" + "c" * 32
A_TOKEN = "a" * 32
ORPHAN = ".releasing-a11ce5"
TORN = b"{unreadable orphan"


@unittest.skipUnless(SOURCE, "requires exact production Phase 2 source")
class Phase2ProtocolOnlyBoundary(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "ws"
        self.b = ws.Workspace.create(root, "Protocol boundary")
        self.meta = self.b.meta_dir
        self.locks = self.meta / "locks"
        self.b_token = self.b._lock_token
        self.addCleanup(self._cleanup_writer)

    def _cleanup_writer(self):
        self.b._lock_token = None
        self.b.close()

    def _pause_preflight(self, callback):
        """B has passed _check_lock_still_held; callback interleaves before publication."""
        previous, entered = self.b._current_rev, []
        def paused(kind, rid):
            if not entered:
                entered.append(1)
                callback()
            return previous(kind, rid)
        self.b._current_rev = paused
        with self.assertRaises(self.ws.LockLostError):
            self.b.commit("B", [
                self.ws.RecordPut("project", ID_B, {"by": "B"}, expect_rev=0)
            ])
        self.assertEqual(entered, [1])
        with self.ws.Workspace.open(self.meta) as r:
            self.assertIsNone(r.get("project", ID_B))
            self.assertEqual(r.get("project", ID_C)["by"], "C")
            self.assertIn(self.b_token, self.ws.Workspace._revoked_tokens(self.meta))

    def _install_crash_orphans(self):
        """Files left by interrupted releases; B's writer.lock is displaced to .releasing."""
        (self.locks / ".releasing-aaaa").write_text(
            json.dumps({"token": A_TOKEN, "user": "A", "host": "host", "pid": 1})
        )
        os.replace(self.locks / "writer.lock", self.locks / ".releasing-bbbb")

    def _acquire_and_commit_successor(self):
        with self.ws.Workspace.open(self.meta, writable=True) as c:
            c.commit("C", [
                self.ws.RecordPut("project", ID_C, {"by": "C"}, expect_rev=0)
            ])

    def _assert_unique_fencing(self):
        with self.ws.Workspace.open(self.meta) as r:
            revocations = r._revocations()
        self.assertEqual(sum(tok == self.b_token for _, tok in revocations), 1)
        self.assertEqual(sum(tok == A_TOKEN for _, tok in revocations), 1)
        self.assertFalse(list(self.locks.glob(".releasing-*")))
        self.assertFalse(list(self.locks.glob(".fencing-*")))

    def test_protocol_only_crash_after_first_custody_move_fences_all_before_C(self):
        """Crash at .releasing-A -> .fencing-A, then next writer must revoke A and B."""
        real_replace = self.ws.os.replace
        hit = []
        def crash_after_custody(src, dst, *args, **kwargs):
            if (Path(src).name == ".releasing-aaaa"
                    and Path(dst).name.startswith(".fencing-") and not hit):
                real_replace(src, dst, *args, **kwargs)
                hit.append(1)
                raise SystemExit("fencer died immediately after custody move")
            return real_replace(src, dst, *args, **kwargs)

        def interleave():
            self._install_crash_orphans()  # stopped releaser's normal move
            with patch.object(self.ws.os, "replace", side_effect=crash_after_custody):
                with self.assertRaises(SystemExit):
                    self.ws.Workspace.open(self.meta, writable=True)
            self.assertEqual(hit, [1])
            self.assertFalse((self.locks / "writer.lock").exists())
            self.assertEqual(len(list(self.locks.glob(".fencing-*"))), 1)
            self.assertTrue((self.locks / ".releasing-bbbb").exists())
            self._acquire_and_commit_successor()
            self._assert_unique_fencing()

        self._pause_preflight(interleave)

    def test_protocol_only_crash_after_first_revocation_fences_all_before_C(self):
        """A revoked before archive, B still orphaned: no B effect after restart."""
        real_replace = self.ws.os.replace
        hit = []
        def crash_before_archival(src, dst, *args, **kwargs):
            if (Path(src).name.startswith(".fencing-")
                    and Path(dst).name.startswith("writer.lock.orphaned-")
                    and not hit):
                hit.append(1)
                raise SystemExit("fencer died after journal revocation")
            return real_replace(src, dst, *args, **kwargs)

        def interleave():
            self._install_crash_orphans()
            with patch.object(self.ws.os, "replace", side_effect=crash_before_archival):
                with self.assertRaises(SystemExit):
                    self.ws.Workspace.open(self.meta, writable=True)
            self.assertEqual(hit, [1])
            self.assertFalse((self.locks / "writer.lock").exists())
            self.assertTrue(list(self.locks.glob(".fencing-*")))
            self.assertTrue((self.locks / ".releasing-bbbb").exists())
            self.assertIn(A_TOKEN, self.ws.Workspace._revoked_tokens(self.meta))
            self._acquire_and_commit_successor()
            self._assert_unique_fencing()

        self._pause_preflight(interleave)


@unittest.skipUnless(SOURCE, "requires exact production Phase 2 source")
class Phase2AdministratorConflictBoundary(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "ws"
        owner = ws.Workspace.create(root, "Admin conflict")
        self.meta = owner.meta_dir
        self.locks = self.meta / "locks"
        self.orphan = self.locks / ORPHAN
        os.replace(self.locks / "writer.lock", self.orphan)
        self.orphan.write_bytes(TORN)
        owner._lock_token = None
        owner.close()

    def _entries(self, op):
        with self.ws.Workspace.open(self.meta) as r:
            return [r.read_entry(s) for s in r.journal_seqs()
                    if r.read_entry(s)["op"] == op]

    def test_different_administrator_cannot_appropriate_pending_clear(self):
        """Crash after durable authority, conflicting requests cannot adopt it silently."""
        publish = self.ws.Workspace._publish_admin_entry
        triggered = []
        def die_after_authorization(meta, op, data, *args, **kwargs):
            seq = publish(meta, op, data, *args, **kwargs)
            if op == "workspace.orphan.clear" and not triggered:
                triggered.append(1)
                raise SystemExit("authorization durable, before custody")
            return seq

        with patch.object(self.ws.Workspace, "_publish_admin_entry",
                          side_effect=die_after_authorization):
            with self.assertRaises(SystemExit):
                self.ws.Workspace.clear_orphaned_release(
                    self.meta, ORPHAN, "supervisor A confirmed stopped"
                )
        self.assertEqual(triggered, [1])
        self.assertTrue(self.orphan.exists())
        self.assertEqual(len(self._entries("workspace.orphan.clear")), 1)
        self.assertEqual(len(self._entries("workspace.orphan.clear.result")), 0)

        with self.assertRaises(self.ws.OrphanClearConflict) as difference:
            self.ws.Workspace.clear_orphaned_release(
                self.meta, ORPHAN, "supervisor B did not inspect")
        self.assertEqual(difference.exception.outcome, "pending")
        with patch.object(self.ws, "current_actor",
                          return_value={"user": "other", "host": "remote", "pid": 99}):
            with self.assertRaises(self.ws.OrphanClearConflict):
                self.ws.Workspace.clear_orphaned_release(
                    self.meta, ORPHAN, "supervisor A confirmed stopped")

        self.assertTrue(self.orphan.exists())
        self.assertEqual(len(self._entries("workspace.orphan.clear")), 1)
        self.assertEqual(len(self._entries("workspace.orphan.clear.result")), 0)

        recovered = self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN, "supervisor A confirmed stopped")
        retry = self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN, "supervisor A confirmed stopped")
        self.assertEqual(recovered, retry)
        self.assertTrue(recovered["adopted"])
        self.assertEqual(recovered["outcome"], "completed")
        self.assertEqual(len(self._entries("workspace.orphan.clear")), 1)
        self.assertEqual(len(self._entries("workspace.orphan.clear.result")), 1)
        self.assertEqual((self.locks / recovered["evidence"]).read_bytes(), TORN)

    def test_conflicting_completed_clear_never_changes_reason_or_archive(self):
        """A late request may not retroactively authorize an already completed action."""
        original = self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN, "original inspected supervisor")
        evidence = self.locks / original["evidence"]
        with self.assertRaises(self.ws.OrphanClearConflict) as conflict:
            self.ws.Workspace.clear_orphaned_release(
                self.meta, ORPHAN, "different supervisor")
        self.assertEqual(conflict.exception.outcome, "completed")
        self.assertEqual(len(self._entries("workspace.orphan.clear")), 1)
        self.assertEqual(len(self._entries("workspace.orphan.clear.result")), 1)
        self.assertEqual(evidence.read_bytes(), TORN)

    def test_recreated_identical_evidence_is_new_clear_incarnation(self):
        """Name+bytes are not durable identity after a completed recovery."""
        first = self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN, "operator inspected and confirmed stopped")
        self.assertEqual(first["outcome"], "completed")
        self.orphan.write_bytes(TORN)  # new observed orphan at the same pathname
        with self.assertRaises(self.ws.WorkspaceError):
            self.ws.Workspace.open(self.meta, writable=True)
        second = self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN, "operator inspected and confirmed stopped")
        self.assertNotEqual(first["clear_id"], second["clear_id"])
        self.assertFalse(second["adopted"])
        self.assertEqual(len(self._entries("workspace.orphan.clear")), 2)
        self.assertEqual(len(self._entries("workspace.orphan.clear.result")), 2)
        self.assertEqual((self.locks / first["evidence"]).read_bytes(), TORN)
        self.assertEqual((self.locks / second["evidence"]).read_bytes(), TORN)

    def test_verify_reports_post_archive_evidence_tampering_and_removal(self):
        """Evidence is bound to an audit on publication; later tampering is detected."""
        result = self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN, "operator inspected and confirmed stopped")
        evidence = self.locks / result["evidence"]
        with self.ws.Workspace.open(self.meta) as reader:
            initial = reader.verify()["problems"]
        self.assertFalse([p for p in initial if p["problem"].startswith("orphan-evidence")])
        evidence.write_bytes(b"unauthorized modification")
        with self.ws.Workspace.open(self.meta) as reader:
            altered = reader.verify()["problems"]
        self.assertTrue(any(p["problem"] == "orphan-evidence-altered"
                            and p["clear_id"] == result["clear_id"] for p in altered))
        evidence.unlink()
        with self.ws.Workspace.open(self.meta) as reader:
            missing = reader.verify()["problems"]
        self.assertTrue(any(p["problem"] == "orphan-evidence-missing"
                            and p["clear_id"] == result["clear_id"] for p in missing))



if __name__ == "__main__":
    unittest.main()
