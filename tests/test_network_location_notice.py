# SPDX-License-Identifier: GPL-3.0-or-later
"""1.0.0: a workspace in a network folder is flagged, because multi-computer writing to a share is not
verified (docs/ARCHITECTURE.md). Detection only ever produces a notice; it never blocks or raises."""
import json
import os
from pathlib import Path

import pytest

from revisionworx import cli, paths
from revisionworx.gui.controller import Controller
from revisionworx.gui.viewmodels import AppStatus
from revisionworx.workspace import Workspace

MOUNTS = "\n".join([
    "/dev/sda1 / ext4 rw,relatime 0 0",
    "proc /proc proc rw 0 0",
    "server:/export/eng /mnt/eng nfs4 rw 0 0",
    "//nas/projects /mnt/my\\040projects cifs rw 0 0",
    "/dev/sdb1 /mnt/eng/local ext4 rw 0 0",
    "user@host:/x /home/u/remote fuse.sshfs rw 0 0",
])


@pytest.mark.parametrize("path, expected", [
    ("/home/u/work", False),
    ("/mnt/eng", True),
    ("/mnt/eng/Projects/J-1", True),
    ("/mnt/engineering", False),             # a prefix of a mount point name is not inside it
    ("/mnt/my projects/A", True),            # escaped space in the mount table
    ("/mnt/eng/local/A", False),             # a local disk mounted inside a share: longest match wins
    ("/home/u/remote/x", True),
])
@pytest.mark.skipif(os.name == "nt", reason="Linux mount-table parsing")
def test_linux_mount_table(path, expected):
    assert paths.is_network_location(Path(path), mounts_text=MOUNTS) is expected


@pytest.mark.skipif(os.name == "nt", reason="Linux mount-table parsing")
def test_unknown_or_garbage_mount_table_gives_no_answer_rather_than_raising():
    assert paths.is_network_location(Path("/x"), mounts_text="") is None
    assert paths.is_network_location(Path("/x"), mounts_text="\x00garbage") is None


@pytest.mark.parametrize("unc", [r"\\server\share\eng", "//server/share/eng", r"\\?\UNC\server\share"])
def test_unc_paths_are_remote(unc):
    assert paths._windows_is_remote(Path(unc)) is True


def test_a_temporary_folder_is_not_reported_as_network(tmp_path):
    assert paths.is_network_location(tmp_path) in (False, None)


def _ctl(tmp_path):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")


def test_gui_notice_adds_the_network_warning_only_for_a_network_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    Workspace.create(ws, "Notice").close()
    ctl = _ctl(tmp_path)
    status = AppStatus(ctl)
    monkeypatch.setattr(paths, "is_network_location", lambda location, **kw: False)
    ctl.open_workspace(ws)
    assert status.notice == status.alpha_notice
    monkeypatch.setattr(paths, "is_network_location", lambda location, **kw: True)
    ctl.open_workspace(ws)
    assert paths.NETWORK_NOTICE in status.notice and status.alpha_notice in status.notice
    ctl.close_workspace()
    assert status.notice == status.alpha_notice


def test_cli_status_reports_a_network_workspace(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "ws"
    assert cli.main(["init", str(ws), "--name", "Net"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(paths, "is_network_location", lambda location, **kw: True)
    assert cli.main(["-w", str(ws), "--cache-dir", str(tmp_path / "c"), "status"]) == 0
    assert "warning: " + paths.NETWORK_NOTICE in capsys.readouterr().out
    assert cli.main(["-w", str(ws), "--cache-dir", str(tmp_path / "c"), "--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["network_location"] is True
    monkeypatch.setattr(paths, "is_network_location", lambda location, **kw: False)
    assert cli.main(["-w", str(ws), "--cache-dir", str(tmp_path / "c"), "status"]) == 0
    assert "warning" not in capsys.readouterr().out
