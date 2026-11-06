# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI-independent application controller.

Holds no toolkit imports so it is fully testable headless. Slow work (scans, index sync) runs on a
background thread; results come back through a queue that the GUI drains on its own thread, so the
UI never blocks on slow disk I/O.

The writer lock is held only for the duration of each write operation, not while the window is
open (Phase 1 single-writer model). RevisionWorx 1.0 is supported on one computer's local disk.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import atomicio, documents, paths, projects
from .. import workspace as workspace_module
from ..lifecycle import allowed_targets
from ..clientstate import CheckoutTokens
from ..index import LocalIndex, open_index
from ..workspace import Workspace

PAGE_SIZE = 200

SCOPE_NOTICE = ("RevisionWorx 1.0 \u2014 made for one computer's local disk (several windows on that computer "
                "are safe together). Shared network folders: let only one computer make changes at a time. "
                "Keep regular backups.")
TEAM_NOTICE = ("RevisionWorx team workspace \u2014 several people work from this shared folder, taking turns to save changes. Not yet "
               "verified between real computers on your network: run 'revisionworx share test' on two of them to find out. "
               "Keep regular backups.")
ALPHA_NOTICE = SCOPE_NOTICE  # former name, kept for callers written against the 0.1.0 alpha
DEFAULT_ROOT_NAME = "Projects"


class ImportError_(documents.DocumentError):
    """Import refused before anything was written (bad source, destination exists, ...)."""


def config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "RevisionWorx"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "revisionworx"


@dataclass
class JobResult:
    """Outcome of a background job. ``warning`` is set when the change IS saved but something
    secondary failed (core ``CommittedNotApplied``): callers must present that as success."""
    name: str
    value: Any = None
    error: BaseException | None = None
    on_done: Callable[["JobResult"], None] | None = None
    warning: str | None = None
    holds_job_slot: bool = False  # set by the worker: delivering this result frees the slot

    @property
    def saved(self) -> bool:
        return self.error is None


class SavedWithWarning(Exception):
    """Internal carrier: the operation's change was durably committed, with a caveat."""

    def __init__(self, warning: str, value: Any = None):
        super().__init__(warning)
        self.warning, self.value = warning, value


class RecoveryBlocked(Exception):
    """The workspace is recovery-blocked; the GUI is read-only until health is re-checked."""


