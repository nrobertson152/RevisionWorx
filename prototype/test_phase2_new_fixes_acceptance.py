"""Independent acceptance of Claude's ADR-019/017 fix at exact Phase 2 2a28048.

These tests run against the checked-out production source but never modify it.
Expected failures are *unaccepted* protocol/restore properties. The test suite
runs on LOCAL Windows/Ubuntu GitHub Actions runners, NOT physical two-host SMB.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "actual Phase 2 source required")
class Phase2FixedSafetyAcceptance(unittest.TestCase):
    def _load(self):
        root = Path(SOURCE).resolve()
        sys.path.insert(0, str(root))
        import revisionworx.workspace as w
        from revisionworx import atomicio
        self.assertEqual(Path(w.__file__).resolve(), root / "revisionworx" / "workspace.py")
        return w, atomicio

    def test_identical_request_survives_restart_and_altered_check_fails(self):
        w, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / "ws"
            with w.Workspace.create(location, "FullIdentity") as initial:
                rid = "prj_" + "a" * 32
                txid = "1" * 32
                ops = [w.RecordCheck("settings", "workspace", 1),
                       w.RecordPut("project", rid, {"name": "A"}, expect_rev=0)]
                seq = initial.commit("create", ops, txid=txid)
            with w.Workspace.open(location, writable=True) as restarted:
                self.assertEqual(restarted.commit("create", ops, txid=txid), seq)
                with self.assertRaises(w.TxidReuseError):
                    restarted.commit("create", [
                        w.RecordCheck("settings", "workspace", 999),
                        w.RecordPut("project", rid, {"name": "A"}, expect_rev=0)],
                        txid=txid)
                self.assertEqual(restarted.transaction_outcome(txid),
                                 {"state": "committed", "seq": seq})

    def test_reordered_checks_are_rejected_without_new_commit(self):
        w, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            with w.Workspace.create(Path(directory) / "ws", "CheckSequence") as ws:
                rid = "prj_" + "b" * 32
                a = w.RecordCheck("settings", "workspace", 1)
                b = w.RecordCheck("project", "prj_" + "c" * 32, 0)
                put = w.RecordPut("project", rid, {"name": "B"}, expect_rev=0)
                seq = ws.commit("create", [a, b, put], txid="2" * 32)
                with self.assertRaises(w.TxidReuseError):
                    ws.commit("create", [b, a, put], txid="2" * 32)
                self.assertEqual(ws.head_seq(), seq)
                self.assertEqual(ws.get("project", rid)["_rev"], seq)

    def test_changed_check_only_precondition_rejected(self):
        w, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            with w.Workspace.create(Path(directory) / "ws", "CheckOnly") as ws:
                seq = ws.commit("verify", [
                    w.RecordCheck("settings", "workspace", 1)], txid="3" * 32)
                with self.assertRaises(w.TxidReuseError):
                    ws.commit("verify", [
                        w.RecordCheck("settings", "workspace", 555)], txid="3" * 32)
                self.assertEqual(ws.head_seq(), seq)

    def test_unreadable_orphan_blocks_acquisition_and_preserves_evidence(self):
        w, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            writer = w.Workspace.create(Path(directory) / "ws", "OrphanDenied")
            meta = writer.meta_dir
            locks = meta / "locks"
            orphan = locks / ".releasing-deadbeef"
            os.replace(locks / "writer.lock", orphan)
            raw = b"{invalid incomplete holder"
            orphan.write_bytes(raw)
            try:
                for _ in range(2):  # retries/restarts must not grant new authority
                    with self.assertRaises(w.WorkspaceCorruptError):
                        w.Workspace.open(meta, writable=True)
                    self.assertIsNone(w.Workspace.read_writer_lock(meta))
                    self.assertEqual(orphan.read_bytes(), raw)
                with self.assertRaises(w.WorkspaceError):
                    w.Workspace.clear_orphaned_release(meta, orphan.name, "")
                self.assertTrue(orphan.exists())
                # The supervisor must prove the old owner stopped OUTSIDE the app.
                writer._lock_token = None
                writer.close()
                w.Workspace.clear_orphaned_release(
                    meta, orphan.name, "administrator verified former writer stopped")
                self.assertFalse(orphan.exists())
                with w.Workspace.open(meta, writable=True) as successor:
                    seq = successor.commit("after-supervised-recovery", [
                        w.RecordPut("project", "prj_" + "d" * 32, {"safe": True})])
                    self.assertEqual(successor.get("project", "prj_" + "d" * 32)["_rev"], seq)
                self.assertEqual(len(list(locks.glob("writer.lock.orphaned-*"))), 1)
                self.assertEqual(list(locks.glob("writer.lock.orphaned-*"))[0].read_bytes(),
                                 raw)
                with w.Workspace.open(meta) as reader:
                    self.assertEqual(sum(
                        reader.read_entry(i).get("op") == "workspace.orphan.clear"
                        for i in reader.journal_seqs()), 1)
            finally:
                writer._lock_token = None
                writer.close()

    @unittest.expectedFailure
    def test_interleaving_checks_and_writes_changes_identity(self):
        """PROPOSED v2 full-list ordering not enforced by separate checks/writes arrays."""
        w, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            with w.Workspace.create(Path(directory) / "ws", "Interleaved") as ws:
                a = w.RecordCheck("settings", "workspace", 1)
                b = w.RecordCheck("project", "prj_" + "f" * 32, 0)
                put = w.RecordPut("project", "prj_" + "e" * 32,
                                  {"name": "E"}, expect_rev=0)
                txid = "5" * 32
                ws.commit("create", [a, put, b], txid=txid)
                with self.assertRaises(w.TxidReuseError):
                    ws.commit("create", [put, a, b], txid=txid)

    @unittest.expectedFailure
    def test_legacy_without_persisted_checks_cannot_prove_identical_request(self):
        """Historical journal omits checks; prior.get('checks',[]) invents absence."""
        w, atomicio = self._load()
        with tempfile.TemporaryDirectory() as directory:
            with w.Workspace.create(Path(directory) / "ws", "LegacyTxid") as ws:
                put = w.RecordPut("project", "prj_" + "6" * 32,
                                  {"name": "Old"}, expect_rev=0)
                seq = ws.head_seq() + 1
                txid = "6" * 32
                entry = {
                    "seq": seq, "txid": txid, "time": w.now_iso(),
                    "actor": w.current_actor(), "op": "legacy",
                    "records": [put.as_json()], "blobs": [],
                    "writer_token": ws._lock_token,
                    # Actual pre-2a28048 entries OMITTED checks entirely.
                }
                entry["digest"] = w.entry_digest(entry)
                atomicio.publish_exclusive(
                    ws.meta_dir / "journal" / f"{seq:020d}.json",
                    atomicio.dumps_json(entry))
                ws.recover()
                self.assertEqual(ws.get("project", put.id)["_rev"], seq)
                with self.assertRaises(w.TxidReuseError):
                    ws.commit("legacy", [put], txid=txid)

    @unittest.expectedFailure
    def test_clear_orphan_crash_after_evidence_move_must_not_silently_allow_writes(self):
        """Audit AFTER evidence move leaves a crash window of unaudited recovery."""
        w, _ = self._load()
        with tempfile.TemporaryDirectory() as directory:
            old = w.Workspace.create(Path(directory) / "ws", "AdminCrash")
            meta = old.meta_dir
            locks = meta / "locks"
            orphan = locks / ".releasing-cafebabe"
            os.replace(locks / "writer.lock", orphan)
            orphan.write_text("{corrupt", encoding="utf-8")
            before = old.head_seq()
            try:
                with patch.object(w.Workspace, "_publish_admin_entry",
                                  side_effect=SystemExit("crash before audit write")):
                    with self.assertRaises(SystemExit):
                        w.Workspace.clear_orphaned_release(
                            meta, orphan.name, "verified old writer stopped")
                self.assertFalse(orphan.exists())
                self.assertEqual(old.head_seq(), before,
                                 "no recovery audit was durably published")
                # This must be refused until the missing audit is reconciled.
                with self.assertRaises(w.WorkspaceCorruptError):
                    with w.Workspace.open(meta, writable=True) as successor:
                        successor.commit("unaudited-resumption", [
                            w.RecordPut("project", "prj_" + "7" * 32,
                                        {"should_block": True})])
            finally:
                old._lock_token = None
                old.close()


if __name__ == "__main__":
    unittest.main()
