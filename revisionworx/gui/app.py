# SPDX-License-Identifier: GPL-3.0-or-later
"""Tkinter desktop window (Phase 1 skeleton).

A thin view over :class:`Controller`. All behaviour that matters lives in the controller, which is
tested headless; this module only lays out widgets and forwards events.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import __version__, projects
from .controller import Controller, JobResult, Progress, describe_error


def open_in_file_manager(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class NewProjectDialog(tk.Toplevel):
    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app, self.result = app, None
        self.title("New project")
        self.transient(app.root)
        self.roots = app.ctl.roots()
        self.templates = app.ctl.templates()
        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        self.vars = {k: tk.StringVar() for k in ("name", "number", "customer", "description", "path")}
        for row, (key, label) in enumerate([("name", "Project name *"), ("number", "Project number"),
                                            ("customer", "Customer"), ("description", "Description"),
                                            ("path", "Folder inside root (optional)")]):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(frm, textvariable=self.vars[key], width=44).grid(row=row, column=1, sticky="ew", pady=2)
        self.root_var = tk.StringVar(value=self.roots[0]["name"] if self.roots else "")
        ttk.Label(frm, text="Engineering root").grid(row=5, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.root_var, values=[r["name"] for r in self.roots],
                     state="readonly").grid(row=5, column=1, sticky="ew")
        self.tpl_var = tk.StringVar(value="standard")
        ttk.Label(frm, text="Folder template").grid(row=6, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.tpl_var, values=list(self.templates),
                     state="readonly").grid(row=6, column=1, sticky="ew")
        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Create", command=self.ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def ok(self):
        root = next((r for r in self.roots if r["name"] == self.root_var.get()), None)
        np = projects.NewProject(name=self.vars["name"].get(), number=self.vars["number"].get(),
                                 customer=self.vars["customer"].get(),
                                 description=self.vars["description"].get(),
                                 rel_path=self.vars["path"].get().strip() or None,
                                 template_id=self.tpl_var.get(), root_id=root["id"] if root else None)
        try:
            self.result = self.app.ctl.create_project(np)
        except Exception as exc:
            messagebox.showerror("Could not create project", describe_error(exc), parent=self)
            return
        self.destroy()


class AdoptDialog(tk.Toplevel):
    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.preview = None
        self.title("Adopt existing project")
        self.transient(app.root)
        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(5, weight=1)
        self.folder = tk.StringVar()
        ttk.Label(frm, text="Folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.folder, width=60).grid(row=0, column=1, sticky="ew")
        ttk.Button(frm, text="Browse…", command=self.browse).grid(row=0, column=2, padx=4)
        self.name, self.number, self.customer = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for row, (var, label) in enumerate([(self.name, "Project name *"), (self.number, "Project number"),
                                            (self.customer, "Customer")], start=1):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(frm, textvariable=var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        self.mode = tk.StringVar(value="indexed")
        modes = ttk.Frame(frm)
        modes.grid(row=4, column=1, sticky="w")
        ttk.Radiobutton(modes, text="Indexed (search only)", variable=self.mode, value="indexed").pack(side="left")
        ttk.Radiobutton(modes, text="Managed", variable=self.mode, value="managed").pack(side="left", padx=8)
        self.report = tk.Text(frm, height=16, width=90, wrap="word", state="disabled")
        self.report.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=8)
        self.progress = ttk.Label(frm, text="Preview scans only this folder and changes nothing.")
        self.progress.grid(row=6, column=0, columnspan=3, sticky="w")
        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=3, sticky="e")
        ttk.Button(btns, text="Close", command=self.close).pack(side="right")
        self.adopt_btn = ttk.Button(btns, text="Adopt", command=self.adopt, state="disabled")
        self.adopt_btn.pack(side="right", padx=6)
        ttk.Button(btns, text="Cancel scan", command=app.ctl.cancel).pack(side="right")
        ttk.Button(btns, text="Preview", command=self.run_preview).pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def browse(self):
        d = filedialog.askdirectory(parent=self, mustexist=True)
        if d:
            self.folder.set(d)
            if not self.name.get():
                self.name.set(Path(d).name)
            self.adopt_btn.configure(state="disabled")

    def show(self, text: str):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def on_progress(self, ev: Progress):
        self.progress.configure(text=f"{ev.name}: {ev.count} files… {ev.detail}")

    def run_preview(self):
        self.adopt_btn.configure(state="disabled")
        if not self.app.ctl.start_preview(Path(self.folder.get()), on_done=self.preview_done):
            messagebox.showinfo("Busy", "Another background task is running.", parent=self)

    def preview_done(self, res: JobResult):
        if res.error:
            self.progress.configure(text="Preview failed.")
            self.show(describe_error(res.error))
            return
        s = self.preview = res.value
        lines = [f"Folder: {s['scope']}", f"Location in root: {s['rel_path']}",
                 f"Files: {s['files']}   Folders: {s['directories']}   Size: {s['bytes']:,} bytes", ""]
        lines += [f"  {cat}: {n}" for cat, n in s["by_category"].items()]
        lines += ["", *[f"WARNING: {w}" for w in s["warnings"]], "", s["cad_references"],
                  "", "Nothing has been changed."]
        self.show("\n".join(lines))
        self.progress.configure(text="Preview complete.")
        if not s["conflicts"] and not s["truncated"]:
            self.adopt_btn.configure(state="normal")

    def adopt(self):
        if not messagebox.askyesno("Adopt project", "Register this folder? Files are not modified; "
                                   "a baseline inventory with SHA-256 hashes is recorded.", parent=self):
            return
        self.adopt_btn.configure(state="disabled")
        self.app.ctl.start_adopt(Path(self.folder.get()), self.name.get(), self.mode.get(),
                                 self.number.get(), self.customer.get(), on_done=self.adopt_done)

    def adopt_done(self, res: JobResult):
        if res.error:
            messagebox.showerror("Adoption failed", describe_error(res.error), parent=self)
            return
        self.app.refresh()
        self.progress.configure(text=f"Adopted: {res.value['name']}")

    def close(self):
        self.app.ctl.cancel()
        self.destroy()


class App:
    COLUMNS = (("number", "Number", 110), ("name", "Name", 260), ("customer", "Customer", 160),
               ("mode", "Mode", 80), ("rel_path", "Folder", 320))

    def __init__(self, root: tk.Tk, ctl: Controller):
        self.root, self.ctl = root, ctl
        self.page = 0
        self.dialogs: list = []
        root.title(f"RevisionWorx {__version__}")
        root.geometry("1100x680")
        self._menu()
        top = ttk.Frame(root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        self.search = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search, width=40)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.refresh())
        self.show_archived = tk.BooleanVar()
        ttk.Checkbutton(top, text="Include archived", variable=self.show_archived,
                        command=self.refresh).pack(side="left")
        ttk.Button(top, text="New project", command=self.new_project).pack(side="right")
        ttk.Button(top, text="Adopt existing…", command=self.adopt).pack(side="right", padx=6)

        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)
        left = ttk.Frame(panes)
        self.tree = ttk.Treeview(left, columns=[c[0] for c in self.COLUMNS], show="headings",
                                 selectmode="browse")
        for key, label, width in self.COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=key in ("name", "rel_path"))
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_details())
        self.tree.bind("<Double-1>", lambda e: self.open_folder())
        panes.add(left, weight=3)
        right = ttk.Frame(panes, padding=(8, 0))
        self.details = tk.Text(right, width=50, height=14, wrap="word", state="disabled")
        self.details.pack(fill="x")
        rbtns = ttk.Frame(right)
        rbtns.pack(fill="x", pady=4)
        ttk.Button(rbtns, text="Open folder", command=self.open_folder).pack(side="left")
        ttk.Button(rbtns, text="Archive / restore", command=self.toggle_archive).pack(side="left", padx=6)
        self.files = ttk.Treeview(right, columns=("path", "category"), show="headings", height=12)
        self.files.heading("path", text="Baseline file")
        self.files.heading("category", text="Type")
        self.files.column("category", width=110, stretch=False)
        self.files.pack(fill="both", expand=True)
        panes.add(right, weight=2)

        nav = ttk.Frame(root, padding=(8, 4))
        nav.pack(fill="x")
        self.prev_btn = ttk.Button(nav, text="◀ Previous", command=lambda: self.goto(self.page - 1))
        self.prev_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next ▶", command=lambda: self.goto(self.page + 1))
        self.next_btn.pack(side="left", padx=4)
        self.status = ttk.Label(nav, text="No workspace open.")
        self.status.pack(side="left", padx=12)
        root.bind("<Control-f>", lambda e: entry.focus_set())
        root.bind("<Control-n>", lambda e: self.new_project())
        root.bind("<F5>", lambda e: self.refresh())
        root.after(100, self.poll)

    # -------------------------------------------------------------- menu & workspace
    def _menu(self):
        bar = tk.Menu(self.root)
        wsm = tk.Menu(bar, tearoff=False)
        wsm.add_command(label="Open workspace…", command=self.open_ws)
        wsm.add_command(label="Create workspace…", command=self.create_ws)
        self.recent_menu = tk.Menu(wsm, tearoff=False)
        wsm.add_cascade(label="Recent", menu=self.recent_menu)
        wsm.add_separator()
        wsm.add_command(label="Add engineering root…", command=self.add_root)
        wsm.add_command(label="Rebuild local index", command=self.rebuild)
        wsm.add_separator()
        wsm.add_command(label="Quit", command=self.root.destroy)
        bar.add_cascade(label="Workspace", menu=wsm)
        self.root.config(menu=bar)
        self._fill_recent()

    def _fill_recent(self):
        self.recent_menu.delete(0, "end")
        for loc in self.ctl.recent_workspaces():
            self.recent_menu.add_command(label=loc, command=lambda l=loc: self._open(Path(l)))

    def _open(self, location: Path):
        try:
            info = self.ctl.open_workspace(location)
        except Exception as exc:
            messagebox.showerror("Cannot open workspace", describe_error(exc))
            return
        self.root.title(f"RevisionWorx — {info['name']}")
        self._fill_recent()
        self.page = 0
        self.refresh()

    def open_ws(self):
        d = filedialog.askdirectory(title="Folder containing .revisionworx")
        if d:
            self._open(Path(d))

    def create_ws(self):
        d = filedialog.askdirectory(title="Where should the shared .revisionworx folder live?")
        if not d:
            return
        name = simpledialog.askstring("Workspace name", "Name for this workspace:")
        if not name:
            return
        try:
            self.ctl.create_workspace(Path(d), name)
        except Exception as exc:
            messagebox.showerror("Cannot create workspace", describe_error(exc))
            return
        self._open(Path(d))

    def add_root(self):
        d = filedialog.askdirectory(title="Engineering root folder")
        if not d:
            return
        name = simpledialog.askstring("Root name", "Name for this root:", initialvalue=Path(d).name)
        if not name:
            return
        try:
            self.ctl.add_root(name, Path(d))
        except Exception as exc:
            messagebox.showerror("Cannot add root", describe_error(exc))

    def rebuild(self):
        self.ctl.start_rebuild_index(on_done=lambda r: self.refresh() if not r.error else
                                     messagebox.showerror("Rebuild failed", describe_error(r.error)))

    # -------------------------------------------------------------- projects
    def refresh(self):
        if self.ctl.location is None:
            return
        try:
            data = self.ctl.project_page(self.search.get(), self.page, self.show_archived.get())
        except Exception as exc:
            self.status.configure(text=f"Error: {describe_error(exc)}")
            return
        self.tree.delete(*self.tree.get_children())
        for p in data["rows"]:
            self.tree.insert("", "end", iid=p["id"], values=[p.get(c[0], "") for c in self.COLUMNS])
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if data["has_more"] else "disabled")
        cov = data["coverage"]
        stale = " — index out of date" if data["status"]["stale"] else ""
        self.status.configure(text=f"Page {self.page + 1} · registered projects: "
                                   f"{cov['registered_projects']} · unmanaged folders are not searched{stale}")

    def goto(self, page: int):
        self.page = max(0, page)
        self.refresh()

    def selected(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def show_details(self):
        pid = self.selected()
        if not pid:
            return
        rec = self.ctl.project(pid) or {}
        lines = [f"{k}: {rec.get(k, '')}" for k in ("name", "number", "customer", "mode", "status", "origin",
                                                   "created", "description")]
        lines.append(f"folder: {rec.get('folder') or rec.get('folder_error')}")
        if rec.get("baseline"):
            b = rec["baseline"]
            lines.append(f"baseline: {b['files']} files, hashed={b['hashed']}, complete={b['complete']}")
            lines += [f"  ! {w}" for w in b["warnings"]]
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(lines))
        self.details.configure(state="disabled")
        self.files.delete(*self.files.get_children())
        for f in self.ctl.project_files(pid):
            tag = " ✓" if f.get("verified_format") else ""
            self.files.insert("", "end", values=(f["path"], f["category"] + tag))

    def open_folder(self):
        pid = self.selected()
        rec = self.ctl.project(pid) if pid else None
        if rec and rec.get("folder"):
            open_in_file_manager(rec["folder"])
        elif rec:
            messagebox.showwarning("Folder unavailable", rec.get("folder_error", ""))

    def toggle_archive(self):
        pid = self.selected()
        rec = self.ctl.project(pid) if pid else None
        if not rec:
            return
        new = "active" if rec["status"] == "archived" else "archived"
        try:
            self.ctl.update_project(pid, rec.get("_rev"), status=new)
        except Exception as exc:
            messagebox.showerror("Cannot change status", describe_error(exc))
        self.refresh()

    def new_project(self):
        if self.ctl.location is None:
            messagebox.showinfo("No workspace", "Open or create a workspace first.")
            return
        if not self.ctl.roots():
            messagebox.showinfo("No roots", "Add an engineering root first (Workspace menu).")
            return
        dlg = NewProjectDialog(self)
        self.root.wait_window(dlg)
        if dlg.result:
            self.refresh()

    def adopt(self):
        if self.ctl.location is None:
            messagebox.showinfo("No workspace", "Open or create a workspace first.")
            return
        dlg = AdoptDialog(self)
        self.dialogs.append(dlg)

    def poll(self):
        for ev in self.ctl.drain():
            if isinstance(ev, Progress):
                for d in list(self.dialogs):
                    if d.winfo_exists():
                        d.on_progress(ev)
                    else:
                        self.dialogs.remove(d)
        self.root.after(100, self.poll)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("clam" if os.name != "nt" else "vista")
    except tk.TclError:
        pass
    app = App(root, Controller())
    if argv:
        app._open(Path(argv[0]))
    elif app.ctl.recent_workspaces():
        app._open(Path(app.ctl.recent_workspaces()[0]))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
