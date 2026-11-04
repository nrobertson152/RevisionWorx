# SPDX-License-Identifier: GPL-3.0-or-later
"""One protocol participant in its own process, for tests that need a real process to stop or die.

    python proc_actor.py <spec.json>

The child runs real RevisionWorx code. Test-only wrappers around the filesystem calls can

* **pause** at a named point: write ``<actor>.at.<pause>`` in the control folder and wait for
  ``<actor>.go.<pause>``, so the parent decides the interleaving. The parent can also kill the child there;
* **die** with ``os._exit`` just before the Nth filesystem change after arming: no ``finally``, no ``atexit``,
  no cleanup handlers, exactly what a crash or a killed process leaves behind.

Nothing is patched in the code under test beyond those wrappers, and only inside this process.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import pathlib
import sys
import time
import traceback
from pathlib import Path

KILLED_EXIT = 137
LOST_PARENT_EXIT = 99
_real = {name: getattr(os, name) for name in ("replace", "rename", "link", "unlink", "mkdir", "rmdir", "open")}
_real_open = builtins.open


def _note(ctl: Path, name: str, text: str) -> None:
    """Publish a marker for the parent. Written whole, then renamed, using the unwrapped calls."""
    tmp = ctl / f".{name}.{os.getpid()}.part"
    with _real_open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    _real["replace"](tmp, ctl / name)


class Failpoints:
    """Counts filesystem changes, dies on request, pauses on request. See the module docstring."""

    def __init__(self, spec: dict):
        self.ctl = Path(spec["ctl"])
        self.actor = spec["name"]
        self.die_at = spec.get("die_at")
        self.arm_after = spec.get("arm_after")
        self.armed = False
        self.count = 0
        self.ops: list[str] = []
        self.pauses = [dict(p, seen=0) for p in spec.get("pauses", [])]

    # ---------------------------------------------------------------- pauses
    def pause(self, name: str) -> None:
        _note(self.ctl, f"{self.actor}.at.{name}", "")
        go = self.ctl / f"{self.actor}.go.{name}"
        deadline = time.monotonic() + 180
        while not go.exists():
            if time.monotonic() > deadline:
                os._exit(LOST_PARENT_EXIT)
            time.sleep(0.001)
        if self.arm_after == name:
            self.armed = True

    def hook(self, hook: str) -> None:
        """A pause at a place in the code rather than at a filesystem call."""
        for p in self.pauses:
            if p.get("hook") == hook:
                p["seen"] += 1
                if p["seen"] == p.get("nth", 1):
                    self.pause(p["name"])

    @staticmethod
    def _matches(p: dict, op: str, src: Path | None, dst: Path | None) -> bool:
        if "op" not in p or op not in p["op"]:
            return False
        checks = (("src_name", src, lambda f, v: f.name == v), ("src_prefix", src, lambda f, v: f.name.startswith(v)),
                  ("dst_name", dst, lambda f, v: f.name == v), ("dst_prefix", dst, lambda f, v: f.name.startswith(v)),
                  ("dst_parent", dst, lambda f, v: f.parent.name == v))
        return all(key not in p or (path is not None and test(path, p[key])) for key, path, test in checks)

    # ---------------------------------------------------------------- every filesystem change passes here
    def before(self, op: str, src=None, dst=None) -> None:
        src, dst = Path(src) if src is not None else None, Path(dst) if dst is not None else None
        for p in self.pauses:
            if self._matches(p, op, src, dst):
                p["seen"] += 1
                if p["seen"] == p.get("nth", 1):
                    self.pause(p["name"])
        if not self.armed:
            return
        what = f"{op}({src.name if src else ''}{' -> ' + dst.name if dst else ''})"
        if self.die_at is not None and self.count == self.die_at:
            _note(self.ctl, f"{self.actor}.died", what)
            os._exit(KILLED_EXIT)
        self.count += 1
        self.ops.append(what)

    def install(self) -> None:
        def path_op(op):
            def call(path, *args, **kwargs):
                self.before(op, path)
                return _real[op](path, *args, **kwargs)
            return call

        def move_op(op):
            def call(src, dst, *args, **kwargs):
                self.before(op, src, dst)
                return _real[op](src, dst, *args, **kwargs)
            return call

        def os_open(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                self.before("create", path)
            return _real["open"](path, flags, *args, **kwargs)

        def any_open(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wxa+"):
                self.before("create", file)
            return _real_open(file, mode, *args, **kwargs)
        for op in ("replace", "rename", "link"):
            setattr(os, op, move_op(op))
        for op in ("unlink", "mkdir", "rmdir"):
            setattr(os, op, path_op(op))
        # Python 3.10's pathlib calls these through an accessor that bound the os functions at import, so
        # Path.unlink() etc. would bypass the hooks above and their crash points would silently vanish.
        accessor = getattr(pathlib, "_NormalAccessor", None)
        if accessor is not None:
            for op in ("replace", "rename", "link"):
                if hasattr(accessor, op):
                    setattr(accessor, op, staticmethod(move_op(op)))
            for op in ("unlink", "mkdir", "rmdir"):
                if hasattr(accessor, op):
                    setattr(accessor, op, staticmethod(path_op(op)))
        os.open = os_open
        builtins.open = any_open
        io.open = any_open


class Script:
    """Runs the spec's steps. Results go to ``<actor>.result``. A refused operation is a result, not a failure
    of the script; anything that is not a WorkspaceError or OSError is flagged ``unexpected``."""

    def __init__(self, spec: dict, fp: Failpoints):
        self.root = Path(spec["root"])
        self.fp = fp
        self.ws = None
        self.saved: dict = {}

    # ---------------------------------------------------------------- steps
    def open(self, writable=True):
        from revisionworx.workspace import Workspace
        self.ws = Workspace.open(self.root, writable=writable)
        return {"token": self.ws._lock_token}

    def close(self):
        self.ws.close()

    def commit(self, id, name="x", kind="root", op="t", expect_rev=None, txid=None):
        from revisionworx.workspace import RecordPut
        return self.ws.commit(op, [RecordPut(kind, id, {"name": name}, expect_rev=expect_rev)], txid=txid)

    def checkout(self, doc):
        from revisionworx import documents
        return documents.checkout(self.ws, doc)["token"]

    def checkin(self, doc, token, description="edited", txid=None):
        from revisionworx import documents
        return documents.checkin(self.ws, doc, token, description, txid=txid)["id"]

    def cancel(self, doc, token, revert=False):
        from revisionworx import documents
        documents.cancel_checkout(self.ws, doc, token, revert=revert)

    def restore(self, doc, token, rev):
        from revisionworx import documents
        documents.restore_to_working(self.ws, doc, token, rev)

    def register(self, project, file):
        from revisionworx import documents
        doc, rev = documents.register_document(self.ws, project, Path(file))
        return {"doc": doc["id"], "rev": rev["id"] if rev else None}

    def transition(self, rev, to):
        from revisionworx import documents
        documents.transition(self.ws, rev, to)

    def create_project(self, name, number=""):
        from revisionworx import projects
        return projects.create_project(self.ws, projects.NewProject(name=name, number=number, customer="c"))["id"]

    def adopt(self, folder, name):
        from revisionworx import projects
        return projects.adopt_project(self.ws, Path(folder), name=name)["id"]

    def gc(self, apply=True, min_age=0.0):
        from revisionworx import documents
        return documents.gc_content(self.ws, apply=apply, min_age_seconds=min_age)["removed"]

    def repair(self):
        return self.ws.verify(repair=True)["repaired"]

    def recover(self):
        return self.ws.recover()

    def create_workspace(self, name="w"):
        from revisionworx.workspace import Workspace
        self.ws = Workspace.create(self.root, name)

    def break_lock(self, token, reason="crash test"):
        from revisionworx.workspace import Workspace
        Workspace.break_writer_lock(self.root, token, reason)

    def clear_orphan(self, name, reason="writer confirmed stopped"):
        from revisionworx.workspace import Workspace
        return Workspace.clear_orphaned_release(self.root, name, reason)["outcome"]

    def outcome(self, txid):
        return self.ws.transaction_outcome(txid)

    def sleep_forever(self):
        self.fp.pause("forever")

    NEEDS_HANDLE = {"close", "commit", "checkout", "checkin", "cancel", "restore", "register", "transition",
                    "create_project", "adopt", "gc", "repair", "recover", "outcome"}

    def run(self, steps: list[dict]) -> list[dict]:
        from revisionworx.workspace import WorkspaceError
        out = []
        for step in steps:
            args = dict(step)
            action = args.pop("do")
            arm = args.pop("arm", False)
            save = args.pop("save", None)
            if arm:
                self.fp.armed = True
            if self.ws is None and action in self.NEEDS_HANDLE:
                out.append({"do": action, "ok": False, "error": "skipped", "message": "no handle: open was refused"})
                continue
            args = {k: self.saved.get(v[1:], v) if isinstance(v, str) and v.startswith("$") else v
                    for k, v in args.items()}
            try:
                value = getattr(self, action)(**args)
                if save:
                    self.saved[save] = value
                out.append({"do": action, "ok": True, "value": value})
            except (WorkspaceError, OSError, ValueError) as exc:
                out.append({"do": action, "ok": False, "error": type(exc).__name__, "message": str(exc)})
            except Exception as exc:                # noqa: BLE001 - reported, never hidden
                out.append({"do": action, "ok": False, "error": type(exc).__name__, "message": str(exc),
                            "unexpected": True, "traceback": traceback.format_exc()})
        return out


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    fp = Failpoints(spec)
    from revisionworx.workspace import Workspace       # imported first: nothing below is a test's doing
    fp.install()
    real_rev = Workspace._current_rev

    def current_rev(self, kind, rid):                  # called in commit() right after every lock check
        fp.hook("after_preflight")
        return real_rev(self, kind, rid)
    Workspace._current_rev = current_rev
    real_scan = Workspace._fence_orphaned_releases

    def scan(meta, own_token):                         # the name is taken, the orphan scan has not run
        fp.hook("before_scan")
        return real_scan(meta, own_token)
    Workspace._fence_orphaned_releases = staticmethod(scan)
    script = Script(spec, fp)
    steps = script.run(spec["steps"])
    _note(fp.ctl, f"{fp.actor}.result", json.dumps({"steps": steps, "ops": fp.ops}))


if __name__ == "__main__":
    main()
