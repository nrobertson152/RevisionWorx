# SPDX-License-Identifier: GPL-3.0-or-later
"""Engineering changes reaching units (docs/UNITS.md section 10).

Change orders are unchanged. Beside an order this module records an **effectivity** decision (written once; its id comes from the
order's) and, for every unit it reaches, a **compliance** record whose steps are only ever appended. A unit's documents move only
through ``document.set`` events in its own history that cite the change, written in the same journal entry as the step that
explains them. Every action goes through :meth:`UnitService._run`, so it is one entry, fenced, replayed after a crash, signed in a
team workspace and safe to retry with a ``txid``.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Iterable

from ..workspace import RecordPut
from .model import (DONE_STATES, EFFECTIVITY_KINDS, LIMITS, NEEDS_REASON, REQUIRED_KINDS, SCOPE_RULES, TRANSITIONS,
                    MalformedUnitRecord, NotFound, ValidationError, check_choice, clean, compliance_id, effectivity_digest,
                    effectivity_id, is_id, load_compliance, load_effectivity, norm, unit_label)
from .service import KIND, UnitService, _Tx

#: what a person may set with :func:`set_state`; installing, verifying and reversing have their own actions
SETTABLE = ("pending", "accepted", "scheduled", "deferred", "declined", "not-applicable")
DOC_CORRECTION = "documentation correction, no physical change"


# ================================================================================================ the order
def order(svc: UnitService, ref: str) -> Any:
    """The change order ``ref`` (an ``eco_`` id or its number), read through change control. A request is refused."""
    from .. import changes
    from ..changes.model import ChangeError
    ref = (ref or "").strip()
    if not ref:
        raise ValidationError("which change order?")
    cs = changes.service_for(svc.ws)
    try:
        found = cs.get(ref) if re.match(r"^ec[or]_[0-9a-f]{32}$", ref) else cs.find_by_number(ref)
    except ChangeError as exc:
        raise NotFound(str(exc)) from None
    if found is None:
        raise NotFound(f"no change order {ref!r}")
    if not found.id.startswith("eco_"):
        raise ValidationError(f"{found.number} is a change request; units carry out change orders (ECO)")
    return found


def order_rows(o: Any) -> list[dict]:
    """``[{document, from, to}]``: every document item of the order implemented by a revision (its baseline -> that revision)."""
    done = {i.item_key: i for i in o.implementations if i.status.value == "implemented" and i.rev_id}
    rows = [{"document": item.item_id, "from": item.baseline_rev_id, "to": done[item.key].rev_id}
            for item in o.affected
            if item.item_type.value == "document" and item.item_id and item.baseline_rev_id and item.key in done]
    return sorted(rows, key=lambda r: r["document"])


def _parts(o: Any) -> set[str]:
    return {item.item_id for item in o.affected if item.item_type.value == "part" and item.item_id}


# ============================================================================================ configurations
class _Standards:
    def __init__(self, svc: UnitService):
        self.svc, self.cache = svc, {}

    def __call__(self, sid: str) -> dict | None:
        if sid not in self.cache:
            try:
                self.cache[sid] = self.svc.standard(sid)
            except (NotFound, MalformedUnitRecord):
                self.cache[sid] = None
        return self.cache[sid]


def applies(unit: dict, document: str, standards: Callable[[str], dict | None]) -> str | None:
    """The revision of ``document`` that applies to the unit: its own, else its standard's. ``""`` when the unit says the document
    doesn't apply, ``None`` when nothing names it."""
    if document in unit["documents"]:
        return unit["documents"][document]["revision"]
    if unit["standard"] is not None:
        std = standards(unit["standard"]["id"])
        if std is not None:
            return std["documents"].get(document)
    return None


def _natural(text: str) -> tuple:
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", norm(text)) if t)


def candidates(svc: UnitService, o: Any, rows: list[dict] | None = None) -> list[dict]:
    """Units, not scrapped, whose applicable revision of a row's document is the row's ``from``, or that have one of the order's
    affected parts fitted: ``[{"unit": record, "why": [text]}]``, by label."""
    rows = order_rows(o) if rows is None else rows
    parts, stds, names = _parts(o), _Standards(svc), _names(svc)
    units, _problems = svc.scan()
    out = []
    for u in units:
        if u["status"] == "scrapped":
            continue
        why = [f"has {names('document', r['document'])} rev {names('revision', r['from'])}" for r in rows
               if applies(u, r["document"], stds) == r["from"]]
        why += [f"has {names('part', c['part'])} at {c['position']}" for c in u["components"].values() if c["part"] in parts]
        if why:
            out.append({"unit": u, "why": why})
    return sorted(out, key=lambda c: _natural(unit_label(c["unit"])))


