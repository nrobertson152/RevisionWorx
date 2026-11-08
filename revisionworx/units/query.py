# SPDX-License-Identifier: GPL-3.0-or-later
"""Questions about units: which documents apply to one, which units use a revision, a part, a serial or a lot, what a unit
looked like on a date, and whether the register is intact (docs/UNITS.md sections 5 and 7).

Every answer is read from the journal through :class:`~revisionworx.units.service.UnitService`; nothing here writes.
"""
from __future__ import annotations

from typing import Any, Callable

from .. import documents as D
from . import history as H
from .model import (MERGED, REQUIRED_KINDS, MalformedUnitRecord, NotFound, PERMANENT_SCHEMES, STANDARD_SCHEME, check_date,
                    day_of, effectivity_digest, is_id, key_id, load_compliance, load_effectivity, load_event, load_key,
                    load_numbering, load_standard, load_unit, norm, position_key, standard_digest, unit_label)
from .service import KIND, UnitService


class _Names:
    """Ids to the numbers and labels people use, read once each."""

    def __init__(self, svc: UnitService):
        self.svc, self.ws = svc, svc.ws
        self._cache: dict[tuple[str, str], Any] = {}

    def _get(self, kind: str, rid: str) -> dict | None:
        key = (kind, rid)
        if key not in self._cache:
            try:
                self._cache[key] = self.ws.get(kind, rid) if rid else None
            except Exception:  # noqa: BLE001 - a name is for display; a damaged record shows its id
                self._cache[key] = None
        return self._cache[key]

    def document(self, rid: str) -> dict | None:
        return self._get("document", rid)

    def lifecycle(self) -> dict:
        key = ("lifecycle", "")
        if key not in self._cache:
            self._cache[key] = D.get_lifecycle(self.ws)
        return self._cache[key]

    def standard(self, sid: str) -> dict:
        """A build standard, checked once per question; raises like ``UnitService.standard``."""
        key = ("standard", sid)
        if key not in self._cache:
            try:
                self._cache[key] = self.svc.standard(sid)
            except (NotFound, MalformedUnitRecord) as exc:
                self._cache[key] = exc
        found = self._cache[key]
        if isinstance(found, Exception):
            raise found
        return found

    def revision(self, rid: str) -> dict | None:
        return self._get("revision", rid)

    def __call__(self, kind: str, rid: str) -> str:
        if not rid:
            return ""
        if kind == "document":
            d = self.document(rid)
            return (d.get("number") or d.get("title") or rid) if d else rid
        if kind == "revision":
            r = self.revision(rid)
            return r.get("label", rid) if r else rid
        if kind == "project":
            p = self._get("project", rid)
            return (p.get("number") or p.get("name") or rid) if p else rid
        if kind == "unit":
            u = self._get("unit", rid)
            try:
                return unit_label(load_unit(u)) if u else rid
            except MalformedUnitRecord:
                return rid
        if kind == "standard":
            s = self._get("buildstd", rid)
            return s.get("number", rid) if isinstance(s, dict) else rid
        if kind == "part":
            p = self._get("part", rid)
            return p.get("number", rid) if isinstance(p, dict) else rid
        if kind == "change":
            c = self._get("changeord", rid)
            return c.get("number", rid) if isinstance(c, dict) else rid
        return rid


