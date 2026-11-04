# SPDX-License-Identifier: GPL-3.0-or-later
"""Parent side of the real-process tests: start actors, steer them, read what they did. See proc_actor.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ACTOR = Path(__file__).with_name("proc_actor.py")
REPO = Path(__file__).resolve().parent.parent
KILLED_EXIT = 137
WAIT = 120


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@dataclass
class Result:
    exit_code: int
    steps: list = field(default_factory=list)
    ops: list = field(default_factory=list)
    died_before: str | None = None
    stderr: str = ""

    @property
    def killed(self) -> bool:
        return self.exit_code == KILLED_EXIT

    @property
    def clean(self) -> bool:
        return self.exit_code == 0 and all(s.get("ok") for s in self.steps)

    def step(self, do: str) -> dict:
        return next(s for s in self.steps if s["do"] == do)

    def unexpected(self) -> list:
        return [s for s in self.steps if s.get("unexpected")]


class Actor:
    def __init__(self, ctl: Path, name: str, root: Path, steps: list[dict], *, pauses=(), die_at=None, arm_after=None):
        self.ctl, self.name = Path(ctl), name
        self.ctl.mkdir(parents=True, exist_ok=True)
        self.spec_path = self.ctl / f"{name}.spec.json"
        self.spec_path.write_text(json.dumps(
            {"ctl": str(self.ctl), "name": name, "root": str(root), "steps": steps, "pauses": list(pauses),
             "die_at": die_at, "arm_after": arm_after}), encoding="utf-8")
        self.stderr_path = self.ctl / f"{name}.stderr"
        self.proc: subprocess.Popen | None = None

    def start(self) -> "Actor":
        with open(self.stderr_path, "wb") as err:
            self.proc = subprocess.Popen([sys.executable, str(ACTOR), str(self.spec_path)], env=_env(),
                                         stdout=subprocess.DEVNULL, stderr=err)
        return self

    def _stderr(self) -> str:
        try:
            return self.stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def wait_at(self, pause: str, timeout: float = WAIT) -> None:
        marker = self.ctl / f"{self.name}.at.{pause}"
        deadline = time.monotonic() + timeout
        while not marker.exists():
            if self.proc.poll() is not None:
                raise AssertionError(f"{self.name} exited ({self.proc.returncode}) before reaching {pause}: "
                                     f"{self._result_text()}\n{self._stderr()}")
            if time.monotonic() > deadline:
                raise AssertionError(f"{self.name} did not reach {pause} in {timeout}s\n{self._stderr()}")
            time.sleep(0.002)

    def reached(self, pause: str) -> bool:
        return (self.ctl / f"{self.name}.at.{pause}").exists()

    def _result_text(self) -> str:
        try:
            return (self.ctl / f"{self.name}.result").read_text(encoding="utf-8")
        except OSError:
            return "(no result)"

    def resume(self, pause: str) -> None:
        (self.ctl / f"{self.name}.go.{pause}").write_text("", encoding="utf-8")

    def kill(self) -> None:
        """TerminateProcess on Windows, SIGKILL elsewhere: the process gets no chance to do anything."""
        self.proc.kill()
        self.proc.wait(WAIT)

    def finish(self, timeout: float = WAIT) -> Result:
        code = self.proc.wait(timeout)
        result = Result(exit_code=code, stderr=self._stderr())
        path = self.ctl / f"{self.name}.result"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            result.steps, result.ops = data["steps"], data["ops"]
        died = self.ctl / f"{self.name}.died"
        if died.exists():
            result.died_before = died.read_text(encoding="utf-8")
        return result

    def run(self, timeout: float = WAIT) -> Result:
        return self.start().finish(timeout)


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """The real command line, as an administrator would run it, in its own process."""
    return subprocess.run([sys.executable, "-m", "revisionworx", *args], env=_env(), capture_output=True, text=True,
                          timeout=WAIT, cwd=cwd)
