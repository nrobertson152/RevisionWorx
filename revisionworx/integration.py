# RevisionWorx - lightweight engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Integration interface for other programs (Caldera, Ledgerbook, OpenSchematix Viewer, TraceMarx, ...).

Exposed on the command line as ``revisionworx link <command>``: every command prints exactly one JSON object
on stdout, so another program needs nothing but the ``revisionworx`` executable (a pip install or the
standalone build) and a JSON parser. ``docs/INTEGRATION.md`` is the contract; ``API_VERSION`` increments on
incompatible changes.

Everything here is composed from the same operations the desktop window uses (``gui.controller.Controller``,
which has no GUI toolkit imports), so integrations get the same safety: one writer at a time, fail closed,
check-in through a real checkout, and imports that never overwrite a file.

Which workspace: an explicit ``workspace`` argument, else ``REVISIONWORX_WORKSPACE``, else (for a file) the
nearest folder above it holding ``.revisionworx``, else the workspaces recently opened in the desktop window.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, documents, paths, projects
from .workspace import Workspace, WorkspaceError

API_VERSION = 1


class LinkError(Exception):
    """A refusal with a message meant for the user of the calling program. Nothing was changed."""

    def __init__(self, message: str, kind: str = "refused"):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------------------------- helpers
def _controller(workspace: Path | None = None, cfg_dir: Path | None = None, cache_dir: Path | None = None,
                state_dir: Path | None = None):
    from .gui.controller import Controller
    ctl = Controller(cfg_dir=cfg_dir, cache_dir=cache_dir, state_dir=state_dir)
    if workspace is not None:
        ctl.open_workspace(Path(workspace))
    return ctl


def _is_workspace(folder: Path) -> bool:
    return (Workspace.meta_dir_for(folder) / "workspace.json").is_file()


def candidate_workspaces(near: Path | None = None, workspace: Path | None = None,
                         cfg_dir: Path | None = None) -> list[Path]:
    """Workspaces to consider, best first, without duplicates."""
    found: list[Path] = []

    def add(p: Path | str | None) -> None:
        if not p:
            return
        p = Path(p)
        try:
            if _is_workspace(p) and all(not _same(p, q) for q in found):
                found.append(p)
        except OSError:
            pass

    if workspace is not None:
        if not _is_workspace(Path(workspace)):
            raise LinkError(f"{workspace} is not a RevisionWorx workspace", "no-workspace")
        return [Path(workspace)]
    add(os.environ.get("REVISIONWORX_WORKSPACE"))
    if near is not None:
        for parent in Path(os.path.abspath(near)).parents:
            add(parent)
    from .gui.controller import Controller
    for r in Controller(cfg_dir=cfg_dir).recent_workspaces():
        add(r)
    return found


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def _rev_summary(ws: Workspace, rev: dict | None) -> dict | None:
    if not rev:
        return None
    lc = documents.get_lifecycle(ws)
    names = {s["id"]: s["name"] for s in lc["states"]}
    return {"id": rev["id"], "label": rev["label"], "state": rev["state"],
            "state_name": names.get(rev["state"], rev["state"]), "sha256": rev["sha256"], "size": rev["size"],
            "created": rev.get("created"), "author": (rev.get("author") or {}).get("user", ""),
            "description": rev.get("description", "")}


def _doc_summary(ws: Workspace, doc: dict, tokens=None) -> dict:
    project = ws.get("project", doc["project_id"]) or {}
    hist = documents.history(ws, doc["id"])
    latest = hist[-1] if hist else None
    released_state = documents.get_lifecycle(ws)["released"]
    released = next((r for r in reversed(hist) if r["state"] == released_state), None)
    co = ws.get("checkout", doc["id"])
    try:
        path = str(documents.working_path(ws, doc))
    except Exception:  # noqa: BLE001 - an unreachable root still lets the record be described
        path = None
    out = {"id": doc["id"], "number": doc.get("number", ""), "title": doc.get("title", ""),
           "status": doc.get("status", ""), "type": doc.get("type_id", ""),
           "project": {"id": doc["project_id"], "name": project.get("name", ""),
                       "number": project.get("number", "")},
           "path": path, "revisions": len(hist), "latest": _rev_summary(ws, latest),
           "released": _rev_summary(ws, released), "checkout": None}
    if co:
        owner = co.get("owner") or {}
        out["checkout"] = {"user": owner.get("user", ""), "host": owner.get("host", ""), "since": co.get("acquired"),
                           "mine": bool(tokens is not None and documents.is_mine(ws, tokens, {**co, "document_id": doc["id"]}))}
    return out


