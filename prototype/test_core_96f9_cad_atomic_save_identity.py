"""Exact-core content snapshot substitution regression; separate from .revisionworx threat model.

A CAD editor may save by writing a temporary file and renaming over the
working file. If the replacement has the SAME size and mtime, size+mtime
alone do not prove that the open file still represents the current path.

This test forces the replacement *after the original reader handle opens*
but before content is copied. It uses actual native rename and file I/O.
On Windows this also exercises the new FILE_SHARE_DELETE reader path.

Tests labeled UNSAFE explicitly document old behavior, not acceptance.
Positive XFAIL is the desired SourceChangedError result.
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


@unittest.skipUnless(SOURCE, "requires exact pinned core checkout")
class CadAtomicReplacement(unittest.TestCase):
    def setUp(self):
        source = Path(SOURCE).resolve()
        sys.path.insert(0, str(source))
        import revisionworx.atomicio as atom
        import revisionworx.content as content
        import revisionworx.workspace as workspace
        self.assertEqual(Path(atom.__file__).resolve(), source / "revisionworx" / "atomicio.py")
        self.atom, self.content, self.workspace = atom, content, workspace
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        self.source = Path(self.tmp.name) / "assembly.ipt"
        self.source.write_bytes(b"OLD-CAD-DATA")
        self.old_stat = self.source.stat()

    def _capture_after_replacement(self, *, preserve_mtime: bool):
        source, atom = self.source, self.atom
        original_open = atom._open_for_read
        replacement_bytes = b"NEW-CAD-DATA"
        self.assertEqual(len(replacement_bytes), len(source.read_bytes()))
        happened = []
        def open_then_save_by_rename(path):
            fh = original_open(path)
            if Path(path) == source:
                replacement = source.with_suffix(".tmp")
                replacement.write_bytes(replacement_bytes)
                if preserve_mtime:
                    os.utime(replacement, ns=(self.old_stat.st_atime_ns, self.old_stat.st_mtime_ns))
                os.replace(replacement, source)
                happened.append(True)
            return fh
        with self.workspace.Workspace.create(self.root, "cad replace") as ws, \
             patch.object(atom, "_open_for_read", side_effect=open_then_save_by_rename):
            try:
                result = self.content.ContentStore(ws).store_file(source)
                outcome = ("stored", result.sha256)
            except self.content.SourceChangedError:
                outcome = ("changed", None)
            self.assertEqual(happened, [True])
            self.assertEqual(source.read_bytes(), replacement_bytes)
            self.assertEqual(source.stat().st_size, self.old_stat.st_size)
            if preserve_mtime:
                self.assertEqual(source.stat().st_mtime_ns, self.old_stat.st_mtime_ns)
            if outcome[0] == "stored":
                self.assertEqual(
                    self.content.ContentStore(ws).read_verified(outcome[1], 4096),
                    b"OLD-CAD-DATA", "captured bytes are from old open handle, not current CAD pathname")
            return outcome

    def test_unsafe_same_size_same_mtime_atomic_save_is_not_detected(self):
        self.assertEqual(self._capture_after_replacement(preserve_mtime=True)[0], "stored")

    @unittest.expectedFailure
    def test_positive_safety_atomic_save_with_preserved_mtime_must_be_rejected(self):
        self.assertEqual(self._capture_after_replacement(preserve_mtime=True)[0], "changed",
                         "the pathname now points to a different file: SourceChangedError expected")

    def test_control_changed_mtime_atomic_save_is_rejected(self):
        # Ensure mtime differs even on a coarse-resolution filesystem.
        changed = []
        original = self.atom._open_for_read
        def replace_with_different_mtime(path):
            fh = original(path)
            if Path(path) == self.source:
                replacement = self.source.with_suffix(".tmp")
                replacement.write_bytes(b"NEW-CAD-DATA")
                os.utime(replacement, ns=(self.old_stat.st_atime_ns, self.old_stat.st_mtime_ns + 10_000_000_000))
                os.replace(replacement, self.source)
                changed.append(True)
            return fh
        with self.workspace.Workspace.create(self.root, "cad replace") as ws, \
             patch.object(self.atom, "_open_for_read", side_effect=replace_with_different_mtime):
            with self.assertRaises(self.content.SourceChangedError):
                self.content.ContentStore(ws).store_file(self.source)
        self.assertEqual(changed, [True])


if __name__ == "__main__":
    unittest.main()
