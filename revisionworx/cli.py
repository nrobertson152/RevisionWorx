# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line interface. Every GUI action has a scriptable equivalent here."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from . import __version__, atomicio, doctor, documents, paths, projects
from .clientstate import CheckoutTokens
from .index import open_index
from .workspace import CommittedNotApplied, Workspace, WorkspaceError


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


CLI_LOCK_WAIT = 90.0   # a script would rather wait its turn than fail, and this outlasts a stuck lock clearing itself


def _open(args, writable: bool) -> Workspace:
    return Workspace.open(_ws_location(args), writable=writable, state_dir=args.state_dir, wait=CLI_LOCK_WAIT)


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
                    "coverage": idx.coverage(), "settings": ws.settings(),
                    "network_location": paths.is_network_location(ws.meta_dir)}
            lock = info["writer_lock"]
            text = "\n".join([
                f"Workspace {ws.name!r} ({ws.id})", f"  location: {ws.meta_dir}",
                f"  journal: {info['journal_seq']} entries (applied {info['applied_seq']})",
                f"  writer: {'%s@%s since %s' % (lock['user'], lock['host'], lock['acquired']) if lock else 'none'}",
                f"  roots: {', '.join(r['name'] for r in info['roots']) or 'none'}",
                f"  projects: {info['coverage']['registered_projects']}",
                f"  index: {info['index']['cache_path']} (seq {info['index']['cache_seq']})",
                f"  {info['coverage']['note']}"])
            if info["network_location"]:
                text += f"\n  warning: {paths.NETWORK_NOTICE}"
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
    if any(p.get("severity") != "info" for p in report["problems"]) and not report["repaired"]:
        raise SystemExit(1)


def cmd_doctor(args):
    report = doctor.examine(_ws_location(args), hash_content=not args.quick, state_dir=args.state_dir)
    _emit(args, report, doctor.render(report))
    if not report["ok"]:
        raise SystemExit(1)


def cmd_lock_show(args):
    holder = Workspace.read_writer_lock(_ws_location(args))
    _emit(args, holder, json.dumps(holder, indent=2) if holder else "No writer lock is held.")


def cmd_lock_clear_orphan(args):
    r = Workspace.clear_orphaned_release(_ws_location(args), args.file, args.reason)
    if r["outcome"] == "refused":
        raise WorkspaceError(f"{args.file} was NOT cleared: its bytes changed after you inspected them. "
                             f"They were kept as {r['quarantine']}, which still blocks writers; inspect "
                             "that file and clear it separately (recorded in the journal).")
    if r["outcome"] != "completed":
        raise WorkspaceError(f"{args.file} is being cleared by another administrator; retry shortly.")
    note = " (resumed your earlier authorisation)" if r["adopted"] else ""
    _emit(args, {"cleared": args.file, **r},
          f"Cleared {args.file} as {r['evidence']} (verified against the audited SHA-256; recorded in "
          f"the journal){note}.")


def cmd_lock_break(args):
    holder = Workspace.break_writer_lock(_ws_location(args), args.token, args.reason, state_dir=args.state_dir)
    _emit(args, holder, f"Broke writer lock held by {holder.get('user')}@{holder.get('host')}; recorded in journal.")


# ---------------------------------------------------------------- a team working from a shared folder (ADR-024)
def _ask_password(args, prompt: str, confirm: bool = False) -> str:
    if getattr(args, "password_stdin", False):
        line = sys.stdin.readline()
        if not line:
            raise WorkspaceError("expected a password on standard input")
        return line.rstrip("\r\n")
    first = getpass.getpass(f"{prompt}: ")
    if confirm and getpass.getpass("Type it again: ") != first:
        raise WorkspaceError("those didn't match, so nothing was changed")
    return first


def _team_handle(args, writable: bool):
    ws = _open(args, writable)
    if ws.materialises:
        ws.close()
        raise WorkspaceError("this workspace isn't set up for a team. An administrator can do that with 'team enable'")
    return ws


def cmd_team_enable(args):
    from . import accounts
    password = _ask_password(args, f"Choose a password for {args.admin}", confirm=True)
    made = accounts.enable_team_use(_ws_location(args), args.admin, args.name or args.admin, password, state_dir=args.state_dir)
    from .shared import save_session
    save_session(made["session"], args.state_dir)
    lines = [f"This workspace is now set up for a team, with {made['session'].account} as its administrator on this computer.",
             "", f"    Recovery code: {made['recovery_code']}", "",
             "Write the recovery code down and keep it somewhere safe, away from the shared folder. It's shown only this once. It's "
             "the only way to get an administrator account back if this computer is lost or the password is forgotten.",
             "", "Not verified: whether this works across real computers on your share. Run 'revisionworx share test FOLDER' on two "
             "computers at the same time to find out."]
    _emit(args, {"recovery_code": made["recovery_code"], "admin": made["session"].account, "check": made["check"]}, "\n".join(lines))