def _ws_summary(ws: Workspace, location: Path) -> dict:
    return {"id": ws.id, "name": ws.name, "location": str(location)}


def _find_document(ws: Workspace, ref: str, project_id: str | None = None) -> dict | None:
    """By id, or by document number (unique within the project if given, else within the workspace)."""
    doc = ws.get("document", ref)
    if doc is not None:
        return doc
    key = ref.strip().casefold()
    hits = [d for d in ws.list("document").values()
            if (d.get("number") or "").strip().casefold() == key and (project_id is None or d["project_id"] == project_id)]
    if len(hits) > 1:
        raise LinkError(f"document number {ref!r} is used in several projects; give the project or the id",
                        "ambiguous")
    return hits[0] if hits else None


def _find_project(ws: Workspace, ref: str) -> dict:
    rec = ws.get("project", ref)
    if rec is not None:
        return rec
    key = ref.strip().casefold()
    hits = [p for p in ws.list("project").values()
            if key in ((p.get("number") or "").strip().casefold(), (p.get("name") or "").strip().casefold())]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LinkError(f"no project {ref!r} in this workspace", "not-found")
    raise LinkError(f"{ref!r} matches several projects; use the project id", "ambiguous")


def _document_for_path(ws: Workspace, file: Path) -> dict | None:
    target = Path(os.path.abspath(file))
    for doc in ws.list("document").values():
        try:
            managed = documents.working_path(ws, doc)
        except Exception:  # noqa: BLE001
            continue
        try:
            if os.path.exists(target) and os.path.exists(managed):
                if os.path.samefile(managed, target):
                    return doc
            elif os.path.normcase(os.path.abspath(managed)) == os.path.normcase(str(target)):
                return doc
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------------------------- commands
FEATURES = ["units"]                       # additive commands since API 1: ask before relying on one


def version() -> dict:
    return {"api": API_VERSION, "version": __version__, "features": list(FEATURES)}


def workspaces(cfg_dir: Path | None = None) -> dict:
    out = []
    for loc in candidate_workspaces(cfg_dir=cfg_dir):
        try:
            with Workspace.open(loc) as ws:
                out.append(_ws_summary(ws, loc))
        except (WorkspaceError, OSError) as exc:
            out.append({"id": None, "name": None, "location": str(loc), "error": str(exc)})
    return {"workspaces": out}


def status(file: Path, workspace: Path | None = None, cfg_dir: Path | None = None,
           state_dir: Path | None = None) -> dict:
    """Is ``file`` a RevisionWorx document, and how does the file on disk compare with its latest revision?"""
    from .clientstate import CheckoutTokens
    file = Path(file)
    tokens = CheckoutTokens(state_dir)
    for loc in candidate_workspaces(near=file, workspace=workspace, cfg_dir=cfg_dir):
        try:
            ws = Workspace.open(loc)
        except (WorkspaceError, OSError):
            continue
        with ws:
            doc = _document_for_path(ws, file)
            if doc is None:
                continue
            summary = _doc_summary(ws, doc, tokens)
            st = documents.document_status(ws, doc["id"])
            return {"controlled": True, "workspace": _ws_summary(ws, loc), "document": summary,
                    "file": {"path": str(file), "state": st["state"],
                             "matches_latest": st["state"] == "unchanged"}}
    return {"controlled": False, "workspace": None, "document": None,
            "file": {"path": str(file), "state": "unmanaged", "matches_latest": False}}


