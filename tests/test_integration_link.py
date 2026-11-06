# SPDX-License-Identifier: GPL-3.0-or-later
"""`revisionworx link` (docs/INTEGRATION.md): the contract other programs rely on.

Real workspace on a real temporary folder, real processes for the command line, and the reference client
(contrib/rwx_client.py) exactly as Caldera, Ledgerbook, TraceMarx and the viewer embed it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from revisionworx import cli, integration
from revisionworx.workspace import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "contrib"))
import rwx_client  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                     ("APPDATA", "cfg"), ("LOCALAPPDATA", "local")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    ws = tmp_path / "ws"
    assert cli.main(["init", str(ws), "--name", "Link"]) == 0
    (ws / "Projects").mkdir()
    assert cli.main(["-w", str(ws), "root", "add", "Projects", str(ws / "Projects")]) == 0
    assert cli.main(["-w", str(ws), "project", "new", "--name", "Pump", "--number", "J-1"]) == 0
    return tmp_path, ws


def link(capsys, *args):
    code = cli.main(list(args))
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1])


def test_every_answer_is_one_json_object_with_ok(env, capsys):
    tmp, ws = env
    code, data = link(capsys, "link", "version")
    assert code == 0 and data == {"ok": True, "api": integration.API_VERSION, "version": data["version"]}
    code, data = link(capsys, "-w", str(ws), "link", "fetch", "NOPE", "--to", str(tmp / "o"))
    assert code == 2 and data["ok"] is False and data["kind"] == "not-found"
    code, data = link(capsys, "link", "frobnicate")
    assert code == 2 and data["kind"] == "bad-input"


def test_publish_creates_then_revises_then_reports_unchanged(env, capsys):
    tmp, ws = env
    src = tmp / "calc.pdf"
    src.write_bytes(b"calc v1")
    code, made = link(capsys, "-w", str(ws), "link", "publish", str(src), "--project", "J-1", "--number", "CALC-1",
                      "--title", "Pump sizing", "-m", "Initial")
    assert code == 0 and made["result"] == "created" and made["action"] == "imported"
    doc = made["document"]
    assert doc["number"] == "CALC-1" and doc["latest"]["label"] == "A" and doc["revisions"] == 1
    assert Path(doc["path"]).read_bytes() == b"calc v1" and src.read_bytes() == b"calc v1"

    code, same = link(capsys, "-w", str(ws), "link", "publish", str(src), "--project", "J-1", "--number", "CALC-1")
    assert code == 0 and same["result"] == "unchanged" and same["document"]["revisions"] == 1
    assert same["document"]["checkout"] is None          # the checkout this call made was released

    src.write_bytes(b"calc v2")
    code, rev = link(capsys, "-w", str(ws), "link", "publish", str(src), "--document", "CALC-1", "-m", "Bigger pump")
    assert code == 0 and rev["result"] == "revised" and rev["revision"]["label"] == "B"
    assert rev["document"]["checkout"] is None and Path(doc["path"]).read_bytes() == b"calc v2"
    with Workspace.open(ws) as w:
        assert w.verify()["problems"] == []
        revs = [r for r in w.list("revision").values() if r["document_id"] == doc["id"]]
        assert sorted(r["description"] for r in revs) == ["Bigger pump", "Initial"]


def test_a_draftworx_drawing_is_published_as_an_engineering_drawing(env, capsys):
    """As DraftWorx checks its drawings in: lettered revisions, and in the drawing register's category."""
    tmp, ws = env
    src = tmp / "bracket.draftworx"
    src.write_bytes((Path(__file__).parent / "fixtures" / "draftworx" / "bracket.draftworx").read_bytes())
    code, made = link(capsys, "-w", str(ws), "link", "publish", str(src), "--project", "J-1", "--number", "DWG-1",
                      "--title", "Bracket", "-m", "First issue")
    assert code == 0 and made["document"]["type"] == "drawing" and made["document"]["latest"]["label"] == "A"


def test_publish_refuses_a_document_someone_else_has_checked_out(env, capsys, tmp_path):
    tmp, ws = env
    src = tmp / "a.txt"
    src.write_bytes(b"one")
    _, made = link(capsys, "-w", str(ws), "link", "publish", str(src), "--project", "J-1", "--number", "A-1")
    other_state = tmp / "other-session"
    assert cli.main(["-w", str(ws), "--state-dir", str(other_state), "doc", "checkout", made["document"]["id"]]) == 0
    capsys.readouterr()
    with Workspace.open(ws) as w:
        head = w.head_seq()
    src.write_bytes(b"two")
    code, data = link(capsys, "-w", str(ws), "link", "publish", str(src), "--document", "A-1")
    assert code == 2 and data["kind"] == "checked-out"
    with Workspace.open(ws) as w:
        assert w.head_seq() == head
    assert Path(made["document"]["path"]).read_bytes() == b"one"   # the other session's working file untouched


