# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line interface. Every GUI action has a scriptable equivalent here."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, atomicio, projects
from .index import open_index
from .workspace import Workspace, WorkspaceError


def _emit(args, obj, text: str | None = None) -> None:
    if args.json or text is None:
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    else:
        print(text)


def _ws_location(args) -> Path:
    loc = args.workspace or os.environ.get("REVISIONWORX_WORKSPACE")
    if not loc:
        raise WorkspaceError("no workspace given: use --workspace PATH or set REVISIONWORX_WORKSPACE")
    return Path(loc)


def _open(args, writable: bool) -> Workspace:
    return Workspace.open(_ws_location(args), writable=writable)


def _project_line(p: dict) -> str:
    num = f"[{p['number']}] " if p.get("number") else ""
    cust = f" ({p['customer']})" if p.get("customer") else ""
    arch = " ARCHIVED" if p.get("status") == "archived" else ""
    return f"{p['id']}  {num}{p['name']}{cust}  {p['mode']}{arch}  {p['rel_path']}"


def cmd_init(args):
    ws = Workspace.create(Path(args.location), args.name)
    try:
        _emit(args, {"workspace_id": ws.id, "path": str(ws.meta_dir)},
              f"Created workspace {ws.name!r} at {ws.meta_dir}")
    finally:
        ws.close()


def cmd_status(args):
    with _open(args, False) as ws:
        with open_index(ws, args.cache_dir) as idx:
            info = {"workspace_id": ws.id, "name": ws.name, "path": str(ws.meta_dir),
                    "journal_seq": ws.head_seq(), "applied_seq": ws.applied_seq(),
                    "writer_lock": Workspace.read_writer_lock(ws.meta_dir),
                    "roots": list(ws.list("root").values()), "index": idx.status(),
                    "coverage": idx.coverage(), "settings": ws.settings()}
            lock = info["writer_lock"]
            text = "\n".join([
                f"Workspace {ws.name!r} ({ws.id})", f"  location: {ws.meta_dir}",
                f"  journal: {info['journal_seq']} entries (applied {info['applied_seq']})",
                f"  writer: {'%s@%s since %s' % (lock['user'], lock['host'], lock['acquired']) if lock else 'none'}",
                f"  roots: {', '.join(r['name'] for r in info['roots']) or 'none'}",
                f"  projects: {info['coverage']['registered_projects']}",
                f"  index: {info['index']['cache_path']} (seq {info['index']['cache_seq']})",
                f"  {info['coverage']['note']}"])
            _emit(args, info, text)


def cmd_root_add(args):
    with _open(args, True) as ws:
        rid = projects.add_root(ws, args.name, Path(args.path), args.other_platform_path)
        _emit(args, {"root_id": rid}, f"Added root {args.name!r} ({rid})")


def cmd_root_list(args):
    with _open(args, False) as ws:
        roots = list(ws.list("root").values())
        _emit(args, roots, "\n".join(f"{r['id']}  {r['name']}  {r['paths']}" for r in roots) or "No roots.")


def cmd_template_list(args):
    with _open(args, False) as ws:
        t = projects.list_templates(ws)
        _emit(args, t, "\n".join(f"{k}  {v['name']}  ({len(v['folders'])} folders)" for k, v in t.items()))


def cmd_template_import(args):
    data = atomicio.read_json(Path(args.file))
    with _open(args, True) as ws:
        tid = projects.save_template(ws, data)
        _emit(args, {"template_id": tid}, f"Saved template {tid!r}")


def cmd_template_export(args):
    with _open(args, False) as ws:
        t = projects.list_templates(ws).get(args.id)
        if t is None:
            raise WorkspaceError(f"unknown template {args.id!r}")
        out = Path(args.file)
        if out.exists() and not args.force:
            raise WorkspaceError(f"{out} exists; use --force to overwrite")
        atomicio.atomic_write_json(out, t)
        _emit(args, {"file": str(out)}, f"Exported template {args.id!r} to {out}")


