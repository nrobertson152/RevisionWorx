# SPDX-License-Identifier: GPL-3.0-or-later
"""``revisionworx [-w WORKSPACE] unit ...``: the unit register from the command line (docs/UNITS.md).

    list [--text T] [--project P] [--json]            show REF [--at N | --on DATE] [--json]
    new [--serial S [--issuer I]] [--tag T [--tag-issuer I]] [--model PN] [--name N] [--project P] [--standard STD]
        [--doc DOC@REV ...] [--built DATE] [--status S] ...
    import FILE [--dry-run] [--fieldworx-ids | --no-fieldworx-ids]      export FILE
    update REF [--name] [--model] [--class] [--project] [--customer] [--site] [--location] [--notes]
    id add|retire|remove REF SCHEME VALUE [--issuer I]                  status REF STATUS
    milestone REF NAME [--on DATE]                                      note REF TEXT
    standard new NUMBER --title T (--doc DOC@REV ... | --from-project P) [--allow-unreleased]
    standard list | standard show STD                                   conform STD REF [REF ...] [--clear]
    doc REF DOC@REV | doc REF DOC --not-applicable | doc REF DOC --reset
    fit REF POSITION (--component REF | --part-number PN) [--serial S] [--lot L] [--replace [--disposition D]]
    remove REF POSITION [--disposition D]                               void REF N [N ...] --reason R
    merge DUPLICATE --into REF --reason R  (two records of one physical unit: the duplicate's identifiers move to REF)
    history REF                                                         where-used ...        verify
    pack REF FOLDER [--on DATE] [--sign-with KEYFILE]   (a handover pack: a page, the PDFs that apply, the record, SHA256SUMS;
                                           recorded in the unit's history as issued)
    packs REF                             withdraw-pack REF N --reason R      pack-signing [on --key FILE.pub | off]
    check-pack FOLDER [--key FILE.pub]    (is a handover pack whole? needs no workspace; with one, did it issue the pack?)
    transfer REF FOLDER --to WHO [--sign-with KEYFILE]   (another workspace keeps the unit, and everything fitted in it, from
                                           now on: a transfer pack, recorded here; the units take no more actions here)
    transfer-pack REF FOLDER              (the transfer pack again, exactly as it was issued)
    receive FOLDER [--key FILE.pub | --fingerprint SHA256:...]   (the units of a transfer pack, recorded here with their ids)
    numbering new NAME --pattern 'NH-{yy}-{seq:4}' [--issuer I]       numbering list | numbering next NAME
    new --serial-from NAME ...  (the next serial of a numbering, taken in the same entry)
    change candidates|status ECO          change decide ECO --kind K [--standard STD] [--scope RULE --value V | --unit REF]
    change add ECO REF                    change set REF ECO STATE      change install|verify|reverse REF ECO
    compliance [REF]                      (no REF: every unit out of compliance with a required change; exit 1 if any)
    deviation new --kind deviation|concession --title T --departure TEXT --reason R --unit REF ... [--doc DOC@REV ...]
        [--temporary [--until DATE]] [--number N]                    deviation list | deviation show DEV
    deviation add DEV REF --reason R      deviation clear REF DEV [--event N ...]      deviation not-used REF DEV --reason R
    deviation overdue                     (every unit with a temporary deviation past its end date; exit 1 if any)

Recording actions also take --effective DATE, --basis verified|reported|declared|legacy, --reason TEXT, --change ECO and
--evidence KIND:REF[=LABEL] (repeatable). ``REF`` is a unit id or a serial, tag or other code. Exit codes: 0 ok, 1 problems
found, 2 refused or bad input (nothing was changed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..workspace import CommittedNotApplied, Workspace, WorkspaceError
from . import csvio, deviation, effect, query
from .model import (BASES, CLASSES, DEVIATION_KINDS, DISPOSITIONS, EFFECTIVITY_KINDS, MILESTONES, SCHEMES, SCOPE_RULES,
                    STATUSES, UnitError, ValidationError, parse_evidence, unit_label)
from .service import UnitService, service_for

READS = {"list", "show", "history", "where-used", "verify", "export", "compliance", "packs", "transfer-pack"}
CHANGE_READS = {"candidates", "status"}
DEVIATION_READS = {"list", "show", "overdue"}


def _common(s: argparse.ArgumentParser) -> None:
    s.add_argument("--effective", default="", help="when it happened (YYYY-MM-DD), if not today")
    s.add_argument("--basis", default="declared", choices=BASES, help="how well it is known (default: declared)")
    s.add_argument("--reason", default="", help="why")
    s.add_argument("--change", default="", help="the ECO it carries out (number or id)")
    s.add_argument("--evidence", action="append", default=[], metavar="KIND:REF[=LABEL]",
                   help="a record that supports it, e.g. inspection:ins_... or work-order:WO-00012 (repeatable)")


def build_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="The unit register: serial-numbered units and their configuration.")
    p.add_argument("workspace", help="folder containing .revisionworx")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="units, optionally searched")
    s.add_argument("--text", default=""); s.add_argument("--project", default=""); s.add_argument("--json", action="store_true")
    s = sub.add_parser("show", help="one unit: identity, the documents that apply to it, components and history")
    s.add_argument("ref"); g = s.add_mutually_exclusive_group()
    g.add_argument("--at", type=int, help="as it stood after this event"); g.add_argument("--on", help="as known now, on this date")
    s.add_argument("--json", action="store_true"); s.add_argument("--no-history", action="store_true")
    s = sub.add_parser("history", help="a unit's events"); s.add_argument("ref"); s.add_argument("--json", action="store_true")

    s = sub.add_parser("new", help="register a unit")
    s.add_argument("--serial"); s.add_argument("--issuer", default="", help="who issued the serial (default: you)")
    s.add_argument("--serial-from", default="", metavar="NUMBERING", help="issue the next serial from this numbering")
    s.add_argument("--tag"); s.add_argument("--tag-issuer", default="", help="whose tag it is, e.g. the customer")
    s.add_argument("--other", help="another code"); s.add_argument("--name", default="")
    s.add_argument("--model", default="", help="model or part number"); s.add_argument("--class", dest="cls", default="product",
                                                                                        choices=CLASSES)
    s.add_argument("--status", default="planned", choices=STATUSES); s.add_argument("--project", default="")
    s.add_argument("--customer", default=""); s.add_argument("--site", default=""); s.add_argument("--location", default="")
    s.add_argument("--standard", default="", help="build standard number"); s.add_argument("--doc", action="append", default=[],
                                                                                          metavar="DOC@REV")
    for m in MILESTONES:
        s.add_argument(f"--{m}", default="", metavar="DATE", help=f"{m} on this date")
    s.add_argument("--notes", default=""); s.add_argument("--json", action="store_true"); _common(s)

    s = sub.add_parser("import", help="register every unit in a CSV register (all or nothing)")
    s.add_argument("file"); s.add_argument("--dry-run", action="store_true")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--fieldworx-ids", dest="fwx", action="store_true", default=None,
                   help="keep FieldWorx asset ids (the default for a *.fwassets.csv register)")
    g.add_argument("--no-fieldworx-ids", dest="fwx", action="store_false")
    s.add_argument("--status", default="in-service", choices=STATUSES, help="for rows that don't say (default in-service)")
    s.add_argument("--reason", default="")
    s = sub.add_parser("export", help="the register as CSV"); s.add_argument("file"); s.add_argument("--overwrite", action="store_true")

    s = sub.add_parser("update", help="change a unit's description, model, project or whereabouts")
    s.add_argument("ref")
    for f in ("name", "model", "project", "customer", "site", "location", "notes"):
        s.add_argument(f"--{f}")
    s.add_argument("--class", dest="cls", choices=CLASSES); _common(s)

    s = sub.add_parser("id", help="add, retire or remove an identifier")
    s.add_argument("action", choices=("add", "retire", "remove")); s.add_argument("ref")
    s.add_argument("scheme", choices=SCHEMES); s.add_argument("value"); s.add_argument("--issuer", default=""); _common(s)
    s = sub.add_parser("status", help="set a unit's status"); s.add_argument("ref"); s.add_argument("status", choices=STATUSES)
    _common(s)
    s = sub.add_parser("milestone", help="record built, shipped, installed, commissioned or decommissioned")
    s.add_argument("ref"); s.add_argument("milestone", choices=MILESTONES)
    s.add_argument("--on", default="", help="the date (empty when unknown)"); _common(s)
    s = sub.add_parser("note", help="add a note to a unit's history"); s.add_argument("ref"); s.add_argument("text"); _common(s)

    nb = sub.add_parser("numbering", help="numberings that issue serials").add_subparsers(dest="sub", required=True)
    s = nb.add_parser("new", help="a numbering, e.g. NH --pattern 'NH-{yy}-{seq:4}'")
    s.add_argument("name"); s.add_argument("--pattern", required=True)
    s.add_argument("--issuer", default="", help="whose serials these are (default: yours)")
    s.add_argument("--start", type=int, default=1, help="the first number of each run (default 1)")
    s = nb.add_parser("list"); s.add_argument("--json", action="store_true")
    s = nb.add_parser("next", help="the serial the next unit would get, without taking it")
    s.add_argument("name"); s.add_argument("--on", default="", help="for a unit built on this date")

    st = sub.add_parser("standard", help="build standards").add_subparsers(dest="sub", required=True)
    s = st.add_parser("new", help="a named set of document revisions, written once")
    s.add_argument("number"); s.add_argument("--title", default=""); s.add_argument("--model", default="")
    s.add_argument("--doc", action="append", default=[], metavar="DOC@REV")
    s.add_argument("--from-project", default="", help="every released document of this project")
    s.add_argument("--based-on", default=""); s.add_argument("--change", default=""); s.add_argument("--notes", default="")
    s.add_argument("--allow-unreleased", action="store_true"); s.add_argument("--json", action="store_true")
    s = st.add_parser("list"); s.add_argument("--json", action="store_true")
    s = st.add_parser("show"); s.add_argument("standard"); s.add_argument("--json", action="store_true")

    s = sub.add_parser("conform", help="record that units conform to a build standard ('none' for none)")
    s.add_argument("standard"); s.add_argument("refs", nargs="+")
    s.add_argument("--clear", action="store_true", help="drop unit differences for documents the standard names"); _common(s)
    s = sub.add_parser("doc", help="a unit's own revision of a document")
    s.add_argument("ref"); s.add_argument("document", help="DOC@REV, or DOC with --not-applicable or --reset")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--not-applicable", action="store_true"); g.add_argument("--reset", action="store_true"); _common(s)
    s = sub.add_parser("fit", help="fit something at a position")
    s.add_argument("ref"); s.add_argument("position")
    s.add_argument("--component", default="", help="a unit fitted in its own right"); s.add_argument("--part-number", default="")
    s.add_argument("--serial", default=""); s.add_argument("--lot", default=""); s.add_argument("--replace", action="store_true")
    s.add_argument("--disposition", default="unknown", choices=DISPOSITIONS, help="of what is replaced"); _common(s)
    s = sub.add_parser("remove", help="take something out of a position")
    s.add_argument("ref"); s.add_argument("position"); s.add_argument("--disposition", default="unknown", choices=DISPOSITIONS)
    _common(s)
    s = sub.add_parser("merge", help="record that two units are one physical unit: the duplicate's identifiers move to the "
                       "other, and the duplicate takes no more actions (undone by voiding the merge)")
    s.add_argument("duplicate"); s.add_argument("--into", required=True, metavar="REF", help="the unit that stays")
    _common(s)
    s = sub.add_parser("void", help="mark recorded events as mistakes")
    s.add_argument("ref"); s.add_argument("events", nargs="+", type=int); s.add_argument("--reason", required=True)

    s = sub.add_parser("where-used", help="units that use a revision, document, standard, part, serial, lot or component")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--revision", metavar="DOC@REV"); g.add_argument("--document"); g.add_argument("--standard")
    g.add_argument("--part-number"); g.add_argument("--serial"); g.add_argument("--lot", metavar="PN@LOT")
    g.add_argument("--component", metavar="REF")
    s.add_argument("--history", action="store_true", help="where it has ever been, not only now")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("verify", help="check every unit's history and references"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("pack", help="a handover pack for one unit: a page to read and print, the PDFs of the revisions that "
                       "apply to it, its record, and the SHA-256 of every file. It is recorded in the unit's history "
                       "as issued, with its number")
    s.add_argument("ref"); s.add_argument("folder", help="a new folder for the pack")
    s.add_argument("--on", default="", help="the unit as known on this date (default: now)")
    s.add_argument("--sign-with", default="", metavar="KEYFILE",
                   help="where packs are signed: the key file ssh-keygen signs with (a private key, or a security key's handle)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("pack-signing", help="whether handover packs are signed, and with which key (off by default)")
    s.add_argument("action", nargs="?", choices=("on", "off"), help="on: sign every pack from now on with --key; off: stop")
    s.add_argument("--key", default="", metavar="FILE.pub", help="the company's public key (ssh-ed25519 or sk-ssh-ed25519)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("packs", help="the handover packs issued for a unit")
    s.add_argument("ref"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("withdraw-pack", help="record that an issued handover pack is not to be relied on any more")
    s.add_argument("ref"); s.add_argument("number", type=int, help="the pack's number"); s.add_argument("--reason", required=True)
    s = sub.add_parser("transfer", help="hand a unit, and everything fitted in it, over to another workspace: a transfer "
                       "pack (the unit's next handover pack, with what the receiving workspace needs), recorded here; the "
                       "units take no more actions here")
    s.add_argument("ref"); s.add_argument("folder", help="a new folder for the transfer pack")
    s.add_argument("--to", required=True, help="who keeps the unit's record from now on: a company, a site, a workspace")
    s.add_argument("--sign-with", default="", metavar="KEYFILE",
                   help="where packs are signed: the key file ssh-keygen signs with (a private key, or a security key's handle)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("transfer-pack", help="write a transferred unit's transfer pack again, exactly as it was issued")
    s.add_argument("ref"); s.add_argument("folder", help="a new folder"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("receive", help="record the units of a transfer pack another workspace made for this one, with their "
                       "ids; the revisions they use stay that workspace's, kept here as references with their PDFs")
    s.add_argument("folder"); s.add_argument("--json", action="store_true")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--key", default="", metavar="FILE.pub", help="the pack must be signed by this key (the sender's)")
    g.add_argument("--fingerprint", default="", metavar="SHA256:...", help="the pack must be signed by the key with this "
                   "fingerprint")
    s = sub.add_parser("check-pack", help="is a handover pack whole? Needs no workspace; given the one it came from "
                       "(revisionworx -w FOLDER unit check-pack ...), also whether that workspace issued it")
    s.add_argument("folder"); s.add_argument("--json", action="store_true")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--key", default="", metavar="FILE.pub", help="the signature must be by this key (your supplier's)")
    g.add_argument("--fingerprint", default="", metavar="SHA256:...", help="the signature must be by the key with this "
                   "fingerprint")

    ch = sub.add_parser("change", help="which units an engineering change reaches, and where each one stands"
                        ).add_subparsers(dest="sub", required=True)
    s = ch.add_parser("candidates", help="units that hold a revision the change replaces, or one of its affected parts")
    s.add_argument("change", help="the ECO, by number or id"); s.add_argument("--json", action="store_true")
    s = ch.add_parser("decide", help="record which units the change reaches and how (written once)")
    s.add_argument("change"); s.add_argument("--kind", required=True, choices=EFFECTIVITY_KINDS)
    s.add_argument("--standard", default="", help="production: the build standard new units are built to")
    s.add_argument("--scope", default="", choices=SCOPE_RULES[:-1], help="how the units are chosen (default: candidates; "
                   "units for --kind unit)")
    s.add_argument("--value", default="", help="the standard, FIRST..LAST serials or customer the scope names")
    s.add_argument("--unit", action="append", default=[], help="a unit, for --scope units (repeatable)")
    s.add_argument("--reason", required=True); s.add_argument("--json", action="store_true")
    s = ch.add_parser("status", help="the decision and every unit in scope")
    s.add_argument("change"); s.add_argument("--json", action="store_true")
    s = ch.add_parser("add", help="a unit found later joins a retrofit")
    s.add_argument("change"); s.add_argument("ref"); s.add_argument("--reason", default="")
    s = ch.add_parser("set", help="schedule, defer, accept, decline, or mark not applicable")
    s.add_argument("ref"); s.add_argument("change"); s.add_argument("state", choices=effect.SETTABLE)
    s.add_argument("--reason", default=""); s.add_argument("--effective", default="")
    s = ch.add_parser("install", help="the change was made on the unit: its documents move to the new revisions")
    s.add_argument("ref"); s.add_argument("change")
    s.add_argument("--basis", default="reported", choices=BASES, help="reported (default) or verified if it was seen")
    s.add_argument("--effective", default=""); s.add_argument("--reason", default="")
    s.add_argument("--evidence", action="append", default=[], metavar="KIND:REF[=LABEL]")
    s = ch.add_parser("verify", help="a reported installation confirmed")
    s.add_argument("ref"); s.add_argument("change"); s.add_argument("--effective", default="")
    s.add_argument("--reason", default=""); s.add_argument("--evidence", action="append", default=[], metavar="KIND:REF[=LABEL]")
    s = ch.add_parser("reverse", help="the change was taken out again")
    s.add_argument("ref"); s.add_argument("change"); s.add_argument("--reason", required=True)
    s.add_argument("--effective", default=""); s.add_argument("--evidence", action="append", default=[], metavar="KIND:REF[=LABEL]")
    s = sub.add_parser("compliance", help="the changes that reach a unit, or every unit out of compliance")
    s.add_argument("ref", nargs="?", default=""); s.add_argument("--json", action="store_true")

    dv = sub.add_parser("deviation", help="approved departures from the documents for named units: a deviation before the "
                        "work, a concession for units already made").add_subparsers(dest="sub", required=True)
    s = dv.add_parser("new", help="record an approved deviation or concession and the units it covers (written once)")
    s.add_argument("--kind", required=True, choices=DEVIATION_KINDS)
    s.add_argument("--title", required=True); s.add_argument("--departure", required=True,
                                                             help="what differs from the documents, in words")
    s.add_argument("--reason", required=True, help="why it is acceptable")
    s.add_argument("--unit", action="append", default=[], required=True, help="a unit it covers (repeatable)")
    s.add_argument("--doc", action="append", default=[], metavar="DOC@REV", help="a revision it departs from (repeatable)")
    s.add_argument("--temporary", action="store_true", help="until the unit conforms again (cleared on each unit)")
    s.add_argument("--until", default="", metavar="DATE", help="a temporary one's end date")
    s.add_argument("--number", default="", help="its number (default: the next DEV-nnnn or CON-nnnn)")
    s.add_argument("--json", action="store_true")
    s = dv.add_parser("list", help="every deviation and concession"); s.add_argument("--json", action="store_true")
    s = dv.add_parser("show", help="one, and where it stands on each unit it covers")
    s.add_argument("deviation"); s.add_argument("--json", action="store_true")
    s = dv.add_parser("add", help="approve the same departure for one more unit")
    s.add_argument("deviation"); s.add_argument("ref"); s.add_argument("--reason", required=True)
    s = dv.add_parser("clear", help="a temporary departure has ended: the unit conforms again")
    s.add_argument("ref"); s.add_argument("deviation")
    s.add_argument("--event", action="append", type=int, default=[], metavar="N",
                   help="the unit's event that restored it, e.g. the right part fitted (repeatable)")
    s.add_argument("--basis", default="declared", choices=BASES); s.add_argument("--effective", default="")
    s.add_argument("--reason", default="")
    s = dv.add_parser("not-used", help="the unit was made or repaired without it after all")
    s.add_argument("ref"); s.add_argument("deviation"); s.add_argument("--reason", required=True)
    s.add_argument("--effective", default="")
    s = dv.add_parser("overdue", help="every unit with a temporary deviation past its end date (exit 1 if any)")
    s.add_argument("--json", action="store_true")
    return p


# ---------------------------------------------------------------------------------------------------- helpers
def _change_id(ws: Workspace, ref: str) -> str:
    if not ref:
        return ""
    from .. import changes
    from .model import is_id
    if is_id("change", ref):
        return ref
    found = changes.service_for(ws).find_by_number(ref)
    if found is None:
        raise ValidationError(f"no change order {ref!r}")
    if not found.id.startswith("eco_"):
        raise ValidationError(f"{ref} is a change request; units carry out change orders (ECO)")
    return found.id


def _meta(ws: Workspace, a: argparse.Namespace) -> dict:
    evidence = parse_evidence(a.evidence)
    return {"effective": a.effective, "basis": a.basis, "reason": a.reason, "change": _change_id(ws, a.change),
            "evidence": evidence}


def _print_unit(out, svc: UnitService, detail: dict) -> None:
    flags = {"newer-released": "a later revision is released, not applied to this unit",
             "not-released": "not released", "obsolete": "obsolete", "missing": "MISSING: this reference is damaged",
             "foreign": "kept from another workspace"}
    out.write(f"{detail['label']}  {detail['name']}  [{detail['status']}]\n  id {detail['id']}   class {detail['class']}"
              f"   model {detail['model']['number'] or '-'}\n")
    for i in detail["identifiers"]:
        out.write(f"  {i['scheme']} {i['value']}{' (' + i['issuer'] + ')' if i['issuer'] else ''}"
                  f"{'' if i['active'] else ' (former)'}\n")
    where = [f"{k} {detail[k]}" for k in ("customer", "site", "location") if detail[k]]
    if detail["project"]:
        where.insert(0, f"project {detail['project']['number'] or detail['project']['name']}")
    if where:
        out.write("  " + "   ".join(where) + "\n")
    if detail["parent"]:
        out.write(f"  fitted in {detail['parent']['label']} at {detail['parent']['position']}\n")
    if detail.get("merged_into"):
        m = detail["merged_into"]
        out.write(f"  MERGED into {m['label']} ({m['unit']}) on {m['time'][:10]}, event {m['event']}: record on it\n")
    for m in detail.get("merged_from", []):
        out.write(f"  {m['label']} ({m['unit']}) was merged into this unit on {m['time'][:10]}\n")
    for r in detail.get("received", []):
        out.write(f"  received from {r['from']['name'] or r['from']['workspace']} on {r['time'][:10]}, event {r['event']}"
                  + (f", signed with the key {r['key']}" if r["key"] else ", unsigned") + "\n")
    if detail.get("transferred"):
        t = detail["transferred"]
        out.write(f"  TRANSFERRED to {t['to']} on {t['time'][:10]}, event {t['event']}: its record is kept there now\n")
    for name, m in detail["milestones"].items():
        out.write(f"  {name} {m['date'] or '(date unknown)'}\n")
    if "as_of" in detail:
        a = detail["as_of"]
        out.write(f"  AS OF {'event ' + str(a['event']) if 'event' in a else a['date']}"
                  f"{'; recorded out of order: events ' + ', '.join(map(str, a['late'])) if a.get('late') else ''}\n")
    std = detail["standard"]
    out.write(f"  build standard {std['number'] + ' ' + std['title'] if std else '(none)'}\n")
    if not detail["configuration_recorded"]:
        out.write("  documents: configuration not recorded\n")
    else:
        out.write("  documents:\n")
        for r in detail["documents"]:
            name = r["document"]["number"] or r["document"]["id"]
            if not r["applies"]:
                what = "doesn't apply to this unit"
            elif r["revision"] is None:
                what = "?"
            else:
                what = f"rev {r['revision']['label']} ({r['revision']['state_name']})"
            src = "standard" if r["source"] == "standard" else f"this unit, event {r['event']}"
            notes = "; ".join(f"from {r['foreign']['name'] or r['foreign']['workspace']}'s workspace, kept here as a reference"
                              if f == "foreign" else
                              flags[f] + (f" (rev {r['released']['label']})" if f == "newer-released" and r["released"] else "")
                              for f in r["flags"])
            out.write(f"    {name:<16} {what:<28} {src}{'   ! ' + notes if notes else ''}\n")
    if detail.get("changes"):
        out.write("  engineering changes:\n")
        for c in detail["changes"]:
            out.write(f"    {c['number']:<12} {c['kind']:<18} {c['state']:<15} since {c['since']}"
                      f"{'   OUTSTANDING' if c['outstanding'] else ''}\n")
    if detail.get("deviations"):
        out.write("  deviations and concessions:\n")
        for x in detail["deviations"]:
            when = ("temporary" + (f" until {x['until']}" if x["until"] else "")) if x["temporary"] else "permanent"
            out.write(f"    {x['number']:<12} {x['kind']:<11} {x['state']:<10} {when:<26} since {x['since']}"
                      f"{'   PAST ITS END DATE' if x['overdue'] else ''}  {x['title']}\n")
    if detail["components"]:
        out.write("  components:\n")
        for c in detail["components"]:
            what = c["label"] or c["number"] or c["part"]
            extra = "".join(f" {k} {c[k]}" for k in ("serial", "lot") if c[k])
            out.write(f"    {c['position']:<10} {what}{extra}{'  since ' + c['since'] if c['since'] else ''}\n")
    if detail.get("factory_lots"):
        shared = max(x["units"] for x in detail["factory_lots"])
        out.write("  built from these lots" + (f" (shared by the {shared} units built together: this unit holds some of "
                                               "them, not necessarily all)" if shared > 1 else "") + ":\n")
        for x in detail["factory_lots"]:
            out.write(f"    {x['number']:<12} lot {x['lot']}{'  ' + x['quantity'] if x['quantity'] else ''}"
                      f"{' [' + x['basis'] + ']' if x['basis'] != 'declared' else ''}\n")
    if "history" in detail:
        out.write("  history:\n")
        for e in detail["history"]:
            who = e["actor"].get("name") or e["actor"].get("account") or e["actor"].get("user", "?")
            out.write(f"    {e['n']:>3} {e['time'][:16]} {who}: {e['summary']}"
                      f"{' [' + e['basis'] + ']' if e['basis'] != 'declared' else ''}{' (VOID)' if e['voided'] else ''}"
                      f"{' - ' + e['reason'] if e['reason'] else ''}\n")


def _units(svc: UnitService, refs: list[str]) -> list[str]:
    return [svc.resolve(r)["id"] for r in refs]


# ----------------------------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    a = build_parser(prog or "python -m revisionworx.units").parse_args(argv)
    out = sys.stdout
    sub = getattr(a, "sub", None)
    writable = a.cmd not in READS and not (a.cmd in ("standard", "numbering") and sub in ("list", "show", "next")) and \
        not (a.cmd == "import" and a.dry_run) and not (a.cmd == "change" and sub in CHANGE_READS) and \
        not (a.cmd == "deviation" and sub in DEVIATION_READS) and not (a.cmd == "pack-signing" and a.action is None)
    try:
        if a.cmd == "check-pack":                    # a pack is checked anywhere; the workspace '-' is none
            return _check_pack(a, out)
        if a.cmd == "pack":                          # put together on a read-only handle, then recorded
            return _make_pack(a, out)
        if a.cmd == "transfer":                      # the same: put together, then recorded
            return _transfer(a, out)
        with Workspace.open(Path(a.workspace), writable=writable) as ws:
            svc = service_for(ws)
            return _dispatch(a, ws, svc, out)
    except CommittedNotApplied as exc:
        sys.stderr.write(f"saved; the workspace finishes recording it when next opened for writing ({exc})\n")
        return 0
    except csvio.ImportProblems as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except (UnitError, WorkspaceError, OSError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


def _dispatch(a: argparse.Namespace, ws: Workspace, svc: UnitService, out) -> int:
    cmd = a.cmd
    if cmd == "list":
        project = svc.project_id(a.project) if a.project else ""
        rows = [query.unit_summary(u) for u in query.search(svc, a.text, project)]
        if a.json:
            out.write(json.dumps({"units": rows}, indent=1, ensure_ascii=False) + "\n")
        else:
            for r in rows:
                out.write(f"{r['label']:<20} {r['status']:<15} {r['model']['number']:<14} {r['name']}\n")
            out.write(f"{len(rows)} unit(s)\n")
        return 0
    if cmd in ("show", "history"):
        unit = svc.resolve(a.ref)
        if cmd == "history":
            from . import history as H, transfer
            detail = query.unit_detail(svc, unit)
            came = H.receipts(svc.events(unit["id"]))
            # what other workspaces recorded before it came here: the history the latest receipt brought (section 16)
            before = [c for c in (transfer.read_history(ws, came[-1]["history"])["custodians"] if came else [])
                      if c["workspace"] != ws.id]          # its own earlier record is the start of what follows
            if a.json:
                out.write(json.dumps(detail["history"] if not before else
                                     {"before": before, "here": detail["history"]}, indent=1, ensure_ascii=False) + "\n")
                return 0
            for c in before:
                out.write(f"recorded in {c['name'] or c['workspace']}, before it came here:\n")
                for e in c["history"]:
                    out.write(f"{e['n']:>3} {e['time']} {e['effective'] or '':<10} {e['basis']:<8} {e['summary']}"
                              f"{' (VOID)' if e['voided'] else ''}\n")
            if before:
                out.write("recorded here:\n")
            for e in detail["history"]:
                out.write(f"{e['n']:>3} {e['time']} {e['effective'] or '':<10} {e['basis']:<8} {e['summary']}"
                          f"{' (VOID)' if e['voided'] else ''}\n")
            return 0
        detail = query.unit_detail(svc, unit, history=not a.no_history, at=a.at, on=a.on or "")
        if a.json:
            out.write(json.dumps(detail, indent=1, ensure_ascii=False) + "\n")
        else:
            _print_unit(out, svc, detail)
        return 0
    if cmd == "verify":
        problems = query.verify(svc)
        if a.json:
            out.write(json.dumps({"problems": problems}, indent=1) + "\n")
        else:
            for p in problems:
                out.write(f"{p['code']}: {p['message']}\n")
            out.write(f"{len(svc.ws.list('unit'))} unit(s) checked, {len(problems)} problem(s)\n")
        return 1 if problems else 0
    if cmd == "where-used":
        return _where_used(a, svc, out)
    if cmd == "receive":
        return _receive(a, ws, svc, out)
    if cmd == "transfer-pack":
        from . import transfer
        made = transfer.write_again(svc, a.ref, Path(a.folder))
        if a.json:
            out.write(json.dumps(made, indent=1, ensure_ascii=False) + "\n")
        else:
            out.write(f"the transfer pack of {made['unit']} for {made['to']}, as it was issued, in {made['path']}: "
                      f"{made['files']} file(s)\n")
        return 0
    if cmd == "packs":
        unit = svc.resolve(a.ref)
        rows = svc.packs(unit["id"])
        if a.json:
            out.write(json.dumps({"unit": unit_label(unit), "packs": rows}, indent=1, ensure_ascii=False) + "\n")
            return 0
        for r in rows:
            who = r["actor"].get("name") or r["actor"].get("account") or r["actor"].get("user") or ""
            shows = f"as known on {r['as_of']}" if r["as_of"] else "as it stood then"
            state = "in effect" if r["withdrawn"] is None else \
                f"withdrawn {r['withdrawn']['time'][:10]}: {r['withdrawn']['reason']}"
            out.write(f"pack {r['pack']:<3} {r['time'][:10]} {who:<20} {shows:<26} {state}\n")
        out.write(f"{len(rows)} handover pack(s) of {unit_label(unit)}\n")
        return 0
    if cmd == "pack-signing":
        return _pack_signing(a, ws, out)
    if cmd == "merge":
        dup, keep = svc.resolve(a.duplicate), svc.resolve(a.into)
        label = unit_label(dup)
        u = svc.merge(dup["id"], keep["id"], **_meta(ws, a))
        moved = ", ".join(f"{i['scheme']} {i['value']}" for i in dup["identifiers"]) or "no identifiers"
        out.write(f"merged {label} ({dup['id']}) into {unit_label(u)}: it has {moved} now, and {dup['id']} finds it too. "
                  f"The duplicate's history stays readable ('unit history {dup['id']}'); voiding the merge undoes it\n")
        return 0
    if cmd == "withdraw-pack":
        unit = svc.resolve(a.ref)
        svc.withdraw_pack(unit["id"], a.number, a.reason)
        out.write(f"handover pack {a.number} of {unit_label(unit)} withdrawn: checking it now says so\n")
        return 0
    if cmd == "export":
        from ..bom.fileio import write_file
        write_file(Path(a.file), csvio.units_to_csv(svc).encode("utf-8-sig"), overwrite=a.overwrite)
        out.write(f"wrote {a.file}\n")
        return 0
    if cmd == "standard":
        return _standard(a, ws, svc, out)
    if cmd == "change":
        return _change(a, ws, svc, out)
    if cmd == "numbering":
        if a.sub == "new":
            n = svc.create_numbering(a.name, a.pattern, issuer=a.issuer, start=a.start)
            out.write(f"numbering {n['name']}: next serial {svc.next_serial_preview(n['id'])}\n")
        elif a.sub == "next":
            out.write(svc.next_serial_preview(a.name, a.on) + "\n")
        else:
            rows = svc.numberings()
            if a.json:
                out.write(json.dumps({"numberings": rows}, indent=1, ensure_ascii=False) + "\n")
            for n in ([] if a.json else rows):
                out.write(f"{n['name']:<16} {n['pattern']:<24} {n['issuer'] or '(yours)':<16} next "
                          f"{svc.next_serial_preview(n['id'])}\n")
        return 0
    if cmd == "compliance":
        return _compliance(a, svc, out)
    if cmd == "deviation":
        return _deviation(a, svc, out)
    if cmd == "import":
        text = Path(a.file).read_text(encoding="utf-8-sig")
        if a.dry_run:
            specs = csvio.plan_import(svc, text, fieldworx_ids=a.fwx, status=a.status)
            out.write(f"{len(specs)} unit(s) would be registered; nothing was written\n")
            return 0
        made = csvio.import_register(svc, text, fieldworx_ids=a.fwx, status=a.status, reason=a.reason)
        out.write(f"registered {len(made)} unit(s)\n")
        return 0
    if cmd == "new":
        return _new(a, ws, svc, out)
    meta = _meta(ws, a) if hasattr(a, "evidence") else {}
    if cmd == "void":
        u = svc.void(svc.resolve(a.ref)["id"], a.events, a.reason)
    elif cmd == "update":
        fields = {k: getattr(a, k) for k in ("name", "customer", "site", "location", "notes") if getattr(a, k) is not None}
        if a.model is not None:
            fields["model"] = {"part": "", "number": a.model}
        if a.project is not None:
            fields["project"] = a.project
        if a.cls is not None:
            fields["class"] = a.cls
        u = svc.update(svc.resolve(a.ref)["id"], **fields, **meta)
    elif cmd == "id":
        uid = svc.resolve(a.ref)["id"]
        fn = {"add": svc.add_identifier, "retire": svc.retire_identifier, "remove": svc.remove_identifier}[a.action]
        u = fn(uid, a.scheme, a.value, a.issuer, **meta)
    elif cmd == "status":
        u = svc.set_status(svc.resolve(a.ref)["id"], a.status, **meta)
    elif cmd == "milestone":
        meta.pop("effective")
        u = svc.milestone(svc.resolve(a.ref)["id"], a.milestone, a.on, **meta)
    elif cmd == "note":
        u = svc.note(svc.resolve(a.ref)["id"], a.text, **meta)
    elif cmd == "conform":
        std = "" if a.standard.casefold() == "none" else svc.resolve_standard(a.standard)["id"]
        made = svc.set_standard(_units(svc, a.refs), std, clear_differences=a.clear, **meta)
        for u in made:
            out.write(f"{unit_label(u)}: {'build standard ' + a.standard if std else 'no build standard'}\n")
        return 0
    elif cmd == "doc":
        uid = svc.resolve(a.ref)["id"]
        if a.not_applicable or a.reset:
            did = svc.document_id(a.document)
            u = svc.reset_document(uid, did, **meta) if a.reset else svc.set_document(uid, did, "", **meta)
        else:
            did, rid = svc.doc_rev(a.document)
            u = svc.set_document(uid, did, rid, **meta)
    elif cmd == "fit":
        comp = svc.resolve(a.component)["id"] if a.component else ""
        u = svc.install(svc.resolve(a.ref)["id"], a.position, number=a.part_number, component=comp, serial=a.serial,
                        lot=a.lot, replace=a.replace, disposition=a.disposition, **meta)
    elif cmd == "remove":
        u = svc.remove(svc.resolve(a.ref)["id"], a.position, a.disposition, **meta)
    else:  # pragma: no cover
        raise ValidationError(f"unknown command {cmd}")
    out.write(f"{unit_label(u)}: event {u['events']['count']} recorded\n")
    return 0


def _new(a: argparse.Namespace, ws: Workspace, svc: UnitService, out) -> int:
    idents = []
    if a.serial:
        idents.append({"scheme": "serial", "value": a.serial, "issuer": a.issuer})
    if a.tag:
        idents.append({"scheme": "tag", "value": a.tag, "issuer": a.tag_issuer})
    if a.other:
        idents.append({"scheme": "other", "value": a.other, "issuer": ""})
    if not idents and not a.serial_from:
        raise ValidationError("give the unit a --serial (or --serial-from a numbering), --tag or --other code, so it can be "
                              "found")
    docs = {}
    for ref in a.doc:
        did, rid = svc.doc_rev(ref)
        docs[did] = rid
    spec = {"identifiers": idents, "class": a.cls, "name": a.name, "model": {"part": "", "number": a.model},
            "status": a.status, "project": svc.project_id(a.project) if a.project else "", "customer": a.customer,
            "site": a.site, "location": a.location, "notes": a.notes,
            "standard": svc.resolve_standard(a.standard)["id"] if a.standard else "", "documents": docs,
            "milestones": {m: getattr(a, m) for m in MILESTONES if getattr(a, m)}, "serial_from": a.serial_from}
    u = svc.register(**spec, **_meta(ws, a))
    if a.json:
        out.write(json.dumps(query.unit_detail(svc, u), indent=1, ensure_ascii=False) + "\n")
    else:
        out.write(f"registered {unit_label(u)} as {u['id']}\n")
    return 0


def _change(a: argparse.Namespace, ws: Workspace, svc: UnitService, out) -> int:
    def dump(value: Any) -> int:
        out.write(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
        return 0

    if a.sub == "candidates":
        o = effect.order(svc, a.change)
        found = effect.candidates(svc, o)
        if a.json:
            return dump({"change": o.id, "number": o.number, "rows": effect.order_rows(o),
                         "candidates": [{"unit": query.unit_summary(c["unit"]), "why": c["why"]} for c in found]})
        for c in found:
            u = c["unit"]
            out.write(f"{unit_label(u):<20} {u['status']:<15} {u['customer']:<20} {'; '.join(c['why'])}\n")
        out.write(f"{len(found)} candidate(s) for {o.number} among the units registered in this workspace\n")
        return 0
    if a.sub == "status":
        st = effect.change_status(svc, a.change)
        if a.json:
            return dump(st)
        eff = st["effectivity"]
        out.write(f"{st['number']}  {st['title']}  [{st['status'].replace('_', ' ')}]\n")
        if eff is None:
            out.write("  no effectivity decision yet ('unit change decide')\n")
            return 0
        scope = eff["scope"]["rule"] + (f" {eff['scope']['value']}" if eff["scope"]["value"] else "")
        out.write(f"  {eff['kind']}, scope {scope}, decided {eff['decided'][:10]}: {eff['reason']}\n")
        names = query._Names(svc)
        for r in eff["documents"]:
            out.write(f"  {names('document', r['document']):<16} rev {names('revision', r['from'])} -> "
                      f"rev {names('revision', r['to'])}\n")
        if eff["standard"]:
            out.write(f"  new units: build standard {names('standard', eff['standard'])}\n")
        for u in st["units"]:
            out.write(f"  {u['label']:<20} {u['state']:<15} since {u['since']}{'   OUTSTANDING' if u['outstanding'] else ''}\n")
        if st["units"]:
            out.write(f"  {len(st['units'])} unit(s), {st['outstanding']} outstanding\n")
        return 0
    if a.sub == "decide":
        eff = effect.decide(svc, _change_id(ws, a.change) or a.change, a.kind, standard=a.standard, rule=a.scope,
                            value=a.value, units=a.unit, reason=a.reason)
        if a.json:
            return dump(eff)
        what = {"production": "new units only", "documentation": f"{len(eff['units'])} unit(s) corrected",
                }.get(eff["kind"], f"{len(eff['units'])} unit(s) in scope")
        out.write(f"recorded: {a.change} is {eff['kind']} ({what})\n")
        return 0
    if a.sub == "add":
        rec = effect.add(svc, a.change, a.ref, reason=a.reason)
    elif a.sub == "set":
        rec = effect.set_state(svc, a.ref, a.change, a.state, reason=a.reason, effective=a.effective)
    elif a.sub == "install":
        rec = effect.install(svc, a.ref, a.change, basis=a.basis, effective=a.effective, reason=a.reason,
                             evidence=parse_evidence(a.evidence))
    elif a.sub == "verify":
        rec = effect.verify(svc, a.ref, a.change, effective=a.effective, reason=a.reason, evidence=parse_evidence(a.evidence))
    elif a.sub == "reverse":
        rec = effect.reverse(svc, a.ref, a.change, reason=a.reason, effective=a.effective,
                             evidence=parse_evidence(a.evidence))
    else:  # pragma: no cover
        raise ValidationError(f"unknown command change {a.sub}")
    out.write(f"{unit_label(svc.unit(rec['unit']))}: {a.change} {rec['state']}\n")
    return 0


def _compliance(a: argparse.Namespace, svc: UnitService, out) -> int:
    if a.ref:
        u = svc.resolve(a.ref)
        rows = effect.unit_changes(svc, u["id"])
        if a.json:
            out.write(json.dumps({"unit": u["id"], "changes": rows}, indent=1, ensure_ascii=False) + "\n")
            return 0
        for c in rows:
            out.write(f"{c['number']:<12} {c['kind']:<18} {c['state']:<15} since {c['since']}"
                      f"{'   OUTSTANDING' if c['outstanding'] else ''}  {c['title']}\n")
        out.write(f"{unit_label(u)}: {len(rows)} change(s), {sum(c['outstanding'] for c in rows)} outstanding\n")
        return 0
    rows = effect.out_of_compliance(svc)
    if a.json:
        out.write(json.dumps({"out_of_compliance": rows}, indent=1, ensure_ascii=False) + "\n")
        return 0
    for r in rows:
        out.write(f"{r['label']:<20} " + ", ".join(f"{c['number']} {c['state']}" for c in r["changes"]) + "\n")
    out.write(f"{len(rows)} unit(s) out of compliance with a required change\n")
    return 1 if rows else 0


def _deviation(a: argparse.Namespace, svc: UnitService, out) -> int:
    def dump(value: Any) -> int:
        out.write(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
        return 0

    if a.sub == "list":
        rows = deviation.listing(svc)
        if a.json:
            return dump({"deviations": rows})
        for d in rows:
            when = ("temporary" + (f" until {d['until']}" if d["until"] else "")) if d["temporary"] else "permanent"
            late = f", {d['overdue']} past its end date" if d["overdue"] else ""
            out.write(f"{d['number']:<12} {d['kind']:<11} {when:<26} in effect on {d['in_effect']} of {d['units']}{late}"
                      f"  {d['title']}\n")
        out.write(f"{len(rows)} deviation(s) and concession(s)\n")
        return 0
    if a.sub == "show":
        st = deviation.status(svc, a.deviation)
        if a.json:
            return dump(st)
        when = ("temporary" + (f", until {st['until']}" if st["until"] else "")) if st["temporary"] else "permanent"
        out.write(f"{st['number']}  {st['kind']}, {when}: {st['title']}\n  approved {st['approved'][:10]} by "
                  f"{st['approved_by'].get('name') or st['approved_by'].get('account') or st['approved_by'].get('user', '?')}\n"
                  f"  departure: {st['departure']}\n  reason: {st['reason']}\n")
        for r in st["documents"]:
            out.write(f"  departs from {r['number']} rev {r['label']}\n")
        for u in st["covered"]:
            out.write(f"    {u['label']:<20} {u['state']:<10} since {u['since']}{'   PAST ITS END DATE' if u['overdue'] else ''}"
                      f"{'  - ' + u['reason'] if u['reason'] else ''}\n")
        return 0
    if a.sub == "overdue":
        rows = deviation.overdue_units(svc)
        if a.json:
            dump({"overdue": rows})
            return 1 if rows else 0
        for r in rows:
            out.write(f"{r['label']:<20} " + ", ".join(f"{d['number']} (until {d['until']})" for d in r["deviations"]) + "\n")
        out.write(f"{len(rows)} unit(s) with a temporary deviation past its end date\n")
        return 1 if rows else 0
    if a.sub == "new":
        dev = deviation.approve(svc, a.kind, title=a.title, departure=a.departure, reason=a.reason, units=a.unit,
                                documents=a.doc, temporary=a.temporary, until=a.until, number=a.number)
        if a.json:
            return dump({k: v for k, v in dev.items() if k != "_rev"})
        out.write(f"recorded: {dev['number']} ({dev['kind']}) for {len(dev['units'])} unit(s)\n")
        return 0
    if a.sub == "add":
        rec = deviation.add(svc, a.deviation, a.ref, reason=a.reason)
    elif a.sub == "clear":
        rec = deviation.clear(svc, a.ref, a.deviation, events=a.event, basis=a.basis, effective=a.effective, reason=a.reason)
    else:
        rec = deviation.not_used(svc, a.ref, a.deviation, reason=a.reason, effective=a.effective)
    out.write(f"{a.deviation} on {a.ref}: {rec['state'].replace('-', ' ')}\n")
    return 0


def _standard(a: argparse.Namespace, ws: Workspace, svc: UnitService, out) -> int:
    def show(std: dict) -> None:
        out.write(f"{std['number']}  {std['title']}\n  id {std['id']}   model {std['model']['number'] or '-'}\n")
        for doc, rev in sorted(std["documents"].items(), key=lambda kv: query._Names(svc)("document", kv[0])):
            names = query._Names(svc)
            out.write(f"  {names('document', doc):<16} rev {names('revision', rev)}\n")
    if a.sub == "list":
        stds = svc.standards()
        if a.json:
            out.write(json.dumps({"standards": stds}, indent=1, ensure_ascii=False) + "\n")
        else:
            for s in stds:
                out.write(f"{s['number']:<20} {len(s['documents']):>4} document(s)  {s['title']}\n")
        return 0
    if a.sub == "show":
        std = svc.resolve_standard(a.standard)
        if a.json:
            users = query.units_on_standard(svc, std["id"])
            out.write(json.dumps({"standard": std, "units": users}, indent=1, ensure_ascii=False) + "\n")
        else:
            show(std)
            out.write(f"  {len(query.units_on_standard(svc, std['id']))} unit(s) conform to it\n")
        return 0
    docs: dict[str, str] = {}
    skipped: list[str] = []
    if a.from_project:
        from .. import documents as D
        pid = svc.project_id(a.from_project)
        for d in D.list_documents(ws, pid):
            if d.get("released_rev_id"):
                docs[d["id"]] = d["released_rev_id"]
            else:
                skipped.append(d.get("number") or d["id"])
    for ref in a.doc:
        did, rid = svc.doc_rev(ref)
        docs[did] = rid
    std = svc.create_standard(a.number, a.title, docs, model={"part": "", "number": a.model}, based_on=(
        svc.resolve_standard(a.based_on)["id"] if a.based_on else ""), change=_change_id(ws, a.change), notes=a.notes,
        allow_unreleased=a.allow_unreleased)
    if a.json:
        out.write(json.dumps({"standard": std, "skipped": skipped}, indent=1, ensure_ascii=False) + "\n")
    else:
        out.write(f"build standard {std['number']} names {len(std['documents'])} document revision(s)\n")
        if skipped:
            out.write("left out, with nothing released: " + ", ".join(skipped) + "\n")
    return 0


def _where_used(a: argparse.Namespace, svc: UnitService, out) -> int:
    if a.revision:
        did, rid = svc.doc_rev(a.revision)
        rows = query.units_using_revision(svc, rid)
    elif a.document:
        rows = query.units_using_document(svc, svc.document_id(a.document))
    elif a.standard:
        rows = [{"unit": u} for u in query.units_on_standard(svc, svc.resolve_standard(a.standard)["id"])]
    else:
        kwargs = {"history": a.history}
        if a.part_number:
            kwargs["number"] = a.part_number
        elif a.serial:
            kwargs["serial"] = a.serial
        elif a.lot:
            pn, sep, lot = a.lot.rpartition("@")
            if not sep:
                raise ValidationError("--lot looks like PART-NUMBER@LOT")
            kwargs.update(number=pn, lot=lot)
        else:
            kwargs["component"] = svc.resolve(a.component, follow=True)["id"]
        rows = query.units_with_component(svc, **kwargs)
    if a.json:
        out.write(json.dumps({"rows": rows, "coverage": "every unit in this workspace's register"}, indent=1,
                             ensure_ascii=False) + "\n")
        return 0
    for r in rows:
        u = r["unit"]
        extra = ""
        if r.get("built_from"):
            n = r["built_from"]["units"]
            extra = f" built from lot {r['lot']}" + (f" (with {n - 1} other unit{'s' if n > 2 else ''})" if n > 1 else "")
        elif "position" in r:
            extra = f" at {r['position']}" + ("" if r.get("current") else f" (removed, event {r['removed']})")
        elif "revision" in r:
            extra = f" rev {r['revision']['label']}" if r.get("revision") else " (doesn't apply)"
        out.write(f"{u['label']:<20} {u['status']:<15}{extra}\n")
    out.write(f"{len(rows)} found among the units registered in this workspace (units recorded nowhere else can't be "
              "searched)\n")
    return 0


def _make_pack(a: argparse.Namespace, out) -> int:
    """Put the pack together on a read-only handle (exporting PDFs can take a while, and nobody need wait for it), then record
    it on a writable one, which refuses if anything was recorded on the unit in between. Nothing is left if it isn't."""
    from . import pack
    with Workspace.open(Path(a.workspace)) as ws:
        draft = pack.build(service_for(ws), a.ref, Path(a.folder), on=a.on, sign_with=Path(a.sign_with) if a.sign_with else None)
    with draft:
        with Workspace.open(Path(a.workspace), writable=True) as ws:
            made = pack.issue(service_for(ws), draft)
    if a.json:
        out.write(json.dumps(made, indent=1, ensure_ascii=False) + "\n")
        return 0
    out.write(f"handover pack {made['pack']} of {made['unit']} in {made['path']}: {made['files']} file(s), "
              f"{made['pdfs']} PDF(s)\n")
    for m in made["missing"]:
        out.write(f"  not included: {m['document']} rev {m['revision']}: {m['missing']}\n")
    out.write("  recorded in its history as issued" + (" (the workspace finishes recording it when next opened for writing)"
                                                      if made["pending"] else "")
              + (f"; signed with the key {made['key']}\n" if made["signed"] else "; not signed\n"))
    return 0


