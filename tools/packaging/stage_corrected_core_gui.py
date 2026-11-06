#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage TEST-ONLY corrected-core plus desktop-GUI compatibility source.

This creates a disposable mixed source tree inside a CI checkout. It NEVER
changes GitHub refs, any checked-in production source, or an existing workspace.
The receipt records BOTH exact Git source SHAs and the SHA-256 of every
overlaid GUI file; a green experiment is not an accepted integrated source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def revision(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def clean(path: Path) -> bool:
    return not subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain"], text=True
    ).strip()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stage(core: Path, gui: Path, receipt: Path, core_sha: str, gui_sha: str) -> dict:
    core, gui, receipt = core.resolve(), gui.resolve(), receipt.resolve()
    if core == gui or core in gui.parents or gui in core.parents:
        raise ValueError("core and GUI checkouts must be separate")
    if any(receipt == p or p in receipt.parents for p in (core, gui)):
        raise ValueError("receipt must be outside both source checkouts")
    if receipt.exists():
        raise ValueError("receipt already exists; refusing to overwrite")
    if revision(core) != core_sha or revision(gui) != gui_sha:
        raise ValueError("exact checkout SHA mismatch")
    if not clean(core) or not clean(gui):
        raise ValueError("source checkout was already modified")
    source = gui / "revisionworx" / "gui"
    dest = core / "revisionworx" / "gui"
    if not (source / "qt" / "mainwindow.py").is_file() or not dest.is_dir():
        raise ValueError("GUI package layout does not match supported desktop source")
    if any(f.is_symlink() for f in source.rglob("*")):
        raise ValueError("GUI source symlinks are not allowed in test staging")
    if any(f.is_symlink() for f in dest.rglob("*")):
        raise ValueError("core GUI destination symlinks are not allowed")
    # The staging checkout is disposable in CI; this is NOT a production edit.
    shutil.rmtree(dest)
    shutil.copytree(source, dest)
    files = {
        f.relative_to(dest).as_posix(): digest(f)
        for f in sorted(dest.rglob("*"))
        if f.is_file()
    }
    if not files or not (dest / "qt" / "startpage.py").is_file():
        raise ValueError("expected desktop alpha source was not staged")
    if revision(core) != core_sha or not clean(gui):
        raise ValueError("unexpected source revision changed during staging")
    if clean(core):
        raise ValueError("the composite unexpectedly has no GUI source changes")
    result = {
        "purpose": "TEST ONLY; no release, merge, repin, backport, or production approval",
        "core_sha": core_sha,
        "gui_sha": gui_sha,
        "gui_path": "revisionworx/gui",
        "overlaid_files_sha256": files,
        "composite_git_head": core_sha,
        "composite_tree_is_dirty_by_design": True,
        "scope": "trusted single-user disposable local projects only",
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("TEST_ONLY_COMPOSITE_SOURCE_RECEIPT " + json.dumps({
        "core_sha": core_sha,
        "gui_sha": gui_sha,
        "overlaid_files": len(files),
        "composite_tree_is_dirty_by_design": True,
    }, sort_keys=True), flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--core-sha", required=True)
    parser.add_argument("--gui-sha", required=True)
    args = parser.parse_args()
    stage(args.core, args.gui, args.receipt, args.core_sha, args.gui_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