class OutcomeUnknown(Exception):
    """An unexpected error followed a change and the journal could not be read to say whether the
    change took effect. Neither success nor failure may be implied."""


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
        self.state_dir = state_dir
        self.tokens = CheckoutTokens(state_dir)
        self.workspace_id: str | None = None
        self.location: Path | None = None
        self.network_location: bool | None = None  # True: open workspace is on a network share (notice only)
        self.team_mode = False                     # True: the open workspace is set up for a team (ADR-024)
        self.session = None                        # who's logged in here, in that case
        self.results: "queue.Queue[JobResult | Progress]" = queue.Queue()
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self.blocked_reason: str | None = None  # set by health(); makes the GUI read-only
        # True when the block was found by a refused change rather than by the read-only check. The
        # read-only check cannot see such causes (e.g. the lock namespace), so it may not clear them.
        self._block_found_by_write = False
        self.preferences_warning: str | None = None  # the recent list could not be saved; nothing else is affected

    # ------------------------------------------------------------------ preferences
    def _prefs_path(self) -> Path:
        return self.cfg_dir / "preferences.json"

    def _preferences(self) -> dict:
        """Per-user convenience data. Damage here never stops the application: it reads as empty."""
        try:
            prefs = atomicio.read_json(self._prefs_path())
        except (OSError, ValueError):
            return {}
        return prefs if isinstance(prefs, dict) else {}

    def recent_workspaces(self) -> list[str]:
        recent = self._preferences().get("recent")
        return [r for r in recent if r and isinstance(r, str)] if isinstance(recent, list) else []

    def preference(self, key: str, default: Any = None) -> Any:
        """A per-user setting (theme, window layout, last selection). Never fails: damage reads as default."""
        value = self._preferences().get(key, default)
        return default if value is None else value

    def set_preference(self, key: str, value: Any) -> None:
        """Save a per-user setting. Failing to is never a failure of what the user was doing."""
        prefs = self._preferences()
        prefs[key] = value
        try:
            self.cfg_dir.mkdir(parents=True, exist_ok=True)
            atomicio.atomic_write_json(self._prefs_path(), prefs)
        except OSError as exc:
            self.preferences_warning = f"Preferences could not be saved in {self.cfg_dir}: {exc.strerror or exc}"

    def _remember(self, location: Path) -> None:
        """Failing to record a convenience list is not a failure to open the workspace."""
        recent = [str(location)] + [r for r in self.recent_workspaces() if r != str(location)]
        prefs = self._preferences()
        prefs["recent"] = recent[:10]
        try:
            self.cfg_dir.mkdir(parents=True, exist_ok=True)
            atomicio.atomic_write_json(self._prefs_path(), prefs)
        except OSError as exc:
            self.preferences_warning = (f"The recent workspaces list could not be saved in {self.cfg_dir}: "
                                        f"{exc.strerror or exc}")
        else:
            self.preferences_warning = None

    # ------------------------------------------------------------------ workspace access
    def create_workspace(self, location: Path, name: str) -> None:
        Workspace.create(Path(location), name).close()
        self.open_workspace(location)

    def open_workspace(self, location: Path) -> dict:
        location = Path(location)
        with Workspace.open(location, state_dir=self.state_dir) as ws:  # validates format before we accept it
            info = {"id": ws.id, "name": ws.name}
            team_mode, session = not ws.materialises, None if ws.materialises else ws.session
        if not (self.location == location and self.session is not None):   # reopening doesn't log anyone out
            self.session = session
        self.team_mode = team_mode
        self.location = location
        self.workspace_id = info["id"]
        self.network_location = paths.is_network_location(location)
        self._set_block(None)
        self._remember(location)
        return info

    def close_workspace(self) -> None:
        self.location = None
        self.workspace_id = None
        self.network_location = None
        self.team_mode = False
        self.session = None
        self._set_block(None)

    def _set_block(self, reason: str | None, found_by_write: bool = False) -> None:
        self.blocked_reason = reason
        self._block_found_by_write = reason is not None and found_by_write

    @property
    def read_only(self) -> bool:
        return self.blocked_reason is not None

    def quick_start(self, folder: Path, project_name: str, number: str = "", customer: str = "",
                    workspace_name: str | None = None) -> dict:
        """Alpha "create a local project" in one step, composed only of public core operations.

        ``folder`` becomes the workspace location (``folder/.revisionworx``) and ``folder/Projects``
        the engineering root. An existing workspace there is opened and reused, never re-created.
        The steps are separate commits: if one fails, what already exists is valid and is simply
        reused by the next attempt. Returns the new project record."""
        folder = Path(folder)
        if not project_name.strip():
            raise projects.ProjectError("a project name is required")
        rel = project_folder_name(project_name, number)  # validate before touching the disk
        folder.mkdir(parents=True, exist_ok=True)
        if not (Workspace.meta_dir_for(folder) / "workspace.json").is_file():
            Workspace.create(folder, workspace_name or folder.name or "RevisionWorx").close()
        self.open_workspace(folder)
        with self._write() as ws:
            roots = ws.list("root")
            if not roots:
                root_dir = folder / DEFAULT_ROOT_NAME
                root_dir.mkdir(exist_ok=True)
                root_id = projects.add_root(ws, DEFAULT_ROOT_NAME, root_dir)
            elif len(roots) == 1:
                root_id = next(iter(roots))
            else:
                named = [r for r in roots.values() if r["name"] == DEFAULT_ROOT_NAME]
                if not named:
                    raise projects.ProjectError("this workspace has several engineering roots; "
                                                "use Projects \u203a New project to choose one")
                root_id = named[0]["id"]
            return projects.create_project(ws, projects.NewProject(
                name=project_name.strip(), number=number.strip(), customer=customer.strip(),
                root_id=root_id, rel_path=rel, mode="managed"))

    def health(self) -> dict:
        """Check the workspace using public core calls (CORE_API §5). Never repairs and never commits.

        ``blocked`` (WorkspaceCorruptError) makes the GUI read-only until checked again; other
        problems are shown as a warning only. ``pending`` counts changes that are committed to the
        journal but not yet applied; the next change applies them, so they are not inconsistencies.

        A block found by a refused change is one the read-only ``verify()`` cannot see, so it is not
        allowed to clear it. It is re-checked the only way that can see it: the same writable open
        every change starts with (writer lock, journal check, roll-forward of entries that are
        already committed), closed again without committing anything. Until that succeeds the GUI
        stays read-only (fail closed). Core request A1 (read-only ``Workspace.health()``) would let
        this be a pure read."""
        out = {"ok": True, "blocked": False, "problems": [], "pending": 0, "message": ""}
        if self.location is None:
            self._set_block(None)
            return out
        from ..workspace import WorkspaceCorruptError
        if self.blocked_reason is not None and self._block_found_by_write:
            still = self._recheck_write_block()
            if still is not None:
                return {**out, "ok": False, "blocked": True, "message": still}
        try:
            with self._read() as ws:
                report = ws.quick_check()
                pending_keys, pending = self._pending_changes(ws)
        except WorkspaceCorruptError as exc:
            self._set_block(str(exc))
            return {**out, "ok": False, "blocked": True, "message": describe_error(exc)}
        except (OSError, workspace_module.WorkspaceError) as exc:
            # Moved, disconnected, or refused: nothing can be established, so nothing is offered. A job
            # still writing can cause a refusal by itself, and it must not leave a lasting block.
            message = (f"This workspace cannot be read right now: {exc}\nIts folder may have been moved or "
                       f"its drive disconnected ({self.location}).")
            if self.busy:
                return {**out, "ok": False, "message": message}
            self._set_block(message)
            return {**out, "ok": False, "blocked": True, "unavailable": True, "message": message}
        self._set_block(None)
        problems = [p for p in report.get("problems", []) if p.get("problem") != "entry-without-digest"
                    and (p.get("kind"), p.get("id")) not in pending_keys]
        parts = []
        if pending:
            it = "it" if pending == 1 else "them"
            head = (f"{pending} saved change{' is' if pending == 1 else 's are'} waiting to be applied "
                    "(left by an interruption or an administrator command). Nothing is lost: ")
            holder = self._writer_lock_holder()
            if holder is None:
                parts.append(head + f"your next change applies {it} first.")
            else:
                # Typically the session that was interrupted: its lock is still there, so every change is
                # refused until it is released or broken. Don't promise that the next change will work.
                parts.append(head + f"the first change after the writer lock is free applies {it}. The "
                             f"writer lock is held by {holder}, so changes are refused until it is released. "
                             "If that session has crashed, the change you try shows how an administrator "
                             "can break the lock.")
        if problems:
            first = problems[0]
            what = ", ".join(f"{k}={v}" for k, v in first.items())
            parts.append(
                f"Workspace check found {len(problems)} inconsistenc{'y' if len(problems) == 1 else 'ies'} "
                f"({what}{', …' if len(problems) > 1 else ''}). If RevisionWorx was interrupted, your next "
                "change completes it automatically; otherwise run 'revisionworx verify' as an administrator.")
        out.update(problems=problems, pending=pending, message="\n".join(parts))
        return out

    def _recheck_write_block(self) -> str | None:
        """Re-check a block found by a refused change. Returns None if it is gone (and clears it),
        otherwise the message to show; the block is kept."""
        from ..workspace import WorkspaceCorruptError
        if self.busy:
            return (describe_error(WorkspaceCorruptError(self.blocked_reason)) +
                    "\n\nIt will be re-checked when the current operation finishes (press F5).")
        try:
            with Workspace.open(self.location, writable=True, **self._team_options(writing=True)):
                pass  # nothing is committed: open, check and close only
        except WorkspaceCorruptError as exc:
            self._set_block(str(exc), found_by_write=True)
            return describe_error(exc)
        except Exception as exc:  # e.g. another session holds the lock: not proven gone, stay closed
            return (describe_error(WorkspaceCorruptError(self.blocked_reason)) +
                    f"\n\nThe block could not be re-checked just now: {exc}")
        self._set_block(None)
        return None

    def _writer_lock_holder(self) -> str | None:
        """Who holds the writer lock, for display only (public ``Workspace.read_writer_lock``). Read only
        when changes are pending, so it adds no lock-file reads in normal use. Unreadable → described as
        unknown, never as free."""
        try:
            holder = Workspace.read_writer_lock(self.location)
        except (OSError, ValueError):
            return "an unknown session (the lock file could not be read)"
        if not holder:
            return None
        return (f"{holder.get('user', '?')}@{holder.get('host', '?')} (pid {holder.get('pid', '?')}, "
                f"since {holder.get('acquired', '?')})")

    @staticmethod
    def _pending_changes(ws: Workspace) -> tuple[set, int]:
        """Records written by journal entries that are committed but not yet applied (public journal
        reads only). Fenced entries never take effect, so they are not pending."""
        keys: set = set()
        pending = 0
        for seq in ws.journal_seqs():
            if seq <= ws.applied_seq():
                continue
            entry = ws.read_entry(seq)
            if ws.is_fenced(entry):
                continue
            pending += 1
            keys.update((r["kind"], r["id"]) for r in entry.get("records", []))
        return keys, pending

    def _read(self) -> Workspace:
        if self.location is None:
            raise RuntimeError("no workspace is open")
        return Workspace.open(self.location, **self._team_options())

    def _team_options(self, writing: bool = False) -> dict:
        if not self.team_mode:
            return {}
        options = {"state_dir": self.state_dir, "session": self.session}
        if writing:                                  # waiting for someone's turn to end mustn't freeze the window for long
            options["wait"] = 3.0 if threading.current_thread() is threading.main_thread() else 60.0
        return options

    @contextmanager
    def _write(self) -> Iterator[Workspace]:
        """A short writable handle. A refusal for safety (CORE_API §1) makes the GUI read-only: the
        read-only health check cannot see every cause, such as a blocked lock namespace, so this may
        be the first anyone learns of it. F5 (``health()``) re-checks it with a writable open and keeps
        the GUI read-only until that succeeds."""
        from ..workspace import WorkspaceCorruptError, WorkspaceLockedError
        if self.location is None:
            raise RuntimeError("no workspace is open")
        if self.blocked_reason is not None:
            raise RecoveryBlocked(self.blocked_reason)
        try:
            ws = Workspace.open(self.location, writable=True, **self._team_options(writing=True))
            try:
                start = ws.head_seq()
                yield ws
            except BaseException as exc:
                # Release, but never let a release failure replace the change's own outcome: that
                # could turn CommittedNotApplied (saved) into a failure, or hide a plain refusal.
                try:
                    ws.close()
                except Exception as close_exc:
                    if hasattr(exc, "add_note"):
                        exc.add_note(f"Also, releasing the writer lock failed: {close_exc}")
                raise
            try:
                end = ws.head_seq()
            except OSError:
                end = None  # the block returned normally, so whatever it committed succeeded
            try:
                ws.close()
            except Exception as close_exc:
                if end == start:
                    raise  # nothing was committed: a plain failure
                # Committed (single writer: only this handle can have advanced the journal), so the
                # change is saved; only the lock release failed. Never report that as a failure, even
                # when the release found a reason to refuse further changes.
                if isinstance(close_exc, WorkspaceCorruptError):
                    self._set_block(str(close_exc), found_by_write=True)
                raise SavedWithWarning(
                    f"Saved, but releasing the workspace writer lock then failed: {close_exc} Until it is "
                    "released, further changes are refused; an administrator can break the lock "
                    "(audited).") from close_exc
        except WorkspaceCorruptError as exc:
            self._set_block(str(exc), found_by_write=True)
            raise
        except WorkspaceLockedError as exc:
            exc.workspace_location = self.location  # for the recovery command in describe_error
            raise

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

    def search_everything(self, text: str, limit: int = 25) -> dict:
        """Projects and documents of the whole workspace matching ``text`` (local index; never unmanaged folders)."""
        with self._read() as ws, self._index(ws) as idx:
            projects_ = idx.search_projects(text, include_archived=True, limit=limit)
            docs = idx.documents(None, text, limit=limit) if text.strip() else []
            names = {p["id"]: p for p in ws.list("project").values()}
            for d in docs:
                p = names.get(d["project_id"]) or {}
                d["project_name"], d["project_number"] = p.get("name", ""), p.get("number", "")
            return {"projects": projects_, "documents": docs}

    def my_checkouts(self) -> list[dict]:
        """Documents this session has checked out (it holds their tokens), across the workspace."""
        with self._read() as ws:
            out = []
            for co in ws.list("checkout").values():
                if documents.is_mine(ws, self.tokens, co):
                    doc = ws.get("document", co["document_id"])
                    if doc:
                        out.append({"id": doc["id"], "project_id": doc["project_id"], "number": doc.get("number", ""),
                                    "title": doc.get("title", ""), "rel_path": doc["rel_path"]})
            return out

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
                    "mine": bool(co and documents.is_mine(ws, self.tokens, co)),
                    "can_release": bool(co and not ws.materialises and ws.is_admin and not documents.is_mine(ws, self.tokens, co)),
                    "latest": latest,
                    "transitions": allowed_targets(lc, latest["state"]) if latest else []}

    def state_names(self) -> dict[str, str]:
        """Lifecycle state id -> display name (the workspace's lifecycle)."""
        with self._read() as ws:
            return {st["id"]: st["name"] for st in documents.get_lifecycle(ws)["states"]}

    def doctypes(self) -> dict[str, dict]:
        with self._read() as ws:
            return documents.list_doctypes(ws)

    def checkout(self, doc_id: str, note: str = "") -> dict:
        """Raises SavedWithWarning if the checkout was committed but its token could not be
        returned by core (CommittedNotApplied): the document IS checked out, but this session
        cannot prove ownership, so an administrator must release it (API request A6)."""
        from ..workspace import CommittedNotApplied
        with self._write() as ws:
            try:
                co = documents.checkout(ws, doc_id, note)
            except CommittedNotApplied as exc:
                raise SavedWithWarning(
                    f"The checkout was saved, but this session did not receive its ownership token "
                    f"({exc}). You cannot check in from here; ask an administrator to release the "
                    "checkout (audited) and check out again.") from exc
            try:
                self.tokens.put(ws.id, doc_id, co["token"])
            except OSError as exc:
                raise SavedWithWarning(
                    f"The checkout was saved, but this computer could not store its ownership token "
                    f"({exc}). You cannot check in from here; ask an administrator to release the "
                    "checkout (audited) and check out again.") from exc
            return {k: v for k, v in co.items() if k != "token"}

    def cancel_checkout(self, doc_id: str, revert: bool = False) -> None:
        from ..workspace import CommittedNotApplied
        with self._write() as ws:
            try:
                documents.cancel_checkout(ws, doc_id, documents.my_token(ws, self.tokens, doc_id) or "", revert=revert)
            except CommittedNotApplied as exc:
                self._forget_token(ws.id, doc_id)
                raise SavedWithWarning(f"The checkout was released. {exc}") from exc
            problem = self._forget_token(ws.id, doc_id)
        if problem:
            raise SavedWithWarning(f"The checkout was released. {problem}")

    def _forget_token(self, workspace_id: str | None, doc_id: str) -> str | None:
        """Drop a finished checkout's local token. The change is already committed by now, so a
        failure here is only a caveat (the stale token matches nothing); never report it as if the
        change had failed. Returns the caveat, if any."""
        try:
            self.tokens.forget(workspace_id, doc_id)
        except OSError as exc:
            return f"The local checkout list could not be updated ({exc}); this is harmless."
        return None

    def transition(self, rev_id: str, state: str, comment: str, expected_rev: int | None) -> dict:
        with self._write() as ws:
            return documents.transition(ws, rev_id, state, comment, expected_rev=expected_rev)

    def export_revision(self, rev_id: str, dest: Path, overwrite: bool = False) -> Path:
        """Export a copy. An export is never a way to change managed data, so it refuses the workspace
        metadata and any registered working file, whatever ``overwrite`` says."""
        with self._read() as ws:
            target = Path(dest).resolve()
            if ws.meta_dir.resolve() in target.parents:
                raise ImportError_(f"{dest} is inside the workspace's own data; choose another folder.")
            for doc in ws.list("document").values():
                try:
                    managed = documents.working_path(ws, doc).resolve()
                except Exception:  # unreachable root: it cannot be the target of this write either
                    continue
                if managed == target:
                    raise ImportError_(f"{dest} is the working file of a managed document, so it was not "
                                       "changed. To bring back an older revision, check the document out "
                                       "and use Restore to working file.")
            return documents.export_revision(ws, rev_id, Path(dest), overwrite=overwrite)

    def start_export(self, rev_id: str, dest: Path, on_done=None) -> bool:
        """For a destination the user chose in a save dialog, which has already confirmed replacing."""
        return self._run("export", lambda: self.export_revision(rev_id, dest, overwrite=True), on_done)

    def start_register(self, project_id: str, file_path: Path, on_done=None, **kw) -> bool:
        def job():
            with self._write() as ws:
                return documents.register_document(ws, project_id, Path(file_path), **kw)
        return self._run("register", job, on_done)

    def project_subfolders(self, project_id: str) -> list[str]:
        """Existing folders inside a managed project (relative, ``/``-separated), for the import
        destination picker. Hidden folders and links are skipped; nothing is created."""
        with self._read() as ws:
            rec = ws.get("project", project_id)
            if rec is None:
                raise documents.DocumentError(f"unknown project {project_id}")
            base = projects.project_folder(ws, rec)
        out = [""]
        for dirpath, dirnames, _files in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                                 and not os.path.islink(os.path.join(dirpath, d)))
            rel = Path(dirpath).relative_to(base).as_posix()
            if rel != ".":
                out.append(rel)
            if rel.count("/") >= 2:  # keep the picker short
                dirnames[:] = []
        return out

    def import_document(self, project_id: str, source: Path, subfolder: str = "", *, number: str = "",
                        title: str = "", description: str = "Initial revision") -> tuple[dict, dict | None]:
        """Copy an outside file into a managed project and register it as revision 1.

        The copy is created exclusively (an existing file is never overwritten) while the writer
        lock is held. If registration fails, only the file this call created is removed.
        Residual risk: a crash between copy and commit leaves an unregistered copy in the project
        folder (no data loss; it can be registered with "Register file")."""
        source = Path(source)
        try:
            st = os.lstat(source)
        except FileNotFoundError:
            raise ImportError_(f"{source} does not exist") from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ImportError_(f"{source} is not a regular file (links are not followed)")
        with self._write() as ws:
            rec = ws.get("project", project_id)
            if rec is None:
                raise documents.DocumentError(f"unknown project {project_id}")
            if rec["mode"] != "managed":
                raise ImportError_(f"project {rec['name']!r} is {rec['mode']} (search only); documents "
                                   "can only be imported into managed projects")
            ws.verify()  # fail closed (WorkspaceCorruptError) before touching the project folder
            base = projects.project_folder(ws, rec)
            rel = paths.normalize_relative("/".join(p for p in (subfolder.strip("/"), source.name) if p))
            dest = paths.resolve_under_root(base, rel)
            try:
                if os.path.samefile(source, dest):
                    raise ImportError_(f"{source.name} is already in the project; use Register file")
            except FileNotFoundError:
                pass
            before = ws.head_seq()
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                out = open(dest, "xb")
            except FileExistsError:
                raise ImportError_(f"{rel} already exists in the project. Nothing was overwritten; "
                                   "rename the file or register the existing one") from None
            registering = False
            try:
                with out, open(source, "rb") as src:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                shutil.copystat(source, dest)
                registering = True
                return documents.register_document(
                    ws, project_id, dest, number=number.strip(), title=title.strip(),
                    revision_description=description.strip() or "Initial revision")
            except BaseException as exc:
                # Remove our copy only when it is certain that nothing was committed: the copy itself
                # failed, or the journal has not moved (this handle is the only writer). Otherwise the
                # file may already be the registered working file, so it stays. So does anything left by
                # the interpreter being stopped (not an Exception): a real crash could not clean up either,
                # and the same recovery applies, Register file.
                from ..workspace import CommittedNotApplied
                if isinstance(exc, Exception) and (not registering or (
                        not isinstance(exc, CommittedNotApplied) and self._head_is(ws, before))):
                    try:
                        os.unlink(dest)  # ours: created exclusively above and not registered
                    except OSError:
                        pass
                raise

    @staticmethod
    def _head_is(ws: Workspace, seq: int) -> bool:
        try:
            return ws.head_seq() == seq
        except OSError:  # cannot tell: treat as moved
            return False

    def start_import(self, project_id: str, source: Path, subfolder: str = "", on_done=None, **kw) -> bool:
        return self._run("import", lambda: self.import_document(project_id, source, subfolder, **kw), on_done)

    def file_states(self, doc_ids: list[str]) -> dict[str, str]:
        """Working-file state per document (``unchanged``/``modified``/``modified-externally``/
        ``missing``/``unrevisioned``/``unavailable``) using the cheap metadata check (size+mtime,
        hashing only when the size matches but the mtime moved)."""
        out: dict[str, str] = {}
        with self._read() as ws:
            for did in doc_ids:
                try:
                    out[did] = documents.document_status(ws, did)["state"]
                except Exception:  # unreachable root, path error, ... shown per row, never fatal
                    out[did] = "unavailable"
        return out

    def restore_revision(self, doc_id: str, rev_id: str) -> None:
        """While checked out by this session: overwrite the working file with an earlier revision.
        History is not rewritten; the next check-in records a new revision marked as restored."""
        with self._write() as ws:
            documents.restore_to_working(ws, doc_id, self.tokens.get(ws.id, doc_id) or "", rev_id)

    def compare(self, rev_a: str, rev_b: str) -> dict:
        with self._read() as ws:
            return documents.compare_revisions(ws, rev_a, rev_b)

    def working_file(self, doc_id: str) -> Path:
        with self._read() as ws:
            doc = ws.get("document", doc_id)
            if doc is None:
                raise documents.DocumentError(f"unknown document {doc_id}")
            return documents.working_path(ws, doc)

    def start_checkin(self, doc_id: str, message: str, label: str | None = None,
                      allow_unchanged: bool = False, on_done=None, txid: str | None = None) -> bool:
        """Copy + hash can be slow for large CAD files, so it runs in the background.

        One ``txid`` per user action (CORE_API §4). If the outcome is ambiguous (an unexpected
        error after the request may have reached the journal), ``transaction_outcome`` decides:
        committed -> saved with a warning; otherwise the original error stands."""
        txid = txid or uuid.uuid4().hex

        def job():
            return self.checkin_now(doc_id, message, label=label, allow_unchanged=allow_unchanged, txid=txid)
        return self._run("checkin", job, on_done)

    def checkin_now(self, doc_id: str, message: str, label: str | None = None,
                    allow_unchanged: bool = False, txid: str | None = None) -> dict:
        from ..workspace import CommittedNotApplied
        txid = txid or uuid.uuid4().hex
        ws_id = None
        try:
            with self._write() as ws:
                ws_id = ws.id
                rev = documents.checkin(ws, doc_id, documents.my_token(ws, self.tokens, doc_id) or "", message,
                                        label=label, allow_unchanged=allow_unchanged, txid=txid)
        except CommittedNotApplied as exc:
            self._forget_token(ws_id or self.workspace_id, doc_id)
            raise SavedWithWarning(f"Checked in. {exc}") from exc
        except SavedWithWarning as exc:  # committed; only the writer-lock release failed
            self._forget_token(ws_id or self.workspace_id, doc_id)
            raise SavedWithWarning(f"Checked in. {exc.warning}") from exc
        except (documents.DocumentError, RecoveryBlocked):
            raise  # validation refusals: nothing was written
        except Exception as exc:
            from ..workspace import ConflictError, WorkspaceLockedError
            if ws_id is None or isinstance(exc, (ConflictError, WorkspaceLockedError)):
                raise
            outcome = self.transaction_outcome(txid)
            if outcome.get("state") == "committed":
                self._forget_token(ws_id, doc_id)
                raise SavedWithWarning(
                    f"Checked in (confirmed from the journal, entry {outcome.get('seq')}), but an error "
                    f"followed: {describe_error(exc)}") from exc
            if outcome.get("state") == "unknown":
                raise OutcomeUnknown(f"{describe_error(exc)} ({outcome.get('error')})") from exc
            raise
        problem = self._forget_token(ws_id, doc_id)
        if problem:
            raise SavedWithWarning(f"Checked in revision {rev['label']}. {problem}", rev)
        return rev

    def transaction_outcome(self, txid: str) -> dict:
        try:
            with self._read() as ws:
                return ws.transaction_outcome(txid)
        except Exception as exc:  # cannot determine: never report success
            return {"state": "unknown", "error": str(exc)}

    # ------------------------------------------------------------------ a team working from a shared folder (ADR-024)
    def team(self) -> dict:
        """Who's working here. ``team`` is False for a workspace that isn't set up for one."""
        out = {"team": False, "account": None, "name": None, "role": None, "must_change": False, "head": None}
        if self.location is None:
            return out
        with self._read() as ws:
            if ws.materialises:
                return out
            out.update(team=True, head=ws.head_seq())
            if ws.session is not None:
                record = ws.get("account", ws.session.account) or {}
                if record.get("active") is True and (record.get("key") or {}).get("public") == ws.session.public:
                    out.update(account=ws.session.account, name=ws.session.name, role=record.get("role"),
                               must_change=bool(record.get("must_change")))
                else:                                # disabled, or the password was changed elsewhere: not a login any more
                    self.log_out()
        return out

    def my_account(self) -> str | None:
        """The account logged in here, if there is one. Cheap enough to ask for each row of a list: no network, no journal."""
        return self.session.account if self.team_mode and self.session else None

    def changes_since(self, head: int | None) -> dict:
        """What's been published since this head, and who by, not counting this user. A window asks every few seconds, and when nothing
        has happened it costs a few small reads across the network. It also lets a stuck writer lock be noticed before anyone tries
        to write: the wait for it to clear counts from the first time this computer saw it."""
        if self.location is None:
            return {"head": None, "others": []}
        with self._read() as ws:
            if ws.materialises:
                return {"head": None, "others": []}
            holder = Workspace.read_writer_lock(ws.meta_dir)
            if holder:
                from ..shared import lock_silence
                lock_silence(ws.meta_dir, holder)
            now = ws.head_seq()
            me = ws.session.account if ws.session else None
            others = set()
            if head is not None and now != head:
                for entry in ws.entries_after(head):
                    actor = entry.get("actor") or {}
                    if actor.get("account") != me:
                        others.add(str(actor.get("name") or actor.get("user") or "someone"))
            return {"head": now, "others": sorted(others)}

    def log_in(self, account: str, password: str, remember_hours: float | None = None) -> dict:
        from .. import accounts
        made = accounts.login(self.location, account, password, state_dir=self.state_dir, remember_hours=remember_hours)
        self.session = made["session"]
        self._set_block(None)
        return made

    def log_out(self) -> None:
        from .. import accounts
        self.session = None
        if self.workspace_id:
            accounts.logout(self.workspace_id, self.state_dir)

    def change_password(self, old_password: str, new_password: str) -> None:
        from .. import accounts
        self.session = accounts.change_password(self.location, old_password, new_password, session=self.session, state_dir=self.state_dir)

    def enable_team(self, admin: str, name: str, password: str) -> dict:
        from .. import accounts
        made = accounts.enable_team_use(self.location, admin, name, password, state_dir=self.state_dir)
        self.session, self.team_mode = made["session"], True
        return made

    def recover_admin(self, code: str, account: str, new_password: str) -> None:
        from .. import accounts
        self.session = accounts.recover_admin(self.location, code, account, new_password, state_dir=self.state_dir)

    def check_share(self) -> dict:
        from .. import sharecheck
        return sharecheck.check_folder(Workspace.meta_dir_for(self.location))

    def accounts(self) -> list[dict]:
        from .. import accounts
        with self._read() as ws:
            return accounts.list_accounts(ws)

    def create_account(self, account: str, password: str, name: str = "", role: str = "user") -> dict:
        from .. import accounts
        with self._write() as ws:
            return accounts.create_account(ws, account, password, name=name, role=role)

    def release_checkout(self, doc_id: str, reason: str) -> None:
        """An administrator's release of someone else's checkout. It's recorded, and their file is left as it is."""
        with self._write() as ws:
            co = ws.get("checkout", doc_id)
            if co is None:
                raise documents.NotCheckedOut("this document is not checked out")
            documents.admin_release_checkout(ws, doc_id, co["token"], reason)

    def set_account_active(self, account: str, active: bool) -> dict:
        from .. import accounts
        with self._write() as ws:
            return accounts.set_active_flag(ws, account, active)

    def set_account_role(self, account: str, role: str) -> dict:
        from .. import accounts
        with self._write() as ws:
            return accounts.set_role(ws, account, role)

    def reset_account_password(self, account: str, password: str) -> dict:
        from .. import accounts
        with self._write() as ws:
            return accounts.reset_password(ws, account, password)

    # ------------------------------------------------------------------ background jobs
    @property
    def busy(self) -> bool:
        """A background write/scan is running, or its result is not delivered yet (only one at a
        time). False means the outcome has reached ``on_done``, so the job ends as one moment for
        the UI: ``drain()`` frees the slot immediately before calling ``on_done``."""
        return self._busy.locked()

    def wait_idle(self, timeout: float) -> bool:
        """Wait briefly for a background job to finish, delivering its result. True when idle.
        Call from the GUI thread, which is the one that runs ``on_done``."""
        end = time.monotonic() + timeout
        while True:
            self.drain()
            if not self.busy:
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, name: str, fn: Callable[[], Any], on_done: Callable[[JobResult], None] | None) -> bool:
        if not self._busy.acquire(blocking=False):
            return False
        self._cancel.clear()

        def worker():
            from ..workspace import CommittedNotApplied
            try:
                self.results.put(JobResult(name, value=fn(), on_done=on_done, holds_job_slot=True))
            except SavedWithWarning as exc:
                self.results.put(JobResult(name, value=exc.value, warning=exc.warning, on_done=on_done,
                                           holds_job_slot=True))
            except CommittedNotApplied as exc:  # the change IS saved (CORE_API §4)
                self.results.put(JobResult(name, warning=describe_error(exc), on_done=on_done,
                                           holds_job_slot=True))
            except BaseException as exc:  # reported to the UI, never swallowed
                self.results.put(JobResult(name, error=exc, on_done=on_done, holds_job_slot=True))

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
            if isinstance(ev, JobResult):
                if ev.holds_job_slot:
                    self._busy.release()
                if ev.on_done:
                    ev.on_done(ev)


