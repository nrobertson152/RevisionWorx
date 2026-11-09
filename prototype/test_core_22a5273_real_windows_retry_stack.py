"""Real Win32 kernel-based confirmation of nested retry budget on core 22a5273.

Pins an actual CreateFileW handle with no sharing granted and measures
Workspace._read_lock_file()'s attempted Windows CreateFileW reads.
All retry limits are reduced so this never hangs a runner.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(os.name == "nt" and SOURCE, "requires native Windows and pinned Phase 2 checkout")
class NativeReadSharingBudget(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lock = Path(self.temp.name) / "writer.lock"
        self.lock.write_text('{"token":"valid"}', encoding="utf8")

    def _hold_no_share_read(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        h = create(str(self.lock), 0x80000000, 0, None, 3, 0x80, None)
        if h == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return h, close

    def _exercise(self):
        ws = self.ws
        atom = ws.atomicio
        opened = []
        original = atom._open_sharing_delete
        h, close = self._hold_no_share_read()
        try:
            def attempted(path):
                opened.append(1)
                return original(path)
            with patch.object(atom, "SHARING_RETRIES", 3), \
                 patch.object(atom, "SHARING_DELAY", 0), \
                 patch.object(ws.Workspace, "LOCK_RELEASE_ATTEMPTS", 4), \
                 patch.object(ws.Workspace, "LOCK_RELEASE_DELAY", 0), \
                 patch.object(atom, "_open_sharing_delete", side_effect=attempted):
                with self.assertRaises(PermissionError) as raised:
                    ws.Workspace._read_lock_file(self.lock)
                # Actual Windows kernel response, not injected PermissionError
                self.assertEqual(getattr(raised.exception, "winerror", None), 32)
        finally:
            self.assertTrue(close(h))
        self.assertEqual(ws.Workspace._read_lock_file(self.lock), {"token": "valid"})
        return len(opened)

    def test_real_windows_sharing_violation_multiplies_retry_budget_UNSAFE(self):
        self.assertEqual(self._exercise(), 12)

    @unittest.expectedFailure
    def test_positive_safety_real_windows_sharing_has_single_budget(self):
        self.assertLessEqual(self._exercise(), 3)


if __name__ == "__main__":
    unittest.main()