def test_status_finds_the_workspace_from_the_file_alone(env, capsys):
    tmp, ws = env
    src = tmp / "s.txt"
    src.write_bytes(b"x")
    _, made = link(capsys, "-w", str(ws), "link", "publish", str(src), "--project", "J-1")
    working = made["document"]["path"]
    code, st = link(capsys, "link", "status", working)            # no -w, no environment variable
    assert code == 0 and st["controlled"] and st["file"]["state"] == "unchanged" and st["file"]["matches_latest"]
    Path(working).write_bytes(b"edited outside")
    _, st = link(capsys, "link", "status", working)
    assert st["file"]["state"] == "modified-externally" and not st["file"]["matches_latest"]
    _, st = link(capsys, "link", "status", str(src))              # the original outside the project
    assert st == {"ok": True, "controlled": False, "workspace": None, "document": None,
                  "file": {"path": str(src), "state": "unmanaged", "matches_latest": False}}


def test_publish_registers_a_file_already_in_the_project_folder(env, capsys):
    tmp, ws = env
    _, projects = link(capsys, "-w", str(ws), "link", "projects")
    folder = Path(projects["projects"][0]["folder"])
    inside = folder / "wiring.csv"
    inside.write_text("a,b\n")
    code, made = link(capsys, "-w", str(ws), "link", "publish", str(inside), "--project", "J-1")
    assert code == 0 and made["action"] == "registered" and Path(made["document"]["path"]) == inside


def test_fetch_latest_released_and_by_label(env, capsys):
    tmp, ws = env
    src = tmp / "d.pdf"
    src.write_bytes(b"A content")
    _, made = link(capsys, "-w", str(ws), "link", "publish", str(src), "--project", "J-1", "--number", "D-1")
    rev_a = made["document"]["latest"]["id"]
    for state in ("in_review", "approved", "released"):
        assert cli.main(["-w", str(ws), "rev", "transition", rev_a, state]) == 0
    src.write_bytes(b"B content")
    link(capsys, "-w", str(ws), "link", "publish", str(src), "--document", "D-1")
    out = tmp / "out"
    _, latest = link(capsys, "-w", str(ws), "link", "fetch", "D-1", "--to", str(out))
    _, released = link(capsys, "-w", str(ws), "link", "fetch", "D-1", "--to", str(out), "--released")
    _, by_label = link(capsys, "-w", str(ws), "link", "fetch", "D-1", "--to", str(out), "--revision", "a")
    assert Path(latest["path"]).read_bytes() == b"B content" and latest["revision"]["label"] == "B"
    assert Path(released["path"]).read_bytes() == b"A content" and released["revision"]["state"] == "released"
    assert by_label["path"] == released["path"]


def test_open_builds_the_window_command_without_launching(env):
    tmp, ws = env
    src = tmp / "o.txt"
    src.write_bytes(b"o")
    made = integration.publish(src, workspace=ws, project="J-1", number="O-1")
    out = integration.open_in_gui("O-1", workspace=ws, launch=False)
    assert out["command"][-3:] == [str(ws), "--document", made["document"]["id"]]


def test_reference_client_against_the_real_command(env):
    """The client other programs copy: it runs `python -m revisionworx` in a real separate process."""
    tmp, ws = env
    rwx = rwx_client.RevisionWorx(executable=None, workspace=str(ws))
    rwx.command = [sys.executable, "-m", "revisionworx"]
    assert rwx.available()
    src = tmp / "client.pdf"
    src.write_bytes(b"from a client")
    made = rwx.publish(str(src), project="J-1", number="CL-1", title="Client", message="via client")
    assert made["result"] == "created"
    st = rwx.status(made["document"]["path"])
    assert rwx_client.describe(st) == "CL-1 rev A · Work in Progress"
    with pytest.raises(rwx_client.RwxError) as exc:
        rwx.fetch("NOPE", str(tmp / "o"))
    assert exc.value.kind == "not-found"
    missing = rwx_client.RevisionWorx(executable=str(tmp / "no-such-program"))
    assert not missing.available()
    with pytest.raises(rwx_client.RwxError) as exc:
        missing.status(str(src))
    assert exc.value.kind == "unavailable"


def test_link_works_through_the_installed_entry_point_shape(env):
    """A separate interpreter, environment-only configuration: what a calling program actually does."""
    tmp, ws = env
    proc = subprocess.run([sys.executable, "-m", "revisionworx", "link", "projects"], capture_output=True, text=True,
                          env={**os.environ, "REVISIONWORX_WORKSPACE": str(ws)}, timeout=120)
    data = json.loads(proc.stdout)
    assert proc.returncode == 0 and data["projects"][0]["number"] == "J-1"
