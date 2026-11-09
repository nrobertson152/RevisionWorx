"""Protocol-only revoked-lock cleanup TOCTOU, independent real Workspace oracle.

Exact vulnerable source: d2b7a1cfead5b83acecce2252e70d0a155f146b7.
No outsider mutates the lock directory. The only competing actions are
Workspace.open and the production revoked-lock cleanup path.
The positive oracle is strict expected-failure at the pinned vulnerable SHA;
green XFAIL does NOT mean safety acceptance.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "requires pinned Phase 2 source")
class RevokedCleanupProtocolRace(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        from revisionworx import workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        with ws.Workspace.create(self.root, "fencing race"):
            pass
        self.meta = self.root / ".revisionworx"
        self.lock = self.meta / "locks" / "writer.lock"

    def _interleave(self):
        """D sees revoked A, B legitimately acquires, D resumes stale cleanup."""
        ws = self.ws
        a = ws.Workspace.open(self.root, writable=True)
        old_token = a._lock_token
        real_replace = os.replace

        def refuse_admin_archive(src, dst, *args, **kwargs):
            if Path(src) == self.lock and Path(dst).name.startswith("writer.lock.broken-"):
                raise PermissionError("forced ordinary post-revocation archive error")
            return real_replace(src, dst, *args, **kwargs)

        with patch.object(os, "replace", side_effect=refuse_admin_archive):
            ws.Workspace.break_writer_lock(self.root, old_token, "old A stopped")
        a.close()
        self.assertEqual(ws.Workspace.read_writer_lock(self.root)["token"], old_token)
        observed = []

        def pause_d_revoked_cleanup(src, dst, *args, **kwargs):
            if Path(src) == self.lock and Path(dst).name.startswith("writer.lock.revoked-") and not observed:
                observed.append("D previously inspected revoked A")
                # B is an ordinary product process; B legally clears revoked A
                # and acquires its own lock while D has not moved anything.
                with patch.object(os, "replace", real_replace):
                    b = ws.Workspace.open(self.root, writable=True)
                try:
                    live_token = b._lock_token
                    self.assertNotEqual(live_token, old_token)
                    self.assertEqual(ws.Workspace.read_writer_lock(self.root)["token"], live_token)
                    # Resume D's original os.replace on that SAME pathname.
                    real_replace(src, dst, *args, **kwargs)
                    observed.append(("B", live_token))
                finally:
                    # B is intentionally still alive and not administratively revoked.
                    # Do not close it yet: B's lock ownership is the safety property.
                    self.b = b
                # D's rename has already happened.
                return None
            return real_replace(src, dst, *args, **kwargs)

        self.b = None
        try:
            with patch.object(os, "replace", side_effect=pause_d_revoked_cleanup):
                try:
                    with ws.Workspace.open(self.root, writable=True) as d:
                        observed.append(("D", d._lock_token))
                except ws.WorkspaceError as exc:
                    observed.append(("D refused", type(exc).__name__))
        finally:
            if self.b is not None:
                b_token = self.b._lock_token
                # Read before close; a safe cleanup may leave B as sole writer.
                holder = ws.Workspace.read_writer_lock(self.root)
                b_revoked = b_token in ws.Workspace._revoked_tokens(self.meta)
                b_survived = holder is not None and holder.get("token") == b_token
                self.b.close()
            else:
                b_survived = False
                b_revoked = False
        self.assertIn("D previously inspected revoked A", observed)
        return b_survived, b_revoked, observed

    def test_unsafe_reproduction_stale_revoked_cleanup_displaces_live_successor(self):
        survives, revoked, trace = self._interleave()
        self.assertFalse(survives, trace)
        self.assertFalse(revoked, trace)

    @unittest.expectedFailure
    def test_positive_oracle_live_successor_must_survive_or_be_fenced(self):
        survives, revoked, trace = self._interleave()
        self.assertTrue(survives or revoked, trace)


if __name__ == "__main__":
    unittest.main()
