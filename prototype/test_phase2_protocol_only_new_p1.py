"""Independent executable ADR-019 protocol-only P1 counterexamples (Codex on d2b7a1c).

Two *passing UNSAFE* tests prove two EFFECTIVE writer commits with no B revocation.
Two strict expected-failure positive safety oracles document the unmet guarantees.
These are protocol API/path operations, not substitutions onto private custody
names by an outside file editor. This module does NOT claim safety on green CI.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
ID_B = "prj_" + "b" * 32
ID_C = "prj_" + "c" * 32
OLD_TOKEN = "a" * 32


@unittest.skipUnless(SOURCE, "requires exact pinned Phase 2 checkout")
class ProtocolOnlyP1Counterexamples(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "ws"
        self.c = None
        self.addCleanup(self._release_c)
        self.meta = None
        self.locks = None

    def _release_c(self):
        if self.c is not None:
            self.c.close()

    def _state(self, b_token):
        with self.ws.Workspace.open(self.meta) as reader:
            b = reader.get("project", ID_B)
            c = reader.get("project", ID_C)
            bentries = [reader.read_entry(i) for i in reader.journal_seqs()
                        if reader.read_entry(i)["op"] == "writer.B"]
        revoked = self.ws.Workspace._revoked_tokens(self.meta)
        return {
            "b_effective": b is not None and b.get("by") == "B",
            "c_effective": c is not None and c.get("by") == "C",
            "b_unrevoked": b_token not in revoked,
            "b_journal_effective": any(
                e.get("writer_token") == b_token and
                not self.ws.Workspace(self.meta, {"workspace_id": "probe"}, False, None).is_fenced(e)
                for e in bentries),
        }

    def _safety_oracle(self, state):
        self.assertFalse(
            state["b_effective"] and state["c_effective"] and state["b_unrevoked"]
            and state["b_journal_effective"],
            "P1: two authoritative writers committed while B was never fenced"
        )

    def _run_revoked_cleanup_race(self):
        """D reads revoked A; B archives A and acquires; delayed D archives B, C commits."""
        ws = self.ws
        old = ws.Workspace.create(self.root, "Revoked acquisition")
        self.meta = old.meta_dir
        self.locks = self.meta / "locks"
        lock = self.locks / "writer.lock"
        a_token = old._lock_token
        real_replace = ws.os.replace

        def keep_revoked_lock(src, dst, *args, **kwargs):
            if Path(src) == lock and Path(dst).name.startswith("writer.lock.broken-"):
                raise PermissionError("fault injection: leave revoked A lock in place")
            return real_replace(src, dst, *args, **kwargs)

        with patch.object(ws.os, "replace", side_effect=keep_revoked_lock):
            ws.Workspace.break_writer_lock(self.meta, a_token, "A stopped")
        old._lock_token = None
        old.close()
        self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"], a_token)

        interleaved, b_holder = [], []
        def delayed_revoked_cleanup(src, dst, *args, **kwargs):
            if (Path(src) == lock and Path(dst).name.startswith("writer.lock.revoked-")
                    and not interleaved):
                interleaved.append(1)
                # D paused after reading A's revoked token. B uses only supported acquire.
                with patch.object(ws.os, "replace", real_replace):
                    b = ws.Workspace.open(self.meta, writable=True)
                b_holder.append(b._lock_token)
                current = b._current_rev
                run_once = []
                def pause_b(kind, rid):
                    if not run_once:
                        run_once.append(1)
                        # Resume D's original protocol os.replace: it removes B, not A.
                        real_replace(lock, dst)
                        # C's ordinary acquire cannot see B in the .revoked evidence namespace.
                        with patch.object(ws.os, "replace", real_replace):
                            self.c = ws.Workspace.open(self.meta, writable=True)
                        self.c.commit("writer.C", [
                            ws.RecordPut("project", ID_C, {"by": "C"}, expect_rev=0)])
                    return current(kind, rid)
                b._current_rev = pause_b
                try:
                    b.commit("writer.B", [
                        ws.RecordPut("project", ID_B, {"by": "B"}, expect_rev=0)])
                finally:
                    b._lock_token = None
                    b.close()
                return None               # delayed D rename has already happened
            return real_replace(src, dst, *args, **kwargs)

        with patch.object(ws.os, "replace", side_effect=delayed_revoked_cleanup):
            with self.assertRaises(ws.WorkspaceLockedError):
                ws.Workspace.open(self.meta, writable=True)   # D's retry meets C's lock
        self.assertEqual(interleaved, [1])
        self.assertEqual(len(b_holder), 1)
        self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"], self.c._lock_token)
        return self._state(b_holder[0])

    def _run_release_displacement_crash(self):
        """Stale A release moves B to .releasing, then crash after displacement archival."""
        ws = self.ws
        b = ws.Workspace.create(self.root, "Displaced release")
        self.meta = b.meta_dir
        self.locks = self.meta / "locks"
        lock = self.locks / "writer.lock"
        b_token = b._lock_token
        # This is A's protocol release continuation after its earlier ownership check.
        old_releaser = ws.Workspace(self.meta, {"workspace_id": "release"}, False, OLD_TOKEN)
        real_restore = old_releaser._restore_foreign_lock
        real_fence = ws.Workspace._fence_orphaned_releases
        real_replace = ws.os.replace
        resumed, archived = [], []

        def restore_while_c_is_acquiring(private, public, holder):
            self.assertEqual(holder["token"], b_token)
            resumed.append(1)
            def pause_c_fence(meta, own_token):
                def die_after_archive(src, dst, *args, **kwargs):
                    if (Path(src) == private and
                            Path(dst).name.startswith("writer.lock.displaced-")
                            and not archived):
                        real_replace(src, dst, *args, **kwargs)
                        archived.append(1)
                        raise SystemExit("A died after displacing B but before revocation")
                    return real_replace(src, dst, *args, **kwargs)
                with patch.object(ws.os, "replace", side_effect=die_after_archive):
                    with self.assertRaises(SystemExit):
                        real_restore(private, public, holder)
                return real_fence(meta, own_token)
            with patch.object(ws.Workspace, "_fence_orphaned_releases",
                              staticmethod(pause_c_fence)):
                self.c = ws.Workspace.open(self.meta, writable=True)
            self.c.commit("writer.C", [
                ws.RecordPut("project", ID_C, {"by": "C"}, expect_rev=0)])

        current = b._current_rev
        invoked = []
        def pause_b(kind, rid):
            if not invoked:
                invoked.append(1)
                # A's own release method (no external mv into private custody).
                with patch.object(old_releaser, "_restore_foreign_lock",
                                  side_effect=restore_while_c_is_acquiring):
                    old_releaser._unlink_own_lock(lock)
            return current(kind, rid)
        b._current_rev = pause_b
        try:
            b.commit("writer.B", [
                ws.RecordPut("project", ID_B, {"by": "B"}, expect_rev=0)])
        finally:
            b._lock_token = None
            b.close()
        self.assertEqual(invoked, [1])
        self.assertEqual(resumed, [1])
        self.assertEqual(archived, [1])
        self.assertTrue(list(self.locks.glob("writer.lock.displaced-*")))
        self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"], self.c._lock_token)
        return self._state(b_token)

    def test_revoked_lock_cleanup_removes_successor_UNSAFE_demonstration(self):
        state = self._run_revoked_cleanup_race()
        self.assertTrue(all(state.values()), state)  # green means exposed P1, NOT accepted safety

    @unittest.expectedFailure
    def test_revoked_lock_cleanup_must_fence_successor_positive_safety(self):
        self._safety_oracle(self._run_revoked_cleanup_race())

    def test_release_displacement_crash_loses_revocation_UNSAFE_demonstration(self):
        state = self._run_release_displacement_crash()
        self.assertTrue(all(state.values()), state)  # green means exposed P1, NOT accepted safety

    @unittest.expectedFailure
    def test_displaced_lock_must_remain_blocking_until_revoked_positive_safety(self):
        self._safety_oracle(self._run_release_displacement_crash())


if __name__ == "__main__":
    unittest.main()
