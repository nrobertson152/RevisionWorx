# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless change-control commands for QA, scripting and administrators.

    python -m revisionworx.changes WORKSPACE list [--open] [--text T] [--json]
    python -m revisionworx.changes WORKSPACE show NUMBER [--json]
    python -m revisionworx.changes WORKSPACE impact NUMBER [--csv]
    python -m revisionworx.changes WORKSPACE verify            # every change's audit chain
    python -m revisionworx.changes WORKSPACE export FILE [--overwrite]
    python -m revisionworx.changes WORKSPACE import-csv FILE   # creates draft ECRs (writable)
    python -m revisionworx.changes - verify-package FILE        # no workspace needed

Workflow (each one audited commit; also reachable as ``revisionworx change ...``):

    new-request TITLE --reason R [--doc DWG-1 ...] [--item part:P-100@B ...] [--priority high] [--number N]
    submit | review | approve NUMBER [-m TEXT] [--role ROLE]
    reject | rework | cancel NUMBER --reason TEXT
    new-order ECR [ECR ...] [--title T] [--action TITLE ...] [--number N]
    signoff ECO approve|reject [-m TEXT] [--role ROLE]   # release (planning) or closure (implemented)
    start | implemented | close ECO [-m TEXT]
    action-done ECO ACTION_ID [-m TEXT];  action-waive ECO ACTION_ID --reason TEXT
    implement ECO ITEM_KEY (--rev LABEL | --label TEXT) [--ack];  not-required ECO ITEM_KEY --reason TEXT
    reopen ECO --reason TEXT

``show`` lists the item keys and action ids these commands take. Exit codes: 0 ok, 1 problems found,
2 usage or input error (nothing was changed).
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


WORKFLOW = {"new-request", "submit", "review", "approve", "reject", "rework", "cancel", "new-order", "signoff",
            "start", "implemented", "close", "action-done", "action-waive", "implement", "not-required", "reopen"}


