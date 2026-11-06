# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for team-workspace tests. Each "computer" is its own local state folder: its own copy of the journal, device secrets
and saved logins. These run in one process or as separate processes on one disk: that says nothing about real network shares."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import revisionworx
from revisionworx import accounts
from revisionworx.workspace import Workspace

PASSWORD = "correct horse battery"
FAST_KDF = {"name": "scrypt", "n": 2 ** 4, "r": 8, "p": 1}       # the tests log in thousands of times; real accounts stretch far harder


def computer(tmp_path: Path, name: str) -> Path:
    return tmp_path / "computers" / name


def new_team(tmp_path: Path, admin="boss", folder: Path | None = None):
    """A team workspace with its administrator on the computer called 'boss'. Returns (folder, boss's computer, recovery code, login)."""
    folder = folder or tmp_path / "share"
    folder.mkdir(parents=True, exist_ok=True)
    Workspace.create(folder, "Team").close()
    boss = computer(tmp_path, "boss")
    made = accounts.enable_team_use(folder, admin, "The Boss", PASSWORD, state_dir=boss)
    return folder, boss, made["recovery_code"], made["session"]


def add_user(folder, boss_state, boss_session, name, password=PASSWORD + "!", role="user"):
    with Workspace.open(folder, writable=True, session=boss_session, state_dir=boss_state) as ws:
        accounts.create_account(ws, name, password, role=role)


def log_in(folder, state, name, password=PASSWORD + "!", new_password=None):
    """Log a user in on a computer, choosing a real password if they're still on the one they were given."""
    made = accounts.login(folder, name, password, state_dir=state)
    session = made["session"]
    if made["must_change"]:
        session = accounts.change_password(folder, password, new_password or password + "?", session=session, state_dir=state)
    return session


def process_env() -> dict:
    tests = Path(__file__).parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tests), str(Path(revisionworx.__file__).parent.parent), env.get("PYTHONPATH", "")])
    return env


class Crew:
    """A team, and computers for it, which can run as real processes. ``clients`` are paths to the same folder as different clients see
    it; each computer uses one of them in turn. On one local disk there's only the one path."""

    def __init__(self, tmp_path, users=("u1", "u2", "u3", "u4"), clients=None):
        self.tmp_path = tmp_path
        self.clients = [Path(c) for c in clients] if clients else None
        self.folder, self.boss_pc, self.code, self.boss = new_team(tmp_path, folder=self.clients[0] if self.clients else None)
        self.sessions = {}
        self.paths = {"boss": self.folder}
        for i, name in enumerate(users):
            self.paths[name] = self.clients[(i + 1) % len(self.clients)] if self.clients else self.folder
            add_user(self.folder, self.boss_pc, self.boss, name)
            self.sessions[name] = log_in(self.paths[name], computer(tmp_path, name), name)

    def spawn(self, name, do, **spec):
        session = self.sessions[name]
        out = self.tmp_path / f"{name}.{time.monotonic_ns()}.out"
        spec = {"folder": str(self.paths[name]), "state": str(computer(self.tmp_path, name)), "account": name, "workspace_id": session.workspace_id,
                "public": session.public, "seed": session.seed.hex(), "do": do, "out": str(out), "ctl": str(self.tmp_path), **spec}
        path = self.tmp_path / f"{name}.{time.monotonic_ns()}.spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(Path(__file__).parent / "team_actor.py"), str(path)], env=process_env(),
                                stderr=subprocess.PIPE, text=True)
        proc.out = out
        return proc

    def finish(self, proc, timeout=240):
        _out, err = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, err
        return json.loads(proc.out.read_text(encoding="utf-8"))

    def go(self, gate):
        for name in self.sessions:
            deadline = time.monotonic() + 60
            while not Path(f"{gate}.{name}").exists():
                assert time.monotonic() < deadline, "a process never started"
                time.sleep(0.01)
        Path(gate).write_text("go", encoding="utf-8")

    def open(self, name, writable=False, wait=5.0):
        session = self.boss if name == "boss" else self.sessions[name]
        return Workspace.open(self.paths[name], writable=writable, session=session, state_dir=computer(self.tmp_path, name), wait=wait)

    def all_agree(self, patience=0.0):
        """Every computer, and one that has never seen this journal, reads the same thing and finds nothing wrong. On a real share a client
        can take a while to see the newest entry, so ``patience`` allows for that."""
        give_up = time.monotonic() + patience
        while True:
            names = ["boss", *self.sessions, f"new-{time.monotonic_ns()}"]
            views = []
            for name in names:
                with Workspace.open(self.paths.get(name, self.folder), state_dir=computer(self.tmp_path, name)) as ws:
                    ws.sync(fresh=True, patient=True)
                    assert ws.health()["ok"], ws.health()
                    assert [p for p in ws.verify()["problems"] if p.get("severity") != "info"] == []
                    views.append((ws._replica.head(), ws._replica.anchor(), ws.list("project"), ws.list("checkout"), ws.list("revision")))
            if all(v == views[0] for v in views[1:]) or time.monotonic() >= give_up:
                break
            time.sleep(1.0)
        assert all(v == views[0] for v in views[1:])
        return views[0]