def cmd_team_status(args):
    from . import accounts
    with _open(args, False) as ws:
        if ws.materialises:
            _emit(args, {"team": False}, "This workspace isn't set up for a team: one computer makes changes at a time.")
            return
        rows = accounts.list_accounts(ws)
        me = ws.session.account if ws.session else None
        info = {"team": True, "accounts": rows, "logged_in": me, "role": ws.role, "journal_seq": ws.head_seq(),
                "health": ws.health(), "writer_lock": Workspace.read_writer_lock(ws.meta_dir),
                "network_location": paths.is_network_location(ws.meta_dir), "rules": ws.rules}
    lines = [f"Set up for a team, {len(rows)} account(s). Logged in as {me} ({info['role']})." if me
             else f"Set up for a team, {len(rows)} account(s). Not logged in here."]
    if info["rules"] < 2:
        lines.append("On team rules 1: an administrator can move it to rules 2 with 'revisionworx team upgrade'.")
    if not info["health"]["ok"]:
        lines.append(f"Writes are refused: {info['health']['reason']}")
    _emit(args, info, "\n".join(lines))


def cmd_team_upgrade(args):
    from . import accounts
    with _team_handle(args, True) as ws:
        result = accounts.upgrade_rules(ws)
    text = ("This workspace now follows team rules 2: unit events, build standards, effectivity decisions and deviations, "
            "written once, can't be changed or removed by anyone. RevisionWorx older than this can't open it any more."
            if result["changed"] else f"This workspace already follows team rules {result['rules']}.")
    _emit(args, result, text)


def cmd_team_repair(args):
    from .shared import SharedWorkspace
    report = SharedWorkspace.repair(Workspace.meta_dir_for(_ws_location(args)), state_dir=args.state_dir, remove_damaged=args.remove_damaged)
    restored, removed = report.get("restored", []), report.get("removed", [])
    left = [p for p in report["problems"] if p.get("severity") != "info" and p.get("seq") not in restored]
    text = f"Put back {len(restored)} journal entr{'y' if len(restored) == 1 else 'ies'} from this computer's copy. "
    if removed:
        text += f"Moved {len(removed)} damaged entr{'y' if len(removed) == 1 else 'ies'} to .revisionworx/quarantine. "
    text += f"{len(left)} problem(s) left."
    if any(p["problem"] == "journal-damaged" for p in left):
        text += (" The next entry is damaged. If no computer can read it, nobody can have used it, and --remove-damaged moves it out of the "
                 "way so that work can carry on.")
    _emit(args, report, text)
    if left:
        raise SystemExit(1)


def cmd_team_forget_local(args):
    from .shared import SharedWorkspace
    SharedWorkspace.forget_local(_ws_location(args), state_dir=args.state_dir)
    _emit(args, {"forgotten": True}, "This computer's copy of the journal is gone. It's read again from the shared folder next time.")


def cmd_team_recover(args):
    from . import accounts
    from .shared import save_session
    code = args.code or (sys.stdin.readline().strip() if args.password_stdin else getpass.getpass("Recovery code: "))
    password = _ask_password(args, f"Choose a new password for {args.admin}", confirm=True)
    session = accounts.recover_admin(_ws_location(args), code, args.admin, password, state_dir=args.state_dir)
    save_session(session, args.state_dir)
    _emit(args, {"admin": session.account}, f"{session.account} is an administrator again, on this computer. Any earlier key for that "
          "account has stopped working.")


def cmd_login(args):
    from . import accounts
    password = _ask_password(args, f"Password for {args.name}")
    made = accounts.login(_ws_location(args), args.name, password, state_dir=args.state_dir, remember_hours=args.hours)
    session = made["session"]
    text = f"Logged in as {session.account} ({made['role']}) for {args.hours:g} hours."
    if made["must_change"]:
        text += " This password was only to get you started: run 'revisionworx passwd' to choose your own before doing anything else."
    _emit(args, {"account": session.account, "role": made["role"], "must_change": made["must_change"]}, text)


def cmd_logout(args):
    from . import accounts
    with _open(args, False) as ws:
        accounts.logout(ws.id, args.state_dir)
    _emit(args, {"logged_out": True}, "Logged out on this computer.")


def cmd_whoami(args):
    with _open(args, False) as ws:
        session = None if ws.materialises else ws.session
        info = {"account": session.account if session else None, "role": ws.role if session else None}
    _emit(args, info, f"{info['account']} ({info['role']})" if session else "Not logged in.")


def cmd_passwd(args):
    from . import accounts
    old = _ask_password(args, "Current password")
    new = _ask_password(args, "New password", confirm=True)
    session = accounts.change_password(_ws_location(args), old, new, state_dir=args.state_dir)
    _emit(args, {"account": session.account}, f"Password changed for {session.account}.")


