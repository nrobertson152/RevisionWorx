"""Independent positive ADR-019 rev.3 custody and serialization acceptance.

The vulnerable 366d435 reproductions remain separately pinned in
prototype.test_phase2_orphan_clear_protocol. This suite tests the corrected
e5b38a69edd71eb580f5bae67a570d4def94eb8f source. It is intentionally
not an implementation-owner test. Local filesystems only, no SMB claims.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
NAME = ".releasing-a11ce5"
ORIGINAL = b"{unreadable orphan original"
REPLACED = b"{unreadable replacement distinct bytes"


@unittest.skipUnless(SOURCE, "requires exact pinned production checkout")
class Phase2Rev3IndependentAcceptance(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        workspace = ws.Workspace.create(Path(self.tmp.name) / "ws", "Custody review")
        self.meta = workspace.meta_dir
        self.locks = self.meta / "locks"
        self.orphan = self.locks / NAME
        os.replace(self.locks / "writer.lock", self.orphan)
        self.orphan.write_bytes(ORIGINAL)
        workspace._lock_token = None
        workspace.close()

    def _events(self, event):
        with self.ws.Workspace.open(self.meta) as reader:
            entries = [reader.read_entry(i) for i in reader.journal_seqs()]
        return [
            record["data"]
            for entry in entries
            for record in entry.get("records", [])
            if (record.get("data") or {}).get("event") == event
        ]

    def _auths(self):
        return self._events("orphaned-release-cleared")

    def _results(self):
        return self._events("orphaned-release-clear-result")

    def _clear(self, reason="confirmed writer stopped"):
        return self.ws.Workspace.clear_orphaned_release(self.meta, NAME, reason)

    def _assert_writer_blocked(self):
        with self.assertRaises(self.ws.WorkspaceError):
            with self.ws.Workspace.open(self.meta, writable=True):
                pass
        self.assertIsNone(self.ws.Workspace.read_writer_lock(self.meta))

    def test_replaced_file_object_at_custody_edge_never_matches_original_audit(self):
        """Replace the directory entry, not merely its in-place contents."""
        real_replace = self.ws.os.replace
        seen = []
        def replace_at_custody(src, dst, *args, **kwargs):
            if Path(src) == self.orphan and Path(dst).name.startswith(".clearing-") and not seen:
                seen.append(True)
                replacement = self.locks / "replacement.tmp"
                replacement.write_bytes(REPLACED)
                real_replace(replacement, self.orphan)
            return real_replace(src, dst, *args, **kwargs)
        with patch.object(self.ws.os, "replace", side_effect=replace_at_custody):
            result = self._clear()
        self.assertEqual(seen, [True], "did not exercise the custody edge")
        self.assertEqual(result["outcome"], "refused")
        self.assertEqual(len(self._auths()), 1)
        self.assertEqual(len(self._results()), 1)
        audit = self._auths()[0]
        self.assertEqual(audit["file_sha256"], hashlib.sha256(ORIGINAL).hexdigest())
        self.assertEqual(self._results()[0]["observed_sha256"],
                         hashlib.sha256(REPLACED).hexdigest())
        self.assertFalse((self.locks / audit["evidence"]).exists())
        quarantined = self.locks / self._results()[0]["quarantine"]
        self.assertEqual(quarantined.read_bytes(), REPLACED)
        self._assert_writer_blocked()

    def test_custody_changes_after_verified_read_do_not_enter_evidence(self):
        """The archived bytes must come from the verified buffer, not a later path read."""
        real_publish = self.ws.atomicio.publish_exclusive
        seen = []
        def change_during_evidence_publish(path, data, *args, **kwargs):
            if Path(path).name.startswith("writer.lock.orphaned-") and not seen:
                seen.append(True)
                custody = list(self.locks.glob(".clearing-*"))
                self.assertEqual(len(custody), 1)
                custody[0].write_bytes(REPLACED)
            return real_publish(path, data, *args, **kwargs)
        with patch.object(self.ws.atomicio, "publish_exclusive",
                          side_effect=change_during_evidence_publish):
            result = self._clear()
        self.assertEqual(seen, [True])
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(len(self._auths()), 1)
        self.assertEqual(len(self._results()), 1)
        audit = self._auths()[0]
        evidence = self.locks / audit["evidence"]
        self.assertEqual(evidence.read_bytes(), ORIGINAL)
        self.assertEqual(hashlib.sha256(evidence.read_bytes()).hexdigest(),
                         audit["file_sha256"])
        self.assertTrue(any(p.read_bytes() == REPLACED
                            for p in self.locks.glob(".releasing-*")),
                        "mutated custody copy must remain a blocking orphan")
        self._assert_writer_blocked()

    def test_competing_administrators_adopt_one_authorization_and_result(self):
        """B completes while A is paused at its journal CAS publication."""
        original = self.ws.Workspace._publish_admin_entry
        events = []
        def race(meta, op, audit_data, *args, **kwargs):
            if op == "workspace.orphan.clear" and not events:
                events.append("B")
                b = self._clear("administrator B")
                self.assertEqual(b["outcome"], "completed")
            return original(meta, op, audit_data, *args, **kwargs)
        with patch.object(self.ws.Workspace, "_publish_admin_entry", side_effect=race):
            a = self._clear("administrator A")
        self.assertEqual(events, ["B"])
        self.assertEqual(a["outcome"], "completed")
        self.assertTrue(a["adopted"])
        self.assertEqual(len(self._auths()), 1)
        self.assertEqual(len(self._results()), 1)
        self.assertEqual(self._auths()[0]["reason"], "administrator B")
        self.assertEqual(len(list(self.locks.glob("writer.lock.orphaned-*"))), 1)

    def test_crash_after_custody_before_evidence_keeps_barrier_and_resumes(self):
        """An unfinished authorized transition remains recoverable from .clearing."""
        original = self.ws.atomicio.publish_exclusive
        count = []
        def die_before_evidence(path, data, *args, **kwargs):
            if Path(path).name.startswith("writer.lock.orphaned-"):
                count.append(True)
                raise SystemExit("crash after custody")
            return original(path, data, *args, **kwargs)
        with patch.object(self.ws.atomicio, "publish_exclusive",
                          side_effect=die_before_evidence):
            with self.assertRaises(SystemExit):
                self._clear()
            with self.assertRaises(SystemExit):
                self.ws.Workspace.open(self.meta, writable=True)
        self.assertGreaterEqual(len(count), 2)
        self.assertEqual(len(self._auths()), 1)
        self.assertEqual(len(self._results()), 0)
        self.assertEqual(len(list(self.locks.glob(".clearing-*"))), 1)
        self.assertIsNone(self.ws.Workspace.read_writer_lock(self.meta))
        with self.ws.Workspace.open(self.meta, writable=True):
            pass
        self.assertEqual(len(self._auths()), 1)
        self.assertEqual(len(self._results()), 1)
        self.assertEqual(self._results()[0]["outcome"], "completed")
        self.assertEqual(len(list(self.locks.glob(".clearing-*"))), 0)
        self.assertEqual(len(list(self.locks.glob("writer.lock.orphaned-*"))), 1)

    def test_overlapping_terminal_result_cas_publishes_exactly_once(self):
        """A nested terminal-result writer wins; losing writer must adopt its result."""
        original = self.ws.Workspace._publish_admin_entry
        events = []
        def compete(meta, op, audit_data, *args, **kwargs):
            if op == "workspace.orphan.clear.result" and not events:
                events.append("B")
                auth = self.ws.Workspace._orphan_clear_state(self.meta)[0][0]
                other = self.ws.Workspace._publish_result_once(
                    self.meta, auth, {"outcome": "completed"})
                self.assertEqual(other["outcome"], "completed")
            return original(meta, op, audit_data, *args, **kwargs)
        with patch.object(self.ws.Workspace, "_publish_admin_entry", side_effect=compete):
            outcome = self._clear()
        self.assertEqual(outcome["outcome"], "completed")
        self.assertEqual(events, ["B"])
        self.assertEqual(len(self._auths()), 1)
        self.assertEqual(len(self._results()), 1)
        self.assertEqual((self.locks / self._auths()[0]["evidence"]).read_bytes(), ORIGINAL)
        self.assertEqual(len(list(self.locks.glob(".clearing-*"))), 0)


if __name__ == "__main__":
    unittest.main()
