"""Actual Phase 2 Windows/standalone close-vs-administrative-override research.

This is a deterministic race in the *pathname-based* release algorithm on
any local filesystem, not a physical Windows WinError 32 or SMB qualification.
The second test intentionally demonstrates UNSAFE behavior: re-reading the
owner token before unlink does not make the subsequent unlink conditional.
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
class Phase2LockReleaseRace(unittest.TestCase):
    def _workspace(self):
        root = Path(PHASE2).expanduser().resolve()
        self.assertTrue((root / "revisionworx" / "workspace.py").is_file())
        sys.path.insert(0, str(root))
        from revisionworx.workspace import Workspace
        self.assertEqual(Path(sys.modules[Workspace.__module__].__file__).resolve(),
                         root / "revisionworx" / "workspace.py")
        return Workspace

    def test_normal_close_preserves_next_writer(self):
        Workspace = self._workspace()
        with tempfile.TemporaryDirectory() as temp:
            first = Workspace.create(Path(temp) / "workspace", "LockReleaseControl")
            token = Workspace.read_writer_lock(first.meta_dir)["token"]
            first.close()
            self.assertIsNone(Workspace.read_writer_lock(first.meta_dir))
            with Workspace.open(first.meta_dir, writable=True) as next_writer:
                next_token = Workspace.read_writer_lock(first.meta_dir)["token"]
                self.assertNotEqual(token, next_token)

    def test_stale_close_can_unlink_successor_after_revocation(self):
        """UNSAFE behavior demonstration, NOT a passing safety acceptance."""
        Workspace = self._workspace()
        with tempfile.TemporaryDirectory() as temp:
            stale = Workspace.create(Path(temp) / "workspace", "LockReleaseRace")
            meta = stale.meta_dir
            old_token = Workspace.read_writer_lock(meta)["token"]
            pathname = meta / "locks" / "writer.lock"
            ready = threading.Event()
            resume = threading.Event()
            problems = []
            successor = None
            original_unlink = Path.unlink

            def pause_unlink(path, *args, **kwargs):
                if (threading.current_thread().name == "stale-lock-close"
                        and Path(path) == pathname):
                    ready.set()
                    if not resume.wait(15):
                        raise TimeoutError("stale owner unlink did not resume")
                return original_unlink(path, *args, **kwargs)

            def close_old_writer():
                try:
                    stale.close()
                except Exception as exc:
                    problems.append(repr(exc))

            worker = threading.Thread(target=close_old_writer, name="stale-lock-close")
            try:
                with patch.object(Path, "unlink", pause_unlink):
                    worker.start()
                    self.assertTrue(ready.wait(15),
                                    "old owner did not reach token-checked unlink")
                    Workspace.break_writer_lock(meta, old_token,
                                                reason="administrator revokes old writer")
                    successor = Workspace.open(meta, writable=True)
                    new_token = Workspace.read_writer_lock(meta)["token"]
                    self.assertNotEqual(old_token, new_token)
                    resume.set()
                    worker.join(15)
                self.assertFalse(worker.is_alive())
                self.assertEqual(problems, [])
                # Reproduces the bug: old owner unlinks the NEW writer's lock.
                self.assertIsNone(Workspace.read_writer_lock(meta))
                self.assertTrue(successor.writable)
            finally:
                resume.set()
                worker.join(5)
                if successor is not None:
                    successor._lock_token = None
                    successor.close()
                stale._lock_token = None
                stale.close()


if __name__ == "__main__":
    unittest.main()