def list_projects(workspace: Path | None = None, cfg_dir: Path | None = None) -> dict:
    loc = _one_workspace(workspace, cfg_dir)
    with Workspace.open(loc) as ws:
        rows = []
        for p in sorted(ws.list("project").values(), key=lambda p: ((p.get("number") or ""), p.get("name") or "")):
            try:
                folder = str(projects.project_folder(ws, p))
            except Exception:  # noqa: BLE001
                folder = None
            rows.append({"id": p["id"], "name": p.get("name", ""), "number": p.get("number", ""),
                         "customer": p.get("customer", ""), "mode": p.get("mode", ""),
                         "archived": bool(p.get("archived")), "folder": folder})
        return {"workspace": _ws_summary(ws, loc), "projects": rows}


def list_documents(workspace: Path | None = None, project: str | None = None, text: str = "",
                   cfg_dir: Path | None = None, state_dir: Path | None = None) -> dict:
    from .clientstate import CheckoutTokens
    loc = _one_workspace(workspace, cfg_dir)
    tokens = CheckoutTokens(state_dir)
    with Workspace.open(loc) as ws:
        pid = _find_project(ws, project)["id"] if project else None
        needle = text.strip().casefold()
        rows = []
        for doc in ws.list("document").values():
            if pid and doc["project_id"] != pid:
                continue
            if needle and needle not in " ".join((doc.get("number") or "", doc.get("title") or "",
                                                  doc.get("rel_path") or "")).casefold():
                continue
            rows.append(_doc_summary(ws, doc, tokens))
        rows.sort(key=lambda d: (d["project"]["name"], d["number"], d["title"]))
        return {"workspace": _ws_summary(ws, loc), "documents": rows}


def document(ref: str, workspace: Path | None = None, project: str | None = None,
             cfg_dir: Path | None = None, state_dir: Path | None = None) -> dict:
    from .clientstate import CheckoutTokens
    loc = _one_workspace(workspace, cfg_dir)
    with Workspace.open(loc) as ws:
        pid = _find_project(ws, project)["id"] if project else None
        doc = _find_document(ws, ref, pid)
        if doc is None:
            raise LinkError(f"no document {ref!r}", "not-found")
        out = _doc_summary(ws, doc, CheckoutTokens(state_dir))
        out["history"] = [_rev_summary(ws, r) for r in documents.history(ws, doc["id"])]
        return {"workspace": _ws_summary(ws, loc), "document": out}


def fetch(ref: str, dest_dir: Path, revision: str | None = None, workspace: Path | None = None,
          project: str | None = None, released: bool = False, cfg_dir: Path | None = None) -> dict:
    """Copy one revision's verified content to ``dest_dir`` (read-only use: viewing, printing, analysis).
    Default: the latest revision; ``released``: the latest Released one; ``revision``: that label."""
    loc = _one_workspace(workspace, cfg_dir)
    ctl = _controller(loc, cfg_dir=cfg_dir)
    with Workspace.open(loc) as ws:
        pid = _find_project(ws, project)["id"] if project else None
        doc = _find_document(ws, ref, pid)
        if doc is None:
            raise LinkError(f"no document {ref!r}", "not-found")
        hist = documents.history(ws, doc["id"])
        if revision:
            pick = [r for r in hist if r["label"].casefold() == revision.strip().casefold()]
            if not pick:
                raise LinkError(f"{doc.get('number') or doc['id']} has no revision {revision!r}", "not-found")
            rev = pick[-1]
        elif released:
            state = documents.get_lifecycle(ws)["released"]
            pick = [r for r in hist if r["state"] == state]
            if not pick:
                raise LinkError(f"{doc.get('number') or doc['id']} has no released revision", "not-found")
            rev = pick[-1]
        else:
            if not hist:
                raise LinkError("the document has no revisions", "not-found")
            rev = hist[-1]
        name = Path(rev.get("source_rel_path") or doc["rel_path"]).name
        stem, suffix = os.path.splitext(name)
        summary = _rev_summary(ws, rev)
        ws_summary = _ws_summary(ws, loc)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{stem} (rev {rev['label']}){suffix}"
    out = ctl.export_revision(rev["id"], target, overwrite=True)
    return {"workspace": ws_summary, "document_id": doc["id"], "revision": summary, "path": str(out)}


