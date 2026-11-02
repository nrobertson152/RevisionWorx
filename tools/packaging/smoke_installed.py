#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-level smoke of bundled CLI and Qt-offscreen GUI, not GUI acceptance.

The executables are run from the built dist folders, not Python source.
An offscreen GUI surviving 2.5 seconds cannot prove interactive usability.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time


def smoke(gui: Path, cli: Path) -> None:
    if not gui.is_file() or not cli.is_file():
        raise FileNotFoundError("both packaged executables must exist")
    with tempfile.TemporaryDirectory(prefix="revisionworx-packaging-smoke-") as root:
        home = Path(root)
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
        })
        env.pop("REVISIONWORX_WORKSPACE", None)
        cli_result = subprocess.run(
            [str(cli), "--help"], cwd=home, env=env, timeout=30,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if cli_result.returncode != 0 or "usage:" not in cli_result.stdout.lower():
            raise RuntimeError(
                f"packaged CLI help failed (exit {cli_result.returncode}): "
                + cli_result.stdout[-2000:]
            )

        gui_process = subprocess.Popen(
            [str(gui)], cwd=home, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            time.sleep(2.5)
            if gui_process.poll() is not None:
                out, err = gui_process.communicate(timeout=5)
                raise RuntimeError(
                    f"packaged GUI exited during startup (exit {gui_process.returncode}): "
                    + (out + err)[-2000:].decode(errors="replace")
                )
        finally:
            if gui_process.poll() is None:
                gui_process.terminate()
            try:
                gui_process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                gui_process.kill()
                gui_process.communicate(timeout=5)
        if (home / ".revisionworx").exists():
            raise RuntimeError("smoke unexpectedly created authoritative PDM metadata")
        print("Bundled CLI --help: PASS")
        print("Bundled Qt GUI offscreen process survival: PASS (not interactive GUI QA)")
        print("Native installer, upgrade, user-visible UI and physical SMB: NOT EXECUTED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.gui.resolve(), args.cli.resolve())