def cmd_user_list(args):
    from . import accounts
    with _team_handle(args, False) as ws:
        rows = accounts.list_accounts(ws)
    text = [f"{r['id']:<20} {r['role']:<6} {'active' if r['active'] else 'DISABLED':<9}"
            f"{'must choose a password  ' if r.get('must_change') else ''}{r.get('name', '')}" for r in rows]
    _emit(args, rows, "\n".join(text))


def cmd_user_add(args):
    from . import accounts
    password = _ask_password(args, f"A starting password for {args.name} (they choose their own when they first log in)", confirm=True)
    with _team_handle(args, True) as ws:
        row = accounts.create_account(ws, args.name, password, name=args.display or "", role=args.role)
    _emit(args, row, f"Created {row['id']} ({row['role']}). Give them the starting password yourself; it isn't stored anywhere.")


def cmd_user_disable(args):
    from . import accounts
    with _team_handle(args, True) as ws:
        row = accounts.set_active_flag(ws, args.name, False)
    _emit(args, row, f"Disabled {row['id']}. What they did before stands; from now on, every computer ignores changes in their name.")


def cmd_user_enable(args):
    from . import accounts
    with _team_handle(args, True) as ws:
        row = accounts.set_active_flag(ws, args.name, True)
    _emit(args, row, f"Enabled {row['id']}.")


def cmd_user_reset(args):
    from . import accounts
    password = _ask_password(args, f"A new starting password for {args.name}", confirm=True)
    with _team_handle(args, True) as ws:
        row = accounts.reset_password(ws, args.name, password)
    _emit(args, row, f"Reset {row['id']}. They choose their own password at their next login.")


def cmd_user_role(args):
    from . import accounts
    with _team_handle(args, True) as ws:
        row = accounts.set_role(ws, args.name, args.role)
    _emit(args, row, f"{row['id']} is now {row['role']}.")


def _device_line(r: dict) -> str:
    state = (f"REVOKED {r['revoked'][:10]} by {r['revoked_by']}: {r['reason']}" if r["revoked"] else
             f"NOT COUNTED: {r['problem']}" if r["problem"] else f"enrolled {r['added'][:10]} by {r['added_by']}")
    return f"{r['device']}  {r['account']:<16} {r['fingerprint']}  {r['name'] or '(no name)'}  {state}"


def cmd_user_device_list(args):
    from . import devices
    from .accounts import normal_id
    with _team_handle(args, False) as ws:
        rows = devices.listing(ws)
    if args.name:
        rows = [r for r in rows if r["account"] == normal_id(args.name)]
    _emit(args, rows, "\n".join(_device_line(r) for r in rows) or "No phones are enrolled" + (f" to {args.name}." if args.name else "."))


def cmd_user_device_revoke(args):
    from . import devices
    with _team_handle(args, True) as ws:
        out = devices.revoke(ws, args.name, args.device, args.reason)
    r = out["device"]
    if out["result"] == "already-revoked":
        text = f"Phone {r['device']} was already revoked from {r['account']} ({r['revoked'][:10]}, {r['reason']})."
    else:
        text = (f"Revoked phone {r['device']} ({r['name'] or 'no name'}) from {r['account']}. Its signatures are refused from "
                "now on; what was accepted before stands, and 'revisionworx unit verify' still checks it.")
    _emit(args, out, text)


def cmd_share_check(args):
    from . import sharecheck
    report = sharecheck.check_folder(Path(args.folder))
    lines = [f"{'ok    ' if c['ok'] else 'FAILED'} {c['name']}: {c['detail']}" for c in report["checks"]]
    lines.append(f"{'This folder passed' if report['ok'] else 'This folder FAILED'}, {report['scope']}.")
    _emit(args, report, "\n".join(lines))
    if not report["ok"]:
        raise SystemExit(1)


def _share_report_text(report: dict) -> str:
    lines = [f"Result: {report['verdict']}.", f"Computers: {', '.join(report['hosts']) or 'none'} ({report['runners']} run(s)).",
             f"Names won: {report['slots_won']}, races lost to another: {report['races_lost']}.",
             f"Slowest read of a name just lost: {report['slowest_read_seconds']:.2f} s. Out-of-date folder listings seen: "
             f"{report['stale_listings']} (oldest {report['oldest_stale_listing_seconds']:.1f} s; RevisionWorx doesn't rely on listings). "
             f"Times the share said \"try again\": {report['busy_answers']}."]
    lines += [f"  PROBLEM: {p}" for p in report["problems"][:50]]
    return "\n".join(lines)


def cmd_share_test(args):
    from . import sharecheck
    def say(text):
        if not args.json:
            print(text, flush=True)
    say(f"Racing for {args.seconds:g} seconds. Start this at the same time on your other computers.")
    report = sharecheck.run_test(Path(args.folder), seconds=args.seconds, say=say)
    _emit(args, report, _share_report_text(report) + "\nRun 'share report' afterwards to see every computer's results together.")
    if report["problems"]:
        raise SystemExit(1)


def cmd_share_report(args):
    from . import sharecheck
    report = sharecheck.read_report(Path(args.folder))
    _emit(args, report, _share_report_text(report))
    if report["problems"]:
        raise SystemExit(1)


