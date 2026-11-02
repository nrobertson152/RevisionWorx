# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-independent application controller.

Holds no toolkit imports so it is fully testable headless. Slow work (scans, index sync) runs on a
background thread; results come back through a queue that the GUI drains on its own thread, so the
UI never blocks on network I/O.

The writer lock is held only for the duration of each write operation, not while the window is
open, so other users can make changes between actions (Phase 1 single-writer model).
"""
from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import atomicio, documents, projects
from ..clientstate import CheckoutTokens
from ..index import LocalIndex, open_index
from ..workspace import Workspace

PAGE_SIZE = 200


def config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "RevisionWorx"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "revisionworx"


@dataclass
class JobResult:
    name: str
    value: Any = None
    error: BaseException | None = None
    on_done: Callable[["JobResult"], None] | None = None


@dataclass
class Progress:
    name: str
    count: int
    detail: str


class Controller:
    def __init__(self, cfg_dir: Path | None = None, cache_dir: Path | None = None,
                 state_dir: Path | None = None):
        self.cfg_dir = Path(cfg_dir) if cfg_dir else config_dir()
        self.cache_dir = cache_dir
        self.tokens = CheckoutTokens(state_dir)
        self.workspace_id: str | None = None
        self.location: Path | None = None
        self.results: "queue.Queue[JobResult | Progress]" = queue.Queue()
        self._cancel = threading.Event()
        self._busy = threading.Lock()

    # ------------------------------------------------------------------ preferences
    def _prefs_path(self) -> Path:
        return self.cfg_dir / "preferences.json"

    def recent_workspaces(self) -> list[str]:
        try:
            return list(atomicio.read_json(self._prefs_path()).get("recent", []))
        except (OSError, ValueError):
            return []

    def _remember(self, location: Path) -> None:
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        recent = [str(location)] + [r for r in self.recent_workspaces() if r != str(location)]
        try:
            prefs = atomicio.read_json(self._prefs_path())
        except (OSError, ValueError):
            prefs = {}
        prefs["recent"] = recent[:10]
        atomicio.atomic_write_json(self._prefs_path(), prefs)

    # ------------------------------------------------------------------ workspace access
    def create_workspace(self, location: Path, name: str) -> None:
        Workspace.create(Path(location), name).close()
        self.open_workspace(location)

    def open_workspace(self, location: Path) -> dict:
        location = Path(location)
        with Workspace.open(location) as ws:  # validates format before we accept it
            info = {"id": ws.id, "name": ws.name}
        self.location = location
        self.workspace_id = info["id"]
        self._remember(location)
        return info

    def _read(self) -> Workspace:
        if self.location is None:
            raise RuntimeError("no workspace is open")
        return Workspace.open(self.location)

    def _write(self) -> Workspace:
        if self.location is None:
            raise RuntimeError("no workspace is open")
        return Workspace.open(self.location, writable=True)

    def _index(self, ws: Workspace) -> LocalIndex:
        return open_index(ws, self.cache_dir)

    # ------------------------------------------------------------------ queries (fast, local)
    def roots(self) -> list[dict]:
        with self._read() as ws:
            return sorted(ws.list("root").values(), key=lambda r: r["name"].casefold())

    def templates(self) -> dict[str, dict]:
        with self._read() as ws:
            return projects.list_templates(ws)

    def project_page(self, text: str = "", page: int = 0, include_archived: bool = False) -> dict:
        with self._read() as ws, self._index(ws) as idx:
            rows = idx.search_projects(text, include_archived=include_archived,
                                       limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE)
            return {"rows": rows[:PAGE_SIZE], "has_more": len(rows) > PAGE_SIZE, "page": page,
                    "status": idx.status(), "coverage": idx.coverage()}

    def project(self, project_id: str) -> dict | None:
        with self._read() as ws:
            rec = ws.get("project", project_id)
            if rec is None:
                return None
            out = dict(rec)
            try:
                out["folder"] = str(projects.project_folder(ws, rec))
            except Exception as exc:  # unreachable root, other-OS path, ...
                out["folder_error"] = str(exc)
            return out

    def project_files(self, project_id: str, text: str = "", limit: int = PAGE_SIZE) -> list[dict]:
        with self._read() as ws, self._index(ws) as idx:
            if text.strip():
                return idx.search_files(text, project_id=project_id, limit=limit)
            rows = idx.db.execute(
                "SELECT project_id, path, size, category, sha256, verified_format FROM files "
                "WHERE project_id=? ORDER BY path LIMIT ?", (project_id, limit)).fetchall()
            cols = ["project_id", "path", "size", "category", "sha256", "verified_format"]
            return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------ writes (short lock)
    def add_root(self, name: str, path: Path, other_platform_path: str | None = None) -> str:
        with self._write() as ws:
            return projects.add_root(ws, name, Path(path), other_platform_path)

    def create_project(self, np: projects.NewProject) -> dict:
        with self._write() as ws:
            return projects.create_project(ws, np)

    def update_project(self, project_id: str, expected_rev: int | None, **changes) -> dict:
        """``expected_rev`` is the ``_rev`` of the record the user was shown; a newer change by
        someone else raises ConflictError instead of being overwritten."""
        with self._write() as ws:
            return projects.update_project(ws, project_id, expected_rev=expected_rev, **changes)

    # ------------------------------------------------------------------ Phase 2: documents
    def documents(self, project_id: str, text: str = "") -> list[dict]:
        with self._read() as ws, self._index(ws) as idx:
            return idx.documents(project_id, text)

    def document_detail(self, doc_id: str, verify_hash: bool = False) -> dict:
        with self._read() as ws:
            doc = ws.get("document", doc_id)
            if doc is None:
                raise documents.DocumentError(f"unknown document {doc_id}")
            lc = documents.get_lifecycle(ws)
            hist = documents.history(ws, doc_id)
            co = ws.get("checkout", doc_id)
            latest = hist[-1] if hist else None
            return {"document": doc, "history": hist, "lifecycle": lc,
                    "status": documents.document_status(ws, doc_id, verify_hash=verify_hash),
                    "checkout": {k: v for k, v in co.items() if k != "token"} if co else None,
                    "mine": bool(co and self.tokens.get(ws.id, doc_id) == co["token"]),
                    "latest": latest,
                    "transitions": documents.lc_mod.allowed_targets(lc, latest["state"]) if latest else []}

    def doctypes(self) -> dict[str, dict]:
        with self._read() as ws:
            return documents.list_doctypes(ws)

    def checkout(self, doc_id: str, note: str = "") -> dict:
        with self._write() as ws:
            co = documents.checkout(ws, doc_id, note)
            self.tokens.put(ws.id, doc_id, co["token"])
            return {k: v for k, v in co.items() if k != "token"}

    def cancel_checkout(self, doc_id: str, revert: bool = False) -> None:
        with self._write() as ws:
            documents.cancel_checkout(ws, doc_id, self.tokens.get(ws.id, doc_id) or "", revert=revert)
            self.tokens.forget(ws.id, doc_id)

    def transition(self, rev_id: str, state: str, comment: str, expected_rev: int | None) -> dict:
        with self._write() as ws:
            return documents.transition(ws, rev_id, state, comment, expected_rev=expected_rev)

    def export_revision(self, rev_id: str, dest: Path) -> Path:
        with self._read() as ws:
            return documents.export_revision(ws, rev_id, Path(dest))

    def start_register(self, project_id: str, file_path: Path, on_done=None, **kw) -> bool:
        def job():
            with self._write() as ws:
                return documents.register_document(ws, project_id, Path(file_path), **kw)
        return self._run("register", job, on_done)

    def start_checkin(self, doc_id: str, message: str, label: str | None = None,
                      allow_unchanged: bool = False, on_done=None) -> bool:
        """Copy + hash can be slow for large CAD files, so it runs in the background."""
        def job():
            with self._write() as ws:
                rev = documents.checkin(ws, doc_id, self.tokens.get(ws.id, doc_id) or "", message,
                                        label=label, allow_unchanged=allow_unchanged)
                self.tokens.forget(ws.id, doc_id)
                return rev
        return self._run("checkin", job, on_done)

    # ------------------------------------------------------------------ background jobs
    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, name: str, fn: Callable[[], Any], on_done: Callable[[JobResult], None] | None) -> bool:
        if not self._busy.acquire(blocking=False):
            return False
        self._cancel.clear()

        def worker():
            try:
                self.results.put(JobResult(name, value=fn(), on_done=on_done))
            except BaseException as exc:  # reported to the UI, never swallowed
                self.results.put(JobResult(name, error=exc, on_done=on_done))
            finally:
                self._busy.release()

        threading.Thread(target=worker, name=f"rwx-{name}", daemon=True).start()
        return True

    def _progress(self, name: str):
        return lambda count, detail: self.results.put(Progress(name, count, detail))

    def start_preview(self, folder: Path, on_done=None) -> bool:
        def job():
            with self._read() as ws:
                return projects.preview_adoption(ws, Path(folder), progress=self._progress("preview"),
                                                 cancelled=self._cancel.is_set)
        return self._run("preview", job, on_done)

    def start_adopt(self, folder: Path, name: str, mode: str, number: str = "", customer: str = "",
                    on_done=None) -> bool:
        def job():
            with self._write() as ws:
                return projects.adopt_project(ws, Path(folder), name, mode=mode, number=number,
                                              customer=customer, progress=self._progress("adopt"),
                                              cancelled=self._cancel.is_set)
        return self._run("adopt", job, on_done)

    def start_rebuild_index(self, on_done=None) -> bool:
        def job():
            with self._read() as ws, self._index(ws) as idx:
                return idx.rebuild()
        return self._run("rebuild", job, on_done)

    def drain(self) -> list:
        """Called from the GUI thread: deliver finished jobs and progress. Returns the events."""
        events = []
        while True:
            try:
                ev = self.results.get_nowait()
            except queue.Empty:
                return events
            events.append(ev)
            if isinstance(ev, JobResult) and ev.on_done:
                ev.on_done(ev)


def describe_error(exc: BaseException) -> str:
    """Actionable, human-readable error text for dialogs."""
    from ..workspace import CommittedNotApplied, ConflictError, WorkspaceLockedError
    if isinstance(exc, documents.CheckoutConflict):
        return f"This document is {exc}. Ask them to check it in, or ask an administrator to release it."
    if isinstance(exc, documents.NoChanges):
        return str(exc)
    if isinstance(exc, ConflictError):
        return f"{exc}\n\nSomeone else changed this item while you were viewing it. Reload and reapply your change."
    if isinstance(exc, CommittedNotApplied):
        return f"Your change was saved.\n\n{exc}"
    if isinstance(exc, WorkspaceLockedError):
        return (f"{exc}\n\nAnother RevisionWorx session is saving changes. Try again in a moment. "
                "If that session crashed, an administrator can release the lock with "
                "'revisionworx lock show' / 'lock break'.")
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc.filename or exc}\nCheck your access rights on the share."
    if isinstance(exc, OSError):
        return f"File system or network error: {exc}\nThe share may be unavailable."
    return str(exc) or exc.__class__.__name__


def to_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
