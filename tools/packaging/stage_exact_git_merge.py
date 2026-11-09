#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create a genuine Git two-parent merge in a disposable CI checkout ONLY.

Unlike a GUI-directory overlay, Git's merge algorithm preserves nonconflicting
changes from both independently evolved branches. On conflict, fail closed and
write the paths for the Director; NEVER auto-resolve or push this commit.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

CORE_ONLY = ("revisionworx/atomicio.py", "revisionworx/workspace.py",
             "revisionworx/authority.py", "revisionworx/documents.py")

def git(repo: Path, *args: str, check: bool = True, env: dict | None = None):
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=check, capture_output=True, text=True, env=env)

def output(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()

def stage(core: Path, gui: Path, receipt: Path, core_sha: str, gui_sha: str) -> dict:
    core, gui, receipt = core.resolve(), gui.resolve(), receipt.resolve()
    if core == gui or core in gui.parents or gui in core.parents:
        raise ValueError("core and GUI must be distinct disposable checkouts")
    if any(receipt == s or s in receipt.parents for s in (core, gui)):
        raise ValueError("receipt must be outside both source checkouts")
    if receipt.exists():
        raise ValueError("receipt already exists; refusal to overwrite")
    if output(core, "rev-parse", "HEAD") != core_sha:
        raise ValueError("core checkout SHA changed")
    if output(gui, "rev-parse", "HEAD") != gui_sha:
        raise ValueError("GUI checkout SHA changed")
    if output(core, "status", "--porcelain") or output(gui, "status", "--porcelain"):
        raise ValueError("input checkout was not clean")
    if not (core / "revisionworx/atomicio.py").is_file():
        raise ValueError("missing core atomicio authority source")
    if not (gui / "revisionworx/gui/qt/mainwindow.py").is_file():
        raise ValueError("missing desktop GUI source")
    # Fetch ONLY from a preexisting local Git checkout; no network or remote push.
    git(core, "fetch", "--no-tags", str(gui), gui_sha)
    base = output(core, "merge-base", core_sha, gui_sha)
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME="RevisionWorx Integration Test",
               GIT_AUTHOR_EMAIL="ci-test@example.invalid",
               GIT_COMMITTER_NAME="RevisionWorx Integration Test",
               GIT_COMMITTER_EMAIL="ci-test@example.invalid",
               GIT_AUTHOR_DATE="2026-11-02T00:00:00+00:00",
               GIT_COMMITTER_DATE="2026-11-02T00:00:00+00:00")
    result = git(core, "-c", "commit.gpgsign=false", "merge", "--no-ff",
                 "--no-edit", "-m", "TEST ONLY exact core+GUI integration", gui_sha,
                 check=False, env=env)
    if result.returncode:
        paths = output(core, "diff", "--name-only", "--diff-filter=U").splitlines()
        data = {"status": "CONFLICT / NOT MERGEABLE WITHOUT HUMAN RESOLUTION",
                "core_sha": core_sha, "gui_sha": gui_sha, "merge_base": base,
                "unmerged_paths": paths,
                "git_stdout": result.stdout[-4000:],
                "git_stderr": result.stderr[-4000:]}
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
        print("TEST_ONLY_REAL_GIT_MERGE_CONFLICT " + json.dumps(data, sort_keys=True),
              flush=True)
        raise RuntimeError("real Git integration has conflicts; no files auto-resolved")
    merge_sha = output(core, "rev-parse", "HEAD")
    parents = output(core, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    if parents != [core_sha, gui_sha]:
        raise RuntimeError("synthetic merge has unexpected parent commits")
    if output(core, "status", "--porcelain"):
        raise RuntimeError("synthetic merge left a dirty checkout")
    if output(gui, "rev-parse", "HEAD") != gui_sha or output(gui, "status", "--porcelain"):
        raise RuntimeError("original GUI source was changed")
    protected = {}
    for path in CORE_ONLY:
        wanted, merged = (output(core, "rev-parse", f"{revision}:{path}")
                          for revision in (core_sha, merge_sha))
        if wanted != merged:
            raise RuntimeError("core-only authority file changed in merge: " + path)
        protected[path] = wanted
    data = {"status": "TEST-ONLY THREE-WAY GIT MERGE SUCCEEDED",
            "core_sha": core_sha, "gui_sha": gui_sha,
            "merge_base": base, "synthetic_merge_sha": merge_sha,
            "parent_shas": parents,
            "core_only_authority_blob_oids": protected,
            "changed_paths_from_core": output(core, "diff", "--name-only",
                                              core_sha, merge_sha).splitlines(),
            "release_authorized": False,
            "pushed_to_remote": False}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    print("TEST_ONLY_REAL_GIT_MERGE_RECEIPT " + json.dumps(data, sort_keys=True),
          flush=True)
    return data

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--gui", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--core-sha", required=True)
    parser.add_argument("--gui-sha", required=True)
    a = parser.parse_args()
    stage(a.core, a.gui, a.receipt, a.core_sha, a.gui_sha)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
