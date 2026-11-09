"""Independent two-pass CAD snapshot mitigation pilot against exact Phase 2 core.

This is NOT production code. It tests whether a second source digest and
portable file identity check can reject two observed source-provenance
interleavings; and demonstrates the residual post-verification race. No
cooperative CAD write lock is asserted and no guarantee of an atomic snapshot
under arbitrary external writers is claimed.

All source modifications are actual OS file writes/renames; only the seam for
the timing of those operations is injected. Never modify .revisionworx files.
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
OLD = b"ALPHA-ASM-CAD-DATA-" + b"A" * 73
NEW = b"BRAVO-ASM-CAD-DATA-" + b"B" * 73
assert len(OLD) == len(NEW)


def guarded_two_pass(ws, source, content):
    """Test-only candidate. Verify path identity and a full second digest.

    Not safe against changes after the second digest completes; that is an
    unavoidable boundary without CAD writer cooperation or a real snapshot.
    """
    initial = source.stat()
    stored = content.ContentStore(ws).store_file(source)
    after_store = source.stat()
    if (initial.st_dev, initial.st_ino) != (after_store.st_dev, after_store.st_ino):
        raise content.SourceChangedError("working file identity changed during capture")
    digest, size = content.hash_file(source)
    at_second_end = source.stat()
    if (at_second_end.st_dev, at_second_end.st_ino) != (initial.st_dev, initial.st_ino):
        raise content.SourceChangedError("working file identity changed after second digest")
    if digest != stored.sha256 or size != stored.size:
        raise content.SourceChangedError("working file bytes differ from published capture")
    return stored


@unittest.skipUnless(SOURCE, "pinned production source checkout required")
class CadSecondPassPilot(unittest.TestCase):
    def setUp(self):
        root = Path(SOURCE).resolve()
        sys.path.insert(0, str(root))
        import revisionworx.atomicio as atom
        import revisionworx.workspace as workspace
        import revisionworx.content as content
        self.assertEqual(Path(content.__file__).resolve(), root / "revisionworx" / "content.py")
        self.atom, self.workspace, self.content = atom, workspace, content
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wsroot = Path(self.tmp.name) / "ws"
        self.source = Path(self.tmp.name) / "design.ipt"
        self.source.write_bytes(OLD)
        self.original_stat = self.source.stat()

    def _rewrite_inplace(self):
        with open(self.source, "r+b") as f:
            f.seek(0)
            f.write(NEW)
            f.flush()
            os.fsync(f.fileno())
        os.utime(self.source, ns=(self.original_stat.st_atime_ns, self.original_stat.st_mtime_ns))

    def _replace_with_rename(self):
        replacement = self.source.with_suffix(".tmp")
        replacement.write_bytes(NEW)
        os.utime(replacement, ns=(self.original_stat.st_atime_ns, self.original_stat.st_mtime_ns))
        os.replace(self.source, self.source.with_suffix(".previous"))
        os.rename(replacement, self.source)

    def _mutation_during_copy(self, action):
        original = self.atom._open_for_read
        events = []
        source = self.source
        class HookedReader:
            def __init__(self, fh):
                self.fh = fh
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.fh.__exit__(*args)
            def fileno(self):
                return self.fh.fileno()
            def read(self, count=-1):
                old = self.fh.read(count)
                if old and not events:
                    action()
                    events.append(True)
                return old
        def open_reader(path):
            fh = original(path)
            return HookedReader(fh) if Path(path) == source else fh
        with self.workspace.Workspace.create(self.wsroot, "pilot") as ws:
            before_seq = ws.head_seq()
            with patch.object(self.atom, "_open_for_read", side_effect=open_reader):
                with self.assertRaises(self.content.SourceChangedError):
                    guarded_two_pass(ws, source, self.content)
            self.assertEqual(events, [True], "must exercise actual source modification")
            self.assertEqual(ws.head_seq(), before_seq, "rejection must not commit a CAD revision")
            self.assertEqual(source.read_bytes(), NEW)

    def test_unchanged_source_passes_and_object_bytes_are_verified(self):
        with self.workspace.Workspace.create(self.wsroot, "pilot") as ws:
            stored = guarded_two_pass(ws, self.source, self.content)
            self.assertEqual(stored.sha256, hashlib.sha256(OLD).hexdigest())
            self.assertEqual(self.content.ContentStore(ws).read_verified(stored.sha256, len(OLD)), OLD)

    def test_same_identity_inplace_change_during_copy_is_rejected(self):
        self._mutation_during_copy(self._rewrite_inplace)
        self.assertEqual((self.source.stat().st_dev, self.source.stat().st_ino),
                         (self.original_stat.st_dev, self.original_stat.st_ino))
        self.assertEqual(self.source.stat().st_mtime_ns, self.original_stat.st_mtime_ns)

    def test_same_metadata_backup_rename_during_copy_is_rejected(self):
        self._mutation_during_copy(self._replace_with_rename)
        self.assertNotEqual((self.source.stat().st_dev, self.source.stat().st_ino),
                            (self.original_stat.st_dev, self.original_stat.st_ino))
        self.assertEqual(self.source.stat().st_mtime_ns, self.original_stat.st_mtime_ns)

    def _late_change_after_second_digest(self):
        original_hash = self.content.hash_file
        events = []
        def read_second_then_change(path):
            result = original_hash(path)
            if Path(path) == self.source and not events:
                self._rewrite_inplace()
                events.append(True)
            return result
        with self.workspace.Workspace.create(self.wsroot, "pilot") as ws:
            with patch.object(self.content, "hash_file", side_effect=read_second_then_change):
                result = guarded_two_pass(ws, self.source, self.content)
            self.assertEqual(events, [True])
            self.assertEqual(self.source.read_bytes(), NEW)
            self.assertEqual(self.source.stat().st_mtime_ns, self.original_stat.st_mtime_ns)
            self.assertEqual((self.source.stat().st_dev, self.source.stat().st_ino),
                             (self.original_stat.st_dev, self.original_stat.st_ino))
            return result

    def test_negative_control_second_digest_still_has_late_mutation_race(self):
        result = self._late_change_after_second_digest()
        self.assertEqual(result.sha256, hashlib.sha256(OLD).hexdigest())
        self.assertNotEqual(result.sha256, hashlib.sha256(self.source.read_bytes()).hexdigest())

    @unittest.expectedFailure
    def test_strict_atomic_snapshot_guarantee_is_not_established_by_second_digest(self):
        result = self._late_change_after_second_digest()
        self.assertEqual(result.sha256, hashlib.sha256(self.source.read_bytes()).hexdigest(),
                         "after-second-read mutation invalidates any atomic snapshot claim")


if __name__ == "__main__":
    unittest.main()
