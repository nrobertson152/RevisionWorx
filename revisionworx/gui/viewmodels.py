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
from .controller import (ALPHA_NOTICE, Controller, JobResult, Progress, SavedWithWarning,
                         describe_error, project_folder_name)


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

    @property
    def notice(self) -> str:
        """Empty / error state shown above the project table (never blank when nothing is listed)."""
        if self.error:
            return f"⚠ {self.error}"
        if self.ctl.location is None or self.rows:
            return ""
        if self.query.strip():
            return f"No registered project matches “{self.query.strip()}”."
        return "No projects yet. Create one with Projects › New project (Ctrl+N)."

    def index_of(self, project_id: str | None) -> int | None:
        for i, r in enumerate(self.rows):
            if r["id"] == project_id:
                return i
        return None

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


WORKSPACE_REPLACED = ("This window belongs to a workspace that is no longer open, and another workspace "
                      "may be open now. Nothing was changed. Close this window and start again.")


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
        self.workspace_id = ctl.workspace_id  # this window belongs to one workspace

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
        if not self.can_preview or self._workspace_replaced():
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

    def _workspace_replaced(self) -> bool:
        if self.ctl.workspace_id == self.workspace_id:
            return False
        self.progress_text = WORKSPACE_REPLACED
        self.changed()
        return True

    def adopt(self) -> bool:
        from pathlib import Path
        if not self.can_adopt or self._workspace_replaced():
            return False
        if not self.ctl.start_adopt(Path(self.folder), self.name, self.mode, self.number, self.customer,
                                    on_done=self._adopt_done):
            self.progress_text = "Another background task is running; try again in a moment."
            self.changed()
            return False
        self.state, self.progress_text = "adopting", "Recording baseline…"
        self.changed()
        return True

    def _adopt_done(self, res: JobResult) -> None:
        if res.error:
            self.state = "error"
            self.progress_text = "Adoption failed; nothing was registered."
            self.report_text = describe_error(res.error)
        elif res.warning:  # saved (CommittedNotApplied): success with a caveat, never "failed"
            self.state, self.result = "adopted", None
            self.progress_text = "Adopted (saved) with a warning."
            self.report_text = f"Saved. {res.warning}"
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


# ---------------------------------------------------------------------------- Phase 2: documents
class DocumentListModel(Observable):
    COLUMNS = (Column("number", "Number", 100), Column("title", "Title", 200, True),
               Column("latest_label", "Rev", 50), Column("latest_state", "State", 100),
               Column("released_label", "Released", 70), Column("checked_out_by", "Checked out by", 120),
               Column("file_state", "Working file", 130), Column("rel_path", "File", 260, True))
    FILE_STATE_TEXT = {"unchanged": "OK", "modified": "Edited", "modified-externally": "⚠ Changed, not checked out",
                       "missing": "⚠ Missing", "unrevisioned": "No revision", "unavailable": "⚠ Unavailable"}

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.project_id: str | None = None
        self.project: dict | None = None
        self.text = ""
        self.rows: list[dict] = []
        self.error: str | None = None

    def load(self, project_id: str | None) -> None:
        self.project_id = project_id
        self.project = None
        if project_id:
            try:
                self.project = self.ctl.project(project_id)
            except Exception as exc:
                self.error = describe_error(exc)
        self.refresh()

    def refresh(self) -> None:
        self.error = None
        try:
            rows = self.ctl.documents(self.project_id, self.text) if self.project_id else []
            states = self.ctl.file_states([r["id"] for r in rows]) if rows else {}
            self.rows = [{**r, "file_state": states.get(r["id"], "unavailable")} for r in rows]
        except Exception as exc:
            self.rows, self.error = [], describe_error(exc)
        self.changed()

    def cell(self, row: int, col: int) -> str:
        key = self.COLUMNS[col].key
        v = self.rows[row].get(key)
        if key == "file_state":
            return self.FILE_STATE_TEXT.get(v, v or "")
        return "" if v is None else str(v)

    def row_severity(self, row: int) -> str:
        state = self.rows[row].get("file_state")
        return {"missing": "error", "unavailable": "error", "modified-externally": "warning"}.get(state, "ok")

    @property
    def problem_count(self) -> int:
        return sum(1 for i in range(len(self.rows)) if self.row_severity(i) != "ok")

    @property
    def can_add(self) -> bool:
        p = self.project
        return bool(p and p.get("mode") == "managed" and p.get("status") == "active" and p.get("folder")
                    and not self.ctl.busy and not self.ctl.read_only)

    @property
    def notice(self) -> str:
        """Why documents cannot be added, or how to start, shown above the list."""
        p = self.project
        if self.error:
            return self.error
        if not p:
            return "Select a project to see its documents."
        if p.get("folder_error"):
            return f"⚠ Project folder unavailable: {p['folder_error']}"
        if p.get("mode") != "managed":
            return ("This project is indexed (search only). Documents and revisions need a managed "
                    "project.")
        if p.get("status") != "active":
            return "This project is archived. Restore it to add or change documents."
        if not self.rows and not self.text:
            return "No documents yet. Use “Import file…” to copy a file into this project."
        if self.problem_count:
            return (f"⚠ {self.problem_count} document(s) need attention: working file missing or changed "
                    "without a checkout. Select one for details.")
        return ""

    def row_id(self, row: int) -> str:
        return self.rows[row]["id"]


