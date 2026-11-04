"""Independent ADR-023 busy-index recovery contract (new Phase 2 core).

The API deliberately raises IndexBusyError on SQLITE_BUSY rather than treating
a live index as corrupt. This test exercises six real concurrent SQLite
initializers, with bounded *caller* retries as the published contract requests,
then asserts converged search state and journal integrity.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
RID = "root_" + "a" * 32


@unittest.skipUnless(SOURCE, "exact core checkout required")
class LocalIndexBusyRecovery(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        import revisionworx.index as index
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws, self.index = ws, index
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        self.cache = Path(self.tmp.name) / "cache"
        with ws.Workspace.create(self.root, "index contention") as w:
            w.commit("seed", [ws.RecordPut("root", RID, {"name":"x"}, expect_rev=0)])

    def test_six_competing_first_index_openers_converge_after_bounded_busy_retries(self):
        ws, index = self.ws, self.index
        errors, retries, completed = [], [], []
        barrier = threading.Barrier(6, timeout=20)
        original_timeout = index.BUSY_TIMEOUT
        index.BUSY_TIMEOUT = 0.2
        try:
            with ws.Workspace.open(self.root) as reader:
                def attempt():
                    try:
                        barrier.wait()
                        for n in range(40):
                            try:
                                with index.open_index(reader, self.cache) as idx:
                                    found = idx.db.execute("SELECT name FROM roots").fetchall()
                                    self.assertEqual(found, [("x",)])
                                retries.append(n)
                                completed.append(True)
                                return
                            except index.IndexBusyError:
                                time.sleep(0.02)
                        errors.append("exceeded 40 bounded SQLITE_BUSY retries")
                    except BaseException as exc:
                        errors.append(repr(exc))
                threads = [threading.Thread(target=attempt, name=f"index-{i}") for i in range(6)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(45)
                self.assertTrue(all(not t.is_alive() for t in threads), "index threads hung")
                self.assertFalse(errors, errors)
                self.assertEqual(len(completed), 6)
                self.assertEqual(len(retries), 6)
                with index.open_index(reader, self.cache) as idx:
                    self.assertEqual(idx.db.execute("SELECT name FROM roots").fetchall(), [("x",)])
                self.assertEqual(reader.verify()["problems"], [])
        finally:
            index.BUSY_TIMEOUT = original_timeout


if __name__ == "__main__":
    unittest.main()