def publish(source: Path, *, workspace: Path | None = None, project: str | None = None,
            document_ref: str | None = None, number: str = "", title: str = "", folder: str = "",
            message: str = "", cfg_dir: Path | None = None, cache_dir: Path | None = None,
            state_dir: Path | None = None) -> dict:
    """Put a file produced by another program under revision control.

    * ``document_ref`` (or ``number`` matching an existing document in ``project``, or ``source`` already being a
      document's working file): a NEW REVISION of that document. RevisionWorx checks it out to this session
      (refused if someone else holds it), copies ``source`` onto the working file and checks in with ``message``.
      If the content is identical to the latest revision nothing is recorded (``"result": "unchanged"``) and a
      checkout made by this call is released.
    * otherwise a NEW DOCUMENT in ``project``: registered in place if ``source`` is already inside the project
      folder, else copied into it (``folder`` is a subfolder; an existing file is never overwritten).
    """
    source = Path(source)
    if not source.is_file():
        raise LinkError(f"{source} is not a file", "bad-input")
    message = (message or "").strip()
    ctl = None
    loc = _one_workspace(workspace, cfg_dir, near=source)
    ctl = _controller(loc, cfg_dir=cfg_dir, cache_dir=cache_dir, state_dir=state_dir)
    with Workspace.open(loc) as ws:
        rec = _find_project(ws, project) if project else None
        doc = None
        if document_ref:
            doc = _find_document(ws, document_ref, rec["id"] if rec else None)
            if doc is None:
                raise LinkError(f"no document {document_ref!r}", "not-found")
        if doc is None:
            doc = _document_for_path(ws, source)
        if doc is None and number and rec is not None:
            doc = _find_document(ws, number, rec["id"])
        project_folder = projects.project_folder(ws, rec) if rec is not None and doc is None else None
        ws_summary = _ws_summary(ws, loc)
    if doc is not None:
        return {"workspace": ws_summary, **_new_revision(ctl, doc, source, message or "Updated")}
    if rec is None:
        raise LinkError("give the project to add a new document to (or an existing document)", "bad-input")
    inside = False
    try:
        inside = Path(os.path.abspath(project_folder)) in Path(os.path.abspath(source)).parents
    except (TypeError, ValueError):
        inside = False
    if inside:
        from .workspace import CommittedNotApplied
        try:
            with ctl._write() as ws:
                new_doc, rev = documents.register_document(
                    ws, rec["id"], source, number=number.strip(), title=title.strip(),
                    revision_description=message or "Initial revision")
        except CommittedNotApplied:
            return {"workspace": ws_summary, "result": "created", "warning": "saved; finishing was delayed"}
        action = "registered"
    else:
        new_doc, rev = ctl.import_document(rec["id"], source, folder, number=number, title=title,
                                           description=message or "Initial revision")
        action = "imported"
    with Workspace.open(loc) as ws:
        return {"workspace": ws_summary, "result": "created", "action": action,
                "document": _doc_summary(ws, ws.get("document", new_doc["id"]) or new_doc, ctl.tokens)}


