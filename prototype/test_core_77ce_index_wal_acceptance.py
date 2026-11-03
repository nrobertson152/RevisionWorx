"""Independent exact-source ADR-023 SQLite WAL-creation race and busy-guard acceptance.

Three genuine/controlled scenarios:
  - six callers simultaneously open an empty SQLite index with NO retries;
  - an injected brief SQLite busy condition clears during the bounded wait;
  - a permanent busy condition times out with IndexBusyError and does not
    delete or corrupt the cache.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
RID = "root_" + "a" * 32


@unittest.skipUnless(SOURCE, "requires exact Phase 2 core checkout")
class IndexWalOpenRaceAcceptance(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        import revisionworx.index as index
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws, self.index = ws, index
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ws"
        self.cache = Path(self.temp.name) / "cache"
        with ws.Workspace.create(self.root, "SQLite WAL race") as writer:
            writer.commit("seed", [ws.RecordPut("root", RID, {"name": "x"}, expect_rev=0)])

    def _rows(self, reader):
        with self.index.open_index(reader, self.cache) as idx:
            return idx.db.execute("SELECT name FROM roots").fetchall()

    def test_six_simultaneous_first_openers_succeed_without_callers_retrying(self):
        errors = []
        completed = []
        barrier = threading.Barrier(6, timeout=20)
        with self.ws.Workspace.open(self.root) as reader:
            def opener():
                try:
                    barrier.wait()
                    completed.append(self._rows(reader))
                except BaseException as exc:
                    errors.append(repr(exc))
            threads = [threading.Thread(target=opener, name=f"wal-{i}") for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(50)
            self.assertFalse(any(t.is_alive() for t in threads), "index-open thread hung")
            self.assertEqual(errors, [])
            self.assertEqual(completed, [[("x",)]] * 6)
            self.assertEqual(self._rows(reader), [("x",)])
            self.assertEqual(reader.verify()["problems"], [])

    def test_wal_busy_retries_until_available(self):
        real = self.index.LocalIndex._usable
        attempts = []
        def temporarily_busy(inst, db):
            attempts.append(1)
            if len(attempts) <= 3:
                raise self.index.IndexBusyError("injected short WAL initialization contention")
            return real(inst, db)
        with self.ws.Workspace.open(self.root) as reader, \
             patch.object(self.index, "BUSY_TIMEOUT", 0.5), \
             patch.object(self.index.LocalIndex, "_usable", temporarily_busy):
            self.assertEqual(self._rows(reader), [("x",)])
        self.assertGreaterEqual(len(attempts), 4)

    def test_persistent_wal_busy_fails_closed_and_does_not_discard_index(self):
        def always_busy(inst, db):
            raise self.index.IndexBusyError("injected persistent WAL contention")
        with self.ws.Workspace.open(self.root) as reader:
            self.assertEqual(self._rows(reader), [("x",)])
            file = self.cache / f"{reader.id}.sqlite"
            original = file.read_bytes()
            with patch.object(self.index, "BUSY_TIMEOUT", 0.08), \
                 patch.object(self.index.LocalIndex, "_usable", always_busy):
                start = time.monotonic()
                with self.assertRaises(self.index.IndexBusyError):
                    self._rows(reader)
                self.assertLess(time.monotonic() - start, 2, "persistent busy should fail within a bounded deadline")
            self.assertEqual(file.read_bytes(), original, "a busy SQLite index must not be deleted")
            self.assertEqual(self._rows(reader), [("x",)])


if __name__ == "__main__":
    unittest.main()
