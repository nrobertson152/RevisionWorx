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

    def test_malformed_orphan_kept_as_evidence_but_cannot_block_new_authority(self):
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