def _scope(svc: UnitService, o: Any, rows: list[dict], rule: str, value: str, named: Iterable[str]) -> list[dict]:
    """The units a rule chooses (section 10.1)."""
    if rule == "units":
        out, seen = [], set()
        for ref in named:
            u = svc.resolve(ref)
            if u["id"] in seen:
                continue
            if u["status"] == "scrapped":
                raise ValidationError(f"{unit_label(u)} is scrapped")
            seen.add(u["id"])
            out.append(u)
        if value:
            raise ValidationError("the units rule takes units, not a value")
        return out
    if named:
        raise ValidationError(f"units are named only with the units rule, not {rule}")
    pool = [c["unit"] for c in candidates(svc, o, rows)]
    if rule == "candidates":
        if value:
            raise ValidationError("the candidates rule takes no value")
        return pool
    if not value.strip():
        raise ValidationError(f"the {rule} rule needs a value")
    if rule == "standard":
        sid = svc.resolve_standard(value)["id"]
        return [u for u in pool if (u["standard"] or {}).get("id") == sid]
    if rule == "customer":
        return [u for u in pool if norm(u["customer"]) == norm(value)]
    if rule == "serial-range":
        low, sep, high = value.partition("..")
        if not sep or not low.strip() or not high.strip():
            raise ValidationError("a serial range is FIRST..LAST, e.g. NH-26-0001..NH-26-0040")
        lo, hi = _natural(low), _natural(high)
        if lo > hi:
            raise ValidationError(f"{low.strip()} comes after {high.strip()}")
        return [u for u in pool if any(i["scheme"] == "serial" and i["active"] and lo <= _natural(i["value"]) <= hi
                                       for i in u["identifiers"])]
    raise ValidationError(f"unknown scope rule {rule!r}")  # pragma: no cover - check_choice comes first


# ===================================================================================================== reading
def effectivity(svc: UnitService, change_id: str) -> dict | None:
    """The order's effectivity decision, or None. A damaged record raises MalformedUnitRecord (never used partially)."""
    rec = svc.ws.get(KIND["effectivity"], effectivity_id(change_id))
    if rec is None:
        return None
    eff = load_effectivity(rec)
    if eff["digest"] != effectivity_digest(eff):
        raise MalformedUnitRecord(f"the effectivity decision of {change_id} has been altered; run 'revisionworx unit verify'")
    return {**eff, "_rev": rec.get("_rev", 0)}


def compliance(svc: UnitService, change_id: str, unit_id: str) -> dict | None:
    rec = svc.ws.get(KIND["compliance"], compliance_id(change_id, unit_id))
    return None if rec is None else {**load_compliance(rec), "_rev": rec.get("_rev", 0)}


def all_compliance(svc: UnitService) -> tuple[list[dict], list[str]]:
    """Every readable compliance record, and the ids of those that couldn't be read."""
    good, bad = [], []
    for cid, rec in svc.ws.list(KIND["compliance"]).items():
        try:
            good.append({**load_compliance(rec), "_rev": rec.get("_rev", 0)})
        except MalformedUnitRecord:
            bad.append(cid)
    return good, bad


def outstanding(rec: dict) -> bool:
    """A required change this unit hasn't finished: neither verified nor not applicable."""
    return rec["required"] and rec["state"] not in DONE_STATES


# ===================================================================================================== actions
def _reason(reason: str, required: bool, what: str) -> str:
    return clean(reason, "reason", LIMITS["reason"], multiline=True, required=required) if required or reason else ""


def _step(tx: _Tx, rec: dict, state: str, *, reason: str = "", effective: str = "", basis: str = "declared",
          events: list[str] = ()) -> dict:
    allowed = TRANSITIONS[rec["required"]].get(rec["state"], ())
    if state not in allowed:
        raise ValidationError(f"this change is {rec['state']} on the unit; it can become "
                              f"{', '.join(allowed) if allowed else 'nothing else'}, not {state}")
    if state in NEEDS_REASON and not reason:
        raise ValidationError(f"{state} needs a reason")
    new = copy.deepcopy({k: v for k, v in rec.items() if k != "_rev"})
    new["steps"].append({"state": state, "time": tx.time, "effective": effective, "actor": tx.actor, "basis": basis,
                         "reason": reason, "events": list(events), "txid": tx.txid, "input": tx.input})
    new["state"], new["updated"] = state, tx.time
    load_compliance(new)
    tx.extra_puts.append(RecordPut(KIND["compliance"], new["id"], new, expect_rev=rec["_rev"]))
    return new