class DocumentPanel(Observable):
    """Selected document: details, history and which actions are currently possible."""
    HISTORY_COLUMNS = (Column("label", "Rev", 50), Column("state_name", "State", 110),
                       Column("created", "Date", 160), Column("author", "Author", 100),
                       Column("description", "Change", 260, True))

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.detail: dict | None = None
        self.message = ""
        self.message_level = "ok"  # ok | warning | error: colours the message, never implies success
        self.failed_id: str | None = None  # selected, but its details could not be read
        self._outcome: tuple[str | None, str, str] | None = None  # (doc, text, level) of the last write that happened

    def set_message(self, text: str, level: str = "ok") -> None:
        self.message, self.message_level = text, level
        self.changed()

    def _kept_outcome(self, doc_id: str | None) -> tuple[str | None, str, str] | None:
        """The last write's outcome, if it is for this document and nothing has replaced it since."""
        o = self._outcome
        return o if o and o[0] == doc_id and self.message.startswith(o[1]) else None

    def saved(self, text: str, level: str = "ok") -> None:
        """Report a write that HAS happened, then re-read the document. The re-read can fail (on Windows
        a file the core just replaced can be refused for a moment), and that must never read as the
        write having failed: it invites repeating a change that is already saved."""
        doc_id = self.doc_id or self.failed_id
        self._outcome = (doc_id, text, level)
        self.message, self.message_level = text, level
        self.load_or_report(doc_id)

    def load(self, doc_id: str | None, verify_hash: bool = False) -> None:
        kept = self._kept_outcome(doc_id)
        detail = self.ctl.document_detail(doc_id, verify_hash) if doc_id else None
        if kept:
            self.message, self.message_level = kept[1], kept[2]
        elif not self.detail or doc_id != self.doc_id:
            self.message, self.message_level = "", "ok"
        self.detail = detail
        self.failed_id = None
        self.changed()

    def load_or_report(self, doc_id: str | None) -> bool:
        """Load the selected document. On failure show the reason here and clear the panel, so the
        previous document's actions can never run while another row is selected."""
        try:
            self.load(doc_id)
            return True
        except Exception as exc:
            self.report_unreadable(doc_id, describe_error(exc))
            return False

    def report_unreadable(self, doc_id: str | None, reason: str) -> None:
        """Nothing shown may be acted on any more. If a write just succeeded, say that first."""
        kept = self._kept_outcome(doc_id)
        self.detail, self.failed_id = None, doc_id
        if kept:
            self.message = f"{kept[1]}\n⚠ This document could not be re-read: {reason} Press F5 to try again."
            self.message_level = "warning"
        else:
            self.message, self.message_level = f"⚠ Cannot read this document: {reason}", "error"
        self.changed()

    @property
    def working_path(self) -> str:
        return self.detail["status"]["path"] if self.detail else ""

    @property
    def file_missing(self) -> bool:
        return bool(self.detail) and self.detail["status"]["state"] == "missing"

    @property
    def can_open_file(self) -> bool:
        return bool(self.detail) and not self.file_missing

    @property
    def severity(self) -> str:
        """``ok`` / ``warning`` / ``error`` for colouring the status line."""
        if self.message and self.message_level != "ok":
            return self.message_level
        if not self.detail:
            return "ok"
        state = self.detail["status"]["state"]
        if state == "missing":
            return "error"
        if state == "modified-externally" or (self.detail["checkout"] and not self.detail["mine"]):
            return "warning"
        return "ok"

    @property
    def summary_fields(self) -> list[tuple[str, str]]:
        if not self.detail:
            return []
        d, latest = self.detail["document"], self.detail["latest"]
        from ..lifecycle import state_name
        return [("Title", d.get("title", "")), ("Number", d.get("number") or "—"),
                ("File", d["rel_path"]), ("Type", d.get("type_id", "")),
                ("Latest", f"{latest['label']} · {state_name(self.detail['lifecycle'], latest['state'])}"
                 if latest else "none"),
                ("Revisions", str(d.get("rev_count", 0)))]

    def revision_text(self, row: int) -> str:
        """Full, read-only description of one revision (shown when it is selected)."""
        r = self.history[row]
        from ..lifecycle import state_name
        lc = self.detail["lifecycle"]
        lines = [f"Revision {r['label']} (#{r['number']}) — {state_name(lc, r['state'])}",
                 f"Checked in {r['created']} by {r['author'].get('user', '?')}@{r['author'].get('host', '?')}",
                 f"Size {format_bytes(r['size'])} · SHA-256 {r['sha256']}",
                 "", r.get("description", "")]
        if r.get("restored_from"):
            lines.insert(2, "Restored from an earlier revision.")
        if r.get("transitions"):
            lines += ["", "State changes:"]
            lines += [f"  {t['time']}  {state_name(lc, t['from'])} → {state_name(lc, t['to'])}  "
                      f"by {t['by'].get('user', '?')}" + (f" — {t['comment']}" if t.get("comment") else "")
                      for t in r["transitions"]]
        return "\n".join(lines)

    def reload(self) -> None:
        if self.detail:
            self.load(self.detail["document"]["id"])

    @property
    def doc_id(self) -> str | None:
        return self.detail["document"]["id"] if self.detail else None

    @property
    def history(self) -> list[dict]:
        return list(reversed(self.detail["history"])) if self.detail else []

    def history_cell(self, row: int, col: int) -> str:
        r = self.history[row]
        key = self.HISTORY_COLUMNS[col].key
        if key == "state_name":
            from ..lifecycle import state_name
            return state_name(self.detail["lifecycle"], r["state"])
        if key == "author":
            return r["author"].get("user", "")
        return str(r.get(key, ""))

    @property
    def status_text(self) -> str:
        if not self.detail:
            return ""
        st = self.detail["status"]
        co = self.detail["checkout"]
        texts = {"unchanged": "Working file matches the latest revision.",
                 "modified": "Working file has been edited (checked out).",
                 "modified-externally": "⚠ Working file was changed WITHOUT a checkout.",
                 "missing": "⚠ Working file is missing.",
                 "unrevisioned": "No revision has been captured yet."}
        text = texts.get(st["state"], st["state"])
        if co:
            who = "you" if self.detail["mine"] else f"{co['owner']['user']}@{co['owner']['host']}"
            text += f"  Checked out by {who} since {co['acquired']}."
        return text

    @property
    def can_checkout(self) -> bool:
        return bool(self.detail) and self.detail["checkout"] is None and \
            self.detail["document"]["status"] == "active" and not self.ctl.busy and not self.ctl.read_only

    @property
    def can_checkin(self) -> bool:
        return bool(self.detail) and self.detail["mine"] and not self.ctl.busy and not self.ctl.read_only

    @property
    def ownership_text(self) -> str:
        """Who holds the checkout, stated plainly (shown next to the actions)."""
        if not self.detail:
            return ""
        co = self.detail["checkout"]
        if not co:
            return "Not checked out — available."
        if self.detail["mine"]:
            return "Checked out by you (this session can check in)."
        return (f"Checked out by {co['owner'].get('user')}@{co['owner'].get('host')} since {co['acquired']}. "
                "Read-only for you.")

    can_cancel = can_checkin

    @property
    def can_transition(self) -> bool:
        return bool(self.transition_choices) and not self.ctl.busy and not self.ctl.read_only

    @property
    def transition_choices(self) -> list[tuple[str, str]]:
        if not self.detail or not self.detail["latest"]:
            return []
        from ..lifecycle import state_name
        return [(s, state_name(self.detail["lifecycle"], s)) for s in self.detail["transitions"]]

    def checkout(self) -> None:
        try:
            self.ctl.checkout(self.doc_id)
        except SavedWithWarning as w:
            self.saved(w.warning, "warning")
            return
        self.saved("Checked out to you. Edit the working file, save it, then Check in.")

    def can_restore(self, row: int | None) -> bool:
        """Restoring needs this session's checkout and an OLDER revision (row 0 is the latest)."""
        return self.can_checkin and row is not None and 0 < row < len(self.history)

    def restore(self, row: int) -> None:
        if not self.can_restore(row):
            raise ValueError("check the document out first, then choose an earlier revision")
        rev = self.history[row]
        self.ctl.restore_revision(self.doc_id, rev["id"])
        self.saved(f"Working file replaced with revision {rev['label']}. Check in to record it as a "
                   "new revision (history is not rewritten), or cancel and discard to undo.")

    def can_compare(self, row: int | None) -> bool:
        return row is not None and 0 <= row < len(self.history) - 1

    def compare_text(self, row: int) -> str:
        """Selected revision against the one before it (metadata only; CAD content is not diffed)."""
        newer, older = self.history[row], self.history[row + 1]
        c = self.ctl.compare(older["id"], newer["id"])
        from ..lifecycle import state_name
        lc = self.detail["lifecycle"]
        lines = [f"Revision {c['from']} → {c['to']}",
                 "Content: " + ("identical" if c["same_content"] else
                                f"different ({'+' if c['size_delta'] >= 0 else '−'}"
                                f"{format_bytes(abs(c['size_delta']))})"),
                 f"State: {state_name(lc, c['states'][0])} → {state_name(lc, c['states'][1])}",
                 f"Author: {c['authors'][0]} → {c['authors'][1]}",
                 f"Date: {c['created'][0]} → {c['created'][1]}"]
        if c["path_changed"]:
            lines.append("⚠ The file path differs between these revisions.")
        if c.get("restored_from"):
            lines.append("The newer revision was restored from an earlier one.")
        lines += ["", f"{c['from']}: {c['descriptions'][0]}", f"{c['to']}: {c['descriptions'][1]}", "", c["note"]]
        return "\n".join(lines)

    def cancel(self, revert: bool) -> None:
        try:
            self.ctl.cancel_checkout(self.doc_id, revert)
        except SavedWithWarning as w:
            self.saved(w.warning, "warning")
            return
        self.saved("Checkout released" + ("; working file restored to the latest revision." if revert
                                          else "; your working file was left as it is."))

    def transition_latest(self, state: str, comment: str = "") -> dict:
        from ..lifecycle import state_name
        latest, lifecycle = self.detail["latest"], self.detail["lifecycle"]
        rev = self.ctl.transition(latest["id"], state, comment, latest["_rev"])
        self.saved(f"State changed to {state_name(lifecycle, state)}.")
        return rev

    def start_export(self, row: int, dest: str, on_done=None) -> bool:
        """Copy one revision out, in the background (it can be gigabytes), and say where it went."""
        from pathlib import Path
        rev = self.history[row]

        def done(res: JobResult) -> None:
            exc = res.error
            if isinstance(exc, OSError) and exc.strerror:  # about the destination, not the document
                self.set_message(f"Export failed: {exc.strerror}. Nothing was exported to {dest}.", "error")
            elif exc:
                self.set_message(f"Export failed. {describe_error(exc)}", "error")
            else:
                self.set_message(f"Exported revision {rev['label']} to {dest} (SHA-256 verified).")
            if on_done:
                on_done(res)
        started = self.ctl.start_export(rev["id"], Path(dest), on_done=done)
        if started:
            self.set_message(f"Exporting revision {rev['label']}…")
        else:
            self.set_message("Another task is still running; try again in a moment.", "warning")
        return started

    def start_checkin(self, message: str, on_done=None, allow_unchanged: bool = False,
                      label: str | None = None) -> bool:
        if not message.strip():
            self.message = "Describe the change before checking in."
            self.changed()
            return False

        def done(res: JobResult):
            if res.error:
                self.message, self.message_level = describe_error(res.error), "error"
                try:  # the view must still reflect reality (e.g. file now missing)
                    self.reload()
                except Exception:
                    self.changed()
            elif res.warning:
                self.saved(res.warning, "warning")
            else:
                self.saved(f"Checked in revision {res.value['label']}.")
            if on_done:
                on_done(res)
        started = self.ctl.start_checkin(self.doc_id, message, label=label or None,
                                         allow_unchanged=allow_unchanged, on_done=done)
        if started:
            self.message, self.message_level = "Checking in…", "ok"
        else:
            self.message, self.message_level = "Another task is still running; try again in a moment.", "warning"
        self.changed()
        return started


