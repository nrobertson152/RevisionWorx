"""Deterministic actual-Phase-2 administrative lock-break ordering regression.

Tests the precise window AFTER the administrator moves writer.lock but BEFORE
the break audit is committed. ADR-015 fences entries after the audit sequence,
so a revoked writer's entry published in this window may still take effect.

Run with REVISIONWORX_PHASE2_CHECKOUT=/path/to/phase2-documents from the
research checkout. Skips without a separate, actual Phase 2 checkout.

This is a DEFECT REPRODUCTION: the test passes only if the unsafe behavior
can be produced. It is NOT a passing safety acceptance test.
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
class Phase2AdministrativeBreakOrderingProbe(unittest.TestCase):
    def test_writer_publishes_between_lock_rename_and_audit(self):
        source = Path(PHASE2).expanduser().resolve()
        if not (source / "revisionworx" / "workspace.py").is_file():
            self.fail("REVISIONWORX_PHASE2_CHECKOUT does not contain revisionworx/workspace.py")
        sys.path.insert(0, str(source))
        from revisionworx.workspace import Workspace, RecordPut

        self.assertEqual(Path(sys.modules[Workspace.__module__].__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        with tempfile.TemporaryDirectory() as td:
            stale = Workspace.create(Path(td) / "project", "AdminBreakRace")
            preflight = threading.Event()
            renamed = threading.Event()
            stale_done = threading.Event()
            admin_go = threading.Event()
            errors = []
            stale_result = []
            rid = "prj_" + "8" * 32
            token = Workspace.read_writer_lock(stale.meta_dir)["token"]
            original_check = stale._check_lock_still_held
            original_open = Workspace.open
            admin_thread = None
            writer_thread = None

            def checked_then_wait():
                original_check()
                preflight.set()
                if not renamed.wait(15):
                    raise TimeoutError("admin did not rename lock in time")

            def intercept_open(*args, **kwargs):
                if threading.current_thread() is admin_thread and kwargs.get("writable"):
                    # break_writer_lock() calls cls.open() only AFTER os.replace
                    # moved writer.lock. This blocks audit publication.
                    renamed.set()
                    if not admin_go.wait(15):
                        raise TimeoutError("test did not release admin")
                return original_open(*args, **kwargs)

            def writer():
                try:
                    stale_result.append(stale.commit(
                        "test.stale.between.rename.and.audit",
                        [RecordPut("project", rid, {"id": rid, "name": "stale"})]))
                except Exception as exc:
                    errors.append(("writer", repr(exc)))
                finally:
                    stale_done.set()

            def admin():
                try:
                    Workspace.break_writer_lock(stale.meta_dir, token,
                                                "audit boundary race test")
                except Exception as exc:
                    errors.append(("admin", repr(exc)))
                finally:
                    admin_go.set()

            try:
                stale._check_lock_still_held = checked_then_wait
                with patch.object(Workspace, "open", side_effect=intercept_open):
                    writer_thread = threading.Thread(target=writer, name="revoked-writer")
                    writer_thread.start()
                    self.assertTrue(preflight.wait(15), "writer never reached preflight")
                    admin_thread = threading.Thread(target=admin, name="admin-break")
                    admin_thread.start()
                    self.assertTrue(renamed.wait(15), "admin never moved writer.lock")
                    self.assertTrue(stale_done.wait(15), "writer did not finish while audit paused")
                    admin_go.set()
                    admin_thread.join(15)
                    writer_thread.join(15)
                self.assertFalse(admin_thread.is_alive())
                self.assertFalse(writer_thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(stale_result), 1)
                with original_open(stale.meta_dir) as reader:
                    entries = [reader.read_entry(i) for i in reader.journal_seqs()]
                    stale_entries = [e for e in entries
                                     if e["op"] == "test.stale.between.rename.and.audit"]
                    breaks = [e for e in entries if e["op"] == "workspace.lock.break"]
                    self.assertEqual(len(stale_entries), 1)
                    self.assertEqual(len(breaks), 1)
                    self.assertLess(stale_entries[0]["seq"], breaks[0]["seq"])
                    # This confirms the currently unsafe window. The corrective
                    # implementation should make this assertion FAIL.
                    self.assertFalse(reader.is_fenced(stale_entries[0]))
                    self.assertIsNotNone(reader.get("project", rid))
            finally:
                admin_go.set()
                renamed.set()
                if writer_thread:
                    writer_thread.join(5)
                if admin_thread:
                    admin_thread.join(5)
                stale._lock_token = None
                stale.close()


if __name__ == "__main__":
    unittest.main()
