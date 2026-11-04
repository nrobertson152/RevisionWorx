"""Actual Win32 handle denial: latest a03f core must have ONE bounded retry budget.

Control at historical 22a5273 is independently verified in
test_core_22a5273_real_windows_retry_stack: nested retry multiplied 4x3.
The latest source no longer wraps atomicio.read_json in another retry loop.
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


@unittest.skipUnless(os.name == "nt" and SOURCE, "native Windows and pinned core required")
class CurrentCoreNativeSharingBudget(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock = Path(self.tmp.name) / "writer.lock"
        self.lock.write_text('{"token":"native"}', encoding="utf8")

    def _no_share_handle(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(str(self.lock), 0x80000000, 0, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle, close

    def test_single_budget_for_genuine_kernel_winerror32(self):
        atom = self.ws.atomicio
        underlying = atom._open_sharing_delete
        calls = []
        h, close = self._no_share_handle()
        try:
            def count(path):
                calls.append(1)
                return underlying(path)
            with patch.object(atom, "SHARING_RETRIES", 3), \
                 patch.object(atom, "SHARING_DELAY", 0), \
                 patch.object(atom, "_open_sharing_delete", side_effect=count):
                with self.assertRaises(PermissionError) as caught:
                    atom.read_json(self.lock)
                self.assertEqual(getattr(caught.exception, "winerror", None), 32)
            self.assertEqual(len(calls), 3, "one bounded retry budget is permitted")
        finally:
            self.assertTrue(close(h))
        self.assertEqual(atom.read_json(self.lock)["token"], "native")


if __name__ == "__main__":
    unittest.main()
