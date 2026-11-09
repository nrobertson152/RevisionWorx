#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast local-only safety regression tests for disposable composite source staging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from stage_corrected_core_gui import stage, revision, clean


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def init_fixture(path: Path, files: dict[str, bytes]) -> str:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    for rel, content in files.items():
        dest = path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    git(path, "add", ".")
    git(path, "-c", "user.name=CI Test", "-c", "user.email=ci@example.invalid",
        "commit", "-qm", "isolated source staging fixture")
    return revision(path)


class CompositeSourceStagingSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.core = self.base / "core"
        self.gui = self.base / "gui"
        self.receipt = self.base / "receipt.json"
        self.core_sha = init_fixture(self.core, {
            "pyproject.toml": b"[project]\nname='revisionworx-test'\nversion='0.0.0'\n",
            "revisionworx/gui/qt/mainwindow.py": b"old GUI\n",
            "revisionworx/gui/obsolete.py": b"core-only GUI to be replaced\n",
            "revisionworx/workspace.py": b"authoritative CORE API\n",
        })
        self.gui_sha = init_fixture(self.gui, {
            "revisionworx/gui/qt/mainwindow.py": b"new GUI\n",
            "revisionworx/gui/qt/startpage.py": b"new start\n",
            "revisionworx/gui/controller.py": b"desktop controller\n",
            "revisionworx/workspace.py": b"wrong CORE should NEVER overwrite\n",
        })

    def execute(self):
        return stage(self.core, self.gui, self.receipt, self.core_sha, self.gui_sha)

    def test_stage_only_gui_and_record_both_source_shas_and_hashes(self) -> None:
        before_core = (self.core / "revisionworx/workspace.py").read_bytes()
        before_gui = (self.gui / "revisionworx/gui/qt/mainwindow.py").read_bytes()
        result = self.execute()
        self.assertEqual(result["core_sha"], self.core_sha)
        self.assertEqual(result["gui_sha"], self.gui_sha)
        self.assertTrue(result["composite_tree_is_dirty_by_design"])
        self.assertEqual(result["composite_git_head"], self.core_sha)
        self.assertEqual((self.core / "revisionworx/workspace.py").read_bytes(), before_core)
        self.assertEqual((self.gui / "revisionworx/gui/qt/mainwindow.py").read_bytes(), before_gui)
        self.assertEqual((self.core / "revisionworx/gui/qt/mainwindow.py").read_bytes(), before_gui)
        self.assertFalse((self.core / "revisionworx/gui/obsolete.py").exists())
        self.assertTrue(clean(self.gui))
        self.assertFalse(clean(self.core))
        self.assertEqual(revision(self.core), self.core_sha)
        self.assertEqual(
            result["overlaid_files_sha256"]["qt/mainwindow.py"],
            hashlib.sha256(before_gui).hexdigest(),
        )
        self.assertEqual(json.loads(self.receipt.read_text()), result)

    def test_reject_wrong_core_revision_without_changing_files(self) -> None:
        prior = (self.core / "revisionworx/gui/qt/mainwindow.py").read_bytes()
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            stage(self.core, self.gui, self.receipt, "0" * 40, self.gui_sha)
        self.assertEqual((self.core / "revisionworx/gui/qt/mainwindow.py").read_bytes(), prior)
        self.assertFalse(self.receipt.exists())

    def test_reject_wrong_gui_revision_without_changing_files(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            stage(self.core, self.gui, self.receipt, self.core_sha, "f" * 40)
        self.assertTrue(clean(self.core))
        self.assertFalse(self.receipt.exists())

    def test_reject_dirty_core_before_any_overlay(self) -> None:
        (self.core / "revisionworx/workspace.py").write_bytes(b"uncommitted core\n")
        with self.assertRaisesRegex(ValueError, "already modified"):
            self.execute()
        self.assertEqual((self.core / "revisionworx/gui/qt/mainwindow.py").read_bytes(), b"old GUI\n")

    def test_reject_dirty_gui_before_any_overlay(self) -> None:
        (self.gui / "revisionworx/gui/controller.py").write_bytes(b"uncommitted GUI\n")
        with self.assertRaisesRegex(ValueError, "already modified"):
            self.execute()
        self.assertTrue(clean(self.core))

    def test_reject_overwriting_existing_receipt(self) -> None:
        self.receipt.write_bytes(b"old acceptance evidence\n")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.execute()
        self.assertEqual(self.receipt.read_bytes(), b"old acceptance evidence\n")
        self.assertTrue(clean(self.core))

    def test_reject_receipt_within_either_source(self) -> None:
        for bad in (self.core / "receipt.json", self.gui / "receipt.json"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "outside both"):
                    stage(self.core, self.gui, bad, self.core_sha, self.gui_sha)
                self.assertFalse(bad.exists())
        self.assertTrue(clean(self.core))
        self.assertTrue(clean(self.gui))

    def test_reject_overlapping_or_identical_checkouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be separate"):
            stage(self.core, self.core, self.receipt, self.core_sha, self.core_sha)
        self.assertTrue(clean(self.core))

    def test_reject_unexpected_gui_layout(self) -> None:
        # A clean but wrong Git tree must not delete a valid core GUI folder.
        alien = self.base / "alien"
        alien_sha = init_fixture(alien, {"revisionworx/gui/notes.txt": b"not a GUI\n"})
        with self.assertRaisesRegex(ValueError, "layout"):
            stage(self.core, alien, self.receipt, self.core_sha, alien_sha)
        self.assertEqual((self.core / "revisionworx/gui/qt/mainwindow.py").read_bytes(), b"old GUI\n")
        self.assertTrue(clean(self.core))


if __name__ == "__main__":
    unittest.main()