def _new_revision(ctl, doc: dict, source: Path, message: str) -> dict:
    from .gui.controller import SavedWithWarning
    detail = ctl.document_detail(doc["id"])
    co = detail["checkout"]
    made_checkout = False
    if co and not detail["mine"]:
        owner = (co.get("owner") or {})
        raise LinkError(f"{doc.get('number') or doc['title']} is checked out by {owner.get('user', '?')} on "
                        f"{owner.get('host', '?')}; ask them to check it in first", "checked-out")
    if not co:
        try:
            ctl.checkout(doc["id"], note="publish from another program")
        except SavedWithWarning as w:
            raise LinkError(f"checked out, but: {w.warning}", "warning") from w
        made_checkout = True
    working = ctl.working_file(doc["id"])
    try:
        same_file = working.exists() and os.path.samefile(working, source)
    except OSError:
        same_file = False
    if not same_file:
        tmp = working.with_name(f".{working.name}.rwx-publish.tmp")
        shutil.copyfile(source, tmp)
        os.replace(tmp, working)
    warning = None
    try:
        rev = ctl.checkin_now(doc["id"], message)
    except documents.NoChanges:
        if made_checkout:
            ctl.cancel_checkout(doc["id"], revert=False)
        latest = ctl.document_detail(doc["id"])["latest"]
        with ctl._read() as ws:
            return {"result": "unchanged", "action": "none",
                    "document": _doc_summary(ws, ws.get("document", doc["id"]), ctl.tokens),
                    "revision": _rev_summary(ws, latest)}
    except SavedWithWarning as w:
        warning, rev = w.warning, w.value
    with ctl._read() as ws:
        out = {"result": "revised", "action": "checkin",
               "document": _doc_summary(ws, ws.get("document", doc["id"]), ctl.tokens),
               "revision": _rev_summary(ws, rev) if rev else None}
    if warning:
        out["warning"] = warning
    return out


def _unit_service(ws: Workspace):
    from .units import service_for
    return service_for(ws)


def _unit_error(exc: Exception) -> LinkError:
    from .units import Ambiguous, MalformedUnitRecord, NotFound
    kind = "not-found" if isinstance(exc, NotFound) else "ambiguous" if isinstance(exc, Ambiguous) else \
        "error" if isinstance(exc, MalformedUnitRecord) else "bad-input"
    return LinkError(str(exc), kind)


def list_units(workspace: Path | None = None, project: str | None = None, text: str = "", detail: bool = False,
               cfg_dir: Path | None = None) -> dict:
    """The unit register (docs/UNITS.md): summaries, or with ``detail`` everything ``unit`` answers for each, history
    included, in one call."""
    from .units import UnitError, query
    loc = _one_workspace(workspace, cfg_dir)
    with Workspace.open(loc) as ws:
        svc = _unit_service(ws)
        try:
            pid = _find_project(ws, project)["id"] if project else ""
            units = query.search(svc, text, pid)
            rows = [query.unit_detail(svc, u) if detail else query.unit_summary(u) for u in units]
        except UnitError as exc:
            raise _unit_error(exc) from None
        return {"workspace": _ws_summary(ws, loc), "units": rows}


def unit(ref: str, workspace: Path | None = None, at: int | None = None, on: str = "", cfg_dir: Path | None = None) -> dict:
    """One unit by id, FieldWorx asset id or identifier value: identity, the documents that apply to it (with flags, never
    a substituted revision), components, milestones and history. ``at``/``on`` answer for an event or a date."""
    from .units import UnitError, query
    loc = _one_workspace(workspace, cfg_dir)
    with Workspace.open(loc) as ws:
        svc = _unit_service(ws)
        try:
            u = svc.resolve(ref)
            return {"workspace": _ws_summary(ws, loc), "unit": query.unit_detail(svc, u, at=at, on=on)}
        except UnitError as exc:
            raise _unit_error(exc) from None


def open_in_gui(ref: str | None = None, file: Path | None = None, workspace: Path | None = None,
                cfg_dir: Path | None = None, launch: bool = True) -> dict:
    """Start the desktop window on a document (by reference, or the document a file belongs to)."""
    args: list[str] = []
    if file is not None:
        st = status(Path(file), workspace=workspace, cfg_dir=cfg_dir)
        if not st["controlled"]:
            raise LinkError(f"{file} is not a RevisionWorx document", "not-found")
        args = [st["workspace"]["location"], "--document", st["document"]["id"]]
    elif ref is not None:
        loc = _one_workspace(workspace, cfg_dir)
        with Workspace.open(loc) as ws:
            doc = _find_document(ws, ref)
            if doc is None:
                raise LinkError(f"no document {ref!r}", "not-found")
            args = [str(loc), "--document", doc["id"]]
    else:
        args = [str(_one_workspace(workspace, cfg_dir))]
    cmd = _gui_command() + args
    if launch:
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                                  "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
    return {"launched": launch, "command": cmd}