def cmd_project_new(args):
    with _open(args, True) as ws:
        np = projects.NewProject(name=args.name, number=args.number, customer=args.customer,
                                 template_id=args.template, root_id=args.root, rel_path=args.path,
                                 mode=args.mode, description=args.description)
        rec = projects.create_project(ws, np, allow_duplicate_number=args.allow_duplicate_number)
        _emit(args, rec, f"Created {_project_line(rec)}\n  folder: {projects.project_folder(ws, rec)}")


def _print_preview(summary: dict) -> str:
    lines = [f"Adoption preview of {summary['scope']} (nothing has been changed)",
             f"  location in root: {summary['rel_path']}",
             f"  files: {summary['files']}  folders: {summary['directories']}  bytes: {summary['bytes']}"]
    for cat, n in summary["by_category"].items():
        lines.append(f"    {cat}: {n}")
    for w in summary["warnings"]:
        lines.append(f"  WARNING: {w}")
    lines.append(f"  NOTE: {summary['cad_references']}")
    return "\n".join(lines)


def cmd_project_preview(args):
    with _open(args, False) as ws:
        summary = projects.preview_adoption(ws, Path(args.folder))
        _emit(args, summary, _print_preview(summary))


def cmd_project_adopt(args):
    with _open(args, True) as ws:
        rec = projects.adopt_project(ws, Path(args.folder), args.name, mode=args.mode,
                                     number=args.number, customer=args.customer,
                                     description=args.description,
                                     allow_duplicate_number=args.allow_duplicate_number)
        warn = "".join(f"\n  WARNING: {w}" for w in rec["baseline"]["warnings"])
        _emit(args, rec, f"Adopted {_project_line(rec)}\n  baseline: {rec['baseline']['files']} files"
                         f"{' (SHA-256 hashed)' if rec['baseline']['hashed'] else ''}{warn}")


def cmd_project_list(args):
    with _open(args, False) as ws, open_index(ws, args.cache_dir) as idx:
        rows = idx.search_projects(args.text or "", include_archived=args.archived,
                                   limit=args.limit, offset=args.offset)
        _emit(args, rows, "\n".join(_project_line(p) for p in rows) or "No matching projects.")


def cmd_project_set(args):
    changes = {k: v for k, v in (("name", args.name), ("number", args.number), ("customer", args.customer),
                                 ("description", args.description), ("status", args.status),
                                 ("mode", args.mode)) if v is not None}
    with _open(args, True) as ws:
        rec = projects.update_project(ws, args.id, expected_rev=args.expect_rev, **changes)
        _emit(args, rec, f"Updated {_project_line(rec)}")


def cmd_files_search(args):
    with _open(args, False) as ws, open_index(ws, args.cache_dir) as idx:
        rows = idx.search_files(args.text, project_id=args.project, limit=args.limit)
        _emit(args, rows, "\n".join(f"{r['project_id']}  {r['path']}  [{r['category']}]" for r in rows)
              or "No matching files in registered projects.")


def cmd_index_rebuild(args):
    with _open(args, False) as ws, open_index(ws, args.cache_dir) as idx:
        seq = idx.rebuild()
        _emit(args, {"journal_seq": seq, **idx.coverage()}, f"Rebuilt local index at journal entry {seq}")


def cmd_verify(args):
    with _open(args, args.repair) as ws:
        report = ws.verify(repair=args.repair)
    lines = [f"Verified journal up to entry {report['head_seq']}: {len(report['problems'])} problem(s)"]
    lines += [f"  {p}" for p in report["problems"][:200]]
    if report["repaired"]:
        lines.append("Records were rebuilt from the journal; displaced files are in .revisionworx/quarantine/.")
    _emit(args, report, "\n".join(lines))
    if report["problems"] and not report["repaired"]:
        raise SystemExit(1)


def cmd_lock_show(args):
    holder = Workspace.read_writer_lock(_ws_location(args))
    _emit(args, holder, json.dumps(holder, indent=2) if holder else "No writer lock is held.")