def describe_error(exc: BaseException) -> str:
    """Actionable, human-readable error text for dialogs."""
    from ..paths import PathValidationError
    from ..workspace import (CommittedNotApplied, ConflictError, IdentityUnverifiable, LockLostError,
                             ReadOnlyError, WorkspaceCorruptError, WorkspaceFormatError,
                             WorkspaceLockedError)
    if isinstance(exc, SavedWithWarning):
        return exc.warning
    if isinstance(exc, OutcomeUnknown):
        return (f"RevisionWorx could not confirm whether this check-in was saved: {exc}\n\nCheck the "
                "disk, close and reopen the workspace, and look at the revision history before "
                "trying again.")
    from ..shared import NotLoggedIn, NotPermitted, SetupIncomplete
    if isinstance(exc, NotLoggedIn):
        return f"{exc}.\n\nUse Team \u203a Log in. Nothing was written."
    if isinstance(exc, NotPermitted):
        return f"{str(exc)[0].upper()}{str(exc)[1:]}.\n\nNothing was written."
    if isinstance(exc, SetupIncomplete):
        return f"{str(exc)[0].upper()}{str(exc)[1:]}."
    busy_release = getattr(workspace_module, "LockReleaseBusyError", None)  # not in every supported core
    if busy_release is not None and isinstance(exc, busy_release):
        return (f"{exc}\n\nNothing was written, and this is not a sign of damage. Another RevisionWorx session, "
                "or a program such as a virus scanner or backup tool, has the file open. Wait a moment and try "
                "again. If it keeps happening, close other RevisionWorx windows and run 'revisionworx verify'.")
    if isinstance(exc, RecoveryBlocked):
        return (f"The workspace is read-only because it is recovery-blocked:\n{exc}\n\nNothing was "
                "written. An administrator must resolve this with the command-line tools; then use "
                "Workspace \u203a Check workspace health.")
    if isinstance(exc, WorkspaceCorruptError):
        return (f"RECOVERY BLOCKED \u2014 RevisionWorx cannot confirm this workspace is safe to change:\n{exc}\n\n"
                "To protect your data, it refuses all changes. Nothing was written. Do not edit files "
                "inside .revisionworx. Keep a copy of the workspace folder, follow any instruction above "
                "from a command prompt (otherwise run 'revisionworx verify'), then press F5.")
    if isinstance(exc, (LockLostError, IdentityUnverifiable)):
        return (f"{exc}\n\nRevisionWorx stopped this change to stay safe. Reload and check the document "
                "history before trying again.")
    if isinstance(exc, WorkspaceFormatError):
        return str(exc)
    if isinstance(exc, ReadOnlyError):
        return f"{exc}\n\nThe workspace is open read-only."
    if isinstance(exc, documents.StaleCheckout):
        return (f"Conflicting edit: {exc}.\n\nYour working file was NOT changed. Copy it somewhere safe "
                "before cancelling the checkout.")
    if isinstance(exc, documents.TokenMismatch):
        return (f"{exc}.\n\nOnly the session that checked the document out can check it in. If that "
                "session's state was lost, an administrator can release the checkout (audited).")
    if isinstance(exc, documents.NotCheckedOut):
        return f"{exc}. Check it out first."
    if isinstance(exc, ImportError_):
        return str(exc)
    if isinstance(exc, PathValidationError):
        return f"Invalid file or folder name: {exc}"
    if isinstance(exc, FileNotFoundError) or (isinstance(exc, documents.DocumentError)
                                              and str(exc).endswith("does not exist")):
        return (f"Missing file: {exc.filename if isinstance(exc, OSError) and exc.filename else exc}\n\n"
                "It may have been moved, renamed or deleted outside RevisionWorx. Restore it to its "
                "original location (or cancel the checkout and choose \u201cDiscard edits\u201d to restore "
                "the latest revision).")
    if isinstance(exc, documents.CheckoutConflict):
        return f"This document is {exc}. Ask them to check it in, or ask an administrator to release it."
    if isinstance(exc, documents.NoChanges):
        return ("The working file is identical to the latest revision, so there is nothing to record. "
                "Save your edits first, cancel the checkout, or tick \u201cCheck in even if the file is "
                "unchanged\u201d.")
    if isinstance(exc, ConflictError):
        return f"{exc}\n\nThis item changed while you were viewing it. Reload and reapply your change."
    if isinstance(exc, CommittedNotApplied):
        return f"Saved. Your change is recorded, but finishing it was delayed:\n{exc}"
    if isinstance(exc, WorkspaceLockedError) and (getattr(exc, "holder", None) or {}).get("account"):
        who = exc.holder
        return (f"{who.get('name') or who.get('account')} is saving a change on {who.get('host', 'another computer')}, and it's taking "
                "a while, so yours wasn't made. Try again in a moment.\n\nIf their computer has stopped, this sorts itself out: "
                "RevisionWorx clears a turn once a computer has shown no sign of life for a minute.")
    if isinstance(exc, WorkspaceLockedError):
        token = (getattr(exc, "holder", None) or {}).get("token", "<token from 'lock show'>")
        folder = getattr(exc, "workspace_location", None) or "<workspace folder>"
        return (f"{exc}\n\nNothing was written. Another RevisionWorx window or process is saving changes, so "
                "try again in a moment. If none is running, a previous session crashed and left its lock "
                "behind. Close every other RevisionWorx window first, then from a command prompt run:\n\n"
                f"revisionworx -w \"{folder}\" lock break --token {token} --reason \"session crashed\"")
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc.filename or exc}\nCheck your access rights on this file or folder."
    if isinstance(exc, OSError):
        return f"File system error: {exc}\nCheck that the disk or folder is available."
    return str(exc) or exc.__class__.__name__


def project_folder_name(name: str, number: str = "") -> str:
    """Folder name for a quick-start project, validated for both Windows and Linux."""
    text = " ".join(p for p in (number.strip(), name.strip()) if p)
    return paths.normalize_relative(text)


def to_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
