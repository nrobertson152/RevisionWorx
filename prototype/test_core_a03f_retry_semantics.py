"""Independent follow-up audit of atomic sharing retries at exact core a03f1f0.

The old 22a5273 retry-nesting bug is gone: Workspace now directly delegates
reading to atomicio.read_json, whose one retry budget handles WinError32.
WinError5 is *not* transient sharing: this source still retries it on Windows.
Test unsafe reproducers, positive control and strict expected safety failure.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "requires pinned exact core checkout")
class CurrentCoreSharingSemantics(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.atom = ws.atomicio

    def _probe(self, windows, errcode, fail_first=None, from_commit_preflight=False):
        seen, sleeps = [], []
        def open_reader(_path):
            seen.append(len(seen))
            if fail_first is None or len(seen) <= fail_first:
                e = PermissionError(13, "sharing violation" if errcode == 32 else "access denied")
                e.winerror = errcode
                raise e
            return io.BytesIO(b'{"token":"valid"}')
        with patch.object(self.atom, "RETRY_SHARING", windows), \
             patch.object(self.atom, "SHARING_RETRIES", 5), \
             patch.object(self.atom, "SHARING_DELAY", 0), \
             patch.object(self.atom, "_open_for_read", side_effect=open_reader), \
             patch.object(self.atom.time, "sleep", side_effect=lambda _: sleeps.append(1)):
            try:
                if from_commit_preflight:
                    handle = self.ws.Workspace(Path("not-a-real-meta"), {"workspace_id": "test"}, True, "valid")
                    handle._check_lock_still_held()
                    value = {"token":"valid"}
                else:
                    value = self.atom.read_json(Path("not-a-real-lock"))
            except (PermissionError, self.ws.LockLostError):
                value = None
        return len(seen), len(sleeps), value

    def test_nested_retry_budget_removed_from_lock_preflight(self):
        calls, sleeps, _ = self._probe(True, 32, from_commit_preflight=True)
        self.assertEqual(calls, 5)
        self.assertEqual(sleeps, 4)

    def test_permanent_windows_acl_denial_still_retried_UNSAFE(self):
        calls, sleeps, val = self._probe(True, 5)
        self.assertEqual((calls, sleeps, val), (5, 4, None))

    @unittest.expectedFailure
    def test_positive_safety_permanent_winerror5_is_immediate(self):
        calls, sleeps, _ = self._probe(True, 5)
        self.assertEqual((calls, sleeps), (1, 0))

    def test_posix_denial_is_not_retried(self):
        calls, sleeps, _ = self._probe(False, 13)
        self.assertEqual((calls, sleeps), (1, 0))

    def test_transient_win32_sharing_recovery_remains_functional(self):
        calls, sleeps, val = self._probe(True, 32, fail_first=2)
        self.assertEqual((calls, sleeps, val), (3, 2, {"token":"valid"}))


if __name__ == "__main__":
    unittest.main()
