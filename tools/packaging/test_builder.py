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


if __name__ == "__main__":
    unittest.main()
