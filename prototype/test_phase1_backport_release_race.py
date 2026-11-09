"""Frozen Phase 1 backport 36d1957 old-owner release race.

Passing UNSAFE reproduction and strict positive safety xfail. Local OS only.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_BACKPORT_CHECKOUT")


@unittest.skipUnless(SOURCE, "frozen backport required")
class BackportSuccessorSafety(unittest.TestCase):
    def scenario(self):
        root = Path(SOURCE).resolve()
        sys.path.insert(0, str(root))
        from revisionworx.workspace import Workspace
        self.assertEqual(Path(Workspace.__module__.replace(".", "/") + ".py").name,
                         "workspace.py")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        old = Workspace.create(Path(temp.name) / "ws", "Backport")
        meta, token = old.meta_dir, old._lock_token
        lock = meta / "locks" / "writer.lock"
        ready, resume = threading.Event(), threading.Event()
        failures = []
        original_unlink = Path.unlink

        def pause(path, *a, **kw):
            if Path(path) == lock and threading.current_thread().name == "stale-close":
                ready.set()
                if not resume.wait(15):
                    raise TimeoutError("release barrier not resumed")
            return original_unlink(path, *a, **kw)

        def close_old():
            try:
                old.close()
            except BaseException as err:
                failures.append(str(err))

        thread = threading.Thread(target=close_old, name="stale-close")
        successor = None
        try:
            with patch.object(Path, "unlink", pause):
                thread.start()
                self.assertTrue(ready.wait(15), "did not intercept pathname unlink")
                Workspace.break_writer_lock(meta, token, "admin revokes old writer")
                successor = Workspace.open(meta, writable=True)
                new_token = successor._lock_token
                self.assertNotEqual(token, new_token)
                resume.set()
                thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.addCleanup(self.release, successor)
            return Workspace, meta, successor, new_token
        finally:
            resume.set()
            thread.join(5)
            old._lock_token = None
            old.close()

    @staticmethod
    def release(ws):
        ws._lock_token = None
        ws.close()

    @unittest.expectedFailure
    def test_old_owner_must_not_remove_successor_lock(self):
        Workspace, meta, successor, new_token = self.scenario()
        self.assertEqual(Workspace.read_writer_lock(meta)["token"], new_token)

    def test_stale_close_removes_successor_UNSAFE_reproduction(self):
        Workspace, meta, successor, new_token = self.scenario()
        self.assertTrue(successor.writable)
        self.assertIsNone(Workspace.read_writer_lock(meta))
        with self.assertRaises(Exception):
            successor.commit("successor.lost", [])


if __name__ == "__main__":
    unittest.main()
