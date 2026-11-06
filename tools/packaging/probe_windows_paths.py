#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Windows local-filesystem path qualification of a FROZEN CLI.

All paths and sharing locks are owned by a disposable temporary fixture.
A UNC loopback administrative share is tested ONLY if the runner exposes it;
that is not a two-machine SMB test. Unsupported path variants are reported
as unsupported, never misrepresented as a passing qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from smoke_workspace import checked_cli, isolated_env, rejected_cli, snapshot


def read_status(cli: Path, location: str, home: Path, env: dict) -> dict:
    return json.loads(checked_cli(
        cli, ["--workspace", location, "--cache-dir", str(home / "cache"),
              "--json", "status"], home, env))


def probe(cli: Path) -> dict:
    if os.name != "nt":
        raise RuntimeError("Windows-only probe, not an emulated UNC test")
    with TemporaryDirectory(prefix="revisionworx-windows-path-probe-") as scratch:
        root = Path(scratch)
        home = root / "profile"
        home.mkdir()
        env = isolated_env(home)
        original = root / "Temporary Workspace with spaces Ω"
        original.mkdir()
        checked_cli(cli, ["init", str(original), "--name", "Path Probe"], home, env)
        before = snapshot(original)
        expected = read_status(cli, str(original), home, env)["workspace_id"]
        results = {}

        def verify(label: str, candidate: str, *, available: bool = True) -> None:
            if not available:
                results[label] = {"status": "not_available"}
                return
            try:
                info = read_status(cli, candidate, home, env)
                if info["workspace_id"] != expected:
                    raise AssertionError(f"{label} resolved the wrong workspace")
                results[label] = {"status": "passed"}
            except (OSError, RuntimeError, ValueError) as exc:
                results[label] = {"status": "unsupported",
                                  "reason": str(exc)[-320:]}

        # Windows special extended-length absolute pathname. File count and
        # hash verification prevent the probe from silently rewriting data.
        verify("extended_length_prefix", "\\\\?\\" + str(original))

        # Loopback ADMIN SHARE is a platform-dependent local capability;
        # GitHub-hosted Windows agents often disable C$ / localhost SMB.
        if original.drive and len(original.drive) == 2 and original.drive.endswith(":"):
            tail = str(original)[3:].replace("/", "\\")
            unc = "\\\\localhost\\" + original.drive[0] + "$\\" + tail
            verify("unc_local_admin_share", unc, available=Path(unc).is_dir())
        else:
            results["unc_local_admin_share"] = {"status": "not_available"}

        # Long-path capability differs among Windows policy and executables.
        # The nested directory is still a disposable local filesystem.
        deep = root
        for _ in range(6):
            deep = deep / ("long-" + "x" * 48)
        try:
            deep.mkdir(parents=True)
            checked_cli(cli, ["init", str(deep), "--name", "Long Path Probe"], home, env)
            read_status(cli, str(deep), home, env)
            results["long_path_over_260"] = {"status": "passed",
                                             "path_length": len(str(deep))}
        except (OSError, RuntimeError) as exc:
            results["long_path_over_260"] = {"status": "unsupported",
                                             "path_length": len(str(deep)),
                                             "reason": str(exc)[-320:]}

        # Mandatory Windows file locking is separate from ACL enforcement
        # and from the multi-writer writer.lock concurrency protocol.
        target = original / ".revisionworx" / "workspace.json"
        try:
            import msvcrt
            with target.open("r+b") as handle:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    try:
                        rejected_cli(
                            cli, ["--workspace", str(original), "--json", "status"],
                            home, env)
                        results["byte_range_lock"] = {"status": "read_rejected"}
                    except AssertionError:
                        results["byte_range_lock"] = {"status": "read_was_not_rejected"}
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except (OSError, ImportError) as exc:
            results["byte_range_lock"] = {"status": "not_available",
                                          "reason": str(exc)[-320:]}

        if snapshot(original) != before:
            raise AssertionError("Windows path or sharing probes mutated the original fixture")
        info = read_status(cli, str(original), home, env)
        if info["workspace_id"] != expected:
            raise AssertionError("Windows path probes damaged the disposable workspace")
        results["original_sha256_preserved"] = True
        results["source_sha256_files"] = len(before)
        results["tested_on"] = "local Windows runner only; NOT remote SMB certification"
        print("WINDOWS_PATH_EVIDENCE " + json.dumps(results, sort_keys=True))
        return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True, type=Path)
    args = parser.parse_args()
    probe(args.cli.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