def _new(tx: _Tx, change_id: str, unit_id: str, required: bool, reason: str = "") -> dict:
    rec = {"schema": 1, "id": compliance_id(change_id, unit_id), "change": change_id, "unit": unit_id, "required": required,
           "state": "pending" if required else "offered",
           "steps": [{"state": "pending" if required else "offered", "time": tx.time, "effective": "", "actor": tx.actor,
                      "basis": "declared", "reason": reason, "events": [], "txid": tx.txid, "input": tx.input}],
           "created": tx.time, "updated": tx.time}
    load_compliance(rec)
    tx.extra_puts.append(RecordPut(KIND["compliance"], rec["id"], rec, expect_rev=0))
    return rec


def _move(tx: _Tx, unit_id: str, rows: list[dict], stds: _Standards, forward: bool, names: Callable[[str, str], str],
          **meta: Any) -> tuple[list[str], int]:
    """``document.set`` events taking the unit from each row's ``from`` to its ``to`` (or back). Returns the event ids and how
    many rows the unit already had at the target. Refused if the unit has another revision of a row's document."""
    u = tx.unit(unit_id)
    events, there = [], 0
    for r in rows:
        start, target = (r["from"], r["to"]) if forward else (r["to"], r["from"])
        have = applies(u, r["document"], stds)
        if have == target:
            there += 1
        elif have == start:
            events.append(tx.emit(unit_id, "document.set", {"document": r["document"], "revision": target}, **meta)["id"])
            u = tx.unit(unit_id)
        elif have:
            raise ValidationError(f"{unit_label(u)} has {names('document', r['document'])} rev {names('revision', have)}, "
                                  f"not rev {names('revision', start)}, so this change can't be applied to it automatically; "
                                  "record its documents by hand ('unit doc', citing the change)")
    return events, there


def _names(svc: UnitService) -> Callable[[str, str], str]:
    from .query import _Names
    return _Names(svc)


def decide(svc: UnitService, change: str, kind: str, *, reason: str, standard: str = "", rule: str = "", value: str = "",
           units: Iterable[str] = (), txid: str | None = None) -> dict:
    """Record which units an order reaches and how (section 10.1), in one entry with what the decision writes."""
    o = order(svc, change)
    check_choice(kind, EFFECTIVITY_KINDS, "kind")
    reason = _reason(reason, True, "decision")
    named = [str(u) for u in units]
    eid = effectivity_id(o.id)
    if o.status.value == "cancelled":
        raise ValidationError(f"{o.number} is cancelled")
    replaying = txid is not None and svc.ws.transaction_outcome(txid)["state"] == "committed"
    if not replaying and svc.ws.get(KIND["effectivity"], eid) is not None:
        raise ValidationError(f"{o.number} already has an effectivity decision; it is written once (add units with "
                              "'unit change add')")
    rows: list[dict] = []
    std_id = ""
    if kind == "production":
        if not standard:
            raise ValidationError("production effectivity names the build standard new units are built to (--standard)")
        if rule not in ("", "none") or value or named:
            raise ValidationError("production effectivity reaches no existing unit, so it takes no scope")
        std_id, rule, chosen = svc.resolve_standard(standard)["id"], "none", []
    else:
        if o.status.value not in ("implemented", "closed"):
            raise ValidationError(f"{o.number} is {o.status.value.replace('_', ' ')}; decide which units it reaches once it is "
                                  "implemented, so the revisions it sets are final")
        rows = order_rows(o)
        if not rows:
            raise ValidationError(f"{o.number} has no document implemented by a revision, so there is nothing to change on a unit")
        if standard:
            if kind != "documentation":
                raise ValidationError("a build standard is named only for production or documentation effectivity")
            std_id = svc.resolve_standard(standard)["id"]
        rule = rule or ("units" if kind == "unit" else "candidates")
        check_choice(rule, SCOPE_RULES[:-1], "scope rule")
        chosen = [] if replaying else _scope(svc, o, rows, rule, value, named)
        if not replaying and not chosen:
            raise ValidationError(f"the {rule} rule chooses no unit for {o.number}")
        if kind == "unit" and not replaying and len(chosen) != 1:
            raise ValidationError("unit effectivity is one unit's own modification: name exactly one unit")
    args = {"change": o.id, "kind": kind, "standard": std_id, "rule": rule, "value": value, "units": named, "reason": reason}
    meta = svc._meta(reason=reason, change=o.id)
    names = _names(svc)

    def build(tx: _Tx) -> dict:
        tx.check("changeord", o.id)
        if std_id:
            tx.check(KIND["standard"], std_id)
        eff = {"schema": 1, "id": eid, "change": o.id, "kind": kind, "standard": std_id, "documents": rows,
               "scope": {"rule": rule, "value": value.strip()}, "units": sorted(u["id"] for u in chosen),
               "reason": reason, "decided": tx.time, "decided_by": tx.actor, "txid": tx.txid, "input": tx.input,
               "digest": ""}
        eff["digest"] = effectivity_digest(eff)
        load_effectivity(eff)
        tx.extra_puts.append(RecordPut(KIND["effectivity"], eid, eff, expect_rev=0))
        stds = _Standards(svc)
        for u in chosen:
            tx.unit(u["id"])                              # pinned: the decision is about the units as they were read
            if kind == "documentation":
                events, there = _move(tx, u["id"], rows, stds, True, names, basis="declared",
                                      reason=f"{DOC_CORRECTION}: {reason}"[:LIMITS["reason"]])
                if not events and not there:
                    raise ValidationError(f"nothing {o.number} corrects applies to {unit_label(u)}")
            else:
                _new(tx, o.id, u["id"], kind in REQUIRED_KINDS)
        return eff

    result = svc._run("unit.change.decide", txid, args, [eid], build, meta)
    return result if result is not None else effectivity(svc, o.id)