# ------------------------------------------------------------------------------------------ documents of a unit
def applicable_documents(svc: UnitService, unit: dict, names: _Names | None = None) -> dict:
    """``{"recorded": bool, "standard": {...} | None, "rows": [...]}``: each document the unit's standard or its own differences
    name, with the revision that applies to *this* unit, where that came from, and flags (docs/UNITS.md section 5). The
    released revision is reported beside it and never substituted."""
    names = names or _Names(svc)
    std = None
    std_problem = ""
    if unit["standard"] is not None:
        try:
            std = names.standard(unit["standard"]["id"])
        except (NotFound, MalformedUnitRecord) as exc:
            std_problem = str(exc)
    lc = names.lifecycle()
    state_names = {s["id"]: s["name"] for s in lc["states"]}
    fine = {lc["released"], lc["superseded"]}
    chosen: dict[str, dict] = {}
    if std is not None:
        for doc, rev in std["documents"].items():
            chosen[doc] = {"revision": rev, "source": "standard", "event": unit["standard"]["event"],
                           "basis": unit["standard"]["basis"]}
    for doc, d in unit["documents"].items():
        chosen[doc] = {"revision": d["revision"], "source": "unit", "event": d["event"], "basis": d["basis"]}
    rows = []
    for doc_id, c in chosen.items():
        doc = names.document(doc_id)
        rev = names.revision(c["revision"]) if c["revision"] else None
        flags = []
        released = None
        if doc is None or (c["revision"] and (rev is None or rev.get("document_id") != doc_id)):
            flags.append("missing")
        else:
            rel_id = doc.get("released_rev_id") or ""
            rel = names.revision(rel_id) if rel_id else None
            if rel is not None:
                released = {"id": rel_id, "label": rel.get("label", "")}
            if rev is not None:
                if rel is not None and rel_id != rev["id"] and int(rel.get("number", 0)) > int(rev.get("number", 0)):
                    flags.append("newer-released")
                if rev.get("state") not in fine:
                    flags.append("obsolete" if rev.get("state") == "obsolete" else "not-released")
        rows.append({
            "document": {"id": doc_id, "number": (doc or {}).get("number", ""), "title": (doc or {}).get("title", "")},
            "revision": None if rev is None else {"id": rev["id"], "label": rev.get("label", ""),
                                                  "number": rev.get("number", 0), "state": rev.get("state", ""),
                                                  "state_name": state_names.get(rev.get("state", ""), rev.get("state", "")),
                                                  "sha256": rev.get("sha256", "")},
            "applies": bool(c["revision"]), "source": c["source"], "event": c["event"], "basis": c["basis"],
            "released": released, "flags": flags})
    rows.sort(key=lambda r: (norm(r["document"]["number"] or r["document"]["id"]), r["document"]["id"]))
    return {"recorded": bool(chosen) or std is not None,
            "standard": None if std is None else {"id": std["id"], "number": std["number"], "title": std["title"],
                                                  "event": unit["standard"]["event"], "basis": unit["standard"]["basis"]},
            "standard_problem": std_problem, "rows": rows}


# ------------------------------------------------------------------------------------------------- one unit
def unit_detail(svc: UnitService, unit: dict, *, history: bool = True, at: int | None = None, on: str = "",
                changes: Any = None, deviations: Any = None) -> dict:
    """Everything a person or another program needs about one unit, in the shape ``revisionworx link unit`` answers
    (docs/INTEGRATION.md). ``at`` (an event number) or ``on`` (a date) answer for that point instead of now."""
    names = _Names(svc)
    if on:
        on = day_of(check_date(on, "date"))
    events = svc.events(unit["id"])
    late: list[int] = []
    shown = unit
    if at:
        if not 1 <= at <= len(events):
            raise NotFound(f"{unit_label(unit)} has no event {at}")
        shown = {**H.after(events, at), "_rev": unit.get("_rev", 0)}
    elif on:
        state, late = H.on(events, on)
        shown = {**state, "_rev": unit.get("_rev", 0)}
    docs = applicable_documents(svc, shown, names)
    project = names._get("project", shown["project"]) if shown["project"] else None
    parent = shown["parent"]
    out = {
        "id": shown["id"], "label": unit_label(shown), "class": shown["class"], "name": shown["name"],
        "model": dict(shown["model"]), "identifiers": [dict(i) for i in shown["identifiers"]], "status": shown["status"],
        "project": None if project is None else {"id": shown["project"], "number": project.get("number", ""),
                                                 "name": project.get("name", "")},
        "customer": shown["customer"], "site": shown["site"], "location": shown["location"],
        "parent": None if parent is None else {"unit": parent["unit"], "label": names("unit", parent["unit"]),
                                               "position": parent["position"]},
        "standard": docs["standard"], "configuration_recorded": docs["recorded"], "documents": docs["rows"],
        "components": [{**c, "label": names("unit", c["unit"]) if c["unit"] else ""}
                       for c in sorted(shown["components"].values(), key=lambda c: norm(c["position"]))],
        "milestones": {k: dict(v) for k, v in shown["milestones"].items()},
        "notes": shown["notes"],
        "events": dict(shown["events"]), "created": shown["created"], "updated": shown["updated"], "rev": unit.get("_rev", 0),
    }
    into, merged = H.merged_into(events), H.merged_from(events)
    out["merged_into"] = None if into is None else {**into, "label": names("unit", into["unit"])}
    out["merged_from"] = [{**m, "label": names("unit", m["unit"])} for m in merged]
    out["aliases"] = sorted({i["value"] for i in shown["identifiers"] if i["scheme"] == "fieldworx"}
                            | {m["unit"] for m in merged})                # earlier ids field records may name
    out["factory_lots"] = [dict(x) for x in shown.get("lots", [])]   # the lots the work that built it used
    if docs["standard_problem"]:
        out["problems"] = [docs["standard_problem"]]
    if at or on:
        out["as_of"] = {"event": at} if at else {"date": on, "late": late}
    if not (at or on):
        from .deviation import unit_deviations
        from .effect import unit_changes
        out["changes"] = unit_changes(svc, shown["id"], names, changes)
        out["deviations"] = unit_deviations(svc, shown["id"], names, deviations)
    if history:
        voided = H.voided_by(events)
        out["history"] = [{"n": e["n"], "type": e["type"], "summary": H.summary(e, names), "effective": e["effective"],
                           "time": e["time"], "actor": e["actor"], "basis": e["basis"], "evidence": e["evidence"],
                           "reason": e["reason"], "change": e["change"], "voided": e["n"] in voided}
                          for e in events]
    return out


