# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless change-control commands for QA, scripting and administrators.

    python -m revisionworx.changes WORKSPACE list [--open] [--text T] [--json]
    python -m revisionworx.changes WORKSPACE show NUMBER [--json]
    python -m revisionworx.changes WORKSPACE impact NUMBER [--csv]
    python -m revisionworx.changes WORKSPACE verify            # every change's audit chain
    python -m revisionworx.changes WORKSPACE export FILE [--overwrite]
    python -m revisionworx.changes WORKSPACE import-csv FILE   # creates draft ECRs (writable)
    python -m revisionworx.changes - verify-package FILE        # no workspace needed

Read-only except ``import-csv``. Exit codes: 0 ok, 1 problems found, 2 usage or input error.
Kept separate from the core ``revisionworx`` CLI so no core file changes (issue #19).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..workspace import Workspace, WorkspaceError
from . import exchange, impact, query, service_for
from .model import ChangeError


def _find(svc, ref: str):
    change = svc.find_by_number(ref)
    if change is None:
        try:
            change = svc.get(ref)
        except ChangeError:
            raise ChangeError(f"no change numbered {ref!r}") from None
    return change


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m revisionworx.changes", description=__doc__.split("\n")[0])
    p.add_argument("workspace", help="folder containing .revisionworx ('-' for verify-package)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--open", action="store_true")
    s.add_argument("--text", default="")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("number")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("impact")
    s.add_argument("number")
    s.add_argument("--csv", action="store_true")
    sub.add_parser("verify")
    s = sub.add_parser("export")
    s.add_argument("file")
    s.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("import-csv")
    s.add_argument("file")
    s = sub.add_parser("verify-package")
    s.add_argument("file")
    a = p.parse_args(argv)
    out = sys.stdout
    try:
        if a.cmd == "verify-package":
            problems = exchange.verify_package(exchange.load_package(Path(a.file)))
            out.write(json.dumps(problems, indent=1) + "\n" if problems else "package intact\n")
            return 1 if problems else 0
        writable = a.cmd == "import-csv"
        with Workspace.open(Path(a.workspace), writable=writable) as ws:
            svc = service_for(ws)
            if a.cmd == "list":
                page = query.search(svc, query.ChangeFilter(open_only=a.open, text=a.text), limit=100_000)
                if a.json:
                    out.write(json.dumps(page, indent=1, ensure_ascii=False) + "\n")
                else:
                    for r in page["rows"]:
                        out.write(f"{r['number']:<12} {r['status_name']:<16} {r['priority']:<7} {r['title']}\n")
                    for prob in page["problems"]:
                        out.write(f"UNREADABLE {prob['id']}: {prob['detail']}\n")
                return 1 if page["problems"] else 0
            if a.cmd == "show":
                change = _find(svc, a.number)
                if a.json:
                    out.write(json.dumps({"change": change.to_dict(), "history": svc.history(change.id)},
                                         indent=1, ensure_ascii=False) + "\n")
                else:
                    out.write(f"{change.number} {change.title} [{change.status.value}]\n")
                    for ev in svc.history(change.id):
                        out.write(f"  {ev['n']:>3} {ev['time']} {ev['actor'].get('user')}: {ev['event']}"
                                  f"{' - ' + ev['comment'] if ev['comment'] else ''}\n")
                return 0
            if a.cmd == "impact":
                rep = impact.impact_summary(svc, _find(svc, a.number).id)
                out.write(impact.impact_csv(rep) if a.csv else impact.impact_text(rep))
                return 0
            if a.cmd == "verify":
                changes, problems = svc.scan()
                bad = 0
                for c in changes:
                    for prob in svc.verify_history(c.id):
                        bad += 1
                        out.write(f"{c.number}: {prob}\n")
                for prob in problems:
                    bad += 1
                    out.write(f"UNREADABLE {prob['id']}: {prob['detail']}\n")
                out.write(f"{len(changes)} change(s) checked, {bad} problem(s)\n")
                return 1 if bad else 0
            if a.cmd == "export":
                pkg = exchange.export_package(svc)
                exchange.write_export(Path(a.file), exchange.package_json(pkg), overwrite=a.overwrite)
                out.write(f"exported {len(pkg['changes'])} change(s) to {a.file}\n")
                return 0
            if a.cmd == "import-csv":
                text = Path(a.file).read_text(encoding="utf-8-sig")
                made = exchange.import_requests_csv(svc, text, exchange.document_lookup(ws))
                out.write("created " + ", ".join(c.number for c in made) + "\n")
                return 0
    except (ChangeError, WorkspaceError, OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