# ---------------------------------------------------------------------------- alpha workflows
@dataclass
class CheckinForm:
    """Check-in dialog state. A meaningful description is mandatory."""
    description: str = ""
    label: str = ""
    allow_unchanged: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    MIN_DESCRIPTION = 3

    def validate(self) -> bool:
        self.errors = {}
        text = self.description.strip()
        if not text:
            self.errors["description"] = "Describe what changed in this revision."
        elif len(text) < self.MIN_DESCRIPTION:
            self.errors["description"] = "The description is too short to be meaningful."
        if self.label.strip():
            from ..revlabels import validate_label
            try:
                validate_label(self.label.strip())
            except Exception as exc:
                self.errors["label"] = f"Invalid revision label: {exc}"
        return not self.errors


@dataclass
class QuickStartForm:
    """Start page: create a local workspace + engineering root + first managed project."""
    folder: str = ""
    name: str = ""
    number: str = ""
    customer: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def project_path_preview(self) -> str:
        if not self.folder or not self.name.strip():
            return ""
        from pathlib import Path
        try:
            return str(Path(self.folder) / "Projects" / project_folder_name(self.name, self.number))
        except ValueError:
            return ""

    def validate(self) -> bool:
        self.errors = {}
        if not self.folder.strip():
            self.errors["folder"] = "Choose a folder on this computer for your projects."
        if not self.name.strip():
            self.errors["name"] = "A project name is required."
        else:
            from ..paths import PathValidationError
            try:
                project_folder_name(self.name, self.number)
            except PathValidationError as exc:
                self.errors["name"] = f"Not usable as a folder name: {exc}"
        return not self.errors

    def submit(self, ctl: Controller) -> dict:
        from pathlib import Path
        if not self.validate():
            raise ValueError("; ".join(self.errors.values()))
        return ctl.quick_start(Path(self.folder), self.name, self.number, self.customer)


