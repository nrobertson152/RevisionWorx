"""Independent ADR-019 rev.4 actual displaced-token acceptance (not Claude's tests).

Two-source control:
- d2b7a1c vulnerable path should fail the positive oracle (strict xfail in CI).
- fe8a179 corrected path must pass the positive oracle without xfail.
All participants call real Workspace methods; no external rename or custody tamper.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "requires exact pinned Phase 2 source checkout")
class AdministrativeBreakSuccessorSafety(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ws"
        with ws.Workspace.create(self.root, "admin break owner identity"):
            pass
        self.meta = self.root / ".revisionworx"
        self.lock = self.meta / "locks" / "writer.lock"

    def _interleave(self, crash_after_displacement=False):
        """Admin revokes A, pauses at its real lock move; B legally clears A and acquires.
        Admin resumes moving actual successor B's lock; inspect durable revocation/barriers.
        """
        ws = self.ws
        a = ws.Workspace.open(self.root, writable=True)
        a_token = a._lock_token
        moved = []
        state = {"b": None, "crashed": False}
        original = os.replace
        original_revocation = ws.Workspace._publish_revocation
        # For the genuine pre-revision path, admin displaces into writer.lock.broken-*;
        # for rev4, into .releasing-* and then fencing custody.
        def move_hook(src, dst, *args, **kwargs):
            if (Path(src) == self.lock
                and (Path(dst).name.startswith("writer.lock.broken-")
                     or Path(dst).name.startswith(".releasing-"))
                and not moved):
                moved.append("admin decision paused")
                with patch.object(os, "replace", original):
                    state["b"] = ws.Workspace.open(self.root, writable=True)
                moved.append("B acquired")
            return original(src, dst, *args, **kwargs)

        def fail_b_revocation(cls, meta, holder, reason, evidence):
            if crash_after_displacement and state["b"] is not None and (
                holder.get("token") == state["b"]._lock_token):
                state["crashed"] = True
                raise SystemExit("administrative mover died before revoking B")
            return original_revocation(meta, holder, reason, evidence)

        try:
            with patch.object(os, "replace", side_effect=move_hook):
                with patch.object(ws.Workspace, "_publish_revocation",
                                  classmethod(fail_b_revocation)):
                    try:
                        ws.Workspace.break_writer_lock(self.root, a_token, "A confirmed dead")
                    except SystemExit:
                        pass
            self.assertEqual(moved, ["admin decision paused", "B acquired"])
            b = state["b"]
            self.assertIsNotNone(b)
            tok_b = b._lock_token
            # Fresh acquisition by C must fence any surviving scanned custody.
            try:
                with ws.Workspace.open(self.root, writable=True):
                    pass
            except ws.WorkspaceError:
                pass  # denying writes until a durable fence is also safe
            revoked_b = tok_b in ws.Workspace._revoked_tokens(self.meta)
            live_b = (ws.Workspace.read_writer_lock(self.root) or {}).get("token") == tok_b
            barrier = [p for p in (self.meta / "locks").iterdir()
                       if p.name.startswith((".releasing-", ".fencing-", ".clearing-"))]
            return {"revoked_b": revoked_b, "live_b": live_b,
                    "barrier": bool(barrier), "crashed": state["crashed"]}
        finally:
            if state["b"] is not None:
                state["b"].close()
            a.close()

    def test_successor_token_is_revoked_or_retains_live_authority(self):
        observed = self._interleave()
        self.assertTrue(observed["revoked_b"] or observed["live_b"] or observed["barrier"],
                        observed)

    def test_death_before_b_revocation_cannot_remove_sole_fencing_obligation(self):
        observed = self._interleave(crash_after_displacement=True)
        self.assertTrue(observed["revoked_b"] or observed["live_b"] or observed["barrier"],
                        observed)
        # Ensure the crash injection actually reached B's revocation on the corrected
        # code (old code silently archives B without ever attempting it).
        if hasattr(self.ws.Workspace, "_displace_writer_lock"):
            self.assertTrue(observed["crashed"], observed)


if __name__ == "__main__":
    unittest.main()