def _workflow_parsers(sub) -> None:
    s = sub.add_parser("new-request", help="create a draft change request (ECR)")
    s.add_argument("title")
    s.add_argument("--reason", required=True, help="why the change is needed")
    s.add_argument("--description", default="")
    s.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    s.add_argument("--category", default="design")
    s.add_argument("--number", default=None, help="change number (default: next from the numbering policy)")
    s.add_argument("--doc", action="append", default=[], help="affected document number or id (repeatable)")
    s.add_argument("--item", action="append", default=[],
                   help="declared item, e.g. part:P-100 or part:P-100@B (repeatable)")
    for name in ("submit", "review", "approve", "start", "implemented", "close"):
        s = sub.add_parser(name)
        s.add_argument("number")
        s.add_argument("-m", "--comment", default="")
        if name == "approve":
            s.add_argument("--role", default="")
    for name in ("reject", "rework", "cancel", "reopen"):
        s = sub.add_parser(name)
        s.add_argument("number")
        s.add_argument("--reason", required=True)
        if name == "reject":
            s.add_argument("--role", default="")
    s = sub.add_parser("new-order", help="create a change order (ECO) from approved requests")
    s.add_argument("requests", nargs="+")
    s.add_argument("--title", default="")
    s.add_argument("--description", default="")
    s.add_argument("--number", default=None)
    s.add_argument("--action", action="append", default=[], help="required action title (repeatable)")
    s = sub.add_parser("signoff")
    s.add_argument("number")
    s.add_argument("decision", choices=["approve", "reject"])
    s.add_argument("-m", "--comment", default="")
    s.add_argument("--role", default="")
    s = sub.add_parser("action-done")
    s.add_argument("number")
    s.add_argument("action_id")
    s.add_argument("-m", "--comment", default="")
    s = sub.add_parser("action-waive")
    s.add_argument("number")
    s.add_argument("action_id")
    s.add_argument("--reason", required=True)
    s = sub.add_parser("implement", help="record the revision that implements an affected item")
    s.add_argument("number")
    s.add_argument("item_key")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--rev", help="document revision label (verified against the document's history)")
    g.add_argument("--label", help="declared revision label for a non-document item (recorded, not verified)")
    s.add_argument("--ack", action="store_true", help="acknowledge revisions checked in by other work since "
                                                     "the baseline")
    s.add_argument("-m", "--comment", default="")
    s = sub.add_parser("not-required")
    s.add_argument("number")
    s.add_argument("item_key")
    s.add_argument("--reason", required=True)


def _workflow(svc, ws, a):
    from .. import documents
    from .model import AffectedItem, ChangeKind, ItemType, ValidationError
    if a.cmd == "new-request":
        find = exchange.document_lookup(ws)
        affected = []
        for ref in a.doc:
            doc_id = find(ref)
            if doc_id is None:
                raise ValidationError(f"unknown document {ref!r}")
            affected.append(AffectedItem.create(ItemType.DOCUMENT, "", doc_id))
        for ref in a.item:
            kind, sep, rest = ref.partition(":")
            if not sep or kind.strip().casefold() == "document":
                raise ValidationError(f"declared item {ref!r} must look like part:P-100 or part:P-100@B")
            label, _, rev = rest.partition("@")
            affected.append(AffectedItem.create(kind.strip().casefold(), label, baseline_label=rev))
        return svc.create_request(a.title, a.reason, description=a.description, category=a.category,
                                  priority=a.priority, affected=affected, number=a.number)
    if a.cmd == "new-order":
        ids = [_find(svc, r).id for r in a.requests]
        return svc.create_order(ids, title=a.title, description=a.description, number=a.number,
                                actions=[{"title": t, "required": True} for t in a.action])
    change = _find(svc, a.number)
    is_eco = change.kind is ChangeKind.ECO
    if a.cmd == "submit":
        return svc.submit(change.id, a.comment)
    if a.cmd == "review":
        return svc.begin_review(change.id, a.comment)
    if a.cmd == "approve":
        return svc.approve(change.id, a.comment, role=a.role)
    if a.cmd == "reject":
        return svc.reject(change.id, a.reason, role=a.role)
    if a.cmd == "rework":
        return svc.return_for_rework(change.id, a.reason)
    if a.cmd == "cancel":
        return svc.cancel_order(change.id, a.reason) if is_eco else svc.cancel_request(change.id, a.reason)
    if not is_eco:
        raise ValidationError(f"{change.number} is a change request; '{a.cmd}' applies to change orders")
    if a.cmd == "signoff":
        return svc.sign_off_order(change.id, a.decision, a.comment, role=a.role)
    if a.cmd == "start":
        return svc.start_work(change.id, a.comment)
    if a.cmd == "implemented":
        return svc.mark_implemented(change.id, a.comment)
    if a.cmd == "close":
        return svc.close_order(change.id, a.comment)
    if a.cmd == "reopen":
        return svc.reopen(change.id, a.reason)
    if a.cmd == "action-done":
        return svc.complete_action(change.id, a.action_id, a.comment)
    if a.cmd == "action-waive":
        return svc.waive_action(change.id, a.action_id, a.reason)
    if a.cmd == "not-required":
        return svc.mark_not_required(change.id, a.item_key, a.reason)
    if a.cmd == "implement":
        item = change.item(a.item_key)
        if a.rev is not None:
            if item.item_type is not ItemType.DOCUMENT:
                raise ValidationError("--rev is for documents; use --label for declared items")
            hits = [r for r in documents.history(ws, item.item_id) if r["label"].casefold() == a.rev.casefold()]
            if not hits:
                raise ValidationError(f"{item.label or item.item_id} has no revision {a.rev!r}")
            return svc.record_implementation(change.id, a.item_key, rev_id=hits[0]["id"], note=a.comment,
                                             acknowledge_intervening=a.ack)
        return svc.record_implementation(change.id, a.item_key, rev_label=a.label, note=a.comment,
                                         acknowledge_intervening=a.ack)
    raise ValidationError(f"unknown command {a.cmd}")  # pragma: no cover


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    p = argparse.ArgumentParser(prog=prog or "python -m revisionworx.changes", description=__doc__.split("\n")[0])
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
    _workflow_parsers(sub)
    a = p.parse_args(argv)
    out = sys.stdout
    try:
        if a.cmd == "verify-package":
            problems = exchange.verify_package(exchange.load_package(Path(a.file)))
            out.write(json.dumps(problems, indent=1) + "\n" if problems else "package intact\n")
            return 1 if problems else 0
        writable = a.cmd == "import-csv" or a.cmd in WORKFLOW
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
                    for it in change.affected:
                        out.write(f"  item {it.key}  {it.item_type.value}: {it.label}"
                                  f"{' @' + it.baseline_label if it.baseline_label else ''}\n")
                    for act in getattr(change, "actions", ()):
                        out.write(f"  action {act.id}  [{act.status.value}] {act.title}"
                                  f"{'' if act.required else ' (optional)'}\n")
                    for imp in getattr(change, "implementations", ()):
                        out.write(f"  implementation {imp.item_key}  [{imp.status.value}]"
                                  f"{' ' + imp.rev_label if imp.rev_label else ''}\n")
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
            if a.cmd in WORKFLOW:
                change = _workflow(svc, ws, a)
                out.write(f"{change.number} {change.title} [{change.status.value}]\n")
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
