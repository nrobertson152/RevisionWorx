# SPDX-License-Identifier: GPL-3.0-or-later
"""Other programs (`revisionworx link`, docs/INTEGRATION.md) in a workspace set up for a team (ADR-024): they use the login kept on the
computer, so what they publish is in that person's name, and without one they get a clear refusal rather than a broken file."""
import io
import json

import pytest

from revisionworx import cli
from revisionworx.workspace import Workspace
from team_helpers import PASSWORD


def link(capsys, *args):
    capsys.readouterr()
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.fixture
def team(tmp_path, capsys, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"), ("APPDATA", "cfg"), ("LOCALAPPDATA", "state")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    folder = tmp_path / "ws"
    assert cli.main(["init", str(folder), "--name", "Team"]) == 0
    (folder / "Projects").mkdir()
    w = ["-w", str(folder)]
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main([*w, "team", "enable", "--admin", "boss", "--password-stdin"]) == 0
    assert cli.main([*w, "root", "add", "Projects", str(folder / "Projects")]) == 0
    assert cli.main([*w, "project", "new", "--name", "Pump", "--number", "J-1"]) == 0
    return folder, w, tmp_path


def test_publishing_from_another_program_is_in_the_logged_in_persons_name(team, capsys):
    folder, w, tmp = team
    src = tmp / "calc.txt"
    src.write_text("v1", encoding="utf-8")
    code, made = link(capsys, *w, "link", "publish", str(src), "--project", "J-1", "--number", "CALC-1", "-m", "First")
    assert code == 0 and made["ok"], made
    src.write_text("v2", encoding="utf-8")
    code, again = link(capsys, *w, "link", "publish", str(src), "--document", "CALC-1", "-m", "Second")
    assert code == 0 and again["result"] == "revised", again
    with Workspace.open(folder) as ws:
        revisions = sorted(ws.list("revision").values(), key=lambda r: r["number"])
        assert [r["author"]["account"] for r in revisions] == ["boss", "boss"]
        assert ws.read_entry(ws.head_seq())["signer"] == "boss"
    code, doc = link(capsys, *w, "link", "document", "CALC-1")
    assert code == 0 and doc["document"]["checkout"] is None


def test_without_a_login_other_programs_can_read_but_not_publish(team, capsys):
    folder, w, tmp = team
    src = tmp / "calc.txt"
    src.write_text("v1", encoding="utf-8")
    assert link(capsys, *w, "link", "publish", str(src), "--project", "J-1", "--number", "CALC-1", "-m", "First")[0] == 0
    assert cli.main([*w, "logout"]) == 0
    capsys.readouterr()
    code, listing = link(capsys, *w, "link", "documents", "--project", "J-1")
    assert code == 0 and listing["ok"]
    src.write_text("v2", encoding="utf-8")
    code, refused = link(capsys, *w, "link", "publish", str(src), "--document", "CALC-1", "-m", "Second")
    assert code == 2 and refused["ok"] is False and "Log in" in refused["error"]
    code, status = link(capsys, *w, "link", "status", str(src))
    assert status["ok"] is True
    with Workspace.open(folder) as ws:
        assert len(ws.list("revision")) == 1 and ws.list("checkout") == {}


def test_a_document_someone_else_has_is_refused_with_their_name(team, capsys, monkeypatch):
    folder, w, tmp = team
    src = tmp / "calc.txt"
    src.write_text("v1", encoding="utf-8")
    assert link(capsys, *w, "link", "publish", str(src), "--project", "J-1", "--number", "CALC-1", "-m", "First")[0] == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "!\n"))
    assert cli.main([*w, "user", "add", "alice", "--password-stdin"]) == 0
    capsys.readouterr()
    alice = ["--state-dir", str(tmp / "alices-computer")]
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PASSWORD}!\n{PASSWORD}!\n{PASSWORD}?\n"))
    assert cli.main([*w, *alice, "login", "alice", "--password-stdin"]) == 0
    assert cli.main([*w, *alice, "passwd", "--password-stdin"]) == 0
    capsys.readouterr()
    code, listed = link(capsys, *w, "link", "document", "CALC-1")
    doc_id = listed["document"]["id"]
    assert cli.main([*w, *alice, "doc", "checkout", doc_id]) == 0
    capsys.readouterr()
    src.write_text("v2", encoding="utf-8")
    code, refused = link(capsys, *w, "link", "publish", str(src), "--document", "CALC-1", "-m", "Mine")
    assert code == 2 and refused["kind"] == "checked-out" and "alice" in refused["error"]
    code, doc = link(capsys, *w, "link", "document", "CALC-1")
    assert doc["document"]["checkout"]["user"] == "alice" and doc["document"]["checkout"]["mine"] is False