def add(svc: UnitService, change: str, unit: str, *, reason: str = "", txid: str | None = None) -> dict:
    """A unit found after the decision joins a retrofit (its own compliance record; the decision stays as it was)."""
    o = order(svc, change)
    eff = effectivity(svc, o.id)
    if eff is None:
        raise ValidationError(f"{o.number} has no effectivity decision yet")
    if eff["kind"] not in ("retrofit-required", "retrofit-optional"):
        raise ValidationError(f"{o.number} is {eff['kind']} effectivity; only a retrofit takes more units")
    u = svc.resolve(unit)
    if u["status"] == "scrapped":
        raise ValidationError(f"{unit_label(u)} is scrapped")
    cid = compliance_id(o.id, u["id"])
    replaying = txid is not None and svc.ws.transaction_outcome(txid)["state"] == "committed"
    if not replaying and svc.ws.get(KIND["compliance"], cid) is not None:
        raise ValidationError(f"{unit_label(u)} is already in {o.number}'s scope")
    reason = _reason(reason, False, "add")

    def build(tx: _Tx) -> dict:
        tx.check("changeord", o.id)
        tx.check(KIND["effectivity"], eff["id"])
        tx.unit(u["id"])
        return _new(tx, o.id, u["id"], eff["kind"] in REQUIRED_KINDS, reason or "added after the decision")

    result = svc._run("unit.change.add", txid, {"change": o.id, "unit": u["id"], "reason": reason}, [cid], build,
                      svc._meta(change=o.id))
    return result if result is not None else compliance(svc, o.id, u["id"])


def _context(svc: UnitService, unit: str, change: str) -> tuple[Any, dict, dict, dict]:
    o = order(svc, change)
    u = svc.resolve(unit)
    eff = effectivity(svc, o.id)
    rec = compliance(svc, o.id, u["id"])
    if eff is None or rec is None:
        raise ValidationError(f"{unit_label(u)} isn't in {o.number}'s scope" +
                              ("" if eff is not None else f" ({o.number} has no effectivity decision)"))
    return o, u, eff, rec


def set_state(svc: UnitService, unit: str, change: str, state: str, *, reason: str = "", effective: str = "",
              txid: str | None = None) -> dict:
    """Schedule, defer, accept, decline, mark not applicable, or put back to pending."""
    check_choice(state, SETTABLE, "state")
    o, u, eff, rec = _context(svc, unit, change)
    meta = svc._meta(effective=effective, reason=reason, change=o.id)

    def build(tx: _Tx) -> dict:
        tx.check("changeord", o.id)
        tx.unit(u["id"])
        return _step(tx, rec, state, reason=meta["reason"], effective=meta["effective"])

    result = svc._run("unit.change.step", txid, {"change": o.id, "unit": u["id"], "state": state, "meta": meta},
                      [rec["id"]], build, meta)
    return result if result is not None else compliance(svc, o.id, u["id"])