# ---------------------------------------------------------------- Phase 2: documents
def _tokens(args) -> CheckoutTokens:
    return CheckoutTokens(args.state_dir)


def _rev_line(r: dict) -> str:
    return (f"{r['label']:>4}  {r['state']:<11} {r['created']}  {r['author'].get('user', '')}  "
            f"{r['sha256'][:12]}  {r['description']}")


def cmd_doc_register(args):
    with _open(args, True) as ws:
        doc, rev = documents.register_document(
            ws, args.project, Path(args.file), type_id=args.type, number=args.number, title=args.title,
            owner=args.owner, revision_scheme=args.scheme, capture=not args.no_revision,
            revision_description=args.message, label=args.label,
            allow_duplicate_number=args.allow_duplicate_number)
        _emit(args, {"document": doc, "revision": rev},
              f"Registered {doc['id']}  {doc['rel_path']}  [{doc['type_id']}]"
              + (f"\n  revision {rev['label']} ({rev['sha256'][:12]})" if rev else "\n  no revision captured"))


def cmd_doc_list(args):
    with _open(args, False) as ws, open_index(ws, args.cache_dir) as idx:
        rows = idx.documents(args.project, args.text or "", limit=args.limit)
        _emit(args, rows, "\n".join(
            f"{r['id']}  {r['number'] or '-':<10} rev {r['latest_label'] or '-':<3} {r['latest_state'] or '':<10}"
            f"{' OUT:' + r['checked_out_by'] if r['checked_out_by'] else ''}  {r['rel_path']}" for r in rows)
            or "No documents.")


def cmd_doc_show(args):
    with _open(args, False) as ws:
        doc = ws.get("document", args.id)
        if doc is None:
            raise WorkspaceError(f"unknown document {args.id}")
        status = documents.document_status(ws, args.id, verify_hash=args.hash)
        hist = documents.history(ws, args.id)
        co = ws.get("checkout", args.id)
        data = {"document": doc, "status": status, "history": hist, "checkout": co and
                {k: v for k, v in co.items() if k != "token"}}
        text = [f"{doc['title']}  [{doc['type_id']}]  {doc['number']}", f"  file: {status['path']}",
                f"  working file: {status['state']} (checked by {status['method']})"]
        if co:
            text.append(f"  checked out by {co['owner']['user']}@{co['owner']['host']} since {co['acquired']}")
        text += ["  history:"] + [f"    {_rev_line(r)}" for r in hist]
        _emit(args, data, "\n".join(text))


def cmd_doc_checkout(args):
    with _open(args, True) as ws:
        note = ""
        try:
            co = documents.checkout(ws, args.id, note=args.note)
        except CommittedNotApplied as exc:       # saved: the token must not be lost with the error
            co, note = exc.result, f"\nNote: {exc}"
        _tokens(args).put(ws.id, args.id, co["token"])
        _emit(args, {k: v for k, v in co.items() if k != "token"},
              f"Checked out {args.id}; edit the file in place, then run 'doc checkin'.{note}")


def cmd_doc_checkin(args):
    with _open(args, True) as ws:
        token = documents.my_token(ws, _tokens(args), args.id)
        if not token:
            raise WorkspaceError("this machine holds no checkout token for that document")
        note = ""
        try:
            rev = documents.checkin(ws, args.id, token, args.message, label=args.label,
                                    allow_unchanged=args.allow_unchanged)
        except CommittedNotApplied as exc:       # saved: reporting a failure here would invite a second check-in
            rev, note = exc.result, f"\nNote: {exc}"
        _tokens(args).forget(ws.id, args.id)
        _emit(args, rev, f"Checked in revision {rev['label']} ({rev['sha256'][:12]}){note}")


def cmd_doc_cancel(args):
    with _open(args, True) as ws:
        token = documents.my_token(ws, _tokens(args), args.id)
        if not token:
            raise WorkspaceError("this machine holds no checkout token for that document")
        documents.cancel_checkout(ws, args.id, token, revert=args.revert)
        _tokens(args).forget(ws.id, args.id)
        _emit(args, {"cancelled": args.id}, "Checkout cancelled" + (" and file reverted." if args.revert else
                                                                     "; the working file was left as it is."))


def cmd_doc_restore(args):
    with _open(args, True) as ws:
        token = documents.my_token(ws, _tokens(args), args.id)
        if not token:
            raise WorkspaceError("check the document out first")
        documents.restore_to_working(ws, args.id, token, args.revision)
        _emit(args, {"restored": args.revision}, "Working file restored; check in to record it as a new revision.")


def cmd_doc_release_checkout(args):
    with _open(args, True) as ws:
        co = documents.admin_release_checkout(ws, args.id, args.token, args.reason)
        _emit(args, {k: v for k, v in co.items() if k != "token"},
              f"Released checkout held by {co['owner']['user']}@{co['owner']['host']} (audited).")


