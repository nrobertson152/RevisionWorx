# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated tests for copy-only offline backup/restore rehearsal."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "packaging" / "offline_backup_rehearsal.py"
spec = importlib.util.spec_from_file_location("offline_backup_rehearsal", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BackupRehearsalTests(unittest.TestCase):
    def test_backup_verify_restore_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, backup, restored = (root / n for n in ("source", "backup", "restored"))
            (src / "nested").mkdir(parents=True)
            (src / "nested" / "CAD 零件.ipt").write_bytes(b"\x00\xffCAD")
            self.assertEqual(mod.backup(src, backup), 1)
            self.assertEqual(len(mod.verify(backup)), 1)
            self.assertEqual(mod.restore(backup, restored), 1)
            self.assertEqual((restored / "nested" / "CAD 零件.ipt").read_bytes(), b"\x00\xffCAD")
            self.assertFalse((restored / mod.MANIFEST).exists())

    def test_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, backup = root / "source", root / "backup"
            src.mkdir()
            (src / "part.step").write_bytes(b"original")
            mod.backup(src, backup)
            (backup / "part.step").write_bytes(b"changed!")
            with self.assertRaises(mod.BackupError):
                mod.verify(backup)
            with self.assertRaises(mod.BackupError):
                mod.restore(backup, root / "restored")
            self.assertFalse((root / "restored").exists())

    def test_refuses_overwrite_and_nested_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source"
            src.mkdir()
            with self.assertRaises(mod.BackupError):
                mod.backup(src, src / "nested")
            with self.assertRaises(mod.BackupError):
                mod.backup(src, src)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(mod.BackupError):
                mod.backup(src, existing)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source"
            src.mkdir()
            target = root / "target"
            target.write_bytes(b"x")
            try:
                (src / "link").symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not available")
            with self.assertRaises(mod.BackupError):
                mod.backup(src, root / "backup")

    def test_manifest_traversal_and_version_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, backup = root / "source", root / "backup"
            src.mkdir()
            (src / "a").write_bytes(b"a")
            mod.backup(src, backup)
            import json
            manifest = backup / mod.MANIFEST
            data = json.loads(manifest.read_text(encoding="utf-8"))
            entry = data["files"].pop("a")
            data["files"]["../escape"] = entry
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(mod.BackupError):
                mod.verify(backup)
            data["files"] = {"a": entry}
            data["version"] = 99
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(mod.BackupError):
                mod.verify(backup)


if __name__ == "__main__":
    unittest.main()