def unit_summary(unit: dict) -> dict:
    return {"id": unit["id"], "label": unit_label(unit), "class": unit["class"], "name": unit["name"],
            "model": dict(unit["model"]), "status": unit["status"], "project": unit["project"],
            "customer": unit["customer"], "site": unit["site"], "location": unit["location"],
            "identifiers": [dict(i) for i in unit["identifiers"]], "standard": (unit["standard"] or {}).get("id", ""),
            "parent": (unit["parent"] or {}).get("unit", ""), "events": unit["events"]["count"], "rev": unit.get("_rev", 0)}


def search(svc: UnitService, text: str = "", project: str = "") -> list[dict]:
    """Units whose identifiers, name, model, customer, site or location contain ``text`` (case-insensitive), optionally in
    one project. Exact identifier matches come first."""
    units = svc.units()
    if project:
        units = [u for u in units if u["project"] == project]
    q = norm(text)
    if not q:
        return units
    exact, partial = [], []
    for u in units:
        values = [norm(i["value"]) for i in u["identifiers"]]
        if q in values or u["id"] == text.strip():
            exact.append(u)
        elif any(q in v for v in values + [norm(u[k]) for k in ("name", "customer", "site", "location")]
                 + [norm(u["model"]["number"])]):
            partial.append(u)
    return exact + partial


# ----------------------------------------------------------------------------------------------- where used
def units_using_revision(svc: UnitService, revision_id: str) -> list[dict]:
    """Units whose configuration now uses this revision, through their standard or their own difference."""
    out = []
    names = _Names(svc)
    for u in svc.units():
        for row in applicable_documents(svc, u, names)["rows"]:
            if row["revision"] and row["revision"]["id"] == revision_id:
                out.append({"unit": unit_summary(u), "source": row["source"], "event": row["event"], "basis": row["basis"]})
    return out


def units_using_document(svc: UnitService, document_id: str) -> list[dict]:
    """Units whose configuration names this document, and which revision each uses."""
    out = []
    names = _Names(svc)
    for u in svc.units():
        for row in applicable_documents(svc, u, names)["rows"]:
            if row["document"]["id"] == document_id:
                out.append({"unit": unit_summary(u), "revision": row["revision"], "applies": row["applies"],
                            "source": row["source"], "flags": row["flags"]})
    return out


def units_on_standard(svc: UnitService, standard_id: str) -> list[dict]:
    return [unit_summary(u) for u in svc.units() if (u["standard"] or {}).get("id") == standard_id]


