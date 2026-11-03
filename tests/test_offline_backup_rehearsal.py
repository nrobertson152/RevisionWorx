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


    def test_closed_workspace_tree_copy_preserves_original_bytes(self):
        """Synthetic closed workspace fixture; does not claim core reopen acceptance."""
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive, restored = (root / n for n in ("workspace", "archive", "restored"))
            files = {
                "project.json": b'{"version":1,"documents":["parts/A.ipt"]}',
                "journal/000001.jsonl": b'{"kind":"revision","sha256":"immutable"}\\n',
                "objects/aa/object": b"\\x00\\xff immutable revision payload",
                "parts/A.ipt": b"CAD source must not change",
                "parts/sub/零件.step": b"STEP\\x00\\xff",
            }
            for name, data in files.items():
                path = src / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            before = {name: hashlib.sha256((src / name).read_bytes()).hexdigest() for name in files}
            self.assertEqual(mod.backup(src, archive), len(files))
            self.assertEqual(mod.restore(archive, restored), len(files))
            for name, original_sha in before.items():
                self.assertEqual(hashlib.sha256((src / name).read_bytes()).hexdigest(), original_sha)
                self.assertEqual(hashlib.sha256((restored / name).read_bytes()).hexdigest(), original_sha)
            self.assertFalse((src / mod.MANIFEST).exists())
            self.assertFalse((restored / mod.MANIFEST).exists())

    def test_interrupted_backup_is_not_accepted_as_complete(self):
        """Copy errors retain forensic output without publishing a valid manifest."""
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "source", root / "archive"
            src.mkdir()
            (src / "one").write_bytes(b"one")
            (src / "two").write_bytes(b"two")
            real_copy = mod.shutil.copyfile
            calls = [0]

            def interrupted(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError("simulated interrupted copy")
                return real_copy(*args, **kwargs)

            with patch.object(mod.shutil, "copyfile", side_effect=interrupted):
                with self.assertRaises(OSError):
                    mod.backup(src, archive)
            self.assertTrue(archive.is_dir())
            self.assertFalse((archive / mod.MANIFEST).exists())
            with self.assertRaises(mod.BackupError):
                mod.verify(archive)
            with self.assertRaises(mod.BackupError):
                mod.restore(archive, root / "restored")
            self.assertFalse((root / "restored").exists())
            self.assertEqual((src / "one").read_bytes(), b"one")
            self.assertEqual((src / "two").read_bytes(), b"two")

    def test_extra_file_after_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "source", root / "archive"
            src.mkdir()
            (src / "original").write_bytes(b"untouched")
            mod.backup(src, archive)
            (archive / "unexpected").write_bytes(b"injected")
            with self.assertRaises(mod.BackupError):
                mod.verify(archive)
            with self.assertRaises(mod.BackupError):
                mod.restore(archive, root / "restored")
            self.assertFalse((root / "restored").exists())


    def test_duplicate_manifest_keys_fail_closed(self):
        """Duplicate JSON keys must not silently replace authoritative hashes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "source", root / "archive"
            src.mkdir()
            (src / "part.ipt").write_bytes(b"original")
            mod.backup(src, archive)
            manifest = archive / mod.MANIFEST
            import json
            original = json.loads(manifest.read_text(encoding="utf-8"))
            entry = json.dumps(original["files"]["part.ipt"])
            manifest.write_text(
                '{"version":1,"files":{"part.ipt":' + entry +
                ',"part.ipt":' + entry + '}}', encoding="utf-8"
            )
            with self.assertRaisesRegex(mod.BackupError, "duplicate"):
                mod.verify(archive)
            with self.assertRaises(mod.BackupError):
                mod.restore(archive, root / "restored")
            self.assertFalse((root / "restored").exists())

    def test_source_mutation_during_backup_rejected_without_manifest(self):
        """A changed closed-workspace source cannot produce a valid backup."""
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "source", root / "archive"
            src.mkdir()
            original = src / "part.step"
            original.write_bytes(b"before")
            real_copy = mod.shutil.copyfile

            def mutating_copy(source, destination, **kwargs):
                result = real_copy(source, destination, **kwargs)
                original.write_bytes(b"after")
                return result

            with patch.object(mod.shutil, "copyfile", side_effect=mutating_copy):
                with self.assertRaisesRegex(mod.BackupError, "source changed"):
                    mod.backup(src, archive)
            self.assertFalse((archive / mod.MANIFEST).exists())
            with self.assertRaises(mod.BackupError):
                mod.verify(archive)
            self.assertEqual(original.read_bytes(), b"after")

    def test_interrupted_restore_cannot_be_mistaken_for_verified(self):
        """Partial restore is visible and must never be treated as accepted."""
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive, restored = (root / name for name in ("source", "archive", "restored"))
            src.mkdir()
            (src / "a").write_bytes(b"a")
            (src / "b").write_bytes(b"b")
            mod.backup(src, archive)
            real_copy = mod.shutil.copyfile
            calls = [0]

            def interrupted(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError("simulated restore interruption")
                return real_copy(*args, **kwargs)

            with patch.object(mod.shutil, "copyfile", side_effect=interrupted):
                with self.assertRaises(OSError):
                    mod.restore(archive, restored)
            self.assertTrue(restored.exists())
            self.assertFalse((restored / mod.MANIFEST).exists())
            self.assertEqual(len(list(restored.iterdir())), 1)
            self.assertEqual(len(mod.verify(archive)), 2)
            with self.assertRaises(mod.BackupError):
                mod.restore(archive, restored)


    def test_empty_nested_directory_is_not_silently_lost(self):
        """File-only manifests cannot represent empty workspace directories."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "workspace", root / "archive"
            (src / "empty" / "nested").mkdir(parents=True)
            (src / "document.ipt").write_bytes(b"untouched")
            with self.assertRaisesRegex(mod.BackupError, "empty directory"):
                mod.backup(src, archive)
            self.assertFalse(archive.exists())
            self.assertTrue((src / "empty" / "nested").is_dir())
            self.assertEqual((src / "document.ipt").read_bytes(), b"untouched")

    def test_empty_directory_added_to_archive_invalidates_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "workspace", root / "archive"
            src.mkdir()
            (src / "document.ipt").write_bytes(b"unchanged")
            mod.backup(src, archive)
            (archive / "injected-empty").mkdir()
            with self.assertRaises(mod.BackupError):
                mod.verify(archive)
            with self.assertRaises(mod.BackupError):
                mod.restore(archive, root / "restored")
            self.assertFalse((root / "restored").exists())

    def test_casefold_collision_in_source_refused(self):
        """Case-sensitive Linux trees must not create lossy Windows restores."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source"
            src.mkdir()
            (src / "Part.ipt").write_bytes(b"first")
            (src / "part.ipt").write_bytes(b"second")
            if (src / "Part.ipt").read_bytes() != b"first":
                self.skipTest("filesystem does not support distinct case variants")
            with self.assertRaisesRegex(mod.BackupError, "case-insensitive path collision"):
                mod.backup(src, root / "backup")
            self.assertFalse((root / "backup").exists())

    def test_casefold_collision_in_manifest_refused(self):
        """A crafted archive cannot specify aliases with different casing."""
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, archive = root / "source", root / "archive"
            src.mkdir()
            (src / "Part.ipt").write_bytes(b"original")
            mod.backup(src, archive)
            manifest = archive / mod.MANIFEST
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["files"]["part.ipt"] = data["files"]["Part.ipt"].copy()
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(mod.BackupError, "case-insensitive manifest path collision"):
                mod.verify(archive)
            with self.assertRaises(mod.BackupError):
                mod.restore(archive, root / "restored")
            self.assertFalse((root / "restored").exists())

if __name__ == "__main__":
    unittest.main()
