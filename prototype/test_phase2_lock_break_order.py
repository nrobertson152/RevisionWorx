"""Actual-Phase-2 SAFETY acceptance for administrative journal-first revocation.

The older Phase 2 code (pre-ADR-015 rev.2) allowed an effective entry between
lock rename and audit. Those old-branch defect reproductions are preserved in
GitHub Actions 41381819173 / 41382608414; this suite asserts the reverse:
a token revoked in the authoritative journal can never create an effective
entry. Run with REVISIONWORX_PHASE2_CHECKOUT to the current Phase 2 checkout.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

PHASE2 = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(PHASE2, "requires actual phase2-documents checkout")
class Phase2AdministrativeBreakAcceptance(unittest.TestCase):
    def _load(self):
        source = Path(PHASE2).expanduser().resolve()
        self.assertTrue((source / "revisionworx" / "workspace.py").is_file())
        sys.path.insert(0, str(source))
        from revisionworx.workspace import LockLostError, RecordPut, Workspace
        from revisionworx import workspace as workspace_module
        self.assertEqual(Path(sys.modules[Workspace.__module__].__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        return Workspace, RecordPut, LockLostError, workspace_module

    def test_stale_writer_after_revocation_never_effective(self):
        Workspace, RecordPut, LockLostError, _ = self._load()
        with tempfile.TemporaryDirectory() as td:
            stale = Workspace.create(Path(td) / "work", "Fencing")
            token = Workspace.read_writer_lock(stale.meta_dir)["token"]
            rid = "prj_" + "b" * 32
            original_check = stale._check_lock_still_held

            def break_after_successful_preflight():
                original_check()
                Workspace.break_writer_lock(stale.meta_dir, token, "admin revoked stalled writer")

            try:
                stale._check_lock_still_held = break_after_successful_preflight
                txid = "a" * 32
                with self.assertRaises(LockLostError):
                    stale.commit("test.revoked", [
                        RecordPut("project", rid, {"name": "cannot commit"})],
                        txid=txid)
                with Workspace.open(stale.meta_dir) as reader:
                    self.assertIsNone(reader.get("project", rid))
                    self.assertNotIn(rid, reader.list("project"))
                    self.assertIn(reader.transaction_outcome(txid)["state"],
                                  ("not-found", "fenced"))
                    audit = [reader.read_entry(s) for s in reader.journal_seqs()
                             if reader.read_entry(s).get("op") == "workspace.lock.break"]
                    self.assertEqual(len(audit), 1)
                    self.assertEqual(audit[0].get("revokes"), token)
            finally:
                stale._lock_token = None
                stale.close()

    def test_revocation_committed_before_evidence_move_fences_paused_writer(self):
        """Pause admin after journal revocation, before writer.lock os.replace."""
        Workspace, RecordPut, LockLostError, workmod = self._load()
        with tempfile.TemporaryDirectory() as td:
            stale = Workspace.create(Path(td) / "work", "Ordering")
            token = Workspace.read_writer_lock(stale.meta_dir)["token"]
            rid = "prj_" + "c" * 32
            preflight = threading.Event()
            revocation_published = threading.Event()
            release_admin = threading.Event()
            writer_done = threading.Event()
            errors = []
            outcomes = []
            original_check = stale._check_lock_still_held
            real_replace = workmod.os.replace

            def check_then_pause():
                original_check()
                preflight.set()
                if not revocation_published.wait(15):
                    raise AssertionError("administrator did not publish revocation")

            def pause_evidence_move(source, destination):
                if (threading.current_thread().name == "administrator"
                        and Path(source).name == "writer.lock"
                        and "writer.lock.broken-" in Path(destination).name):
                    revocation_published.set()
                    if not release_admin.wait(15):
                        raise AssertionError("administrator evidence move release timed out")
                return real_replace(source, destination)

            def writer():
                try:
                    stale.commit("test.revoked.before.evidence.move", [
                        RecordPut("project", rid, {"name": "must never be visible"})],
                        txid="d" * 32)
                    outcomes.append("unexpected-commit")
                except LockLostError:
                    outcomes.append("fenced")
                except Exception as exc:
                    errors.append(("writer", repr(exc)))
                finally:
                    writer_done.set()

            def admin():
                try:
                    Workspace.break_writer_lock(stale.meta_dir, token,
                                                "admin journals revocation before evidence")
                except Exception as exc:
                    errors.append(("admin", repr(exc)))
                finally:
                    release_admin.set()

            writer_thread = threading.Thread(target=writer, name="revoked-writer")
            admin_thread = threading.Thread(target=admin, name="administrator")
            try:
                stale._check_lock_still_held = check_then_pause
                with patch.object(workmod.os, "replace", side_effect=pause_evidence_move):
                    writer_thread.start()
                    self.assertTrue(preflight.wait(15), "writer never reached token preflight")
                    admin_thread.start()
                    self.assertTrue(revocation_published.wait(15),
                                    "admin did not journal revocation before evidence move")
                    self.assertTrue(writer_done.wait(15),
                                    "revoked writer did not complete while admin paused")
                    release_admin.set()
                    writer_thread.join(15)
                    admin_thread.join(15)
                self.assertFalse(writer_thread.is_alive())
                self.assertFalse(admin_thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(outcomes, ["fenced"])
                with Workspace.open(stale.meta_dir) as reader:
                    self.assertIsNone(reader.get("project", rid))
                    self.assertIn(reader.transaction_outcome("d" * 32)["state"],
                                  ("not-found", "fenced"))
                    entries = [reader.read_entry(s) for s in reader.journal_seqs()]
                    revocations = [e for e in entries if e.get("revokes") == token]
                    self.assertEqual(len(revocations), 1)
                    stale_entries = [e for e in entries
                                     if e["op"] == "test.revoked.before.evidence.move"]
                    for entry in stale_entries:
                        self.assertGreater(entry["seq"], revocations[0]["seq"])
                        self.assertTrue(reader.is_fenced(entry))
            finally:
                release_admin.set()
                revocation_published.set()
                if writer_thread.is_alive():
                    writer_thread.join(5)
                if admin_thread.is_alive():
                    admin_thread.join(5)
                stale._lock_token = None
                stale.close()


if __name__ == "__main__":
    unittest.main()