def units_with_component(svc: UnitService, *, number: str = "", part: str = "", serial: str = "", lot: str = "",
                         component: str = "", history: bool = False) -> list[dict]:
    """Where a part number, catalog part, serial, lot or component unit is fitted now, or (``history``) has ever been fitted,
    with the events that fitted and removed it. Every condition given must hold."""
    if not (number or part or serial or lot or component):
        raise ValueError("give a part number, part, serial, lot or component")

    known_as = {component} | {m["unit"] for m in H.merged_from(svc.events(component))} if component else set()

    def matches(b: dict) -> bool:                    # a component merged into another was fitted under its earlier id too
        return (not component or b["unit"] in known_as) and all(
            want == "" or norm(have) == norm(want) for want, have in
            ((number, b["number"]), (part, b["part"]), (serial, b["serial"]), (lot, b["lot"])))

    out = []
    lot_wanted = not (part or serial or component)          # a factory lot is only a part number and a lot
    for u in (svc.scan()[0] if history else svc.units()):   # history: a unit merged into another was fitted where it was
        if lot_wanted:
            # The lots the work that built it used: nothing records one leaving the unit, so it is always listed, as built
            # from rather than fitted at a position, saying how many units were built with it.
            for x in u.get("lots", []):
                if matches({"number": x["number"], "part": "", "serial": "", "lot": x["lot"], "unit": ""}):
                    out.append({"unit": unit_summary(u), "position": "", "fitted": x["event"], "fitted_on": x["since"],
                                "removed": None, "current": True, "serial": "", "lot": x["lot"],
                                "built_from": {"units": x["units"], "quantity": x["quantity"]}})
        if not history:
            for c in u["components"].values():
                if matches(c):
                    out.append({"unit": unit_summary(u), "position": c["position"], "fitted": c["event"], "removed": None,
                                "current": True, "serial": c["serial"], "lot": c["lot"]})
            continue
        events = svc.events(u["id"])
        voided = H.voided_by(events)
        open_at: dict[str, dict] = {}
        for e in events:
            if e["n"] in voided or not e["type"].startswith("component."):
                continue
            key = position_key(e["body"]["position"])
            if e["type"] == "component.installed":
                if matches(e["body"]):
                    open_at[key] = {"unit": unit_summary(u), "position": e["body"]["position"], "fitted": e["n"],
                                    "fitted_on": e["effective"], "removed": None, "current": True,
                                    "serial": e["body"]["serial"], "lot": e["body"]["lot"]}
            elif key in open_at:
                row = open_at.pop(key)
                out.append({**row, "removed": e["n"], "removed_on": e["effective"], "current": False,
                            "disposition": e["body"]["disposition"]})
        out += open_at.values()
    return out


def fitted_history(svc: UnitService, component_id: str) -> list[dict]:
    """Where a component unit has been fitted, oldest first, from its own history."""
    events = svc.events(component_id)
    voided = H.voided_by(events)
    names = _Names(svc)
    out = []
    for e in events:
        if e["type"] == "parent.set" and e["n"] not in voided:
            if e["body"]["parent"]:
                out.append({"in": e["body"]["parent"], "label": names("unit", e["body"]["parent"]),
                            "position": e["body"]["position"], "fitted": e["n"], "fitted_on": e["effective"],
                            "removed": None})
            elif out and out[-1]["removed"] is None:
                out[-1].update(removed=e["n"], removed_on=e["effective"])
    return out


