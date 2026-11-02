"""Research-only coordinator facade tests; no Windows SMB physical host certification."""
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from prototype.authority import Fenced
from prototype.coordinator_research import CoordinatorResearch


class CoordinatorResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "authority.db"
        self.guard = self.root / "guard" / "epoch"
        self.c = CoordinatorResearch(self.db, self.guard)

    def test_acquire_verify_release_and_generation(self):
        cred = self.c.acquire("cad", "alice")
        self.assertEqual(self.c.verify("cad", cred), 0)
        self.assertEqual(self.c.release("cad", cred), "released")
        with self.assertRaises(Fenced):
            self.c.verify("cad", cred)
        with self.assertRaises(Fenced):
            self.c.release("cad", cred)
        next_cred = self.c.acquire("cad", "bob")
        self.assertGreater(next_cred["generation"], cred["generation"])

    def test_admin_break_fences_paused_writer(self):
        old = self.c.acquire("cad", "alice")
        self.c.override("cad", "manual recovery", "unverified operator")
        with self.assertRaises(Fenced):
            self.c.publish("cad", old, "tx-stale", "stale", expected_revision=0)
        new = self.c.acquire("cad", "bob")
        self.assertEqual(self.c.publish("cad", new, "tx-new", "new",
                                        expected_revision=0), "committed")
        self.assertEqual(self.c.authority.outcome("tx-stale")["state"], "not-found")

    def test_restart_persists_epoch(self):
        cred = self.c.acquire("cad", "alice")
        fresh = CoordinatorResearch(self.db, self.guard)
        self.assertEqual(fresh.guard.epoch, cred["epoch"])
        self.assertEqual(fresh.verify("cad", cred), 0)

    def test_backup_restore_db_without_guard_fails_closed(self):
        cred = self.c.acquire("cad", "alice")
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup = self.root / "snapshot.db"
        shutil.copy2(self.db, backup)
        self.c.release("cad", cred)
        # Restore an older DB snapshot but keep a *newer* epoch guard. Epoch
        # is intentionally stable during normal operation, so this cannot
        # detect a same-epoch rollback. Documented limitation.
        shutil.copy2(backup, self.db)
        restored = CoordinatorResearch(self.db, self.guard)
        self.assertEqual(restored.verify("cad", cred), 0)

    def test_missing_guard_fails_closed(self):
        self.c.acquire("cad", "alice")
        self.guard.unlink()
        with self.assertRaises(Fenced):
            CoordinatorResearch(self.db, self.guard)
        with self.assertRaises(Fenced):
            self.c.acquire("other", "bob")

    def test_epoch_guard_mismatch_fails_closed(self):
        self.c.acquire("cad", "alice")
        self.guard.write_text("tampered")
        with self.assertRaises(Fenced):
            CoordinatorResearch(self.db, self.guard)

    def test_incremental_change_watermark(self):
        cred = self.c.acquire("cad", "alice")
        for i in range(3):
            self.c.publish("cad", cred, f"tx{i}", f"payload{i}", expected_revision=i)
        page1 = self.c.changes_after(0, limit=2)
        self.assertEqual([x[0] for x in page1["changes"]], [1, 2])
        self.assertEqual(page1["high_watermark"], 3)
        self.assertTrue(page1["stale"])
        page2 = self.c.changes_after(2, limit=2)
        self.assertEqual([x[0] for x in page2["changes"]], [3])
        self.assertFalse(page2["stale"])

    def test_missing_change_sequence_fails_closed(self):
        cred = self.c.acquire("cad", "alice")
        for i in range(3):
            self.c.publish("cad", cred, f"tx{i}", f"payload{i}", expected_revision=i)
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM commits WHERE sequence=2")
        with self.assertRaises(Fenced):
            self.c.changes_after(1)


if __name__ == "__main__":
    unittest.main()
