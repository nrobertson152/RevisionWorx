#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compose exact source commits with real Git merges, in CI temp clones ONLY.

No remote writes, no conflict auto-resolution, no source-history rewrite.
The output receipt is evidence about this source tuple, not release approval.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

PROTECTED = (
    "revisionworx/atomicio.py",
    "revisionworx/authority.py",
    "revisionworx/content.py",
    "revisionworx/documents.py",
    "revisionworx/index.py",
    "revisionworx/workspace.py",
)
MODULE_MARKERS = {
    "gui": "revisionworx/gui/qt/mainwindow.py",
    "bom": "revisionworx/bom/__init__.py",
    "changes": "revisionworx/changes/__init__.py",
}
ROLES = ("gui", "bom", "changes")

def git(path: Path, *args: str, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=check,
        text=True, capture_output=True, env=env,
    )

def read(path: Path, *args: str) -> str:
    return git(path, *args).stdout.strip()

def _receipt(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("TEST_ONLY_PRODUCT_STACK_RECEIPT " + json.dumps(value, sort_keys=True), flush=True)

def compose(core: Path, core_sha: str, sources: dict[str, tuple[Path, str]],
            receipt: Path) -> dict:
    core, receipt = core.resolve(), receipt.resolve()
    if receipt.exists():
        raise ValueError("refusing to overwrite source receipt")
    if set(sources) != set(ROLES):
        raise ValueError("exactly gui, bom and changes inputs are required")
    locations = [core]
    entries = {}
    for role in ROLES:
        path, sha = sources[role]
        path = path.resolve()
        locations.append(path)
        entries[role] = {"path": path, "sha": sha}
    if len(set(locations)) != len(locations):
        raise ValueError("source checkout paths must be distinct")
    if any(a in b.parents or b in a.parents for a in locations for b in locations if a != b):
        raise ValueError("source checkouts may not nest")
    if any(receipt == folder or folder in receipt.parents for folder in locations):
        raise ValueError("receipt must reside outside every source checkout")

    if read(core, "rev-parse", "HEAD") != core_sha:
        raise ValueError("core source SHA mismatch")
    for path in locations:
        if read(path, "status", "--porcelain"):
            raise ValueError("source checkout is dirty: " + str(path))
    for role, meta in entries.items():
        if read(meta["path"], "rev-parse", "HEAD") != meta["sha"]:
            raise ValueError(role + " source SHA mismatch")
        if not (meta["path"] / MODULE_MARKERS[role]).is_file():
            raise ValueError(role + " lacks its required source marker")

    original = {p: read(core, "rev-parse", f"{core_sha}:{p}") for p in PROTECTED}
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="RevisionWorx CI Integration Only",
        GIT_AUTHOR_EMAIL="ci-integration@example.invalid",
        GIT_COMMITTER_NAME="RevisionWorx CI Integration Only",
        GIT_COMMITTER_EMAIL="ci-integration@example.invalid",
        GIT_AUTHOR_DATE="2026-11-03T00:00:00+00:00",
        GIT_COMMITTER_DATE="2026-11-03T00:00:00+00:00",
    )
    result = {
        "purpose": "TEST-ONLY source composition; NOT merged, supported, or releasable",
        "core_sha": core_sha,
        "source_shas": {k: entries[k]["sha"] for k in ROLES},
        "steps": [],
        "pushed_to_remote": False,
        "release_authorized": False,
    }
    try:
        for role in ROLES:
            path, sha = entries[role]["path"], entries[role]["sha"]
            prior = read(core, "rev-parse", "HEAD")
            # A local object transfer from an already checked-out exact SHA.
            git(core, "fetch", "--no-tags", str(path), sha)
            base = read(core, "merge-base", prior, sha)
            operation = git(
                core, "-c", "commit.gpgsign=false", "merge", "--no-ff",
                "--no-edit", "-m", f"TEST ONLY combine {role} at {sha}", sha,
                env=env, check=False,
            )
            step = {"role": role, "input_sha": sha, "previous_sha": prior,
                    "merge_base": base}
            if operation.returncode != 0:
                step["status"] = "CONFLICT"
                step["unmerged_paths"] = read(
                    core, "diff", "--name-only", "--diff-filter=U"
                ).splitlines()
                step["git_message"] = (operation.stdout + operation.stderr)[-4000:]
                result["steps"].append(step)
                result["status"] = "CONFLICT / NO AUTOMATIC RESOLUTION"
                _receipt(receipt, result)
                raise RuntimeError(f"{role} merge conflicts: " +
                                   ", ".join(step["unmerged_paths"]))
            merged = read(core, "rev-parse", "HEAD")
            parents = read(core, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
            if parents != [prior, sha]:
                raise RuntimeError(f"{role}: unexpected synthetic merge parents")
            for protected_path, wanted in original.items():
                if read(core, "rev-parse", f"HEAD:{protected_path}") != wanted:
                    raise RuntimeError(
                        f"{role}: core authority changed: {protected_path}"
                    )
            if read(core, "status", "--porcelain"):
                raise RuntimeError(f"{role}: dirty checkout after merge")
            if read(path, "rev-parse", "HEAD") != sha or read(path, "status", "--porcelain"):
                raise RuntimeError(f"{role}: upstream source checkout was modified")
            step["status"] = "MERGED TEMPORARILY"
            step["synthetic_sha"] = merged
            step["parent_shas"] = parents
            result["steps"].append(step)
        result["status"] = "TEST-ONLY COMPOSITE SOURCE PREPARED"
        result["synthetic_final_sha"] = read(core, "rev-parse", "HEAD")
        result["core_authority_blob_oids"] = original
        result["changed_paths_from_core"] = read(
            core, "diff", "--name-only", core_sha, "HEAD"
        ).splitlines()
        for role, meta in entries.items():
            if read(meta["path"], "rev-parse", "HEAD") != meta["sha"]:
                raise RuntimeError(f"{role}: source moved during staging")
        for role in ROLES:
            if not (core / MODULE_MARKERS[role]).is_file():
                raise RuntimeError(f"{role} missing from composite")
        _receipt(receipt, result)
        return result
    except Exception as exc:
        if not receipt.exists():
            result["status"] = "FAILED / NO RELEASE CLAIM"
            result["error"] = f"{type(exc).__name__}: {exc}"
            _receipt(receipt, result)
        raise

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--core-sha", required=True)
    for role in ROLES:
        parser.add_argument("--" + role, required=True, type=Path)
        parser.add_argument("--" + role + "-sha", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    compose(args.core, args.core_sha, {
        role: (getattr(args, role), getattr(args, role + "_sha")) for role in ROLES
    }, args.receipt)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
