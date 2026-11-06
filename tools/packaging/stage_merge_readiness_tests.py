#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage ONLY current desktop-alpha regression tests for a disposable composite.

The real production test folders and both GitHub branches are unchanged.
The corrected-core checkout passed here must already be the isolated
test-only core+GUI overlay. This does NOT make a merge commit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

GUI_TESTS = (
    "test_desktop_alpha.py",
    "test_gui_imports.py",
    "test_qt_desktop_alpha.py",
    "test_qt_desktop_alpha_keyboard.py",
    "test_qt_desktop_alpha_rc.py",
    "test_qt_gui.py",
)

def git_head(folder: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(folder), "rev-parse", "HEAD"], text=True
    ).strip()

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def stage(core: Path, gui: Path, receipt: Path, core_sha: str, gui_sha: str) -> dict:
    core, gui, receipt = core.resolve(), gui.resolve(), receipt.resolve()
    if core == gui or core in gui.parents or gui in core.parents:
        raise ValueError("core and GUI checkouts must be separate")
    if any(receipt == p or p in receipt.parents for p in (core, gui)):
        raise ValueError("receipt must be outside both source checkouts")
    if receipt.exists():
        raise ValueError("refusing to overwrite existing test manifest")
    if git_head(core) != core_sha or git_head(gui) != gui_sha:
        raise ValueError("test input SHA mismatch")
    if not (core / "revisionworx/gui/qt/mainwindow.py").is_file():
        raise ValueError("the staged combined GUI must exist")
    if not (core / "tests/conftest.py").is_file():
        raise ValueError("corrected core test package is missing")
    if subprocess.check_output(
        ["git", "-C", str(gui), "status", "--porcelain"], text=True
    ).strip():
        raise ValueError("GUI test input is dirty")
    originals = {}
    for filename in GUI_TESTS:
        src, dst = gui / "tests" / filename, core / "tests" / filename
        if not src.is_file() or src.is_symlink() or dst.is_symlink():
            raise ValueError("unsafe or missing GUI regression file: " + filename)
        originals[filename] = {"source_sha256": digest(src),
                               "old_core_test_sha256": digest(dst) if dst.exists() else None}
    for filename in GUI_TESTS:
        shutil.copyfile(gui / "tests" / filename, core / "tests" / filename)
        if digest(core / "tests" / filename) != originals[filename]["source_sha256"]:
            raise RuntimeError("GUI regression copy SHA mismatch: " + filename)
    if git_head(core) != core_sha or git_head(gui) != gui_sha:
        raise RuntimeError("source git heads moved during test overlay")
    payload = {"purpose": "TEST ONLY; GUI regression oracle overlay, not source merge",
               "core_sha": core_sha, "gui_sha": gui_sha,
               "test_files": originals, "count": len(originals)}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    print("TEST_ONLY_MERGE_CANDIDATE_GUI_TEST_RECEIPT "
          + json.dumps({"core_sha": core_sha, "gui_sha": gui_sha,
                        "test_files": len(originals)}, sort_keys=True), flush=True)
    return payload

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--core", required=True, type=Path)
    p.add_argument("--gui", required=True, type=Path)
    p.add_argument("--receipt", required=True, type=Path)
    p.add_argument("--core-sha", required=True)
    p.add_argument("--gui-sha", required=True)
    a = p.parse_args()
    stage(a.core, a.gui, a.receipt, a.core_sha, a.gui_sha)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
