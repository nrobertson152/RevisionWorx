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
import shutil
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import atomicio, documents, paths, projects
from ..clientstate import CheckoutTokens
from ..index import LocalIndex, open_index
from ..workspace import Workspace

PAGE_SIZE = 200

ALPHA_NOTICE = ("RevisionWorx 0.1.0 Alpha \u2014 single user, local disk only. Not suitable for "
                "irreplaceable production data: use disposable test projects and keep your own backups.")
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

    def close_workspace(self) -> None:
        self.location = None
        self.workspace_id = None

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
        """Read-only check shown at startup. Never repairs anything (repair needs the writer lock
        and happens automatically on the next write)."""
        out = {"ok": True, "blocked": False, "pending_recovery": 0, "message": ""}
        if self.location is None:
            return out
        from ..workspace import WorkspaceCorruptError
        try:
            with self._read() as ws:
                head, applied = ws.head_seq(), ws.applied_seq()
                _verify_journal(ws)
        except WorkspaceCorruptError as exc:
            return {"ok": False, "blocked": True, "pending_recovery": 0,
                    "message": describe_error(exc)}
        if head > applied:
            out.update(pending_recovery=head - applied,
                       message=(f"{head - applied} saved change(s) from an interrupted session have not "
                                "been fully applied yet. They will be completed automatically by your "
                                "next change."))
        return out

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
            _verify_journal(ws)  # fail closed before touching the project folder
            base = projects.project_folder(ws, rec)
            rel = paths.normalize_relative("/".join(p for p in (subfolder.strip("/"), source.name) if p))
            dest = paths.resolve_under_root(base, rel)
            try:
                if os.path.samefile(source, dest):
                    raise ImportError_(f"{source.name} is already in the project; use Register file")
            except FileNotFoundError:
                pass
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
                # failed, or registration was refused by validation before the journal write. An
                # ambiguous commit failure (e.g. CommittedNotApplied) keeps the file, because it
                # may already be the registered working file.
                from ..workspace import CommittedNotApplied, ConflictError
                pre_commit = (not registering) or (
                    isinstance(exc, (documents.DocumentError, paths.PathValidationError, ConflictError))
                    and not isinstance(exc, CommittedNotApplied))
                if pre_commit:
                    try:
                        os.unlink(dest)  # ours: created exclusively above and not registered
                    except OSError:
                        pass
                raise

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
            return documents.working_path(ws, documents._require_doc(ws, doc_id))

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
    @property
    def busy(self) -> bool:
        """A background write/scan is running (only one at a time)."""
        return self._busy.locked()

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
    from ..paths import PathValidationError
    from ..workspace import (CommittedNotApplied, ConflictError, IdentityUnverifiable, LockLostError,
                             ReadOnlyError, WorkspaceCorruptError, WorkspaceFormatError,
                             WorkspaceLockedError)
    if isinstance(exc, WorkspaceCorruptError):
        return (f"RECOVERY BLOCKED \u2014 the workspace's saved history failed an integrity check:\n{exc}\n\n"
                "To protect your data, RevisionWorx refuses all changes. Nothing was written. Do not "
                "edit files inside .revisionworx. Run 'revisionworx verify' from a command prompt and "
                "keep a copy of the workspace folder before any repair.")
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


def _verify_journal(ws: Workspace) -> None:
    """Raise WorkspaceCorruptError if any journal entry fails its seq/SHA-256 digest check.

    O(journal); acceptable for alpha-sized workspaces. A core read-only health API is requested in
    the Issue #13 API dependency list so the GUI does not have to do this itself."""
    for seq in ws.journal_seqs():
        ws.read_entry(seq)


def project_folder_name(name: str, number: str = "") -> str:
    """Folder name for a quick-start project, validated for both Windows and Linux."""
    text = " ".join(p for p in (number.strip(), name.strip()) if p)
    return paths.normalize_relative(text)


def to_json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