def cmd_doc_checkout_info(args):
    with _open(args, False) as ws:
        co = ws.get("checkout", args.id)
        _emit(args, co, json.dumps(co, indent=2) if co else "Not checked out.")


def cmd_rev_transition(args):
    with _open(args, True) as ws:
        rev = documents.transition(ws, args.revision, args.state, comment=args.comment,
                                   expected_rev=args.expect_rev)
        _emit(args, rev, f"Revision {rev['label']} is now {rev['state']}")


def cmd_rev_export(args):
    with _open(args, False) as ws:
        out = documents.export_revision(ws, args.revision, Path(args.dest))
        _emit(args, {"file": str(out)}, f"Exported and verified {out}")


def cmd_rev_compare(args):
    with _open(args, False) as ws:
        _emit(args, documents.compare_revisions(ws, args.a, args.b))


def cmd_doc_verify(args):
    from . import derivatives
    with _open(args, False) as ws:
        problems = documents.verify_revisions(ws, args.id)
        pdfs = [p for p in derivatives.verify(ws)
                if not args.id or (ws.get(derivatives.KIND, p["id"]) or {}).get("document") == args.id]
        _emit(args, problems + pdfs, "\n".join([f"{p['document_id']} rev {p['label']}: {p['problem']}" for p in problems]
                                               + [f"PDF {p['id']}: {p['problem']} ({p['detail']})" for p in pdfs])
              or "All stored revision content and PDFs verified (SHA-256).")
        if problems or pdfs:
            raise SystemExit(1)


def cmd_doc_pdf(args):
    from . import derivatives
    with _open(args, True) as ws:
        rev = derivatives.find_revision(ws, args.revision)
        d = derivatives.attach(ws, rev["id"], Path(args.file), tool=args.tool, replace=args.replace, reason=args.reason)
        doc = ws.get("document", rev["document_id"]) or {}
        _emit(args, d, f"{doc.get('number') or doc.get('id')} rev {rev['label']}: PDF {d['sha256'][:12]} attached"
                       + (f" in place of {len(d['replaced'])} earlier one(s)" if d["replaced"] else ""))


def cmd_doc_pdfs(args):
    from . import derivatives
    with _open(args, False) as ws:
        if args.missing:
            rows = derivatives.missing(ws, args.project)
            _emit(args, rows, "\n".join(f"{r['number'] or r['document']} rev {r['label']}: released, no PDF" for r in rows)
                  + f"\n{len(rows)} released revision(s) without a PDF")
            return
        rows = []
        for doc in documents.list_documents(ws, args.project):
            for rev_id, d in derivatives.of_document(ws, doc["id"]).items():
                rev = ws.get("revision", rev_id) or {}
                rows.append({"document": doc["id"], "number": doc.get("number", ""), "label": rev.get("label", ""),
                             "sha256": d["sha256"], "size": d["size"], "tool": d["tool"], "replaced": len(d["replaced"])})
        _emit(args, rows, "\n".join(f"{r['number'] or r['document']} rev {r['label']}: {r['size']} bytes"
                                     f"{', ' + r['tool'] if r['tool'] else ''}" for r in rows) + f"\n{len(rows)} PDF(s)")


def cmd_doc_gc(args):
    with _open(args, True) as ws:
        r = documents.gc_content(ws, apply=args.apply, min_age_seconds=args.min_age_hours * 3600)
        verb = "Removed" if r["removed"] else "Would remove"
        _emit(args, r, f"{verb} {len(r['orphans'])} unreferenced content object(s), {r['bytes']} bytes "
                       f"({r['referenced']} referenced, {r['kept_young']} too recent to touch)."
              + (f" {len(r['unrecognised'])} item(s) in the content folder are not content objects and were left alone: "
                 f"{', '.join(r['unrecognised'])}." if r["unrecognised"] else "")
              + ("" if args.apply else " Dry run; add --apply to remove."))


def cmd_drawing_register(args):
    with _open(args, False) as ws:
        rows = documents.drawing_register(ws, args.project)
        text = documents.drawing_register_csv(rows)
        if args.csv:
            out = Path(args.csv)
            if out.exists() and not args.force:
                raise WorkspaceError(f"{out} exists; use --force to overwrite")
            atomicio.atomic_write_bytes(out, text.encode("utf-8"))
            _emit(args, {"file": str(out), "rows": len(rows)}, f"Wrote {len(rows)} drawings to {out}")
        else:
            _emit(args, rows, text.rstrip())


def cmd_doctype_list(args):
    with _open(args, False) as ws:
        t = documents.list_doctypes(ws)
        _emit(args, t, "\n".join(f"{k:<14} {v['name']:<26} {v['revision_scheme']:<9} {' '.join(v['extensions'])}"
                                  for k, v in t.items()))


def cmd_doctype_save(args):
    with _open(args, True) as ws:
        tid = documents.save_doctype(ws, {"id": args.id, "name": args.name, "extensions": args.ext,
                                          "revision_scheme": args.scheme, "category": args.category})
        _emit(args, {"id": tid}, f"Saved document type {tid!r}")