def _gui_command() -> list[str]:
    if getattr(sys, "frozen", False):                     # standalone build: the window exe sits beside us
        exe = Path(sys.executable).with_name("revisionworx-gui" + (".exe" if os.name == "nt" else ""))
        return [str(exe)]
    found = shutil.which("revisionworx-gui")
    return [found] if found else [sys.executable, "-m", "revisionworx.gui.app"]


def _one_workspace(workspace: Path | None, cfg_dir: Path | None, near: Path | None = None) -> Path:
    found = candidate_workspaces(near=near, workspace=workspace, cfg_dir=cfg_dir)
    if not found:
        raise LinkError("no RevisionWorx workspace found: open one in RevisionWorx first, or set "
                        "REVISIONWORX_WORKSPACE", "no-workspace")
    return found[0]


# ------------------------------------------------------------------------------------------ command line
def main(argv: list[str], workspace: str | None = None) -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(prog="revisionworx link",
                                description="Integration commands for other programs. Output: one JSON object.")
    p.add_argument("-w", "--workspace", default=workspace)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    sub.add_parser("workspaces")
    s = sub.add_parser("status"); s.add_argument("file")
    sub.add_parser("projects")
    s = sub.add_parser("documents"); s.add_argument("--project"); s.add_argument("--text", default="")
    s = sub.add_parser("document"); s.add_argument("ref"); s.add_argument("--project")
    s = sub.add_parser("fetch"); s.add_argument("ref"); s.add_argument("--to", required=True)
    s.add_argument("--revision"); s.add_argument("--released", action="store_true"); s.add_argument("--project")
    s = sub.add_parser("publish"); s.add_argument("file"); s.add_argument("--project"); s.add_argument("--document")
    s.add_argument("--number", default=""); s.add_argument("--title", default=""); s.add_argument("--folder", default="")
    s.add_argument("-m", "--message", default="")
    s = sub.add_parser("open"); s.add_argument("--document"); s.add_argument("--file")
    s = sub.add_parser("units"); s.add_argument("--project"); s.add_argument("--text", default="")
    s.add_argument("--detail", action="store_true")
    s = sub.add_parser("unit"); s.add_argument("ref"); g = s.add_mutually_exclusive_group()
    g.add_argument("--at", type=int); g.add_argument("--on", default="")
    try:
        a = p.parse_args(argv)
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(json.dumps({"ok": False, "error": "invalid arguments; see 'revisionworx link -h'",
                              "kind": "bad-input"}))
        return int(exc.code or 0)
    ws = Path(a.workspace) if a.workspace else None
    try:
        if a.cmd == "version":
            out = version()
        elif a.cmd == "workspaces":
            out = workspaces()
        elif a.cmd == "status":
            out = status(Path(a.file), workspace=ws)
        elif a.cmd == "projects":
            out = list_projects(ws)
        elif a.cmd == "documents":
            out = list_documents(ws, a.project, a.text)
        elif a.cmd == "document":
            out = document(a.ref, ws, a.project)
        elif a.cmd == "fetch":
            out = fetch(a.ref, Path(a.to), a.revision, ws, a.project, a.released)
        elif a.cmd == "publish":
            out = publish(Path(a.file), workspace=ws, project=a.project, document_ref=a.document, number=a.number,
                          title=a.title, folder=a.folder, message=a.message)
        elif a.cmd == "units":
            out = list_units(ws, a.project, a.text, a.detail)
        elif a.cmd == "unit":
            out = unit(a.ref, ws, a.at, a.on)
        else:
            out = open_in_gui(a.document, Path(a.file) if a.file else None, ws)
    except LinkError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "kind": exc.kind}))
        return 2
    except Exception as exc:  # noqa: BLE001 - the caller gets a message, never a traceback on stdout
        from .gui.controller import describe_error
        kind = "busy" if exc.__class__.__name__ in ("WorkspaceLockedError", "LockReleaseBusyError") else "error"
        print(json.dumps({"ok": False, "error": describe_error(exc), "kind": kind}))
        return 2
    print(json.dumps({"ok": True, **out}, ensure_ascii=False, default=str))
    return 0
