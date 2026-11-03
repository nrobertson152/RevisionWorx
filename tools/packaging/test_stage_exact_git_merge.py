#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent regression controls for the test-only true Git merge stager.

Creates real divergent local Git histories. No remote repositories or network
access are needed, and no input checkout is ever pushed.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from stage_exact_git_merge import stage, output


def run(*args: str) -> str:
    result = subprocess.run(["git", *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8")
    return result.stdout.strip()


def write(root: Path, file: str, content: str) -> None:
    target = root / file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def commit(root: Path, msg: str) -> str:
    run("-C", str(root), "add", ".")
    run("-C", str(root), "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid", "commit", "-qm", msg)
    return run("-C", str(root), "rev-parse", "HEAD")


class LiteralMergeStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base"
        self.core = self.root / "core"
        self.gui = self.root / "gui"
        self.receipt = self.root / "merge.json"
        self.base.mkdir()
        run("init", "-q", str(self.base))
        run("-C", str(self.base), "config", "core.autocrlf", "false")
        write(self.base, ".gitattributes", "*.py text eol=lf\n")
        for path in ("revisionworx/atomicio.py", "revisionworx/workspace.py",
                     "revisionworx/authority.py", "revisionworx/documents.py",
                     "revisionworx/index.py"):
            write(self.base, path, "base " + path + "\n")
        write(self.base, "revisionworx/gui/qt/mainwindow.py", "common window\n")
        write(self.base, "tests/test_qt_gui.py", "common test\n")
        commit(self.base, "base for two divergent inputs")
        for dest in (self.core, self.gui):
            # Apply LF checkout policy *during* clone, not afterward: Windows Git\n            # may otherwise convert every untouched base file to CRLF and\n            # manufacture false merge conflicts across unrelated files.\n            run("-c", "core.autocrlf=false", "clone", "-q", str(self.base), str(dest))\n            run("-C", str(dest), "config", "core.autocrlf", "false")\n            if run("-C", str(dest), "status", "--porcelain"):\n                raise AssertionError("fresh fixture clone is unexpectedly dirty")
        write(self.core, "revisionworx/atomicio.py", "corrected writer authority\n")
        write(self.core, "revisionworx/index.py", "authoritative core index\n")
        self.core_sha = commit(self.core, "corrected core")
        write(self.gui, "revisionworx/gui/qt/mainwindow.py", "desktop alpha\n")
        write(self.gui, "tests/test_qt_gui.py", "desktop oracle\n")
        self.gui_sha = commit(self.gui, "independent desktop alpha")

    def merge(self) -> dict:
        return stage(self.core, self.gui, self.receipt,
                     self.core_sha, self.gui_sha)

    def test_real_two_parent_git_merge_retains_core_and_gui(self) -> None:
        result = self.merge()
        self.assertEqual(result["parent_shas"], [self.core_sha, self.gui_sha])
        self.assertEqual(result["core_sha"], self.core_sha)
        self.assertEqual(result["gui_sha"], self.gui_sha)
        self.assertEqual(result["status"], "TEST-ONLY THREE-WAY GIT MERGE SUCCEEDED")
        self.assertEqual(len(result["core_only_authority_blob_oids"]), 4)
        self.assertEqual((self.core / "revisionworx/index.py").read_text(),
                         "authoritative core index\n")
        self.assertEqual((self.core / "revisionworx/gui/qt/mainwindow.py").read_text(),
                         "desktop alpha\n")
        self.assertEqual(output(self.gui, "rev-parse", "HEAD"), self.gui_sha)
        self.assertEqual(output(self.gui, "status", "--porcelain"), "")
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.assertFalse(result["pushed_to_remote"])
        self.assertFalse(result["release_authorized"])

    def test_mismatched_core_sha_fails_without_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "core checkout SHA"):
            stage(self.core, self.gui, self.receipt, "0"*40, self.gui_sha)
        self.assertEqual(output(self.core, "rev-parse", "HEAD"), self.core_sha)
        self.assertFalse(self.receipt.exists())

    def test_mismatched_gui_sha_fails_without_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "GUI checkout SHA"):
            stage(self.core, self.gui, self.receipt, self.core_sha, "f"*40)
        self.assertEqual(output(self.core, "status", "--porcelain"), "")
        self.assertFalse(self.receipt.exists())

    def test_dirty_input_is_refused_before_merge(self) -> None:
        write(self.core, "revisionworx/index.py", "uncommitted\n")
        with self.assertRaisesRegex(ValueError, "not clean"):
            self.merge()
        self.assertEqual(output(self.core, "rev-parse", "HEAD"), self.core_sha)

    def test_unresolved_true_git_conflict_fails_closed(self) -> None:
        write(self.core, "revisionworx/gui/qt/mainwindow.py", "conflicting core GUI\n")
        self.core_sha = commit(self.core, "independent conflicting GUI edits")
        with self.assertRaisesRegex(RuntimeError, "real Git integration has conflicts"):
            self.merge()
        data = json.loads(self.receipt.read_text())
        self.assertIn("revisionworx/gui/qt/mainwindow.py", data["unmerged_paths"])
        self.assertEqual(data["core_sha"], self.core_sha)
        self.assertEqual(data["gui_sha"], self.gui_sha)
        self.assertEqual(output(self.gui, "status", "--porcelain"), "")

    def test_outside_gui_authority_mutation_is_refused(self) -> None:
        write(self.gui, "revisionworx/documents.py", "unauthorized GUI core change\n")
        self.gui_sha = commit(self.gui, "unauthorized core mutation")
        with self.assertRaisesRegex(RuntimeError, "core-only authority file changed"):
            self.merge()
        self.assertEqual(output(self.gui, "status", "--porcelain"), "")
        self.assertFalse(self.receipt.exists())

    def test_existing_receipt_never_overwritten(self) -> None:
        self.receipt.write_text("original evidence\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "receipt already exists"):
            self.merge()
        self.assertEqual(self.receipt.read_text(), "original evidence\n")
        self.assertEqual(output(self.core, "status", "--porcelain"), "")

    def test_receipt_inside_git_sources_is_forbidden(self) -> None:
        for root in (self.core, self.gui):
            with self.subTest(root=root):
                with self.assertRaisesRegex(ValueError, "outside both"):
                    stage(self.core, self.gui, root / "receipt.json",
                          self.core_sha, self.gui_sha)
                self.assertFalse((root / "receipt.json").exists())

    def test_source_alias_is_forbidden(self) -> None:
        with self.assertRaisesRegex(ValueError, "distinct disposable"):
            stage(self.core, self.core, self.receipt,
                  self.core_sha, self.core_sha)

if __name__ == "__main__":
    unittest.main()