def install(svc: UnitService, unit: str, change: str, *, basis: str = "reported", effective: str = "",
            evidence: Iterable = (), reason: str = "", txid: str | None = None) -> dict:
    """The change was made on the unit: its documents move from each row's ``from`` to its ``to`` in the same entry. With basis
    ``verified`` the state goes straight to ``verified``."""
    o, u, eff, rec = _context(svc, unit, change)
    meta = svc._meta(effective=effective, basis=basis, evidence=evidence, reason=reason, change=o.id)
    state = "verified" if meta["basis"] == "verified" else "installed"
    names = _names(svc)

    def build(tx: _Tx) -> dict:
        tx.check("changeord", o.id)
        tx.check(KIND["effectivity"], eff["id"])
        events, there = _move(tx, u["id"], eff["documents"], _Standards(svc), True, names)
        if not events and not there:
            raise ValidationError(f"nothing {o.number} changes applies to {unit_label(u)}: it doesn't use those revisions. "
                                  "If the change doesn't concern it, mark it not applicable")
        return _step(tx, rec, state, reason=meta["reason"], effective=meta["effective"], basis=meta["basis"], events=events)

    result = svc._run("unit.change.install", txid, {"change": o.id, "unit": u["id"], "meta": meta}, [rec["id"]], build, meta)
    return result if result is not None else compliance(svc, o.id, u["id"])


def verify(svc: UnitService, unit: str, change: str, *, effective: str = "", evidence: Iterable = (), reason: str = "",
           txid: str | None = None) -> dict:
    """A reported installation confirmed: a ``verified`` event makes the facts it recorded verified. Nothing moves twice."""
    o, u, eff, rec = _context(svc, unit, change)
    if rec["state"] != "installed":
        raise ValidationError(f"{o.number} is {rec['state']} on {unit_label(u)}; only an installed change can be verified "
                              "(record the installation with 'unit change install', with --basis verified if it was seen)")
    meta = svc._meta(effective=effective, basis="verified", evidence=evidence, reason=reason, change=o.id)
    install_events = next((s["events"] for s in reversed(rec["steps"]) if s["state"] == "installed"), [])

    def build(tx: _Tx) -> dict:
        tx.check("changeord", o.id)
        tx.unit(u["id"])
        numbers = {e["id"]: e["n"] for e in svc.events(u["id"])}
        voided = {n for e in svc.events(u["id"]) if e["type"] == "void" for n in e["body"]["events"]}
        live = sorted(numbers[e] for e in install_events if e in numbers and numbers[e] not in voided)
        if install_events and not live:
            raise ValidationError(f"the installation recorded for {o.number} on {unit_label(u)} was voided; record it again")
        if rec["state"] == "installed" and live:
            tx.emit(u["id"], "verified", {"events": live})
        events = [e["id"] for e in tx.events]
        return _step(tx, rec, "verified", reason=meta["reason"], effective=meta["effective"], basis="verified", events=events)

    result = svc._run("unit.change.verify", txid, {"change": o.id, "unit": u["id"], "meta": meta}, [rec["id"]], build, meta)
    return result if result is not None else compliance(svc, o.id, u["id"])


def reverse(svc: UnitService, unit: str, change: str, *, reason: str, effective: str = "", evidence: Iterable = (),
            basis: str = "reported", txid: str | None = None) -> dict:
    """The change was taken out again: documents go back to each row's ``from``. Nothing is voided; both changes happened."""
    o, u, eff, rec = _context(svc, unit, change)
    meta = svc._meta(effective=effective, basis=basis, evidence=evidence, reason=reason, change=o.id)
    if not meta["reason"]:
        raise ValidationError("reversed needs a reason")
    names = _names(svc)

    def build(tx: _Tx) -> dict:
        tx.check("changeord", o.id)
        tx.check(KIND["effectivity"], eff["id"])
        events, _there = _move(tx, u["id"], eff["documents"], _Standards(svc), False, names)
        return _step(tx, rec, "reversed", reason=meta["reason"], effective=meta["effective"], basis=meta["basis"],
                     events=events)

    result = svc._run("unit.change.reverse", txid, {"change": o.id, "unit": u["id"], "meta": meta}, [rec["id"]], build, meta)
    return result if result is not None else compliance(svc, o.id, u["id"])


