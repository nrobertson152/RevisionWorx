"""Independent ADR-019 safety acceptance on actual Phase 2 code.

Historical *unsafe* proof against f3c54386 is documented at Actions 41387552435.
These tests instead require the successor lock to survive, and require a
third writer in the restore window to fence the displaced writer. They must
FAIL on the old vulnerable implementation, not pass by finding a defect.

REVISIONWORX_PHASE2_CHECKOUT must point to the actual source checkout.
These are local filesystem tests, NOT two-host Windows/SMB qualification.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PHASE2 = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(PHASE2, "requires actual phase2-documents checkout")
class Phase2LockReleaseSafety(unittest.TestCase):
    def _load(self):
        root = Path(PHASE2).resolve()
        self.assertTrue((root / "revisionworx" / "workspace.py").is_file())
        sys.path.insert(0, str(root))
        from revisionworx.workspace import Workspace, LockLostError, RecordPut
        import revisionworx.workspace as wsmod
        self.assertEqual(Path(wsmod.__file__).resolve(), root / "revisionworx" / "workspace.py")
        return Workspace, LockLostError, RecordPut, wsmod

    def test_normal_close_preserves_next_writer(self):
        Workspace, _, _, _ = self._load()
        with tempfile.TemporaryDirectory() as root:
            first = Workspace.create(Path(root) / "workspace", "NormalRelease")
            token = Workspace.read_writer_lock(first.meta_dir)["token"]
            first.close()
            self.assertIsNone(Workspace.read_writer_lock(first.meta_dir))
            with Workspace.open(first.meta_dir, writable=True) as second:
                self.assertNotEqual(token, Workspace.read_writer_lock(first.meta_dir)["token"])

    def test_successor_lock_survives_revocation_interleaving(self):
        """P0 safety oracle: A must not delete or fence B after B acquires."""
        Workspace, _, RecordPut, wsmod = self._load()
        with tempfile.TemporaryDirectory() as root:
            a = Workspace.create(Path(root) / "workspace", "SuccessorSurvival")
            meta = a.meta_dir
            token = a._lock_token
            next_writer = []
            triggered = []
            real_replace = wsmod.os.replace

            def inject_before_release_rename(src, dst, *args, **kwargs):
                if (not triggered and Path(src).name == "writer.lock"
                        and Path(dst).name.startswith(".releasing-")):
                    triggered.append(True)
                    Workspace.break_writer_lock(meta, token, reason="revoke A during close")
                    next_writer.append(Workspace.open(meta, writable=True))
                return real_replace(src, dst, *args, **kwargs)

            try:
                with patch.object(wsmod.os, "replace", side_effect=inject_before_release_rename):
                    a.close()
                self.assertTrue(triggered, "release never attempted a move-to-private-file")
                self.assertEqual(len(next_writer), 1)
                b = next_writer[0]
                self.assertEqual(Workspace.read_writer_lock(meta)["token"], b._lock_token)
                rid = "prj_" + "a" * 32
                seq = b.commit("successor.commit", [
                    RecordPut("project", rid, {"owner": "B"})], txid="a" * 32)
                self.assertEqual(b.get("project", rid)["_rev"], seq)
                self.assertEqual(b.transaction_outcome("a" * 32)["state"], "committed")
                b.close()
                self.assertIsNone(Workspace.read_writer_lock(meta))
            finally:
                for b in next_writer:
                    b._lock_token = None
                    b.close()
                a._lock_token = None
                a.close()

    def test_restore_gap_fences_displaced_writer_and_keeps_third(self):
        """If B is moved aside before restore and C acquires, B is revoked."""
        Workspace, LockLostError, RecordPut, wsmod = self._load()
        with tempfile.TemporaryDirectory() as root:
            a = Workspace.create(Path(root) / "workspace", "ThirdWriter")
            meta = a.meta_dir
            old_token = a._lock_token
            triggered = []
            restore_triggered = []
            writers = []
            c_tokens = []
            real_replace = wsmod.os.replace
            real_link = wsmod.os.link
            real_rename = wsmod.os.rename

            def insert_b_before_release(src, dst, *args, **kwargs):
                if (not triggered and Path(src).name == "writer.lock"
                        and Path(dst).name.startswith(".releasing-")):
                    triggered.append(True)
                    Workspace.break_writer_lock(meta, old_token, "revoke A")
                    writers.append(Workspace.open(meta, writable=True))
                return real_replace(src, dst, *args, **kwargs)

            def introduce_c_before_restore(src, dst, *args, **kwargs):
                if (not restore_triggered and Path(src).name.startswith(".releasing-")
                        and Path(dst).name == "writer.lock"):
                    restore_triggered.append(True)
                    c_tokens.append(Workspace._acquire_writer_lock(meta))
                return (real_rename if os.name == "nt" else real_link)(
                    src, dst, *args, **kwargs)

            patch_restore = ("rename" if os.name == "nt" else "link")
            try:
                with patch.object(wsmod.os, "replace", side_effect=insert_b_before_release):
                    with patch.object(wsmod.os, patch_restore,
                                      side_effect=introduce_c_before_restore):
                        a.close()
                self.assertTrue(triggered)
                self.assertTrue(restore_triggered)
                self.assertEqual(len(writers), 1)
                b = writers[0]
                self.assertEqual(Workspace.read_writer_lock(meta)["token"], c_tokens[0])
                with self.assertRaises(LockLostError):
                    b.commit("displaced.b", [
                        RecordPut("project", "prj_" + "b" * 32, {"owner": "B"})])
                with Workspace.open(meta) as reader:
                    self.assertIsNone(reader.get("project", "prj_" + "b" * 32))
                    journal_entries = [reader.read_entry(seq)
                                       for seq in reader.journal_seqs()]
                    displaced = [e for e in journal_entries
                                 if e.get("revokes") == b._lock_token]
                    self.assertTrue(displaced, "displacement revocation must be in durable journal")
                    self.assertIn("displaced", displaced[-1]["records"][0]["data"]["reason"])
                    self.assertEqual(displaced[-1]["records"][0]["data"]["previous_holder"]["token"],
                                     b._lock_token)
                lock_dir = meta / "locks"
                self.assertTrue(any(f.name.startswith("writer.lock.displaced-")
                                    for f in lock_dir.iterdir()))
                self.assertFalse(any(f.name.startswith(".releasing-")
                                     for f in lock_dir.iterdir()))
            finally:
                for b in writers:
                    b._lock_token = None
                    b.close()
                a._lock_token = None
                a.close()

    def test_revoked_old_writer_never_touches_successor_lock(self):
        Workspace, _, _, wsmod = self._load()
        with tempfile.TemporaryDirectory() as root:
            a = Workspace.create(Path(root) / "workspace", "RevokedOwner")
            meta = a.meta_dir
            Workspace.break_writer_lock(meta, a._lock_token, "admin breaks old lock")
            b = Workspace.open(meta, writable=True)
            real_replace = wsmod.os.replace
            called = []
            def detect_release(src, dst, *args, **kwargs):
                if Path(src).name == "writer.lock" and Path(dst).name.startswith(".releasing-"):
                    called.append(True)
                return real_replace(src, dst, *args, **kwargs)
            try:
                with patch.object(wsmod.os, "replace", side_effect=detect_release):
                    a.close()
                self.assertEqual(called, [])
                self.assertEqual(Workspace.read_writer_lock(meta)["token"], b._lock_token)
            finally:
                b.close()
                a._lock_token = None
                a.close()


if __name__ == "__main__":
    unittest.main()