def _transfer(a: argparse.Namespace, out) -> int:
    from . import transfer
    with Workspace.open(Path(a.workspace)) as ws:
        draft = transfer.build(service_for(ws), a.ref, Path(a.folder), to=a.to,
                               sign_with=Path(a.sign_with) if a.sign_with else None)
    with draft:
        with Workspace.open(Path(a.workspace), writable=True) as ws:
            made = transfer.issue(service_for(ws), draft)
    if a.json:
        out.write(json.dumps(made, indent=1, ensure_ascii=False) + "\n")
        return 0
    others = made["units"][1:]
    out.write(f"transfer pack of {made['unit']} for {made['to']} in {made['path']} (its handover pack {made['pack']}): "
              f"{made['files']} file(s), {made['pdfs']} PDF(s)" + (f"; with {', '.join(others)} fitted in it" if others else "")
              + "\n")
    for m in made["missing"]:
        out.write(f"  not included: {m['document']} rev {m['revision']}: {m['missing']}\n")
    out.write(f"  recorded here as transferred: {'they take' if others else 'it takes'} no more actions in this workspace"
              + (" (the workspace finishes recording it when next opened for writing)" if made["pending"] else "")
              + (f"; signed with the key {made['key']}\n" if made["signed"] else "; not signed\n")
              + "  the workspace that keeps it now records it with: revisionworx unit receive FOLDER\n")
    return 0