# ===================================================================================================== views
def _label(svc: UnitService, unit_id: str) -> str:
    try:
        return unit_label(svc.unit(unit_id))
    except (NotFound, MalformedUnitRecord):
        return unit_id

class ChangeIndex:
    """Compliance records by unit and decisions by order, read once for answers about many units (``link units --detail``)."""

    def __init__(self, svc: UnitService):
        self.svc = svc
        self.by_unit: dict[str, list[dict]] = {}
        for r in all_compliance(svc)[0]:
            self.by_unit.setdefault(r["unit"], []).append(r)
        self._decisions: dict[str, dict | None] = {}
        self._orders: dict[str, dict] = {}

    def decision(self, change_id: str) -> dict | None:
        if change_id not in self._decisions:
            try:
                self._decisions[change_id] = effectivity(self.svc, change_id)
            except MalformedUnitRecord:
                self._decisions[change_id] = None
        return self._decisions[change_id]

    def order(self, change_id: str) -> dict:
        if change_id not in self._orders:
            self._orders[change_id] = self.svc.ws.get("changeord", change_id) or {}
        return self._orders[change_id]


def unit_changes(svc: UnitService, unit_id: str, names: Callable[[str, str], str] | None = None,
                 index: ChangeIndex | None = None) -> list[dict]:
    """The changes that reach one unit, for ``link unit`` and the screens: outstanding ones first."""
    names = names or _names(svc)
    index = index or ChangeIndex(svc)
    out = []
    for rec in index.by_unit.get(unit_id, []):
        eff = index.decision(rec["change"])
        o = index.order(rec["change"])
        last = rec["steps"][-1]
        out.append({"change": rec["change"], "number": o.get("number", rec["change"]), "title": o.get("title", ""),
                    "kind": eff["kind"] if eff else "", "required": rec["required"], "state": rec["state"],
                    "outstanding": outstanding(rec), "since": last["effective"] or last["time"][:10],
                    "documents": [{"document": r["document"], "number": names("document", r["document"]),
                                   "from": r["from"], "from_label": names("revision", r["from"]),
                                   "to": r["to"], "to_label": names("revision", r["to"])}
                                  for r in (eff["documents"] if eff else [])]})
    return sorted(out, key=lambda c: (not c["outstanding"], c["number"]))


def change_status(svc: UnitService, change: str) -> dict:
    """An order's decision and every unit in scope with its state (``unit change status``)."""
    o = order(svc, change)
    eff = effectivity(svc, o.id)
    recs = sorted((r for r in all_compliance(svc)[0] if r["change"] == o.id), key=lambda r: _natural(_label(svc, r["unit"])))
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"change": o.id, "number": o.number, "title": o.title, "status": o.status.value, "effectivity": eff,
            "units": [{"unit": r["unit"], "label": _label(svc, r["unit"]), "required": r["required"], "state": r["state"],
                       "outstanding": outstanding(r), "since": r["steps"][-1]["effective"] or r["steps"][-1]["time"][:10]}
                      for r in recs],
            "counts": counts, "outstanding": sum(1 for r in recs if outstanding(r))}


def out_of_compliance(svc: UnitService) -> list[dict]:
    """Every unit with a required change it hasn't finished, and those changes."""
    by_unit: dict[str, list[dict]] = {}
    for r in all_compliance(svc)[0]:
        if outstanding(r):
            by_unit.setdefault(r["unit"], []).append(r)
    out = []
    for uid, recs in by_unit.items():
        changes = []
        for r in recs:
            o = svc.ws.get("changeord", r["change"]) or {}
            changes.append({"change": r["change"], "number": o.get("number", r["change"]), "state": r["state"]})
        out.append({"unit": uid, "label": _label(svc, uid), "changes": sorted(changes, key=lambda c: c["number"])})
    return sorted(out, key=lambda x: _natural(x["label"]))


__all__ = ["DOC_CORRECTION", "SETTABLE", "ChangeIndex", "add", "all_compliance", "applies", "candidates", "change_status", "compliance",
           "decide", "effectivity", "install", "order", "order_rows", "out_of_compliance", "outstanding", "reverse",
           "set_state", "unit_changes", "verify"]
