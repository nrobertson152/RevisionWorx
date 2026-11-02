"""Independent ADR-019 administrative orphan-clear crash and concurrency acceptance.

This intentionally runs against an ACTUAL, pinned Phase 2 source checkout.
A strict expected-failure means the positive safety property is UNACCEPTED;
the green unittest command does not certify it. No production files are edited.

Current frozen baseline: Phase 2 2a2804820d4aca48b50026bdb7edfe8c241b1866.
These are local-file tests on hosted Ubuntu/Windows, NOT physical SMB/power loss.
"""
from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PHASE2 = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
ORPHAN_NAME = ".releasing-a11ce5"
ORPHAN_BYTES = b"{invalid prior-writer identity"


def _terminate_after_evidence_move(root: str, source: str) -> None:
    """Independent process terminates after bytes move but before next statement."""
    sys.path.insert(0, source)
    import revisionworx.workspace as ws
    original = ws.os.replace

    def crash_on_evidence_move(src, dst, *a, **kw):
        if (Path(src).name == ORPHAN_NAME
                and Path(dst).name.startswith("writer.lock.orphaned-")):
            original(src, dst, *a, **kw)
            os._exit(74)
        return original(src, dst, *a, **kw)

    with patch.object(ws.os, "replace", side_effect=crash_on_evidence_move):
        ws.Workspace.clear_orphaned_release(
            Path(root), ORPHAN_NAME, "supervisor confirmed client terminated")
    os._exit(75)  # Unexpected: the intended fault-injection edge was not reached.


