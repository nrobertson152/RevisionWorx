"""ADR-019 protocol-only revoked-lock cleanup: independent old/new positive oracle.

Uses only Workspace.break_writer_lock / Workspace.open and the actual production
cleanup rename. No direct replacement of a live lock by a foreign process.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "requires pinned Phase 2 checkout")
class RevokedCleanupRev4Acceptance(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "ws"
        with ws.Workspace.create(self.root, "cleanup race"):
            pass
        self.meta = self.root / ".revisionworx"
        self.lock = self.meta / "locks" / "writer.lock"

    def _run(self, crash_before_revoking_successor=False):
        ws = self.ws
        a = ws.Workspace.open(self.root, writable=True)
        a_token = a._lock_token
        original_replace = os.replace
        original_publish_revocation = ws.Workspace._publish_revocation
        state = {"b": None, "paused": False, "crashed": False}

        # Admin break durably revokes A, then cannot archive A's lock because
        # the OS reports a sharing error. This is a legitimate supported state.
        def deny_admin_move(src, dst, *args, **kwargs):
            if Path(src) == self.lock and (
                Path(dst).name.startswith("writer.lock.broken-")
                or Path(dst).name.startswith(".releasing-")
            ):
                raise PermissionError("injected WinError 32 after A revocation")
            return original_replace(src, dst, *args, **kwargs)

        with patch.object(ws.Workspace, "LOCK_RELEASE_ATTEMPTS", 1, create=True):
            with patch.object(os, "replace", side_effect=deny_admin_move):
                ws.Workspace.break_writer_lock(self.root, a_token, "A stopped")
        a.close()
        self.assertEqual(ws.Workspace.read_writer_lock(self.root)["token"], a_token)
        self.assertIn(a_token, ws.Workspace._revoked_tokens(self.meta))

        def pause_d_cleanup_move(src, dst, *args, **kwargs):
            if Path(src) == self.lock and (
                Path(dst).name.startswith("writer.lock.revoked-")
                or Path(dst).name.startswith(".releasing-")
            ) and not state["paused"]:
                state["paused"] = True
                # D already used A's revoked token as its cleanup rationale.
                # Another cooperating process B completes the same cleanup,
                # legitimately publishes a fresh live writer.lock, and remains open.
                with patch.object(os, "replace", original_replace):
                    state["b"] = ws.Workspace.open(self.root, writable=True)
            return original_replace(src, dst, *args, **kwargs)

        def crash_revocation(cls, meta, holder, reason, evidence):
            if (crash_before_revoking_successor and state["b"] is not None
                    and holder.get("token") == state["b"]._lock_token):
                state["crashed"] = True
                raise SystemExit("D died before durable B revocation")
            return original_publish_revocation(meta, holder, reason, evidence)

        try:
            with patch.object(os, "replace", side_effect=pause_d_cleanup_move):
                with patch.object(ws.Workspace, "_publish_revocation",
                                  classmethod(crash_revocation)):
                    try:
                        with ws.Workspace.open(self.root, writable=True):
                            pass
                    except (SystemExit, ws.WorkspaceError):
                        pass
            self.assertTrue(state["paused"])
            b = state["b"]
            self.assertIsNotNone(b)
            tok = b._lock_token
            # Recovery attempt must complete any scanned fence before admitting C.
            try:
                with ws.Workspace.open(self.root, writable=True):
                    pass
            except ws.WorkspaceError:
                pass
            revoked = tok in ws.Workspace._revoked_tokens(self.meta)
            live = (ws.Workspace.read_writer_lock(self.meta) or {}).get("token") == tok
            blocked = any(p.name.startswith((".releasing-", ".fencing-", ".clearing-"))
                          for p in (self.meta / "locks").iterdir())
            return {"revoked": revoked, "live": live,
                    "blocked": blocked, "crashed": state["crashed"]}
        finally:
            if state["b"] is not None:
                state["b"].close()

    def test_racing_revoked_lock_cleaner_must_fence_actual_successor(self):
        observed = self._run()
        self.assertTrue(observed["revoked"] or observed["live"] or observed["blocked"], observed)

    def test_recovery_after_cleaner_crash_retains_fence(self):
        observed = self._run(crash_before_revoking_successor=True)
        self.assertTrue(observed["revoked"] or observed["live"] or observed["blocked"], observed)
        if hasattr(self.ws.Workspace, "_displace_writer_lock"):
            self.assertTrue(observed["crashed"], observed)


if __name__ == "__main__":
    unittest.main()
