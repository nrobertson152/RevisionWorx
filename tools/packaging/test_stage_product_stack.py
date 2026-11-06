#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent four-source real Git staging safeguards on local test repositories."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from stage_product_stack import PROTECTED, compose, read


def call(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def write(repo: Path, name: str, value: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def commit(repo: Path, label: str) -> str:
    call("-C", str(repo), "add", "-A")
    call("-C", str(repo), "-c", "user.name=CI Fixture",
         "-c", "user.email=fixture@example.invalid", "commit", "-qm", label)
    return call("-C", str(repo), "rev-parse", "HEAD")


class FourSourceStaging(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        call("init", "-q", str(self.base))
        call("-C", str(self.base), "config", "core.autocrlf", "false")
        write(self.base, ".gitattributes", "*.py text eol=lf\n")
        for p in PROTECTED:
            write(self.base, p, "common original " + p + "\n")
        write(self.base, "revisionworx/gui/qt/mainwindow.py", "common GUI\n")
        write(self.base, "revisionworx/bom/__init__.py", "common BOM\n")
        write(self.base, "revisionworx/changes/__init__.py", "common ECR\n")
        commit(self.base, "base repository")
        self.repos = {role: self.root / role
                      for role in ("core", "gui", "bom", "changes")}
        for repo in self.repos.values():
            # Set line-ending policy *before* worktree checkout on Windows.
            call("-c", "core.autocrlf=false", "clone", "-q",
                 str(self.base), str(repo))
            call("-C", str(repo), "config", "core.autocrlf", "false")
            self.assertEqual(read(repo, "status", "--porcelain"), "")
        write(self.repos["core"], "revisionworx/atomicio.py", "new authority\n")
        write(self.repos["gui"], "revisionworx/gui/qt/mainwindow.py", "desktop GUI\n")
        write(self.repos["bom"], "revisionworx/bom/__init__.py", "BOM v1\n")
        write(self.repos["changes"], "revisionworx/changes/__init__.py", "ECR v1\n")
        self.shas = {r: commit(p, "update " + r) for r, p in self.repos.items()}
        self.receipt = self.root / "receipt.json"

    def execute(self, *, receipt: Path | None = None):
        return compose(
            self.repos["core"], self.shas["core"],
            {k: (self.repos[k], self.shas[k]) for k in ("gui", "bom", "changes")},
            receipt or self.receipt,
        )

    def test_genuine_three_merges_preserve_sources_and_authority(self):
        result = self.execute()
        self.assertEqual(result["status"], "TEST-ONLY COMPOSITE SOURCE PREPARED")
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual([s["role"] for s in result["steps"]],
                         ["gui", "bom", "changes"])
        self.assertTrue(all(len(s["parent_shas"]) == 2 for s in result["steps"]))
        self.assertEqual(result["synthetic_final_sha"],
                         read(self.repos["core"], "rev-parse", "HEAD"))
        self.assertEqual(result["core_sha"], self.shas["core"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["pushed_to_remote"])
        self.assertEqual(len(result["core_authority_blob_oids"]), len(PROTECTED))
        for role in ("gui", "bom", "changes"):
            self.assertEqual(read(self.repos[role], "rev-parse", "HEAD"),
                             self.shas[role])
            self.assertEqual(read(self.repos[role], "status", "--porcelain"), "")
        self.assertEqual(json.loads(self.receipt.read_text()), result)

    def test_core_sha_mismatch_refused_before_writes(self):
        with self.assertRaisesRegex(ValueError, "core source SHA mismatch"):
            compose(self.repos["core"], "0" * 40,
                    {r: (self.repos[r], self.shas[r])
                     for r in ("gui", "bom", "changes")}, self.receipt)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(read(self.repos["core"], "rev-parse", "HEAD"),
                         self.shas["core"])

    def test_gui_sha_mismatch_refused_before_writes(self):
        wrong = dict(self.shas)
        wrong["gui"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "gui source SHA mismatch"):
            compose(self.repos["core"], self.shas["core"],
                    {r: (self.repos[r], wrong[r])
                     for r in ("gui", "bom", "changes")}, self.receipt)
        self.assertFalse(self.receipt.exists())

    def test_dirty_bom_checkout_refused(self):
        write(self.repos["bom"], "uncommitted.txt", "uncommitted\n")
        with self.assertRaisesRegex(ValueError, "source checkout is dirty"):
            self.execute()
        self.assertFalse(self.receipt.exists())

    def test_missing_ecr_module_fails_closed(self):
        (self.repos["changes"] / "revisionworx/changes/__init__.py").unlink()
        self.shas["changes"] = commit(self.repos["changes"], "remove marker")
        with self.assertRaisesRegex(ValueError, "changes lacks"):
            self.execute()
        self.assertFalse(self.receipt.exists())

    def test_existing_receipt_refused_without_overwrite(self):
        self.receipt.write_text("original\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "overwrite source receipt"):
            self.execute()
        self.assertEqual(self.receipt.read_text(), "original\n")

    def test_receipt_must_be_external_to_checkout(self):
        for role in self.repos:
            with self.subTest(role=role):
                with self.assertRaisesRegex(ValueError, "outside every source"):
                    self.execute(receipt=self.repos[role] / "receipt.json")
                self.assertFalse((self.repos[role] / "receipt.json").exists())

    def test_source_path_alias_refused(self):
        original = self.repos["bom"]
        self.repos["bom"] = self.repos["gui"]
        try:
            with self.assertRaisesRegex(ValueError, "distinct"):
                self.execute()
        finally:
            self.repos["bom"] = original

    def test_real_conflict_records_paths_and_never_autoresolves(self):
        write(self.repos["core"], "revisionworx/gui/qt/mainwindow.py", "different core GUI\n")
        self.shas["core"] = commit(self.repos["core"], "conflicting GUI")
        with self.assertRaisesRegex(RuntimeError, "gui merge conflicts"):
            self.execute()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "CONFLICT / NO AUTOMATIC RESOLUTION")
        self.assertIn("revisionworx/gui/qt/mainwindow.py",
                      result["steps"][0]["unmerged_paths"])
        self.assertEqual(read(self.repos["gui"], "status", "--porcelain"), "")

    def test_bom_attempt_to_replace_core_authority_is_blocked(self):
        write(self.repos["bom"], "revisionworx/workspace.py", "unapproved BOM authority\n")
        self.shas["bom"] = commit(self.repos["bom"], "alter core workspace")
        with self.assertRaisesRegex(RuntimeError, "core authority changed"):
            self.execute()
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result["status"], "FAILED / NO RELEASE CLAIM")
        self.assertEqual(read(self.repos["bom"], "status", "--porcelain"), "")

    def test_missing_role_is_refused(self):
        with self.assertRaisesRegex(ValueError, "exactly gui, bom and changes"):
            compose(self.repos["core"], self.shas["core"],
                    {"gui": (self.repos["gui"], self.shas["gui"])},
                    self.receipt)
        self.assertFalse(self.receipt.exists())


if __name__ == "__main__":
    unittest.main()