def _receive(a: argparse.Namespace, ws: Workspace, svc: UnitService, out) -> int:
    from .. import sshsig
    from . import transfer
    key = a.fingerprint
    if a.key:
        try:
            key = sshsig.parse_public_key(Path(a.key).read_text("utf-8")).fingerprint
        except sshsig.SignatureError as exc:
            raise ValidationError(f"{a.key}: {exc}") from None
    got = transfer.receive(svc, Path(a.folder), key=key)
    if a.json:
        out.write(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
        return 0
    who = got["from"]["name"] or got["from"]["workspace"]
    out.write(("already received: " if got["already"] else "received: ") + ", ".join(got["units"]) + f" from {who}"
              + (f", signed with the key {got['key']}" if got["signed"] else ", NOT SIGNED") + "\n")
    if got["signed"] and not key:
        out.write("anyone can make a key: next time give the sender's key with --key, or compare this one with theirs\n")
    if not got["signed"]:
        out.write(f"the pack is whole, but nothing shows it came from {who}: check that another way\n")
    out.write("the revisions they use stay that workspace's: kept here as references, with their PDFs\n")
    return 0


def _pack_signing(a: argparse.Namespace, ws: Workspace, out) -> int:
    from . import pack
    if a.action == "on" and not a.key:
        raise ValidationError("give the company's public key: pack-signing on --key FILE.pub")
    if a.action is None and a.key:
        raise ValidationError("--key goes with 'on'")
    if a.action == "on":
        state = pack.set_signing(ws, Path(a.key).read_text("utf-8"))
    elif a.action == "off":
        state = pack.set_signing(ws, None)
    else:
        state = pack.signing(ws)
    if a.json:
        out.write(json.dumps({"enabled": state["enabled"], "fingerprint": state["fingerprint"],
                              "key": state["key"].openssh() if state["key"] else "",
                              "allowed_signers": pack.allowed_signers(state["key"]) if state["key"] else "",
                              "changed": state["changed"], "changed_by": state["changed_by"]}, indent=1) + "\n")
        return 0
    if not state["enabled"]:
        out.write("handover packs aren't signed in this workspace (each one is still recorded as issued)\n")
        return 0
    key = state["key"]
    out.write(f"every handover pack is signed with the key {key.fingerprint} ({key.type})\n"
              "give your customers this fingerprint, or this line for an OpenSSH allowed_signers file:\n"
              f"  {pack.allowed_signers(key)}\n")
    return 0


def _check_pack(a: argparse.Namespace, out) -> int:
    from .. import sshsig
    from . import pack
    key = a.fingerprint
    if a.key:
        try:
            key = sshsig.parse_public_key(Path(a.key).read_text("utf-8")).fingerprint
        except sshsig.SignatureError as exc:
            raise ValidationError(f"{a.key}: {exc}") from None
    if a.workspace == "-":
        report = pack.check(Path(a.folder), key=key)
    else:
        with Workspace.open(Path(a.workspace)) as ws:
            report = pack.check(Path(a.folder), service_for(ws), key=key)
    if a.json:
        out.write(json.dumps(report, indent=1, ensure_ascii=False) + "\n")
        return 0 if report["ok"] else 1
    reg = report["register"]
    for problem in report["problems"]:
        if reg is None or problem != reg["message"]:
            out.write(f"{problem}\n")
    what = f"the handover pack of {report['unit']}" if report["unit"] else "this folder"
    if report["pack"]:
        what += f" (pack {report['pack']})"
    whole = len(report["problems"]) - (1 if reg is not None and reg["state"] != "issued" else 0)
    sig = report["signed"]
    out.write(f"{what}: {report['files']} file(s), " + ("whole" if not whole else f"{whole} problem(s)")
              + (f"; signed with the key {sig['key']}" + (" (a security key, touched)" if sig["security_key"] else "")
                 if sig else "; not signed") + "\n")
    if sig and not key and reg is None:
        out.write("anyone can make a key: compare that one with the key your supplier gave you, or give it with --key\n")
    if reg is not None:
        out.write(("issued: " if reg["state"] == "issued" else "NOT CONFIRMED: ") + reg["message"] + "\n")
    elif report["pack"]:
        out.write("not checked against the register of the workspace that made it: to confirm it issued this pack, run "
                  "'revisionworx -w WORKSPACE unit check-pack FOLDER'\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