def cmd_lifecycle_show(args):
    with _open(args, False) as ws:
        lc = documents.get_lifecycle(ws)
        _emit(args, lc, "\n".join([f"States: {', '.join(s['name'] for s in lc['states'])}",
                                   "Transitions:"] + [f"  {a} -> {b}" for a, b in lc["transitions"]]
                                  + [f"  {a} -> {b} (automatic)" for a, b in lc["system_transitions"]]))


def cmd_lifecycle_import(args):
    with _open(args, True) as ws:
        documents.save_lifecycle(ws, atomicio.read_json(Path(args.file)))
        _emit(args, {"saved": True}, "Lifecycle saved.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="revisionworx", description="RevisionWorx engineering PDM")
    p.add_argument("--version", action="version", version=f"RevisionWorx {__version__}")
    p.add_argument("-w", "--workspace", help="workspace folder (or REVISIONWORX_WORKSPACE)")
    p.add_argument("--cache-dir", type=Path, default=None, help="local index cache folder")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--state-dir", type=Path, default=None, help="local client state (checkout tokens)")
    sub = p.add_subparsers(dest="cmd", required=True)
    # Listed for --help only; main() hands these to their own command lines before parsing.
    sub.add_parser("bom", help="BOM files: validate, explode, roll up, compare (run 'revisionworx bom -h')",
                   add_help=False)
    sub.add_parser("change", help="engineering changes (ECR/ECO): list, show, impact, verify, export, "
                   "import-csv (run 'revisionworx change -h')", add_help=False)
    sub.add_parser("unit", help="the unit register: serial numbers, build standards, configuration and history "
                   "(run 'revisionworx unit -h')", add_help=False)
    sub.add_parser("link", help="JSON commands for other programs: status, publish, fetch, open, units, and recording on "
                   "units (see docs/INTEGRATION.md)", add_help=False)

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

    s = sub.add_parser("doctor", help="read-only integrity check of the whole workspace; changes nothing")
    s.add_argument("--quick", action="store_true", help="check content objects are present without hashing them")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("verify", help="check records against the journal (admin)")
    s.add_argument("--repair", action="store_true", help="rebuild records from the journal")
    s.set_defaults(fn=cmd_verify)

    doc = sub.add_parser("doc", help="documents (Phase 2)").add_subparsers(dest="sub", required=True)
    s = doc.add_parser("register", help="register an existing file in a managed project (never moved)")
    s.add_argument("project"); s.add_argument("file")
    s.add_argument("--type"); s.add_argument("--number", default=""); s.add_argument("--title", default="")
    s.add_argument("--owner", default=""); s.add_argument("--scheme", choices=("alpha", "numeric", "numeric2", "manual"))
    s.add_argument("--label"); s.add_argument("-m", "--message", default="Initial revision")
    s.add_argument("--no-revision", action="store_true", help="do not capture the current content")
    s.add_argument("--allow-duplicate-number", action="store_true"); s.set_defaults(fn=cmd_doc_register)
    s = doc.add_parser("list"); s.add_argument("--project"); s.add_argument("text", nargs="?")
    s.add_argument("--limit", type=int, default=200); s.set_defaults(fn=cmd_doc_list)
    s = doc.add_parser("show"); s.add_argument("id"); s.add_argument("--hash", action="store_true",
                                                                   help="hash the working file")
    s.set_defaults(fn=cmd_doc_show)
    s = doc.add_parser("checkout"); s.add_argument("id"); s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_doc_checkout)
    s = doc.add_parser("checkin"); s.add_argument("id"); s.add_argument("-m", "--message", required=True)
    s.add_argument("--label"); s.add_argument("--allow-unchanged", action="store_true"); s.set_defaults(fn=cmd_doc_checkin)
    s = doc.add_parser("cancel"); s.add_argument("id"); s.add_argument("--revert", action="store_true",
                                                                      help="discard edits: restore latest revision")
    s.set_defaults(fn=cmd_doc_cancel)
    s = doc.add_parser("restore", help="put an earlier revision's content into the checked-out file")
    s.add_argument("id"); s.add_argument("revision"); s.set_defaults(fn=cmd_doc_restore)
    s = doc.add_parser("checkout-info"); s.add_argument("id"); s.set_defaults(fn=cmd_doc_checkout_info)
    s = doc.add_parser("release-checkout", help="administrative release of an abandoned checkout")
    s.add_argument("id"); s.add_argument("--token", required=True, help="token shown by checkout-info")
    s.add_argument("--reason", required=True); s.set_defaults(fn=cmd_doc_release_checkout)
    s = doc.add_parser("pdf", help="attach the PDF of one revision (a CAD export), tied to that revision (ADR-026)")
    s.add_argument("revision", metavar="DOC@REV"); s.add_argument("file")
    s.add_argument("--tool", default="", help="how it was made, e.g. 'Inventor 2025 export'")
    s.add_argument("--replace", action="store_true", help="put a corrected PDF in place of the current one")
    s.add_argument("--reason", default=""); s.set_defaults(fn=cmd_doc_pdf)
    s = doc.add_parser("pdfs", help="revisions' PDFs, or with --missing the released revisions without one")
    s.add_argument("--project"); s.add_argument("--missing", action="store_true"); s.set_defaults(fn=cmd_doc_pdfs)
    s = doc.add_parser("verify", help="re-hash stored revision content and PDFs"); s.add_argument("--id")
    s.set_defaults(fn=cmd_doc_verify)
    s = doc.add_parser("gc", help="find/remove content objects no revision references (admin)")
    s.add_argument("--apply", action="store_true"); s.add_argument("--min-age-hours", type=float, default=1.0)
    s.set_defaults(fn=cmd_doc_gc)
    s = doc.add_parser("register-report", help="engineering drawing register of a project")
    s.add_argument("project"); s.add_argument("--csv"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_drawing_register)

    rv = sub.add_parser("rev", help="revisions").add_subparsers(dest="sub", required=True)
    s = rv.add_parser("transition"); s.add_argument("revision"); s.add_argument("state")
    s.add_argument("--comment", default=""); s.add_argument("--expect-rev", type=int); s.set_defaults(fn=cmd_rev_transition)
    s = rv.add_parser("export"); s.add_argument("revision"); s.add_argument("dest"); s.set_defaults(fn=cmd_rev_export)
    s = rv.add_parser("compare"); s.add_argument("a"); s.add_argument("b"); s.set_defaults(fn=cmd_rev_compare)

    dt = sub.add_parser("doctype").add_subparsers(dest="sub", required=True)
    dt.add_parser("list").set_defaults(fn=cmd_doctype_list)
    s = dt.add_parser("save"); s.add_argument("id"); s.add_argument("--name", required=True)
    s.add_argument("--ext", action="append", default=[]); s.add_argument("--category", default="document")
    s.add_argument("--scheme", default="alpha", choices=("alpha", "numeric", "numeric2", "manual"))
    s.set_defaults(fn=cmd_doctype_save)

    lcp = sub.add_parser("lifecycle").add_subparsers(dest="sub", required=True)
    lcp.add_parser("show").set_defaults(fn=cmd_lifecycle_show)
    s = lcp.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_lifecycle_import)

    lk = sub.add_parser("lock").add_subparsers(dest="sub", required=True)
    lk.add_parser("show").set_defaults(fn=cmd_lock_show)
    s = lk.add_parser("clear-orphan", help="admin: resolve an unreadable orphaned .releasing-* file")
    s.add_argument("file"); s.add_argument("--reason", required=True); s.set_defaults(fn=cmd_lock_clear_orphan)
    s = lk.add_parser("break", help="administrative recovery of an abandoned writer lock")
    s.add_argument("--token", required=True, help="token shown by 'lock show'")
    s.add_argument("--reason", required=True); s.set_defaults(fn=cmd_lock_break)

    def password_option(parser):
        parser.add_argument("--password-stdin", action="store_true", help="read passwords from standard input, one per line")

    tm = sub.add_parser("team", help="a team working from a shared folder: set up, status, repair").add_subparsers(dest="sub", required=True)
    s = tm.add_parser("enable", help="set this workspace up for a team, with you as administrator on this computer (can't be undone)")
    s.add_argument("--admin", required=True, help="your user name"); s.add_argument("--name", default="", help="your full name")
    password_option(s); s.set_defaults(fn=cmd_team_enable)
    tm.add_parser("status", help="accounts, who's logged in here, and whether writes are allowed").set_defaults(fn=cmd_team_status)
    tm.add_parser("upgrade", help="move a team workspace set up before 2026-11-08 to team rules 2 (an administrator; older "
                  "RevisionWorx can't open it afterwards)").set_defaults(fn=cmd_team_upgrade)
    s = tm.add_parser("repair", help="put back journal entries the shared folder has lost, from this computer's copy")
    s.add_argument("--remove-damaged", action="store_true", help="also move a damaged next entry, which no computer can have used, to quarantine")
    s.set_defaults(fn=cmd_team_repair)
    tm.add_parser("forget-local", help="discard this computer's copy of the journal (after a restore from backup)").set_defaults(fn=cmd_team_forget_local)
    s = tm.add_parser("recover", help="with the recovery code: an administrator account on this computer, with a new password")
    s.add_argument("--admin", required=True); s.add_argument("--code", default=None, help="the recovery code (asked for if not given)")
    password_option(s); s.set_defaults(fn=cmd_team_recover)

    s = sub.add_parser("login", help="log in on this computer"); s.add_argument("name")
    s.add_argument("--hours", type=float, default=12.0, help="how long to stay logged in (default 12)")
    password_option(s); s.set_defaults(fn=cmd_login)
    sub.add_parser("logout", help="log out on this computer").set_defaults(fn=cmd_logout)
    sub.add_parser("whoami", help="who's logged in here").set_defaults(fn=cmd_whoami)
    s = sub.add_parser("passwd", help="change your password"); password_option(s); s.set_defaults(fn=cmd_passwd)

    us = sub.add_parser("user", help="accounts (an administrator's job)").add_subparsers(dest="sub", required=True)
    us.add_parser("list").set_defaults(fn=cmd_user_list)
    s = us.add_parser("add"); s.add_argument("name"); s.add_argument("--display", default="", help="their full name")
    s.add_argument("--role", choices=("user", "admin"), default="user"); password_option(s); s.set_defaults(fn=cmd_user_add)
    s = us.add_parser("disable"); s.add_argument("name"); s.set_defaults(fn=cmd_user_disable)
    s = us.add_parser("enable"); s.add_argument("name"); s.set_defaults(fn=cmd_user_enable)
    s = us.add_parser("reset", help="give someone a new starting password"); s.add_argument("name"); password_option(s); s.set_defaults(fn=cmd_user_reset)
    s = us.add_parser("role"); s.add_argument("name"); s.add_argument("role", choices=("user", "admin")); s.set_defaults(fn=cmd_user_role)
    dv = us.add_parser("device", help="phones enrolled to accounts: their field records can be accepted as verified "
                       "(enrol one with 'fieldworx-bridge enrol')").add_subparsers(dest="device_cmd", required=True)
    s = dv.add_parser("list", help="phones enrolled, revoked ones too"); s.add_argument("name", nargs="?", default="")
    s.set_defaults(fn=cmd_user_device_list)
    s = dv.add_parser("revoke", help="refuse a phone's signatures from now on (lost, stolen, or handed on)")
    s.add_argument("name"); s.add_argument("device"); s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_user_device_revoke)

    sh = sub.add_parser("share", help="find out whether a network folder is fit for team use").add_subparsers(dest="sub", required=True)
    s = sh.add_parser("check", help="try the file operations a team workspace needs, from this computer"); s.add_argument("folder"); s.set_defaults(fn=cmd_share_check)
    s = sh.add_parser("test", help="run this on two or more computers at the same time: the only real evidence for a share")
    s.add_argument("folder"); s.add_argument("--seconds", type=float, default=60.0); s.set_defaults(fn=cmd_share_test)
    s = sh.add_parser("report", help="what every computer that ran 'share test' saw"); s.add_argument("folder"); s.set_defaults(fn=cmd_share_report)
    return p