@unittest.skipUnless(PHASE2, "requires pinned production Phase 2 checkout")
class Phase2AdministrativeOrphanClearAcceptance(unittest.TestCase):
    def setUp(self):
        source = Path(PHASE2).resolve()
        self.assertTrue((source / "revisionworx" / "workspace.py").is_file())
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws, self.source = ws, source
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        created = ws.Workspace.create(Path(self.tmp.name) / "ws", "UnknownLock")
        self.meta = created.meta_dir
        self.locks = self.meta / "locks"
        self.orphan = self.locks / ORPHAN_NAME
        os.replace(self.locks / "writer.lock", self.orphan)
        self.orphan.write_bytes(ORPHAN_BYTES)
        created._lock_token = None  # Operator has separately confirmed no old client.
        created.close()
        self.initial_head = self._head()

    def _head(self):
        with self.ws.Workspace.open(self.meta) as reader:
            return reader.head_seq()

    def _clear_audits(self):
        with self.ws.Workspace.open(self.meta) as reader:
            entries = [reader.read_entry(n) for n in reader.journal_seqs()]
        return [e for e in entries if e["op"] == "workspace.orphan.clear"]

    def _assert_no_writer(self):
        with self.assertRaises(self.ws.WorkspaceCorruptError):
            self.ws.Workspace.open(self.meta, writable=True)
        self.assertIsNone(self.ws.Workspace.read_writer_lock(self.meta))
        self.assertTrue(self.orphan.exists())
        self.assertEqual(self.orphan.read_bytes(), ORPHAN_BYTES)

    def _clear(self, reason="supervisor confirmed terminated client"):
        return self.ws.Workspace.clear_orphaned_release(
            self.meta, ORPHAN_NAME, reason)

    def test_before_clear_multiple_restarts_deny_writes_and_preserve_bytes(self):
        for _ in range(3):
            self._assert_no_writer()
            self.assertEqual(self._head(), self.initial_head)
        self.assertEqual(len(self._clear_audits()), 0)
        with self.assertRaises(self.ws.WorkspaceError):
            self._clear(reason="")
        self.assertTrue(self.orphan.exists())

    @unittest.expectedFailure
    def test_failure_before_audit_must_leave_guard_in_place(self):
        """POSITIVE safety requirement; frozen baseline moves evidence too early."""
        with patch.object(self.ws.Workspace, "_publish_admin_entry",
                          side_effect=SystemExit("crash before durable audit")):
            with self.assertRaises(SystemExit):
                self._clear()
        self.assertEqual(len(self._clear_audits()), 0)
        self._assert_no_writer()

    @unittest.expectedFailure
    def test_durable_audit_must_precede_attempted_evidence_move(self):
        """At the move linearization point, audit already needs to be durable."""
        real = self.ws.os.replace
        observed = []

        def stop_before_move(src, dst, *args, **kwargs):
            if (Path(src) == self.orphan and
                    Path(dst).name.startswith("writer.lock.orphaned-")):
                observed.append(self._clear_audits())
                self.assertEqual(len(observed[-1]), 1,
                                 "an attributable audit must already be committed")
                raise SystemExit("die after audit, before custody move")
            return real(src, dst, *args, **kwargs)

        with patch.object(self.ws.os, "replace", side_effect=stop_before_move):
            with self.assertRaises(SystemExit):
                self._clear()
        self.assertEqual(len(observed), 1)
        self._assert_no_writer()
        self.assertEqual(len(self._clear_audits()), 1)

    @unittest.expectedFailure
    def test_after_evidence_move_crash_must_leave_durable_audit(self):
        """Move happened, then process died. Never acknowledge unaudited relief."""
        real = self.ws.os.replace
        called = []

        def die_after_move(src, dst, *args, **kwargs):
            if (Path(src) == self.orphan and
                    Path(dst).name.startswith("writer.lock.orphaned-")):
                called.append(True)
                real(src, dst, *args, **kwargs)
                raise SystemExit("die after evidence move")
            return real(src, dst, *args, **kwargs)

        with patch.object(self.ws.os, "replace", side_effect=die_after_move):
            with self.assertRaises(SystemExit):
                self._clear()
        self.assertEqual(called, [True])
        self.assertFalse(self.orphan.exists())
        self.assertEqual(len(self._clear_audits()), 1)
        kept = list(self.locks.glob("writer.lock.orphaned-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), ORPHAN_BYTES)

    @unittest.expectedFailure
    def test_independent_process_death_after_move_has_persisted_audit(self):
        """Spawned process os._exit, not just exception unwinding (Windows/Linux)."""
        ctx = mp.get_context("spawn")
        process = ctx.Process(target=_terminate_after_evidence_move,
                              args=(str(self.meta), str(self.source)))
        process.start()
        process.join(35)
        if process.is_alive():
            process.terminate()
            process.join(5)
            self.fail("fault-injection process hung")
        self.assertEqual(process.exitcode, 74, "injection edge was not reached")
        self.assertFalse(self.orphan.exists())
        self.assertEqual(len(self._clear_audits()), 1,
                         "the moved evidence must have a durable audit BEFORE move")
        kept = list(self.locks.glob("writer.lock.orphaned-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), ORPHAN_BYTES)

    def test_failed_evidence_move_preserves_blocking_orphan(self):
        real = self.ws.os.replace

        def deny_evidence_move(src, dst, *args, **kwargs):
            if (Path(src) == self.orphan and
                    Path(dst).name.startswith("writer.lock.orphaned-")):
                raise PermissionError("injected evidence permission error")
            return real(src, dst, *args, **kwargs)

        with patch.object(self.ws.os, "replace", side_effect=deny_evidence_move):
            with self.assertRaises(PermissionError):
                self._clear()
        self._assert_no_writer()

    def test_repeat_clear_does_not_repeat_audit_or_destroy_evidence(self):
        self._clear("supervisor confirmed writer stopped")
        with self.ws.Workspace.open(self.meta, writable=True) as next_writer:
            seq = next_writer.commit("after.clear", [
                self.ws.RecordPut("project", "prj_" + "1" * 32, {"valid": True})])
            self.assertEqual(next_writer.get("project", "prj_" + "1" * 32)["_rev"], seq)
        try:
            self._clear("duplicate of confirmed writer stop")
        except self.ws.WorkspaceError:
            pass  # A safely rejected duplicate is acceptable; no second audit.
        self.assertEqual(len(self._clear_audits()), 1)
        kept = list(self.locks.glob("writer.lock.orphaned-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), ORPHAN_BYTES)

    def test_reentrant_competing_admin_clear_has_one_terminal_audit(self):
        """Force two callers across the source-exists check deterministically."""
        real_publish = self.ws.Workspace._publish_admin_entry
        seen = []
        inner_failures = []

        def overlap(meta, op, audit_data, *args, **kwargs):
            if not seen:
                seen.append(True)
                try:
                    self._clear("second supervisor confirms writer stopped")
                except (self.ws.WorkspaceError, FileNotFoundError) as exc:
                    inner_failures.append(type(exc).__name__)
            return real_publish(meta, op, audit_data, *args, **kwargs)

        with patch.object(self.ws.Workspace, "_publish_admin_entry", side_effect=overlap):
            try:
                self._clear("first supervisor confirms writer stopped")
            except (self.ws.WorkspaceError, FileNotFoundError):
                pass
        self.assertEqual(seen, [True])
        self.assertEqual(len(self._clear_audits()), 1,
                         "competing clear requests must not publish duplicate effective audits")
        self.assertFalse(self.orphan.exists())
        self.assertEqual(len(list(self.locks.glob("writer.lock.orphaned-*"))), 1)

    def test_rejected_path_or_blank_reason_never_changes_authority(self):
        for name in ("../.releasing-a11ce5", "/tmp/foo", "writer.lock",
                     ".releasing-invalid_name"):
            with self.subTest(name=name), self.assertRaises(self.ws.WorkspaceError):
                self.ws.Workspace.clear_orphaned_release(
                    self.meta, name, "administrator confirmed no writer")
        self.assertEqual(len(self._clear_audits()), 0)
        self._assert_no_writer()


if __name__ == "__main__":
    unittest.main()
