#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental exact-SHA frozen alpha GUI/CLI integration acceptance.

Drives the actual *frozen* Qt widget tree via a separately marked TEST-ONLY
GUI wrapper. No application source imports are used in this host script.
Every workspace and every installation tree is created under a temporary
disposable directory. This is offscreen automation, NOT an interactive test,
native installer test, clean Windows VM, or storage safety certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from smoke_workspace import isolated_env, snapshot

ORIGINAL = b"alpha frozen drawing version 1\n"
REVISED = b"alpha frozen drawing version 2\n"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def probe(gui: Path, phase: str, root: Path, env: dict[str, str]) -> dict:
    report = root / (phase + ".json")
    proc = subprocess.run(
        [str(gui), "--alpha-probe", phase, str(root), str(report)],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=100, check=False
    )
    if not report.is_file():
        raise AssertionError(
            f"frozen GUI {phase} produced no acceptance report; code {proc.returncode}, "
            f"output: {proc.stdout[-2500:]}")
    result = json.loads(report.read_text(encoding="utf-8"))
    if proc.returncode or "error" in result:
        raise AssertionError(f"frozen GUI {phase} rejected: {result!r}; "
                             f"exit={proc.returncode}, stdout={proc.stdout[-2500:]}")
    if result.get("phase") != phase:
        raise AssertionError("frozen GUI report has wrong phase")
    return result


def cli_json(cli: Path, ws: Path, root: Path, env: dict[str, str],
             *args: str) -> dict | list:
    proc = subprocess.run([
        str(cli), "--workspace", str(ws),
        "--state-dir", str(root / "profile" / "state"),
        "--cache-dir", str(root / "profile" / "cache"),
        "--json", *args,
    ], cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
       text=True, encoding="utf-8", errors="replace", timeout=40, check=False)
    if proc.returncode:
        raise AssertionError(f"frozen CLI {args!r} failed ({proc.returncode}): "
                             + proc.stdout[-2500:])
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(f"frozen CLI emitted non-JSON: {proc.stdout[-2500:]}") from exc


def verify_history(cli: Path, ws: Path, root: Path, env: dict[str, str],
                   document_id: str, phase: str) -> list[str]:
    detail = cli_json(cli, ws, root, env, "doc", "show", document_id, "--hash")
    history = detail.get("history", [])
    if len(history) != 2 or detail.get("checkout") is not None:
        raise AssertionError("frozen CLI did not reopen exactly two checked-in revisions")
    hashes = sorted(r["sha256"] for r in history)
    if hashes != sorted((digest(ORIGINAL), digest(REVISED))):
        raise AssertionError(f"frozen CLI history changed: {hashes}")
    ids = []
    for i, revision in enumerate(history):
        rev_id = revision["id"]
        target = root / f"cli-export-{phase}-{i}.bin"
        cli_json(cli, ws, root, env, "rev", "export", rev_id, str(target))
        if not target.is_file() or digest(target.read_bytes()) != revision["sha256"]:
            raise AssertionError(f"CLI historical export mismatch for {rev_id}")
        ids.append(rev_id)
    problems = cli_json(cli, ws, root, env, "doc", "verify", "--id", document_id)
    if problems != []:
        raise AssertionError(f"frozen CLI reported revision integrity defects: {problems}")
    return sorted(ids)


def exercise(gui: Path, cli: Path) -> None:
    if not (gui.is_file() and cli.is_file()):
        raise FileNotFoundError("frozen GUI and CLI executables required")
    with tempfile.TemporaryDirectory(prefix="rw-alpha-fixture-") as tmp:
        root = Path(tmp).resolve()
        home = root / "profile"
        home.mkdir()
        env = isolated_env(home)
        # Nothing runs from or writes into the CI build's authoritative bundle.
        install = root / "Installed alpha with spaces Ω"
        gui_dir = install / "GUI"
        cli_dir = install / "CLI"
        shutil.copytree(gui.parent, gui_dir)
        shutil.copytree(cli.parent, cli_dir)
        installed_gui = gui_dir / gui.name
        installed_cli = cli_dir / cli.name
        if not (installed_gui.is_file() and installed_cli.is_file()):
            raise AssertionError("relocated executable absent")

        made = probe(installed_gui, "create", root, env)
        ws = root / "Projects with spaces Ω"
        if made["workspace"] != str(ws) or not (ws / ".revisionworx").is_dir():
            raise AssertionError("GUI create did not create expected isolated workspace")
        if (root / "external source Ω.txt").read_bytes() != ORIGINAL:
            raise AssertionError("GUI import changed external source fixture")
        if made["history_rows"] != 2:
            raise AssertionError("GUI accepted fewer than two revisions")
        if made.get("gui_observable_contract_v1") != "passed" or made.get("gui_f5_selection_preserved") != "passed":
            raise AssertionError("GUI observable-state v1/F5 assertions not recorded")

        ids = verify_history(installed_cli, ws, root, env, made["document_id"], "initial")
        if ids != made["revision_ids"]:
            raise AssertionError("CLI revision IDs do not match Qt GUI evidence")
        authoritative_before_restart = snapshot(ws)
        reopened = probe(installed_gui, "reopen", root, env)
        if reopened["document_id"] != made["document_id"]:
            raise AssertionError("GUI reopened wrong document")
        if reopened["revision_ids"] != ids or reopened["history_rows"] != 2:
            raise AssertionError("GUI lost history after process restart")
        if reopened.get("gui_observable_after_reopen") != "passed":
            raise AssertionError("GUI observable-state v1 after restart not recorded")
        if reopened.get("gui_missing_file_fail_closed") != "passed" or reopened.get("gui_missing_file_restored") != "passed":
            raise AssertionError("GUI missing-working-file alert/recovery assertions not recorded")
        if snapshot(ws) != authoritative_before_restart:
            raise AssertionError("GUI reopen mutated authoritative revision data")
        if verify_history(installed_cli, ws, root, env, made["document_id"], "reopen") != ids:
            raise AssertionError("CLI revisions changed after GUI restart")
        if snapshot(ws) != authoritative_before_restart:
            raise AssertionError("CLI verification mutated authoritative revision data")

        print("FROZEN_ALPHA_GUI_EVIDENCE " + json.dumps({
            "qt_widget_create": True, "qt_widget_import": True,
            "qt_widget_checkout_checkin": True, "qt_widget_history": True,
            "separate_gui_process_reopen": True, "relocated_unicode_install": True,
            "gui_observable_contract_v1": True, "gui_f5_selection_preserved": True,
            "gui_missing_file_alert_and_restore": True,
            "cli_independent_exports_sha256": True, "revision_count": 2,
            "journal_workspace_unchanged_on_reopen": True,
            "source_file_unchanged": True,
            "frozen_runtime_python_on_path": False,
            "qt_mode": "offscreen automated widgets, not user-driven interactive",
            "windows_network": "proxy disabled only; not clean disconnected VM",
            "safety": "test-only frozen wrapper; disposable local filesystem",
        }, sort_keys=True), flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gui", type=Path, required=True)
    p.add_argument("--cli", type=Path, required=True)
    args = p.parse_args()
    exercise(args.gui.resolve(), args.cli.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