class ImportFlow(Observable):
    """Import an outside file into the selected managed project: idle -> importing -> done/error."""

    def __init__(self, ctl: Controller, project_id: str):
        super().__init__()
        self.ctl = ctl
        self.project_id = project_id
        self.source = ""
        self.subfolder = ""
        self.number = ""
        self.title = ""
        self._auto_title = ""
        self.description = "Initial revision"
        self.subfolders: list[str] = []
        self.state = "idle"
        self.message = ""
        self.result: tuple | None = None
        self.errors: dict[str, str] = {}
        self.workspace_id = ctl.workspace_id  # this window belongs to one workspace
        try:
            self.subfolders = ctl.project_subfolders(project_id)
        except Exception as exc:
            self.state, self.message = "error", describe_error(exc)

    def set_source(self, path: str) -> None:
        from pathlib import Path
        self.source = path
        if path and (not self.title or self.title == self._auto_title):  # never replaces a typed title
            self.title = self._auto_title = Path(path).stem
        self.changed()

    def validate(self) -> bool:
        from pathlib import Path
        self.errors = {}
        if not self.source:
            self.errors["source"] = "Choose a file to import."
        elif not Path(self.source).is_file():
            self.errors["source"] = f"{self.source} does not exist or is not a file."
        if self.subfolder not in self.subfolders:
            self.errors["subfolder"] = "Choose a destination folder inside the project."
        if not self.description.strip():
            self.errors["description"] = "Describe the initial revision."
        return not self.errors

    @property
    def busy(self) -> bool:
        return self.state == "importing"

    @property
    def can_import(self) -> bool:
        return not self.busy and self.state != "done" and bool(self.source)

    @property
    def destination_preview(self) -> str:
        from pathlib import Path
        if not self.source:
            return ""
        return "/".join(p for p in (self.subfolder, Path(self.source).name) if p)

    def start(self, on_done=None) -> bool:
        from pathlib import Path
        if self.ctl.workspace_id != self.workspace_id:
            self.state, self.message = "error", WORKSPACE_REPLACED
            self.changed()
            return False
        if not self.validate():
            self.message = "\n".join(self.errors.values())
            self.changed()
            return False

        def done(res: JobResult) -> None:
            if res.error:
                self.state, self.message = "error", describe_error(res.error)
            elif res.warning:  # saved (CommittedNotApplied): success with a caveat, never "failed"
                self.state, self.result = "done", None
                self.message = f"Imported (saved) with a warning:\n{res.warning}"
            else:
                doc, rev = res.value
                self.state, self.result = "done", res.value
                self.message = (f"Imported {doc['rel_path']} as revision {rev['label']}." if rev
                                else f"Registered {doc['rel_path']}.")
            self.changed()
            if on_done:
                on_done(res)

        if not self.ctl.start_import(self.project_id, Path(self.source), self.subfolder, on_done=done,
                                     number=self.number, title=self.title, description=self.description):
            self.message = "Another task is still running; try again in a moment."
            self.changed()
            return False
        self.state, self.message = "importing", "Copying and recording revision 1…"
        self.changed()
        return True


class AppStatus(Observable):
    """Banner content: the permanent alpha notice plus workspace health."""

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.alpha_notice = ALPHA_NOTICE
        self.health: dict = {"ok": True, "blocked": False, "problems": [], "message": ""}

    def check(self) -> None:
        try:
            self.health = self.ctl.health()
        except Exception as exc:
            self.health = {"ok": False, "blocked": False, "problems": [], "message": describe_error(exc)}
        self.changed()

    def note_write_refused(self) -> None:
        """A write found what the read-only check could not: show the same read-only state."""
        from ..workspace import WorkspaceCorruptError
        self.health = {"ok": False, "blocked": True, "problems": [],
                       "message": describe_error(WorkspaceCorruptError(self.ctl.blocked_reason))}
        self.changed()

    @property
    def blocked(self) -> bool:
        return bool(self.health.get("blocked"))

    @property
    def health_text(self) -> str:
        text = self.health.get("message", "")
        if self.health.get("unavailable"):
            text += ("\n\nAll changes are disabled until it can be read. Put it back or reconnect the drive, "
                     "then press F5.")
        elif self.blocked:
            text += "\n\nRevisionWorx is READ-ONLY for this workspace: browsing and exporting work; all changes are disabled."
        return text
