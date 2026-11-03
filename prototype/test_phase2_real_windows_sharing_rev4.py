"""ADR-019 rev.4 real Windows sharing-denial test, exact production SHA.

Unlike monkeypatched PermissionError probes, this holds an actual Windows
CreateFileW handle permitting reads/writes but denying FILE_SHARE_DELETE.
The administrator must durably revoke the stale holder even if archiving
writer.lock cannot succeed under that kernel sharing rule; writers must not
bypass the blocked lock, and acquisition must recover after the handle closes.

Not a network share, SMB or power-loss qualification.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(os.name == "nt" and SOURCE, "requires native Windows and pinned source")
class RealWindowsSharingDenial(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.workspace as ws
        self.assertEqual(Path(ws.__file__).resolve(), source / "revisionworx" / "workspace.py")
        self.ws = ws
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ws"
        self.owner = ws.Workspace.create(self.root, "Windows share denial")
        self.addCleanup(self.owner.close)
        self.meta = self.root / ".revisionworx"
        self.lock = self.meta / "locks" / "writer.lock"
        self.token = self.owner._lock_token

    def _open_no_share_delete(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        CreateFileW = kernel.CreateFileW
        CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        CreateFileW.restype = wintypes.HANDLE
        CloseHandle = kernel.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL
        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 0x1
        FILE_SHARE_WRITE = 0x2
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80
        h = CreateFileW(str(self.lock), GENERIC_READ,
                        FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if h == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return h, CloseHandle

    def test_real_windows_handle_blocks_archive_but_not_durable_revocation(self):
        ws = self.ws
        h, close = self._open_no_share_delete()
        try:
            # Real filesystem sharing semantics: any attempt to rename
            # writer.lock must fail while h lacks FILE_SHARE_DELETE.
            with self.assertRaises(PermissionError):
                os.replace(self.lock, self.lock.with_name(".releasing-share-denied"))
            seen = ws.Workspace.break_writer_lock(
                self.root, self.token, "operator confirmed original owner stopped")
            self.assertEqual(seen["token"], self.token)
            self.assertIn(self.token, ws.Workspace._revoked_tokens(self.meta))
            self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"], self.token)
            with self.assertRaises(ws.WorkspaceError):
                with ws.Workspace.open(self.root, writable=True):
                    pass
        finally:
            self.assertTrue(close(h))
        # Once the OS sharing blocker disappears, the protocol can archive
        # the already-revoked lock and hand authority to a different token.
        with ws.Workspace.open(self.root, writable=True) as successor:
            self.assertNotEqual(successor._lock_token, self.token)
            self.assertIn(self.token, ws.Workspace._revoked_tokens(self.meta))
            self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"],
                             successor._lock_token)
        self.owner.close()

    def test_true_windows_sharing_violation_does_not_fence_successor(self):
        ws = self.ws
        # A live, UNREVOKED holder must stay authoritative even when a
        # competing open encounters an OS-held handle that blocks deletion.
        h, close = self._open_no_share_delete()
        try:
            with self.assertRaises(ws.WorkspaceLockedError):
                with ws.Workspace.open(self.root, writable=True):
                    pass
            self.assertNotIn(self.token, ws.Workspace._revoked_tokens(self.meta))
            self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"], self.token)
        finally:
            self.assertTrue(close(h))
        self.assertEqual(ws.Workspace.read_writer_lock(self.meta)["token"], self.token)


if __name__ == "__main__":
    unittest.main()