def cmd_lock_break(args):
    holder = Workspace.break_writer_lock(_ws_location(args), args.token, args.reason)
    _emit(args, holder, f"Broke writer lock held by {holder.get('user')}@{holder.get('host')}; recorded in journal.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="revisionworx", description="RevisionWorx engineering PDM")
    p.add_argument("--version", action="version", version=f"RevisionWorx {__version__}")
    p.add_argument("-w", "--workspace", help="workspace folder (or REVISIONWORX_WORKSPACE)")
    p.add_argument("--cache-dir", type=Path, default=None, help="local index cache folder")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new workspace")
    s.add_argument("location"); s.add_argument("--name", required=True); s.set_defaults(fn=cmd_init)
    sub.add_parser("status", help="workspace summary").set_defaults(fn=cmd_status)

    root = sub.add_parser("root").add_subparsers(dest="sub", required=True)
    s = root.add_parser("add"); s.add_argument("name"); s.add_argument("path")
    s.add_argument("--other-platform-path"); s.set_defaults(fn=cmd_root_add)
    root.add_parser("list").set_defaults(fn=cmd_root_list)

    tpl = sub.add_parser("template").add_subparsers(dest="sub", required=True)
    tpl.add_parser("list").set_defaults(fn=cmd_template_list)
    s = tpl.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_template_import)
    s = tpl.add_parser("export"); s.add_argument("id"); s.add_argument("file")
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_template_export)

    prj = sub.add_parser("project").add_subparsers(dest="sub", required=True)
    s = prj.add_parser("new", help="create a project folder from a template")
    s.add_argument("--name", required=True); s.add_argument("--number", default="")
    s.add_argument("--customer", default=""); s.add_argument("--template", default="standard")
    s.add_argument("--root"); s.add_argument("--path", help="relative path inside the root")
    s.add_argument("--mode", choices=projects.MODES); s.add_argument("--description", default="")
    s.add_argument("--allow-duplicate-number", action="store_true"); s.set_defaults(fn=cmd_project_new)
    s = prj.add_parser("preview", help="read-only adoption preview of an existing folder")
    s.add_argument("folder"); s.set_defaults(fn=cmd_project_preview)
    s = prj.add_parser("adopt", help="register an existing folder (never modifies it)")
    s.add_argument("folder"); s.add_argument("--name", required=True)
    s.add_argument("--mode", choices=projects.MODES, default="indexed")
    s.add_argument("--number", default=""); s.add_argument("--customer", default="")
    s.add_argument("--description", default=""); s.add_argument("--allow-duplicate-number", action="store_true")
    s.set_defaults(fn=cmd_project_adopt)
    s = prj.add_parser("list", help="list or search registered projects")
    s.add_argument("text", nargs="?"); s.add_argument("--archived", action="store_true")
    s.add_argument("--limit", type=int, default=100); s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn=cmd_project_list)
    s = prj.add_parser("set", help="change project metadata, archive or restore")
    s.add_argument("id")
    for opt in ("name", "number", "customer", "description"):
        s.add_argument(f"--{opt}")
    s.add_argument("--status", choices=("active", "archived")); s.add_argument("--mode", choices=projects.MODES)
    s.add_argument("--expect-rev", type=int, help="fail if the project changed since this _rev")
    s.set_defaults(fn=cmd_project_set)

    fl = sub.add_parser("files").add_subparsers(dest="sub", required=True)
    s = fl.add_parser("search"); s.add_argument("text"); s.add_argument("--project")
    s.add_argument("--limit", type=int, default=100); s.set_defaults(fn=cmd_files_search)

    ix = sub.add_parser("index").add_subparsers(dest="sub", required=True)
    ix.add_parser("rebuild").set_defaults(fn=cmd_index_rebuild)

    s = sub.add_parser("verify", help="check records against the journal (admin)")
    s.add_argument("--repair", action="store_true", help="rebuild records from the journal")
    s.set_defaults(fn=cmd_verify)

    lk = sub.add_parser("lock").add_subparsers(dest="sub", required=True)
    lk.add_parser("show").set_defaults(fn=cmd_lock_show)
    s = lk.add_parser("break", help="administrative recovery of an abandoned writer lock")
    s.add_argument("--token", required=True, help="token shown by 'lock show'")
    s.add_argument("--reason", required=True); s.set_defaults(fn=cmd_lock_break)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (WorkspaceError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
