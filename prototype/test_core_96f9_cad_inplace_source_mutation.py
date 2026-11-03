"""Independent Tier-1 CAD source in-place mutation against core 96f9c368.

The earlier source-identity safeguard (stat/fstat st_dev/st_ino) catches an
atomic backup+rename of the source pathname. It does NOT detect an in-place
rewrite of the very same file object that preserves both size and mtime.

Actual OS file I/O (no monkeypatched PermissionError, no editing .revisionworx).
A wrapped real reader copies OLD bytes, then another handle rewrites the same
source before the next read. Exact size+mtime+file identity survive, while the
actual current source bytes differ. This is a negative-control test suite:
UNSAFE pass + strict expected-failure safety oracle; no claim of acceptance.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")
OLD = b"OLD-ASM-CAD-DATA"
NEW = b"NEW-ASM-CAD-DATA"
assert len(OLD) == len(NEW)


@unittest.skipUnless(SOURCE, "requires exact pinned production checkout")
class InPlaceSourceRewrite(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.atomicio as atom
        import revisionworx.workspace as workspace
        import revisionworx.content as content
        self.assertEqual(Path(atom.__file__).resolve(), source / "revisionworx" / "atomicio.py")
        self.atom, self.workspace, self.content = atom, workspace, content
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace_root = Path(self.temp.name) / "ws"
        self.cad = Path(self.temp.name) / "assembly.ipt"
        self.cad.write_bytes(OLD)
        self.initial = self.cad.stat()

    def _run(self, *, preserve_mtime):
        target = self.cad
        initial = self.initial
        opener = self.atom._open_for_read
        events = []
        class RewriteAfterFirstRead:
            def __init__(self, handle):
                self._handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self._handle.__exit__(*args)
            def read(self, size=-1):
                original_bytes = self._handle.read(size)
                if original_bytes and not events:
                    with open(target, "r+b") as writer:
                        writer.seek(0)
                        writer.write(NEW)
                        writer.flush()
                        os.fsync(writer.fileno())
                    if preserve_mtime:
                        os.utime(target, ns=(initial.st_atime_ns, initial.st_mtime_ns))
                    else:
                        os.utime(target, ns=(initial.st_atime_ns, initial.st_mtime_ns + 10_000_000_000))
                    events.append(True)
                return original_bytes
            def fileno(self):
                return self._handle.fileno()

        def injected_open(path):
            h = opener(path)
            return RewriteAfterFirstRead(h) if Path(path) == target else h

        with self.workspace.Workspace.create(self.workspace_root, "in-place mutation") as ws, \
             patch.object(self.atom, "_open_for_read", side_effect=injected_open):
            try:
                stored = self.content.ContentStore(ws).store_file(target)
                outcome = ("stored", stored.sha256)
            except self.content.SourceChangedError:
                outcome = ("changed", None)
            self.assertEqual(events, [True], "the real in-place writer was not exercised")
            final = target.stat()
            self.assertEqual((initial.st_dev, initial.st_ino),
                             (final.st_dev, final.st_ino),
                             "same inode/file ID: identity comparison cannot detect in-place replacement")
            self.assertEqual((initial.st_size, len(NEW)), (len(OLD), len(NEW)))
            self.assertEqual(target.read_bytes(), NEW)
            if preserve_mtime:
                self.assertEqual(final.st_mtime_ns, initial.st_mtime_ns)
            if outcome[0] == "stored":
                self.assertEqual(outcome[1], hashlib.sha256(OLD).hexdigest())
                self.assertEqual(self.content.ContentStore(ws).read_verified(outcome[1], 1024), OLD)
            return outcome

    def test_unsafe_same_identity_size_and_mtime_accepts_old_content(self):
        self.assertEqual(self._run(preserve_mtime=True)[0], "stored")

    @unittest.expectedFailure
    def test_positive_safety_same_identity_same_mtime_source_change_must_be_detected(self):
        self.assertEqual(self._run(preserve_mtime=True)[0], "changed",
                         "a changed source must not be recorded as a clean snapshot")

    def test_control_changed_mtime_catches_in_place_rewrite(self):
        self.assertEqual(self._run(preserve_mtime=False)[0], "changed")


if __name__ == "__main__":
    unittest.main()
