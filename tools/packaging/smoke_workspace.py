#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive bundled-app acceptance smoke on a *copied* sample workspace.

The only created RevisionWorx workspace is a disposable fixture, and the
tested GUI/CLI open only its disposable copy. This is NOT migration,
installer, interactive GUI, ACL or SMB certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def snapshot(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("test fixture cannot contain symlinks")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def isolated_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_STATE_HOME": str(home / "state"),
        "QT_QPA_PLATFORM": "offscreen",
        "PYTHONNOUSERSITE": "1",
    })
    env.pop("REVISIONWORX_WORKSPACE", None)
    return env


def checked_cli(cli: Path, args: list[str], home: Path, env: dict[str, str]) -> str:
    proc = subprocess.run(
        [str(cli), *args], cwd=home, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bundled CLI {args!r} failed ({proc.returncode}): {proc.stdout[-2500:]}")
    return proc.stdout


def gui_offscreen(gui: Path, workspace: Path, home: Path, env: dict[str, str]) -> None:
    proc = subprocess.Popen(
        [str(gui), str(workspace)], cwd=home, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        time.sleep(2.5)
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=5)
            raise RuntimeError(
                f"bundled GUI exited opening disposable copy ({proc.returncode}): "
                + (out + err)[-3000:].decode(errors="replace")
            )
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)


def exercise(cli: Path, gui: Path) -> None:
    if not (cli.is_file() and gui.is_file()):
        raise FileNotFoundError("bundled CLI and GUI executables are required")
    with tempfile.TemporaryDirectory(prefix="revisionworx-installed-copy-") as scratch:
        base = Path(scratch)
        home = base / "profile"
        home.mkdir()
        env = isolated_env(home)
        original = base / "Engineering Original"
        original.mkdir()
        checked_cli(cli, ["init", str(original), "--name", "Disposable Test"], home, env)
        metadata = original / ".revisionworx"
        if not metadata.is_dir():
            raise RuntimeError("the bundled CLI did not create its disposable test workspace")
        original_marker = original / "CAD drawings" / "file with spaces Ω.osch"
        original_marker.parent.mkdir(parents=True, exist_ok=True)
        original_marker.write_bytes(b"disposable CAD bytes; no real project content")
        before_original = snapshot(original)

        # Simulates opening *older existing* data with a freshly bundled
        # executable, without ever modifying the original fixture.
        copied = base / "Cloned Existing Workspace Ω"
        shutil.copytree(original, copied)
        before_copy = snapshot(copied)
        status_output = checked_cli(
            cli,
            ["--workspace", str(copied), "--cache-dir", str(home / "cache"),
             "--json", "status"],
            home, env,
        )
        status = json.loads(status_output)
        if status.get("name") != "Disposable Test":
            raise AssertionError(f"the copied workspace was not opened: {status!r}")
        if not (copied / "CAD drawings" / original_marker.name).is_file():
            raise AssertionError("disposable CAD file not preserved")
        if snapshot(copied) != before_copy:
            raise AssertionError("bundled read-only CLI changed authoritative copy")
        gui_offscreen(gui, copied, home, env)
        if snapshot(copied) != before_copy:
            raise AssertionError("bundled GUI opening changed authoritative copy")
        if snapshot(original) != before_original:
            raise AssertionError("original disposable workspace changed during copy smoke")

        print(
            "COPIED_WORKSPACE_READONLY_EVIDENCE "
            + json.dumps({
                "workspace_name": status["name"],
                "authoritative_files": len(before_copy),
                "copy_matches_original": before_copy == before_original,
                "after_cli_sha_unchanged": True,
                "after_gui_sha_unchanged": True,
                "original_unchanged": True,
                "unicode_and_spaces_path": True,
                "gui_mode": "Qt offscreen process, not an interactive display",
                "unexecuted": [
                    "real customer workspace migration", "Windows ACL denial",
                    "long path and UNC mount", "visible installed GUI",
                    "native installer", "physical multi-host SMB",
                ],
            }, sort_keys=True)
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", required=True, type=Path)
    parser.add_argument("--cli", required=True, type=Path)
    args = parser.parse_args()
    exercise(args.cli.resolve(), args.gui.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
