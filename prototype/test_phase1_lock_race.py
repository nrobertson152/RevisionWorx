"""Reproduce Phase 1 lock-break TOCTOU against the REAL phase1-foundation code.

From research/shared-filesystem-protocol checkout:
  REVISIONWORX_PHASE1_CHECKOUT=/absolute/path/to/phase1-foundation \
      python -m unittest -v prototype.test_phase1_lock_race

Skipped unless a separate Phase 1 checkout is provided. A demonstrated stale commit
is a DEFECT reproduction, not the desired production behavior.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

PHASE1 = os.environ.get("REVISIONWORX_PHASE1_CHECKOUT")


@unittest.skipUnless(PHASE1, "requires actual phase1-foundation checkout")
class Phase1FencingRegression(unittest.TestCase):
    def test_real_workspace_commit_after_administrative_override(self):
        source = Path(PHASE1).expanduser().resolve()
        if not (source / "revisionworx" / "workspace.py").is_file():
            self.fail("REVISIONWORX_PHASE1_CHECKOUT does not contain revisionworx/workspace.py")
        sys.path.insert(0, str(source))
        from revisionworx.workspace import Workspace, RecordPut
        self.assertEqual(Path(sys.modules[Workspace.__module__].__file__).resolve(),
                         source / "revisionworx" / "workspace.py")

        with tempfile.TemporaryDirectory() as td:
            stale = Workspace.create(Path(td), "fencing-repro")
            try:
                original_check = stale._check_lock_still_held
                checked = threading.Event()
                resume = threading.Event()
                result = {}

                def preflight_then_pause():
                    original_check()
                    checked.set()
                    if not resume.wait(15):
                        raise RuntimeError("timed out before administrative override")

                stale._check_lock_still_held = preflight_then_pause
                original = stale.get("settings", "workspace")
                original_rev = original["_rev"]

                def writer():
                    try:
                        seq = stale.commit("test.stale.after.break", [
                            RecordPut("settings", "workspace",
                                      {**original, "name": "revoked write"}, expect_rev=original_rev)])
                        result["committed_seq"] = seq
                    except Exception as exc:
                        result["error"] = repr(exc)

                t = threading.Thread(target=writer, daemon=True)
                t.start()
                self.assertTrue(checked.wait(15), "writer never reached preflight pause")
                holder = Workspace.read_writer_lock(stale.meta_dir)
                self.assertIsNotNone(holder)
                Workspace.break_writer_lock(stale.meta_dir, holder["token"], "repro recovery")
                resume.set()
                t.join(15)
                self.assertFalse(t.is_alive(), "stale writer was not released")
                self.assertNotIn("error", result, result)
                self.assertIn("committed_seq", result,
                              "Expected regression demonstration: old writer still published")
                with Workspace.open(stale.meta_dir) as current:
                    entries = list(current.entries_after(0))
                    self.assertTrue(any(e["op"] == "workspace.lock.break" for e in entries))
                    self.assertTrue(any(e["op"] == "test.stale.after.break" for e in entries))
            finally:
                if "resume" in locals():
                    resume.set()
                stale.close()


if __name__ == "__main__":
    unittest.main()
