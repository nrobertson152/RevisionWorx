# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance: no startup traversal of large unmanaged archives; CLI end-to-end."""
import json
import os
from pathlib import Path

import pytest

from conftest import tree_snapshot
from revisionworx import cli, projects
from revisionworx.index import open_index
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace

N_UNMANAGED = 50_000


@pytest.fixture(scope="module")
def big_share(tmp_path_factory):
    share = tmp_path_factory.mktemp("bigshare")
    root = share / "Engineering"
    for c in range(50):
        for j in range(N_UNMANAGED // 50):
            (root / f"Customer {c:02d}" / f"Job {j:04d}").mkdir(parents=True)
    return share


def test_no_traversal_of_50000_unmanaged_directories(big_share, tmp_path, monkeypatch):
    root = big_share / "Engineering"
    root_real = os.path.realpath(root)
    touched = []

    def guard(fn):
        def wrapper(path=".", *a, **kw):
            p = os.path.realpath(os.fspath(path))
            if p == root_real or p.startswith(root_real + os.sep):
                touched.append((fn.__name__, p))
            return fn(path, *a, **kw)
        return wrapper

    ws = Workspace.create(big_share, "Big")
    try:
        root_id = projects.add_root(ws, "Engineering", root)
        # Register one new project: creating it must not enumerate siblings.
        monkeypatch.setattr(os, "scandir", guard(os.scandir))
        monkeypatch.setattr(os, "listdir", guard(os.listdir))
        monkeypatch.setattr(os, "walk", guard(os.walk))
        projects.create_project(ws, NewProject(name="New", customer="Customer 07", root_id=root_id))
        ws.close()
        # "Startup": open workspace, sync index, list and search.
        ws2 = Workspace.open(big_share)
        with open_index(ws2, tmp_path / "cache") as idx:
            idx.search_projects("")
            idx.search_projects("job")
            idx.search_files("job")
            idx.status()
        ws2.close()
        assert touched == [], f"unmanaged tree was enumerated: {touched[:5]}"
        # Control: prove the guard really detects enumeration of the root.
        with os.scandir(root) as it:
            next(it)
        assert touched and touched[0][1] == root_real
    finally:
        monkeypatch.undo()
        ws.close()
    assert sum(1 for _ in os.scandir(root)) == 50  # fixture really has 50 x 1,000 folders


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_end_to_end(tmp_path, capsys):
    share = tmp_path / "share"
    eng = share / "Eng"
    legacy = eng / "Old" / "P-9"
    legacy.mkdir(parents=True)
    (legacy / "a.ipt").write_bytes(b"a")
    w = ["-w", str(share), "--cache-dir", str(tmp_path / "cache")]
    assert run(capsys, *w, "init", str(share), "--name", "CLI")[0] == 0
    assert run(capsys, *w, "root", "add", "Eng", str(eng))[0] == 0
    code, out, _ = run(capsys, *w, "--json", "project", "new", "--name", "Gripper", "--number", "G-1",
                       "--customer", "Acme")
    assert code == 0 and json.loads(out)["number"] == "G-1"
    before = tree_snapshot(legacy)
    code, out, _ = run(capsys, *w, "project", "preview", str(legacy))
    assert code == 0 and "nothing has been changed" in out
    code, out, _ = run(capsys, *w, "--json", "project", "adopt", str(legacy), "--name", "P-9")
    assert code == 0 and json.loads(out)["baseline"]["files"] == 1
    assert tree_snapshot(legacy) == before
    code, out, _ = run(capsys, *w, "--json", "project", "list", "gripper")
    assert [p["name"] for p in json.loads(out)] == ["Gripper"]
    code, out, _ = run(capsys, *w, "files", "search", "a")
    assert "a.ipt" in out
    code, out, _ = run(capsys, *w, "--json", "status")
    status = json.loads(out)
    assert status["coverage"]["registered_projects"] == {"indexed": 1, "managed": 1}
    assert status["index"]["authoritative"] is False
    tpl = tmp_path / "tpl.json"
    assert run(capsys, *w, "template", "export", "standard", str(tpl))[0] == 0
    data = json.loads(tpl.read_text())
    data["id"], data["name"] = "copy", "Copy"
    tpl.write_text(json.dumps(data))
    assert run(capsys, *w, "template", "import", str(tpl))[0] == 0
    code, out, _ = run(capsys, *w, "--json", "template", "list")
    assert "copy" in json.loads(out)


def test_cli_errors_are_reported_not_raised(tmp_path, capsys):
    code, _, err = run(capsys, "-w", str(tmp_path / "missing"), "status")
    assert code == 2 and "no RevisionWorx workspace" in err


def test_cli_lock_show_and_break(tmp_path, capsys):
    share = tmp_path / "s"
    abandoned = Workspace.create(share, "L")
    code, out, _ = run(capsys, "-w", str(share), "--json", "lock", "show")
    token = json.loads(out)["token"]
    code, _, err = run(capsys, "-w", str(share), "lock", "break", "--token", "nope", "--reason", "x")
    assert code == 2 and "changed" in err
    code, out, _ = run(capsys, "-w", str(share), "lock", "break", "--token", token, "--reason", "crash")
    assert code == 0 and Workspace.read_writer_lock(share) is None
    abandoned._lock_token = None
    abandoned.close()


def test_python_module_entry_point_runs(tmp_path):
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "revisionworx", "--version"], capture_output=True, text=True,
                       cwd=Path(__file__).parent.parent)
    assert r.returncode == 0 and "RevisionWorx" in r.stdout
