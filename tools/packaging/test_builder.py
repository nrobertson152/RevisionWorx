#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast stdlib validation of packaging safety; no Qt/network required."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build import validate_locations


class PackagingSafetyTests(unittest.TestCase):
    def test_build_does_not_write_inside_source_or_existing_installation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            (source / "revisionworx" / "gui").mkdir(parents=True)
            (source / "pyproject.toml").write_text("", encoding="utf-8")
            (source / "revisionworx" / "gui" / "app.py").write_text("", encoding="utf-8")
            validate_locations(source.resolve(), (root / "unused").resolve())
            with self.assertRaisesRegex(ValueError, "outside"):
                validate_locations(source.resolve(), (source / "dist").resolve())
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                validate_locations(source.resolve(), existing.resolve())

    def test_build_rejects_non_revisionworx_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "not-a-checkout"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "pyproject.toml"):
                validate_locations(source.resolve(), (root / "output").resolve())


class SnapshotSafetyTests(unittest.TestCase):
    def test_snapshot_detects_content_changes(self):
        from smoke_workspace import snapshot
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "folder" / "sample.txt"
            file.parent.mkdir()
            file.write_bytes(b"original")
            a = snapshot(root)
            file.write_bytes(b"modified")
            b = snapshot(root)
            self.assertNotEqual(a, b)
            self.assertEqual(set(a), {"folder/sample.txt"})

    def test_isolated_environment_clears_real_workspace_variable(self):
        import os
        from unittest.mock import patch
        from smoke_workspace import isolated_env
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REVISIONWORX_WORKSPACE": "LIVE-DATA-MUST-NOT-BE-OPENED"}):
                env = isolated_env(Path(tmp))
            self.assertNotIn("REVISIONWORX_WORKSPACE", env)
            self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")


if __name__ == "__main__":
    unittest.main()
