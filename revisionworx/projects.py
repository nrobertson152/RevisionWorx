# SPDX-License-Identifier: GPL-3.0-or-later
"""Application services for roots, folder templates and projects (Phase 1).

Every mutation goes through :meth:`Workspace.commit`. Nothing here traverses a root; only folders
the user explicitly selects are scanned, and adoption never writes into the adopted folder.
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from . import paths, scan
from .workspace import RecordPut, Workspace, WorkspaceError, current_actor, new_id, now_iso

MODES = ("indexed", "managed")
PLATFORM_KEY = "windows" if os.name == "nt" else "posix"


class ProjectError(WorkspaceError):
    pass


# ---------------------------------------------------------------------------- roots
def add_root(ws: Workspace, name: str, path: Path, other_platform_path: str | None = None) -> str:
    path = Path(path)
    if not path.is_dir():
        raise ProjectError(f"root folder does not exist: {path}")
    real = os.path.realpath(path)
    for rid, root in ws.list("root").items():
        existing = root["paths"].get(PLATFORM_KEY)
        if existing and os.path.normcase(os.path.realpath(existing)) == os.path.normcase(real):
            raise ProjectError(f"that folder is already configured as root {root['name']!r}")
        if root["name"].casefold() == name.casefold():
            raise ProjectError(f"a root named {name!r} already exists")
    paths_map = {PLATFORM_KEY: str(path)}
    if other_platform_path:
        paths_map["posix" if PLATFORM_KEY == "windows" else "windows"] = other_platform_path
    rid = new_id("root")
    ws.commit("root.add", [RecordPut("root", rid, {"id": rid, "name": name, "paths": paths_map,
                                                   "created": now_iso()})])
    return rid


def root_path(ws: Workspace, root_id: str) -> Path:
    root = ws.get("root", root_id)
    if not root:
        raise ProjectError(f"unknown root {root_id}")
    p = root["paths"].get(PLATFORM_KEY)
    if not p:
        raise ProjectError(f"root {root['name']!r} has no path configured for this operating system "
                           f"({PLATFORM_KEY}); add one in Settings")
    return Path(p)


def find_root_for(ws: Workspace, folder: Path) -> tuple[str, str]:
    """Return ``(root_id, relative_path)`` for a folder inside a configured root."""
    real = os.path.normcase(os.path.realpath(folder))
    best = None
    for rid, root in ws.list("root").items():
        p = root["paths"].get(PLATFORM_KEY)
        if not p:
            continue
        rreal = os.path.normcase(os.path.realpath(p))
        if real == rreal or real.startswith(rreal.rstrip(os.sep) + os.sep):
            if best is None or len(rreal) > len(best[1]):
                best = (rid, rreal, p)
    if best is None:
        raise ProjectError(f"{folder} is not inside any configured engineering root")
    return best[0], paths.relative_to_root(Path(best[2]), Path(folder))


# ---------------------------------------------------------------------------- templates
STANDARD_TEMPLATE = {
    "id": "standard",
    "name": "Standard engineering project",
    "description": "General mechanical/fluid-power project layout.",
    "folders": ["01 Customer Data", "02 CAD/Parts", "02 CAD/Assemblies", "03 Drawings/Native",
                "03 Drawings/Released PDF", "04 Schematics", "05 BOM", "06 Documents",
                "07 Correspondence"],
    "files": [],
}


def validate_template(t: dict) -> dict:
    for key in ("id", "name", "folders"):
        if key not in t:
            raise ProjectError(f"template is missing {key!r}")
    tid = str(t["id"])
    if not tid or any(c not in string.ascii_lowercase + string.digits + "-_." for c in tid) or len(tid) > 64:
        raise ProjectError("template id must be lowercase letters, digits, '-', '_' or '.'")
    folders = [paths.normalize_relative(f) for f in t["folders"]]
    files = []
    for f in t.get("files", []):
        rel = paths.normalize_relative(f["path"])
        content = f.get("content", "")
        if not isinstance(content, str) or len(content) > 256 * 1024:
            raise ProjectError(f"starter file {rel!r} must be text under 256 KiB")
        files.append({"path": rel, "content": content})
    keys = [paths.case_key(x) for x in folders + [f["path"] for f in files]]
    if len(keys) != len(set(keys)):
        raise ProjectError("template contains entries that collide on case-insensitive filesystems")
    return {"id": tid, "name": str(t["name"]), "description": str(t.get("description", "")),
            "folders": folders, "files": files}


def save_template(ws: Workspace, template: dict) -> str:
    t = validate_template(template)
    ws.commit("template.save", [RecordPut("template", t["id"], t)])
    return t["id"]


def list_templates(ws: Workspace) -> dict[str, dict]:
    templates = {"standard": STANDARD_TEMPLATE}
    templates.update(ws.list("template"))
    return templates


# ---------------------------------------------------------------------------- projects
@dataclass
class NewProject:
    name: str
    number: str = ""
    customer: str = ""
    template_id: str = "standard"
    root_id: str | None = None
    rel_path: str | None = None
    mode: str | None = None
    description: str = ""


def render_project_path(pattern: str, np: NewProject) -> str:
    values = {"name": np.name, "number": np.number, "customer": np.customer or "Unassigned",
              "year": str(date.today().year)}
    try:
        rendered = pattern.format(**values)
    except (KeyError, IndexError) as exc:
        raise ProjectError(f"project path pattern {pattern!r} uses unknown field {exc}") from exc
    rendered = "/".join(part.strip() for part in rendered.replace("\\", "/").split("/") if part.strip())
    return paths.normalize_relative(rendered)


def _registered_conflicts(ws: Workspace, root_id: str, rel: str) -> list[dict]:
    key = paths.case_key(rel)
    conflicts = []
    for p in ws.list("project").values():
        if p["root_id"] != root_id:
            continue
        other = paths.case_key(p["rel_path"])
        if other == key or key.startswith(other + "/") or other.startswith(key + "/"):
            conflicts.append(p)
    return conflicts


def _check_number(ws: Workspace, number: str, allow_duplicate: bool) -> None:
    if not number or allow_duplicate:
        return
    for p in ws.list("project").values():
        if p.get("number", "").casefold() == number.casefold():
            raise ProjectError(f"project number {number!r} is already used by {p['name']!r}")


def create_project(ws: Workspace, np: NewProject, allow_duplicate_number: bool = False) -> dict:
    """Create a new managed project folder from a template and register it."""
    if not np.name.strip():
        raise ProjectError("project name is required")
    settings = ws.settings()
    mode = np.mode or settings["new_project_mode"]
    if mode not in MODES:
        raise ProjectError(f"mode must be one of {MODES}")
    roots = ws.list("root")
    root_id = np.root_id or (next(iter(roots)) if len(roots) == 1 else None)
    if not root_id:
        raise ProjectError("choose an engineering root for the new project")
    base = root_path(ws, root_id)
    rel = paths.normalize_relative(np.rel_path) if np.rel_path else \
        render_project_path(settings["project_path_pattern"], np)
    if _registered_conflicts(ws, root_id, rel):
        raise ProjectError(f"{rel!r} overlaps an already registered project")
    _check_number(ws, np.number, allow_duplicate_number)
    template = list_templates(ws).get(np.template_id)
    if template is None:
        raise ProjectError(f"unknown template {np.template_id!r}")
    template = validate_template(template)

    target = paths.resolve_under_root(base, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.mkdir()  # exclusive: claims the name, never merges into an existing folder
    except FileExistsError:
        raise ProjectError(f"{target} already exists; use Adopt Existing Project for existing folders") from None
    for folder in template["folders"]:
        target.joinpath(*folder.split("/")).mkdir(parents=True, exist_ok=True)
    for f in template["files"]:
        dest = target.joinpath(*f["path"].split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "x", encoding="utf-8", newline="") as fh:
            fh.write(f["content"])

    pid = new_id("prj")
    record = {"id": pid, "name": np.name.strip(), "number": np.number.strip(),
              "customer": np.customer.strip(), "description": np.description, "mode": mode,
              "root_id": root_id, "rel_path": rel, "status": "active", "origin": "created",
              "template_id": template["id"], "created": now_iso(), "created_by": current_actor(),
              "baseline": None}
    ws.commit("project.create", [RecordPut("project", pid, record)])
    return record


def preview_adoption(ws: Workspace, folder: Path,
                     progress: Callable[[int, str], None] | None = None,
                     cancelled: Callable[[], bool] | None = None) -> dict:
    """Read-only preview of adopting ``folder``. Writes nothing anywhere."""
    folder = Path(folder)
    root_id, rel = find_root_for(ws, folder)
    settings = ws.settings()
    report = scan.scan_scope(folder, settings["adoption_exclusions"], settings["adoption_max_files"],
                             hash_files=False, progress=progress, cancelled=cancelled)
    conflicts = _registered_conflicts(ws, root_id, rel)
    summary = report.summary()
    summary.update({"root_id": root_id, "rel_path": rel,
                    "conflicts": [{"id": c["id"], "name": c["name"], "rel_path": c["rel_path"]}
                                  for c in conflicts]})
    if conflicts:
        summary["warnings"].insert(0, "This folder overlaps an already registered project.")
    summary["cad_references"] = ("Native CAD references are NOT extracted in this version; "
                                 "files are inventoried by name, size and date only.")
    return summary


def adopt_project(ws: Workspace, folder: Path, name: str, mode: str = "indexed", number: str = "",
                  customer: str = "", description: str = "", allow_duplicate_number: bool = False,
                  progress: Callable[[int, str], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> dict:
    """Register an existing folder. Establishes a baseline inventory; never modifies the folder
    and never fabricates historical revisions."""
    if mode not in MODES:
        raise ProjectError(f"mode must be one of {MODES}")
    if not name.strip():
        raise ProjectError("project name is required")
    folder = Path(folder)
    root_id, rel = find_root_for(ws, folder)
    if _registered_conflicts(ws, root_id, rel):
        raise ProjectError(f"{rel!r} overlaps an already registered project")
    _check_number(ws, number, allow_duplicate_number)
    settings = ws.settings()
    report = scan.scan_scope(folder, settings["adoption_exclusions"], settings["adoption_max_files"],
                             hash_files=bool(settings["hash_on_adopt"]), progress=progress, cancelled=cancelled)
    if report.truncated:
        raise ProjectError("the folder exceeds the adoption file limit; raise the limit or adopt a "
                           "smaller scope. Nothing was registered.")
    baseline = {"format": "RevisionWorx Baseline", "version": 1, "taken": now_iso(),
                "taken_by": current_actor(), "scope_rel_path": rel, "root_id": root_id,
                "summary": report.summary(), "files": [f.as_json() for f in report.files],
                "note": "Initial baseline of existing files. No historical revisions are asserted."}
    blob_ref = ws.write_blob("baseline", baseline)
    pid = new_id("prj")
    record = {"id": pid, "name": name.strip(), "number": number.strip(), "customer": customer.strip(),
              "description": description, "mode": mode, "root_id": root_id, "rel_path": rel,
              "status": "active", "origin": "adopted", "template_id": None, "created": now_iso(),
              "created_by": current_actor(),
              "baseline": {"blob": blob_ref, "files": len(report.files), "hashed": report.hashed,
                           "complete": report.complete, "warnings": report.warnings()}}
    ws.commit("project.adopt", [RecordPut("project", pid, record)], blob_refs=[blob_ref])
    return record


def update_project(ws: Workspace, project_id: str, **changes) -> dict:
    allowed = {"name", "number", "customer", "description", "status", "mode"}
    bad = set(changes) - allowed
    if bad:
        raise ProjectError(f"cannot change {sorted(bad)} here")
    record = ws.get("project", project_id)
    if record is None:
        raise ProjectError(f"unknown project {project_id}")
    if "status" in changes and changes["status"] not in ("active", "archived"):
        raise ProjectError("status must be 'active' or 'archived'")
    if "mode" in changes and changes["mode"] not in MODES:
        raise ProjectError(f"mode must be one of {MODES}")
    if changes.get("number") and changes["number"].casefold() != record.get("number", "").casefold():
        _check_number(ws, changes["number"], False)
    new = {**record, **changes, "modified": now_iso()}
    ws.commit("project.update", [RecordPut("project", project_id, new)])
    return new


def project_folder(ws: Workspace, record: dict) -> Path:
    return paths.resolve_under_root(root_path(ws, record["root_id"]), record["rel_path"])