# ----------------------------------------------------------------------------------------------------- verify
def verify(svc: UnitService) -> list[dict]:
    """Everything wrong with the register, as ``{"code", "subject", "message"}``; an empty list means intact
    (docs/UNITS.md section 7). Nothing is repaired."""
    ws = svc.ws
    ws.check_from_journal()
    out: list[dict] = []

    def problem(code: str, subject: str, message: str) -> None:
        out.append({"code": code, "subject": subject, "message": message})

    units: dict[str, dict] = {}
    for uid, rec in ws.list(KIND["unit"]).items():
        try:
            units[uid] = load_unit(rec)
            if units[uid]["id"] != uid:
                problem("malformed", uid, "the record names another unit")
        except MalformedUnitRecord as exc:
            problem("malformed", uid, str(exc))
    standards: dict[str, dict] = {}
    for sid, rec in ws.list(KIND["standard"]).items():
        try:
            std = load_standard(rec)
        except MalformedUnitRecord as exc:
            problem("malformed", sid, str(exc))
            continue
        standards[sid] = std
        if std["digest"] != standard_digest(std) or std["id"] != sid:
            problem("standard-altered", sid, f"build standard {std['number']} doesn't match its digest")
        for doc, rev in std["documents"].items():
            r = ws.get("revision", rev)
            if ws.get("document", doc) is None or r is None or r.get("document_id") != doc:
                problem("dangling-reference", sid, f"build standard {std['number']} names {doc}@{rev}, which doesn't exist")
    claims: dict[str, dict] = {}
    for kid, rec in ws.list(KIND["key"]).items():
        try:
            claims[kid] = load_key(rec)
        except MalformedUnitRecord as exc:
            problem("malformed", kid, str(exc))
    events_of: dict[str, set] = {}
    for eid, rec in ws.list(KIND["event"]).items():
        events_of.setdefault(str(rec.get("unit", "")), set()).add(eid)
    from .. import devices
    accounts = devices.accounts_of(ws)
    checked: dict = {}                               # a signature on several events of one action is checked once
    signed: dict[str, set] = {}                      # signed field record -> the txids of the actions that cite it

    merges: dict[tuple[str, str], set] = {}         # (duplicate, survivor) -> the units whose history records it
    for uid, u in units.items():
        label = unit_label(u)
        raw = {n: ws.get(KIND["event"], H.event_id(uid, n)) for n in range(1, u["events"]["count"] + 2)}
        chain = H.verify_chain(uid, u["events"]["count"], u["events"]["head"], lambda n: raw.get(n))
        for p in chain:
            problem(p["problem"], uid, f"{label}: event {p['n']}" + (f": {p['detail']}" if "detail" in p else ""))
        known = {H.event_id(uid, n) for n in range(1, u["events"]["count"] + 1)}
        for stray in sorted(events_of.get(uid, set()) - known):
            problem("unrecorded-event", uid, f"{label}: event record {stray} isn't in the unit's history")
        if not chain:
            events = [load_event(raw[n]) for n in range(1, u["events"]["count"] + 1)]
            computed, issues = H.reduce(events)
            if issues or computed != u:
                problem("record-mismatch", uid, f"{label}: the record isn't what its history says"
                        + (f" ({'; '.join(issues)})" if issues else ""))
            for issue in H.pack_problems(events):
                problem("pack-register", uid, f"{label}: {issue}")
            into = H.merged_into(events)
            if (u["status"] == MERGED) != (into is not None):
                problem("merge-mismatch", uid, f"{label}: its status and its history disagree about a merge")
            pairs = [(uid, into["unit"])] if into else []
            pairs += [(m["unit"], uid) for m in H.merged_from(events)]
            for dup, survivor in pairs:
                merges.setdefault((dup, survivor), set()).add(uid)
            for ev in events:
                wrong = devices.recheck(accounts, ev, checked)
                if wrong:
                    problem("signature-invalid", uid, f"{label}: event {ev['n']}: {wrong}")
                for x in ev["evidence"]:
                    if isinstance(x, dict) and x.get("kind") == devices.SIGNATURE_EVIDENCE:
                        signed.setdefault(str(x.get("ref", "")), set()).add(ev["txid"])
        for ident in u["identifiers"]:
            kid = key_id(ident["scheme"], ident["issuer"], ident["value"])
            held = ident["active"] or ident["scheme"] in PERMANENT_SCHEMES
            c = claims.get(kid)
            if held and c is None:
                problem("claim-missing", uid, f"{label}: {ident['scheme']} {ident['value']} has no claim")
            elif c is not None and held and c["owner"] != uid:
                problem("claim-mismatch", uid, f"{label}: {ident['scheme']} {ident['value']} is claimed by {c['owner']}")
        if u["project"] and ws.get("project", u["project"]) is None:
            problem("dangling-reference", uid, f"{label}: project {u['project']} doesn't exist")
        if u["standard"] is not None and u["standard"]["id"] not in standards:
            problem("dangling-reference", uid, f"{label}: build standard {u['standard']['id']} doesn't exist")
        for doc, d in u["documents"].items():
            r = ws.get("revision", d["revision"]) if d["revision"] else None
            if ws.get("document", doc) is None or (d["revision"] and (r is None or r.get("document_id") != doc)):
                problem("dangling-reference", uid, f"{label}: {doc}@{d['revision'] or '-'} doesn't exist")
        for c in u["components"].values():
            if c["unit"]:
                comp = units.get(c["unit"])
                if comp is None:
                    problem("dangling-reference", uid, f"{label}: component unit {c['unit']} doesn't exist")
                elif comp["parent"] != {"unit": uid, "position": c["position"]}:
                    problem("parent-mismatch", uid, f"{label}: {unit_label(comp)} doesn't record being fitted at "
                                                    f"{c['position']}")
        if u["parent"] is not None:
            host = units.get(u["parent"]["unit"])
            key = position_key(u["parent"]["position"])
            if host is None:
                problem("dangling-reference", uid, f"{label}: fitted in {u['parent']['unit']}, which doesn't exist")
            elif host["components"].get(key, {}).get("unit") != uid:
                problem("parent-mismatch", uid, f"{label}: {unit_label(host)} doesn't record it at {u['parent']['position']}")
    for kid, c in claims.items():
        if is_id("standard", c["owner"]):
            if c["owner"] not in standards or c["scheme"] != STANDARD_SCHEME:
                problem("claim-orphan", kid, f"claim for {c['value']} names a build standard that doesn't exist")
            elif norm(standards[c["owner"]]["number"]) != norm(c["value"]):
                problem("claim-mismatch", kid, f"claim for {c['value']} names a standard with another number")
            continue
        owner = units.get(c["owner"])
        if owner is None:
            problem("claim-orphan", kid, f"claim for {c['scheme']} {c['value']} names a unit that doesn't exist")
            continue
        if key_id(c["scheme"], c["issuer"], c["value"]) != kid:
            problem("claim-mismatch", kid, f"claim for {c['scheme']} {c['value']} is filed under the wrong name")
        held = [i for i in owner["identifiers"] if i["scheme"] == c["scheme"] and norm(i["issuer"]) == norm(c["issuer"])
                and norm(i["value"]) == norm(c["value"]) and (i["active"] or i["scheme"] in PERMANENT_SCHEMES)]
        if not held:
            problem("claim-orphan", kid, f"{unit_label(owner)} holds a claim on {c['scheme']} {c['value']} it doesn't have")
    for uid in sorted(set(events_of) - set(units)):
        if uid:
            problem("unrecorded-event", uid, f"events for unit {uid}, which has no record")
    for ref, txids in signed.items():
        if len(txids) > 1:
            problem("signature-reused", ref.partition(":")[2] or ref, f"signed field record {ref} is cited by {len(txids)} "
                    "different actions: a field record is accepted once")
    for (dup, survivor), recorded in sorted(merges.items()):
        if recorded != {dup, survivor}:                # both halves of a merge are in both histories, or it's damage
            missing = sorted({dup, survivor} - recorded)
            problem("merge-mismatch", missing[0] if missing else dup,
                    f"the merge of {dup} into {survivor} is recorded in only one of their histories")
    _verify_changes(ws, units, standards, problem)
    _verify_deviations(ws, units, problem)
    for nid, rec in ws.list(KIND["numbering"]).items():
        try:
            if load_numbering(rec)["id"] != nid:
                problem("malformed", nid, "the numbering is filed under another id")
        except MalformedUnitRecord as exc:
            problem("malformed", nid, str(exc))
    return sorted(out, key=lambda p: (p["code"], p["subject"]))


