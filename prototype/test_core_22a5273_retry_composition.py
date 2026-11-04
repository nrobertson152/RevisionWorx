"""Independent P1/P2 liveness audit of core 22a5273 retry nesting.

Executable negative safety evidence, NOT an acceptance suite. Use pinned
REVISIONWORX_PHASE2_CHECKOUT; test a finite, accelerated equivalent of the
real retry bounds so CI cannot hang for 21 minutes.

- Workspace._read_lock_file -> atomicio.read_json -> retry_sharing
  nests 4 x 5 attempts, 20 underlying reads on one permanently busy file.
  At defaults 250 x 250 = 62,500 attempts (~1255 s at .02 s delay).
- Windows WinError 5 access denied is not WinError 32 sharing violation.
  A permanent ACL failure must not be repeatedly retried.
- POSIX PermissionError is final; the Workspace outer loop still retries.
"""
from __future__ import annotations
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "requires exact pinned Phase 2 checkout")
class SharingRetryComposition(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.atom = ws.atomicio
        self.path = source / "not-a-real-lock"  # no real file required for injected kernel response

    def _simulate(self, *, win, winerror=32, fail_first=None):
        seen = []
        sleeps = []
        limit_inner, limit_outer = 5, 4
        def permission():
            exc = PermissionError(13, "access denied" if winerror == 5 else "sharing violation")
            exc.winerror = winerror
            return exc
        def open_read(_):
            seen.append(len(seen) + 1)
            if fail_first is None or len(seen) <= fail_first:
                raise permission()
            return io.BytesIO(b'{"token":"current"}')
        with patch.object(self.atom, "IS_WINDOWS", win), \
             patch.object(self.atom, "SHARING_RETRIES", limit_inner), \
             patch.object(self.atom, "SHARING_DELAY", 0), \
             patch.object(self.ws.Workspace, "LOCK_RELEASE_ATTEMPTS", limit_outer), \
             patch.object(self.ws.Workspace, "LOCK_RELEASE_DELAY", 0), \
             patch.object(self.atom, "_open_for_read", side_effect=open_read), \
             patch.object(self.atom.time, "sleep", side_effect=lambda _: sleeps.append(1)):
            try:
                result = self.ws.Workspace._read_lock_file(self.path)
            except PermissionError:
                result = None
        return len(seen), len(sleeps), result

    def test_unsafe_nested_win32_sharing_attempt_count_is_multiplicative(self):
        reads, _, result = self._simulate(win=True)
        self.assertIsNone(result)
        self.assertEqual(reads, 4 * 5, "source control: both retry loops must really be exercised")

    @unittest.expectedFailure
    def test_positive_safety_win32_sharing_has_only_one_bounded_retry_budget(self):
        reads, _, _ = self._simulate(win=True)
        self.assertLessEqual(reads, 5,
            "permanent WinError 32 must not consume 250 x 250 nested attempts")

    def test_unsafe_nonsharing_windows_permission_denied_is_retried(self):
        reads, _, result = self._simulate(win=True, winerror=5)
        self.assertIsNone(result)
        self.assertEqual(reads, 4 * 5)

    @unittest.expectedFailure
    def test_positive_safety_winerror5_must_fail_immediately(self):
        reads, _, _ = self._simulate(win=True, winerror=5)
        self.assertEqual(reads, 1, "permanent WinError 5 is not an intermittent sharing conflict")

    def test_unsafe_posix_permission_denied_still_retried_by_workspace(self):
        reads, _, result = self._simulate(win=False, winerror=13)
        self.assertIsNone(result)
        self.assertEqual(reads, 4)

    @unittest.expectedFailure
    def test_positive_safety_posix_permission_denied_has_no_retry(self):
        reads, _, _ = self._simulate(win=False, winerror=13)
        self.assertEqual(reads, 1, "POSIX EACCES must fail immediately")

    def test_transient_win32_sharing_retry_remains_functional(self):
        reads, _, result = self._simulate(win=True, winerror=32, fail_first=2)
        self.assertEqual(reads, 3)
        self.assertEqual(result, {"token":"current"})


if __name__ == "__main__":
    unittest.main()
