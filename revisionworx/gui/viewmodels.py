# SPDX-License-Identifier: GPL-3.0-or-later
"""Toolkit-free view-models (the "Model" side of the GUI's Model-View split).

Everything a window needs to decide - what rows to show, what a button's enabled state is, how a
form validates, what text a report contains - lives here and is tested headless. The Qt widgets in
``revisionworx.gui.qt`` only bind these objects to widgets and forward user events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .. import projects
from .controller import Controller, JobResult, Progress, describe_error


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    width: int
    stretch: bool = False


class Observable:
    """Minimal change notification so views can re-render without polling."""

    def __init__(self):
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def changed(self) -> None:
        for fn in list(self._listeners):
            fn()


# ---------------------------------------------------------------------------- project list
class ProjectListModel(Observable):
    COLUMNS = (Column("number", "Number", 110), Column("name", "Name", 260, True),
               Column("customer", "Customer", 160), Column("mode", "Mode", 90),
               Column("status", "Status", 90), Column("rel_path", "Folder", 320, True))

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.query = ""
        self.include_archived = False
        self.page = 0
        self.rows: list[dict] = []
        self.has_more = False
        self.status_text = "No workspace open."
        self.error: str | None = None

    def refresh(self) -> None:
        if self.ctl.location is None:
            self.rows, self.has_more, self.status_text = [], False, "No workspace open."
            self.changed()
            return
        try:
            data = self.ctl.project_page(self.query, self.page, self.include_archived)
        except Exception as exc:
            self.error = describe_error(exc)
            self.status_text = f"Error: {self.error}"
            self.changed()
            return
        self.error = None
        self.rows, self.has_more = data["rows"], data["has_more"]
        cov = data["coverage"]["registered_projects"]
        counts = ", ".join(f"{n} {mode}" for mode, n in sorted(cov.items())) or "none"
        stale = " · local index is out of date" if data["status"]["stale"] else ""
        self.status_text = (f"Page {self.page + 1} · registered projects: {counts} · "
                            f"unmanaged folders are not searched{stale}")
        self.changed()

    def set_query(self, text: str, include_archived: bool | None = None) -> None:
        self.query = text
        if include_archived is not None:
            self.include_archived = include_archived
        self.page = 0
        self.refresh()

    @property
    def can_prev(self) -> bool:
        return self.page > 0

    @property
    def can_next(self) -> bool:
        return self.has_more

    def next_page(self) -> None:
        if self.can_next:
            self.page += 1
            self.refresh()

    def prev_page(self) -> None:
        if self.can_prev:
            self.page -= 1
            self.refresh()

    def cell(self, row: int, col: int) -> str:
        value = self.rows[row].get(self.COLUMNS[col].key, "")
        return "" if value is None else str(value)

    def row_id(self, row: int) -> str:
        return self.rows[row]["id"]


# ---------------------------------------------------------------------------- project details
class ProjectDetailsModel(Observable):
    FILE_COLUMNS = (Column("path", "Baseline file", 320, True), Column("category", "Type", 120),
                    Column("size", "Size", 90))

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.project: dict | None = None
        self.fields: list[tuple[str, str]] = []
        self.warnings: list[str] = []
        self.files: list[dict] = []
        self.file_filter = ""

    def load(self, project_id: str | None) -> None:
        self.project = self.ctl.project(project_id) if project_id else None
        p = self.project or {}
        self.fields = [(label, str(p.get(key) or "")) for key, label in (
            ("name", "Name"), ("number", "Number"), ("customer", "Customer"), ("mode", "Mode"),
            ("status", "Status"), ("origin", "Origin"), ("created", "Registered"),
            ("description", "Description"))] if p else []
        self.warnings = []
        if p:
            self.fields.append(("Folder", p.get("folder") or f"unavailable: {p.get('folder_error')}"))
            b = p.get("baseline")
            if b:
                self.fields.append(("Baseline", f"{b['files']} files"
                                    f"{', SHA-256 hashed' if b['hashed'] else ''}"
                                    f"{'' if b['complete'] else ', INCOMPLETE'}"))
                self.warnings = list(b["warnings"])
        self.files = self.ctl.project_files(project_id, self.file_filter) if p else []
        self.changed()

    @property
    def can_open_folder(self) -> bool:
        return bool(self.project and self.project.get("folder"))

    @property
    def archive_label(self) -> str:
        return "Restore" if self.project and self.project.get("status") == "archived" else "Archive"

    def toggle_archive(self) -> None:
        if not self.project:
            return
        new = "active" if self.project["status"] == "archived" else "archived"
        self.ctl.update_project(self.project["id"], self.project.get("_rev"), status=new)
        self.load(self.project["id"])

    def file_cell(self, row: int, col: int) -> str:
        f = self.files[row]
        key = self.FILE_COLUMNS[col].key
        if key == "size":
            return format_bytes(f["size"])
        if key == "category" and f.get("verified_format"):
            return f"{f['category']} (verified)"
        return str(f.get(key, ""))


# ---------------------------------------------------------------------------- new project form
@dataclass
class NewProjectForm:
    roots: list[dict]
    templates: dict[str, dict]
    name: str = ""
    number: str = ""
    customer: str = ""
    description: str = ""
    rel_path: str = ""
    root_id: str | None = None
    template_id: str = "standard"
    errors: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.root_id is None and self.roots:
            self.root_id = self.roots[0]["id"]

    def validate(self) -> bool:
        self.errors = {}
        if not self.name.strip():
            self.errors["name"] = "A project name is required."
        if not self.roots:
            self.errors["root_id"] = "Add an engineering root first (Workspace menu)."
        elif self.root_id not in {r["id"] for r in self.roots}:
            self.errors["root_id"] = "Choose an engineering root."
        if self.template_id not in self.templates:
            self.errors["template_id"] = "Choose a folder template."
        if self.rel_path.strip():
            from ..paths import PathValidationError, normalize_relative
            try:
                normalize_relative(self.rel_path)
            except PathValidationError as exc:
                self.errors["rel_path"] = str(exc)
        return not self.errors

    def to_new_project(self) -> projects.NewProject:
        return projects.NewProject(name=self.name.strip(), number=self.number.strip(),
                                   customer=self.customer.strip(), description=self.description,
                                   rel_path=self.rel_path.strip() or None, template_id=self.template_id,
                                   root_id=self.root_id)

    def submit(self, ctl: Controller) -> dict:
        if not self.validate():
            raise ValueError("; ".join(self.errors.values()))
        return ctl.create_project(self.to_new_project())


# ---------------------------------------------------------------------------- adopt workflow
class AdoptFlow(Observable):
    """State machine: idle -> previewing -> previewed -> adopting -> adopted (or error)."""

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.state = "idle"
        self.folder = ""
        self.name = ""
        self.number = ""
        self.customer = ""
        self.mode = "indexed"
        self.summary: dict | None = None
        self.report_text = "Choose a folder, then Preview. Preview scans only that folder and changes nothing."
        self.progress_text = ""
        self.result: dict | None = None

    def set_folder(self, folder: str) -> None:
        from pathlib import Path
        self.folder = folder
        if not self.name and folder:
            self.name = Path(folder).name
        self.summary = None
        self.state = "idle"
        self.changed()

    @property
    def busy(self) -> bool:
        return self.state in ("previewing", "adopting")

    @property
    def can_preview(self) -> bool:
        return bool(self.folder) and not self.busy

    @property
    def can_adopt(self) -> bool:
        s = self.summary
        return (self.state == "previewed" and s is not None and not s["conflicts"]
                and not s["truncated"] and bool(self.name.strip()))

    def preview(self) -> bool:
        from pathlib import Path
        if not self.can_preview:
            return False
        if not self.ctl.start_preview(Path(self.folder), on_done=self._preview_done):
            self.progress_text = "Another background task is running."
            self.changed()
            return False
        self.state, self.progress_text = "previewing", "Scanning…"
        self.changed()
        return True

    def _preview_done(self, res: JobResult) -> None:
        if res.error:
            self.state, self.summary = "error", None
            self.report_text = describe_error(res.error)
            self.progress_text = "Preview failed."
        else:
            self.state, self.summary = "previewed", res.value
            self.report_text = preview_report(res.value)
            self.progress_text = "Preview complete. Nothing has been changed."
        self.changed()

    def adopt(self) -> bool:
        from pathlib import Path
        if not self.can_adopt:
            return False
        self.ctl.start_adopt(Path(self.folder), self.name, self.mode, self.number, self.customer,
                             on_done=self._adopt_done)
        self.state, self.progress_text = "adopting", "Recording baseline…"
        self.changed()
        return True

    def _adopt_done(self, res: JobResult) -> None:
        if res.error:
            self.state = "error"
            self.progress_text = "Adoption failed; nothing was registered."
            self.report_text = describe_error(res.error)
        else:
            self.state, self.result = "adopted", res.value
            self.progress_text = f"Adopted “{res.value['name']}”."
        self.changed()

    def on_progress(self, ev: Progress) -> None:
        if self.busy:
            self.progress_text = f"{ev.count:,} files… {ev.detail}"
            self.changed()

    def cancel(self) -> None:
        self.ctl.cancel()


def preview_report(s: dict) -> str:
    lines = [f"Folder: {s['scope']}", f"Location in root: {s['rel_path']}",
             f"Files: {s['files']:,}   Folders: {s['directories']:,}   Size: {format_bytes(s['bytes'])}", ""]
    lines += [f"  {cat}: {n:,}" for cat, n in s["by_category"].items()]
    if s["conflicts"]:
        lines += ["", "Overlaps registered project(s): " + ", ".join(c["name"] for c in s["conflicts"])]
    lines += ["", *[f"⚠ {w}" for w in s["warnings"]], "", s["cad_references"], "", "Nothing has been changed."]
    return "\n".join(lines)