def _verify_changes(ws: Any, units: dict[str, dict], standards: dict[str, dict], problem: Callable[[str, str, str], None]) -> None:
    """Effectivity decisions and compliance records (docs/UNITS.md section 10.3)."""
    decisions: dict[str, dict] = {}
    for eid, rec in ws.list(KIND["effectivity"]).items():
        try:
            eff = load_effectivity(rec)
        except MalformedUnitRecord as exc:
            problem("malformed", eid, str(exc))
            continue
        decisions[eff["change"]] = eff
        if eff["id"] != eid or eff["digest"] != effectivity_digest(eff):
            problem("effectivity-altered", eid, f"the effectivity of {eff['change']} doesn't match its digest")
        if ws.get("changeord", eff["change"]) is None:
            problem("dangling-reference", eid, f"effectivity names change order {eff['change']}, which doesn't exist")
        if eff["standard"] and eff["standard"] not in standards:
            problem("dangling-reference", eid, f"effectivity names build standard {eff['standard']}, which doesn't exist")
        for uid in eff["units"]:
            if uid not in units:
                problem("dangling-reference", eid, f"effectivity names unit {uid}, which doesn't exist")
        for row in eff["documents"]:
            for rev in (row["from"], row["to"]):
                r = ws.get("revision", rev)
                if r is None or r.get("document_id") != row["document"]:
                    problem("dangling-reference", eid, f"effectivity names {row['document']}@{rev}, which doesn't exist")
    for cid, rec in ws.list(KIND["compliance"]).items():
        try:
            c = load_compliance(rec)
        except MalformedUnitRecord as exc:
            problem("compliance-invalid", cid, str(exc))
            continue
        u = units.get(c["unit"])
        label = unit_label(u) if u else c["unit"]
        if c["id"] != cid:
            problem("compliance-invalid", cid, "the record is filed under another id")
        if u is None:
            problem("dangling-reference", cid, f"compliance names unit {c['unit']}, which doesn't exist")
        eff = decisions.get(c["change"])
        if eff is None:
            problem("compliance-invalid", cid, f"{label}: compliance with {c['change']}, which has no effectivity decision")
        elif c["required"] != (eff["kind"] in REQUIRED_KINDS):
            problem("compliance-invalid", cid, f"{label}: marked {'required' if c['required'] else 'optional'}, but the "
                                               f"decision is {eff['kind']}")
        for step in c["steps"]:
            for ev_id in step["events"]:
                ev = ws.get(KIND["event"], ev_id)
                if ev is None or ev.get("unit") != c["unit"]:
                    problem("compliance-invalid", cid, f"{label}: step {step['state']} names event {ev_id}, which isn't on the unit")
                elif ev.get("change") != c["change"]:
                    problem("compliance-invalid", cid, f"{label}: event {ev.get('n')} doesn't cite the change its step is for")