def _delegate(argv: list[str]) -> int | None:
    """``revisionworx bom ...``, ``change ...`` and ``unit ...`` run the BOM, change-control and unit-register command
    lines, which have their own parsers. Global options before the subcommand are honoured for
    ``change`` and ``unit`` (the workspace); ``bom`` works on files and never opens a workspace."""
    workspace = None
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] in ("-w", "--workspace") and i + 1 < len(argv):
            workspace = argv[i + 1]
            i += 2
        elif argv[i].startswith("--workspace="):
            workspace = argv[i].split("=", 1)[1]
            i += 1
        else:
            return None                       # --help, --version etc.: the main parser handles them
    if i >= len(argv) or argv[i] not in ("bom", "change", "link", "unit"):
        return None
    rest = argv[i + 1:]
    if argv[i] == "link":
        from .integration import main as link_main
        return link_main(rest, workspace)
    if argv[i] == "bom":
        from .bom.__main__ import main as bom_main
        return bom_main(rest, prog="revisionworx bom")
    if argv[i] == "unit":
        from .units.__main__ import main as unit_main
        if rest and rest[0] in ("-h", "--help"):
            return unit_main(["--help"], prog="revisionworx [-w WORKSPACE] unit")
        if rest and rest[0] == "check-pack":         # needs no workspace; given one with -w, its register is checked too
            return unit_main([workspace or "-", *rest], prog="revisionworx unit")
        workspace = workspace or os.environ.get("REVISIONWORX_WORKSPACE")
        if not workspace:
            print("error: no workspace: use -w FOLDER or set REVISIONWORX_WORKSPACE", file=sys.stderr)
            return 2
        return unit_main([workspace, *rest], prog="revisionworx unit")
    from .changes.__main__ import main as change_main
    if rest and rest[0] in ("-h", "--help"):
        return change_main(["--help"], prog="revisionworx [-w WORKSPACE] change")
    if rest and rest[0] == "verify-package":
        return change_main(["-", *rest], prog="revisionworx change")
    workspace = workspace or os.environ.get("REVISIONWORX_WORKSPACE")
    if not workspace:
        print("error: no workspace: use -w FOLDER or set REVISIONWORX_WORKSPACE", file=sys.stderr)
        return 2
    return change_main([workspace, *rest], prog="revisionworx change")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    delegated = _delegate(argv)
    if delegated is not None:
        return delegated
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (WorkspaceError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
