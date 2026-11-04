# SPDX-License-Identifier: GPL-3.0-or-later
"""The main ``revisionworx`` command reaches the BOM and change-control command lines (1.0.0).

Before, they were only reachable as ``python -m revisionworx.bom`` / ``python -m revisionworx.changes``,
which an installed user (who has the ``revisionworx`` script, not a source tree) would not find.
"""
import subprocess
import sys

import pytest

from revisionworx import cli
from revisionworx.workspace import Workspace


def run(*args, env=None):
    return subprocess.run([sys.executable, "-m", "revisionworx", *args], capture_output=True, text=True,
                          env=env, timeout=120)


def test_help_lists_bom_and_change():
    out = run("--help")
    assert out.returncode == 0
    assert "bom" in out.stdout and "change" in out.stdout


def test_bom_validate_through_main_command(tmp_path):
    bom = tmp_path / "bom.csv"
    bom.write_text("parent,child,quantity\nA-100,B-200,2\nA-100,C-300,1\n", encoding="utf-8")
    out = run("bom", "validate", str(bom))
    assert out.returncode == 0, out.stderr


def test_bom_validate_reports_a_cycle(tmp_path):
    bom = tmp_path / "bom.csv"
    bom.write_text("parent,child,quantity\nTOP-1,A-100,1\nA-100,B-200,1\nB-200,A-100,1\n", encoding="utf-8")
    out = run("bom", "validate", str(bom))
    assert out.returncode == 1


def test_change_commands_use_the_global_workspace_option(tmp_path):
    ws = tmp_path / "ws"
    with Workspace.create(ws, "CLI test"):
        pass
    assert cli.main(["-w", str(ws), "change", "list"]) == 0
    assert cli.main(["--workspace=" + str(ws), "change", "verify"]) == 0


def test_change_commands_use_the_environment_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    with Workspace.create(ws, "CLI test"):
        pass
    monkeypatch.setenv("REVISIONWORX_WORKSPACE", str(ws))
    assert cli.main(["change", "list"]) == 0


def test_change_without_a_workspace_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    assert cli.main(["change", "list"]) == 2
    assert "no workspace" in capsys.readouterr().err


def test_ordinary_commands_are_unaffected(tmp_path):
    ws = tmp_path / "ws"
    assert cli.main(["init", str(ws), "--name", "Plain"]) == 0
    assert cli.main(["-w", str(ws), "status"]) == 0
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