def _verify_deviations(ws: Any, units: dict[str, dict], problem: Callable[[str, str, str], None]) -> None:
    """Deviations and concessions, and their records on units (docs/UNITS.md section 12)."""
    from .model import deviation_digest, deviation_id, load_deviation, load_devunit
    devs: dict[str, dict] = {}
    for did, rec in ws.list(KIND["deviation"]).items():
        try:
            dev = load_deviation(rec)
        except MalformedUnitRecord as exc:
            problem("malformed", did, str(exc))
            continue
        devs[did] = dev
        if dev["id"] != did or did != deviation_id(dev["number"]) or dev["digest"] != deviation_digest(dev):
            problem("deviation-altered", did, f"{dev['number']} doesn't match its digest or its number")
        for uid in dev["units"]:
            if uid not in units:
                problem("dangling-reference", did, f"{dev['number']} names unit {uid}, which doesn't exist")
        for row in dev["documents"]:
            r = ws.get("revision", row["revision"])
            if r is None or r.get("document_id") != row["document"]:
                problem("dangling-reference", did, f"{dev['number']} names {row['document']}@{row['revision']}, which "
                                                   "doesn't exist")
    covered: set[tuple[str, str]] = set()
    for rid, rec in ws.list(KIND["devunit"]).items():
        try:
            r = load_devunit(rec)
        except MalformedUnitRecord as exc:
            problem("deviation-invalid", rid, str(exc))
            continue
        covered.add((r["deviation"], r["unit"]))
        u = units.get(r["unit"])
        label = unit_label(u) if u else r["unit"]
        dev = devs.get(r["deviation"])
        if r["id"] != rid:
            problem("deviation-invalid", rid, "the record is filed under another id")
        if u is None:
            problem("dangling-reference", rid, f"a deviation record names unit {r['unit']}, which doesn't exist")
        if dev is None:
            problem("deviation-invalid", rid, f"{label}: a record of deviation {r['deviation']}, which doesn't exist")
        elif r["state"] == "cleared" and not dev["temporary"]:
            problem("deviation-invalid", rid, f"{label}: {dev['number']} is permanent, but it is recorded as cleared")
        for step in r["steps"]:
            for ev_id in step["events"]:
                ev = ws.get(KIND["event"], ev_id)
                if ev is None or ev.get("unit") != r["unit"]:
                    problem("deviation-invalid", rid, f"{label}: step {step['state']} names event {ev_id}, which isn't on "
                                                      "the unit")
    for did, dev in devs.items():
        for uid in dev["units"]:
            if (did, uid) not in covered:
                problem("deviation-invalid", did, f"{dev['number']} covers {uid}, which has no record of it")
