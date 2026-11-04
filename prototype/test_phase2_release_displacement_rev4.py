"""Independent Phase 2 ADR-019 release-displacement safety on exact pinned source.

All lock movement uses Workspace._unlink_own_lock, _restore_foreign_lock and
Workspace.open. No outsider writes the locks directory. C's fencing scan is
paused while A resumes a displaced B restoration and optionally crashes.
Only journal-effective B/C records count toward an unsafe writer overlap.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
B_ID = "prj_" + "b" * 32
C_ID = "prj_" + "c" * 32


@unittest.skipUnless(SOURCE, "requires exact pinned Phase 2 checkout")
class ReleaseDisplacementRev4Acceptance(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "ws"
        self.meta = None
        self.lock = None

    def _exercise(self, crash):
        ws = self.ws
        b = ws.Workspace.create(self.root, "independent release crash")
        self.meta = b.meta_dir
        self.lock = self.meta / "locks" / "writer.lock"
        token_b = b._lock_token
        old_a = ws.Workspace(self.meta, {"workspace_id": "releaser"}, False, "a" * 32)
        real_restore = old_a._restore_foreign_lock
        real_fence = ws.Workspace._fence_orphaned_releases
        real_revoke = ws.Workspace._publish_revocation
        state = {"restore": False, "crash": False, "c": None, "c_error": None, "b_error": None}
        real_current_rev = b._current_rev
        entered = []

        def revocation(cls, meta, holder, reason, evidence):
            if crash and holder.get("token") == token_b and not state["crash"]:
                state["crash"] = True
                raise SystemExit("injected release crash before B revocation")
            return real_revoke(meta, holder, reason, evidence)

        def delayed_restore(private, public, holder):
            self.assertEqual(holder.get("token"), token_b)
            state["restore"] = True
            def scanner(meta, own_token):
                try:
                    with patch.object(ws.Workspace, "_publish_revocation", classmethod(revocation)):
                        try:
                            real_restore(private, public, holder)
                        except SystemExit:
                            pass
                finally:
                    # C must not be granted authority until it has inspected
                    # any original release/fencing custody surviving that crash.
                    return real_fence(meta, own_token)
            with patch.object(ws.Workspace, "_fence_orphaned_releases", staticmethod(scanner)):
                try:
                    state["c"] = ws.Workspace.open(self.root, writable=True)
                    state["c"].commit("writer.C", [
                        ws.RecordPut("project", C_ID, {"by": "C"}, expect_rev=0)])
                except ws.WorkspaceError as exc:
                    state["c_error"] = type(exc).__name__
            # A's release has either finished or crashed inside scanner.
            return None

        def resume_b(kind, record_id):
            if not entered:
                entered.append(True)
                with patch.object(old_a, "_restore_foreign_lock", side_effect=delayed_restore):
                    old_a._unlink_own_lock(self.lock)
            return real_current_rev(kind, record_id)

        b._current_rev = resume_b
        try:
            try:
                b.commit("writer.B", [
                    ws.RecordPut("project", B_ID, {"by": "B"}, expect_rev=0)])
            except ws.WorkspaceError as exc:
                state["b_error"] = type(exc).__name__
            self.assertTrue(entered)
            self.assertTrue(state["restore"])
            with ws.Workspace.open(self.root) as reader:
                effective = list(reader.entries_after(0))
                b_effective = any(e.get("op") == "writer.B" and
                                  e.get("writer_token") == token_b for e in effective)
                c_effective = any(e.get("op") == "writer.C" for e in effective)
            b_revoked = token_b in ws.Workspace._revoked_tokens(self.meta)
            barrier = any(p.name.startswith((".releasing-", ".fencing-", ".clearing-"))
                          for p in (self.meta / "locks").iterdir())
            return {"b": b_effective, "c": c_effective, "b_revoked": b_revoked,
                    "barrier": barrier, "crash": state["crash"],
                    "b_error": state["b_error"], "c_error": state["c_error"]}
        finally:
            if state["c"] is not None:
                state["c"].close()
            b._lock_token = None
            b.close()

    def test_normal_displacement_cannot_admit_two_unrevoked_effective_writers(self):
        result = self._exercise(crash=False)
        self.assertFalse(result["b"] and result["c"] and not result["b_revoked"], result)

    def test_crash_before_displaced_token_revocation_remains_fail_closed(self):
        result = self._exercise(crash=True)
        self.assertTrue(result["crash"], "fault-injection edge was not exercised")
        self.assertFalse(result["b"] and result["c"] and not result["b_revoked"], result)
        self.assertTrue(result["b_revoked"] or result["barrier"] or not result["c"], result)


if __name__ == "__main__":
    unittest.main()
