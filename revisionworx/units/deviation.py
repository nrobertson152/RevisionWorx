# SPDX-License-Identifier: GPL-3.0-or-later
"""Deviations and concessions (docs/UNITS.md section 12).

A **deviation** is permission, given before the work, to build or repair named units differently from their documents: a
substitute part while the right one is out of stock, say. A **concession** accepts named units already made that differ from
them. Neither changes a document; a change for every future unit is an engineering change. The permission is written once with
the units it covers, and each unit has its own record, whose steps are only ever appended, of whether the departure is in
effect on it, was cleared (a temporary one, once the unit conforms again) or wasn't needed. Every action goes through
:meth:`UnitService._run`, so it is one entry, fenced, replayed after a crash, signed in a team workspace and safe to retry with a
``txid``.
"""
from __future__ import annotations

import copy
import re
from typing import Callable, Iterable

from ..workspace import RecordPut
from .model import (DEVIATION_KINDS, DEVIATION_TRANSITIONS, LIMITS, MalformedUnitRecord, NotFound, ReplayRefused,
                    ValidationError, check_choice, check_date, clean, day_of, deviation_digest, deviation_id, devunit_id, is_id,
                    load_deviation, load_devunit, norm, unit_label)
from .service import KIND, UnitService, _Tx

#: the number given when none is: the next of these, by kind
AUTO_PREFIX = {"deviation": "DEV", "concession": "CON"}


# ===================================================================================================== reading
def _load(svc: UnitService, rec: dict) -> dict:
    dev = load_deviation(rec)
    if dev["digest"] != deviation_digest(dev):
        raise MalformedUnitRecord(f"deviation {dev['number']} has been altered; run 'revisionworx unit verify'")
    return {**dev, "_rev": rec.get("_rev", 0)}


def find(svc: UnitService, ref: str) -> dict | None:
    """A deviation or concession by id or number, or None. A damaged one raises MalformedUnitRecord (never used partially)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    rec = svc.ws.get(KIND["deviation"], ref if is_id("deviation", ref) else deviation_id(ref))
    return None if rec is None else _load(svc, rec)


def deviation(svc: UnitService, ref: str) -> dict:
    found = find(svc, ref)
    if found is None:
        raise NotFound(f"no deviation or concession {ref!r}")
    return found


def devunit(svc: UnitService, dev_id: str, unit_id: str) -> dict | None:
    rec = svc.ws.get(KIND["devunit"], devunit_id(dev_id, unit_id))
    return None if rec is None else {**load_devunit(rec), "_rev": rec.get("_rev", 0)}


def all_deviations(svc: UnitService) -> tuple[list[dict], list[str]]:
    """Every readable deviation and concession, and the ids of those that couldn't be read or were altered."""
    good, bad = [], []
    for did, rec in svc.ws.list(KIND["deviation"]).items():
        try:
            good.append(_load(svc, rec))
        except MalformedUnitRecord:
            bad.append(did)
    return good, bad


def all_devunits(svc: UnitService) -> tuple[list[dict], list[str]]:
    good, bad = [], []
    for rid, rec in svc.ws.list(KIND["devunit"]).items():
        try:
            good.append({**load_devunit(rec), "_rev": rec.get("_rev", 0)})
        except MalformedUnitRecord:
            bad.append(rid)
    return good, bad


def today(svc: UnitService) -> str:
    return svc.clock()[:10]


def overdue(dev: dict, rec: dict, on: str) -> bool:
    """A temporary departure still in effect on a unit after the day it was to end."""
    return bool(dev["temporary"] and dev["until"] and rec["state"] == "in-effect" and dev["until"] < on)


# ===================================================================================================== actions
def _next_number(svc: UnitService, kind: str) -> str:
    prefix = AUTO_PREFIX[kind]
    pattern = re.compile(rf"^{prefix}-(\d+)$", re.IGNORECASE)
    top = 0
    for rec in svc.ws.list(KIND["deviation"]).values():
        m = pattern.match(str(rec.get("number", "")).strip())
        if m:
            top = max(top, int(m.group(1)))
    return f"{prefix}-{top + 1:04d}"


def _replayed(svc: UnitService, txid: str | None) -> str | None:
    """The deviation an attempt already recorded with this txid, if it was recorded (a retry answers it)."""
    if txid is None or svc.ws.transaction_outcome(txid)["state"] != "committed":
        return None
    for did, rec in svc.ws.list(KIND["deviation"]).items():
        if rec.get("txid") == txid:
            return did
    raise ReplayRefused(f"txid {txid} was already used for a different action")


def _new_unit(tx: _Tx, dev_id: str, unit_id: str, reason: str) -> dict:
    rec = {"schema": 1, "id": devunit_id(dev_id, unit_id), "deviation": dev_id, "unit": unit_id, "state": "in-effect",
           "steps": [{"state": "in-effect", "time": tx.time, "effective": "", "actor": tx.actor, "basis": "declared",
                      "reason": reason, "events": [], "txid": tx.txid, "input": tx.input}],
           "created": tx.time, "updated": tx.time}
    load_devunit(rec)
    tx.extra_puts.append(RecordPut(KIND["devunit"], rec["id"], rec, expect_rev=0))
    return rec


def _step(tx: _Tx, rec: dict, state: str, *, reason: str, effective: str = "", basis: str = "declared",
          events: list[str] = ()) -> dict:
    allowed = DEVIATION_TRANSITIONS.get(rec["state"], ())
    if state not in allowed:
        raise ValidationError(f"this is {rec['state'].replace('-', ' ')} on the unit, so it can't become "
                              f"{state.replace('-', ' ')}")
    new = copy.deepcopy({k: v for k, v in rec.items() if k != "_rev"})
    new["steps"].append({"state": state, "time": tx.time, "effective": effective, "actor": tx.actor, "basis": basis,
                         "reason": reason, "events": list(events), "txid": tx.txid, "input": tx.input})
    new["state"], new["updated"] = state, tx.time
    load_devunit(new)
    tx.extra_puts.append(RecordPut(KIND["devunit"], new["id"], new, expect_rev=rec["_rev"]))
    return new


def approve(svc: UnitService, kind: str, *, title: str, departure: str, reason: str, units: Iterable[str],
            documents: Iterable[str] = (), temporary: bool = False, until: str = "", number: str = "",
            txid: str | None = None) -> dict:
    """Record an approved deviation or concession, written once, with an in-effect record for each unit it covers, in one
    entry. ``documents`` are ``DOC@REV`` references to the revisions it departs from. ``number`` defaults to the next DEV-nnnn
    or CON-nnnn."""
    check_choice(kind, DEVIATION_KINDS, "kind")
    title = clean(title, "title", LIMITS["deviation_title"], required=True)
    departure = clean(departure, "departure (what differs from the documents)", LIMITS["departure"], multiline=True,
                      required=True)
    reason = clean(reason, "reason", LIMITS["reason"], multiline=True, required=True)
    if not isinstance(temporary, bool):
        raise ValidationError("temporary is true or false")
    until = day_of(check_date(until, "end date")) if until else ""
    if until and not temporary:
        raise ValidationError("only a temporary deviation has an end date")
    given = clean(number, "number", LIMITS["deviation_number"])
    named, doc_refs = [str(u) for u in units], [str(d) for d in documents]
    if not named:
        raise ValidationError("name the units it covers")
    if len(named) > LIMITS["deviation_units"] or len(doc_refs) > LIMITS["deviation_documents"]:
        raise ValidationError("too many units or documents for one deviation")
    args = {"kind": kind, "number": given, "title": title, "departure": departure, "reason": reason, "units": named,
            "documents": doc_refs, "temporary": temporary, "until": until}
    done = _replayed(svc, txid)
    if done is not None:
        svc._run("unit.deviation.approve", txid, args, [done], lambda tx: None, svc._meta(reason=reason))
        return deviation(svc, done)
    num = given or _next_number(svc, kind)
    dev_id = deviation_id(num)
    if svc.ws.get(KIND["deviation"], dev_id) is not None:
        raise ValidationError(f"there is already a deviation or concession {num}")
    chosen: dict[str, dict] = {}
    for ref in named:
        u = svc.resolve(ref)
        if u["status"] in ("scrapped", "transferred"):
            raise ValidationError(f"{unit_label(u)} is {u['status']}"
                                  + (": it is recorded in another workspace now" if u["status"] == "transferred" else ""))
        chosen.setdefault(u["id"], u)
    rows, seen = [], set()
    for ref in doc_refs:
        did, rid = svc.doc_rev(ref)
        if did in seen:
            raise ValidationError(f"{ref}: a document is named once, with the revision it departs from")
        seen.add(did)
        rows.append({"document": did, "revision": rid})

    def build(tx: _Tx) -> dict:
        for uid in chosen:
            tx.unit(uid)                                 # pinned: approved for the units as they were read
        rec = {"schema": 1, "id": dev_id, "number": num, "kind": kind, "title": title, "departure": departure,
               "documents": sorted(rows, key=lambda r: r["document"]), "units": sorted(chosen), "temporary": temporary,
               "until": until, "reason": reason, "approved": tx.time, "approved_by": tx.actor, "txid": tx.txid,
               "input": tx.input, "digest": ""}
        rec["digest"] = deviation_digest(rec)
        load_deviation(rec)
        tx.extra_puts.append(RecordPut(KIND["deviation"], dev_id, rec, expect_rev=0))
        for uid in sorted(chosen):
            _new_unit(tx, dev_id, uid, "")
        return rec

    result = svc._run("unit.deviation.approve", txid, args, [dev_id], build, svc._meta(reason=reason))
    return result if result is not None else deviation(svc, dev_id)


def _context(svc: UnitService, unit: str, ref: str) -> tuple[dict, dict, dict]:
    dev = deviation(svc, ref)
    u = svc.resolve(unit)
    rec = devunit(svc, dev["id"], u["id"])
    if rec is None:
        raise ValidationError(f"{dev['number']} doesn't cover {unit_label(u)}")
    return dev, u, rec


def add(svc: UnitService, ref: str, unit: str, *, reason: str, txid: str | None = None) -> dict:
    """Approve the same departure for one more unit (its own record; the deviation stays as it was). Needs a reason."""
    dev = deviation(svc, ref)
    u = svc.resolve(unit)
    if u["status"] in ("scrapped", "transferred"):
        raise ValidationError(f"{unit_label(u)} is {u['status']}"
                              + (": it is recorded in another workspace now" if u["status"] == "transferred" else ""))
    reason = clean(reason, "reason", LIMITS["reason"], multiline=True, required=True)
    rid = devunit_id(dev["id"], u["id"])
    replaying = txid is not None and svc.ws.transaction_outcome(txid)["state"] == "committed"
    if not replaying and svc.ws.get(KIND["devunit"], rid) is not None:
        raise ValidationError(f"{dev['number']} already covers {unit_label(u)}")

    def build(tx: _Tx) -> dict:
        tx.check(KIND["deviation"], dev["id"])
        tx.unit(u["id"])
        return _new_unit(tx, dev["id"], u["id"], reason)

    result = svc._run("unit.deviation.add", txid, {"deviation": dev["id"], "unit": u["id"], "reason": reason}, [rid], build,
                      svc._meta(reason=reason))
    return result if result is not None else devunit(svc, dev["id"], u["id"])


def _events(svc: UnitService, unit: dict, numbers: Iterable[int]) -> list[str]:
    """Unit event numbers as ids: each must be on the unit and not voided."""
    history = svc.events(unit["id"])
    voided = {n for e in history if e["type"] == "void" for n in e["body"]["events"]}
    by_n = {e["n"]: e for e in history}
    out = []
    for n in numbers:
        if isinstance(n, bool) or not isinstance(n, int) or n not in by_n:
            raise ValidationError(f"{unit_label(unit)} has no event {n}")
        if n in voided:
            raise ValidationError(f"event {n} on {unit_label(unit)} was voided")
        if by_n[n]["id"] not in out:
            out.append(by_n[n]["id"])
    return out


def clear(svc: UnitService, unit: str, ref: str, *, events: Iterable[int] = (), reason: str = "", effective: str = "",
          basis: str = "declared", txid: str | None = None) -> dict:
    """A temporary departure has ended on the unit: it conforms to its documents again. ``events`` are the unit's events that
    restored it (the right part fitted, say), by number."""
    dev, u, rec = _context(svc, unit, ref)
    if not dev["temporary"]:
        raise ValidationError(f"{dev['number']} is permanent on {unit_label(u)}, so there is nothing to clear; if it wasn't "
                              "needed, mark it not used")
    meta = svc._meta(effective=effective, basis=basis, reason=reason)
    numbers = [int(n) if isinstance(n, str) and n.strip().isdigit() else n for n in events]
    ids = _events(svc, u, numbers)

    def build(tx: _Tx) -> dict:
        tx.check(KIND["deviation"], dev["id"])
        tx.unit(u["id"])
        return _step(tx, rec, "cleared", reason=meta["reason"], effective=meta["effective"], basis=meta["basis"], events=ids)

    result = svc._run("unit.deviation.clear", txid, {"deviation": dev["id"], "unit": u["id"], "events": numbers,
                                                     "meta": meta}, [rec["id"]], build, meta)
    return result if result is not None else devunit(svc, dev["id"], u["id"])


def not_used(svc: UnitService, unit: str, ref: str, *, reason: str, effective: str = "", txid: str | None = None) -> dict:
    """The unit was made or repaired without the departure after all. Needs a reason."""
    dev, u, rec = _context(svc, unit, ref)
    meta = svc._meta(effective=effective, reason=reason)
    if not meta["reason"]:
        raise ValidationError("not used needs a reason")

    def build(tx: _Tx) -> dict:
        tx.check(KIND["deviation"], dev["id"])
        tx.unit(u["id"])
        return _step(tx, rec, "not-used", reason=meta["reason"], effective=meta["effective"])

    result = svc._run("unit.deviation.not-used", txid, {"deviation": dev["id"], "unit": u["id"], "meta": meta}, [rec["id"]],
                      build, meta)
    return result if result is not None else devunit(svc, dev["id"], u["id"])


# ===================================================================================================== views
def _names(svc: UnitService) -> Callable[[str, str], str]:
    from .query import _Names
    return _Names(svc)


def _label(svc: UnitService, unit_id: str) -> str:
    try:
        return unit_label(svc.unit(unit_id))
    except (NotFound, MalformedUnitRecord):
        return unit_id


def _natural(text: str) -> tuple:
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", norm(text)) if t)


class DeviationIndex:
    """Deviation records by unit, and deviations by id, read once for answers about many units (``link units --detail``)."""

    def __init__(self, svc: UnitService):
        self.svc = svc
        self.by_unit: dict[str, list[dict]] = {}
        for r in all_devunits(svc)[0]:
            self.by_unit.setdefault(r["unit"], []).append(r)
        self.deviations = {d["id"]: d for d in all_deviations(svc)[0]}
        self.today = today(svc)


def _documents(dev: dict, names: Callable[[str, str], str]) -> list[dict]:
    return [{"document": r["document"], "number": names("document", r["document"]), "revision": r["revision"],
             "label": names("revision", r["revision"])} for r in dev["documents"]]


def unit_deviations(svc: UnitService, unit_id: str, names: Callable[[str, str], str] | None = None,
                    index: DeviationIndex | None = None) -> list[dict]:
    """The deviations and concessions that cover one unit, for ``link unit`` and the screens: those in effect first. One
    whose record is damaged is left out here and reported by ``unit verify``."""
    names = names or _names(svc)
    index = index or DeviationIndex(svc)
    out = []
    for rec in index.by_unit.get(unit_id, []):
        dev = index.deviations.get(rec["deviation"])
        if dev is None:
            continue
        last = rec["steps"][-1]
        out.append({"deviation": dev["id"], "number": dev["number"], "kind": dev["kind"], "title": dev["title"],
                    "departure": dev["departure"], "temporary": dev["temporary"], "until": dev["until"],
                    "state": rec["state"], "in_effect": rec["state"] == "in-effect",
                    "overdue": overdue(dev, rec, index.today), "since": last["effective"] or last["time"][:10],
                    "documents": _documents(dev, names)})
    return sorted(out, key=lambda d: (not d["in_effect"], not d["overdue"], _natural(d["number"])))


def status(svc: UnitService, ref: str) -> dict:
    """A deviation or concession and every unit it covers, with where it stands on each (``unit deviation show``)."""
    dev = deviation(svc, ref)
    on = today(svc)
    recs = sorted((r for r in all_devunits(svc)[0] if r["deviation"] == dev["id"]),
                  key=lambda r: _natural(_label(svc, r["unit"])))
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    out = {k: v for k, v in dev.items() if k != "_rev"}
    out["documents"] = _documents(dev, _names(svc))
    out["covered"] = [{"unit": r["unit"], "label": _label(svc, r["unit"]), "state": r["state"],
                       "overdue": overdue(dev, r, on), "since": r["steps"][-1]["effective"] or r["steps"][-1]["time"][:10],
                       "reason": r["steps"][-1]["reason"]} for r in recs]
    out["counts"] = counts
    return out


def listing(svc: UnitService) -> list[dict]:
    """Every deviation and concession, newest number last, with how many units it is still in effect on."""
    on = today(svc)
    by_dev: dict[str, list[dict]] = {}
    for r in all_devunits(svc)[0]:
        by_dev.setdefault(r["deviation"], []).append(r)
    out = []
    for dev in all_deviations(svc)[0]:
        recs = by_dev.get(dev["id"], [])
        out.append({"deviation": dev["id"], "number": dev["number"], "kind": dev["kind"], "title": dev["title"],
                    "temporary": dev["temporary"], "until": dev["until"], "approved": dev["approved"][:10],
                    "units": len(recs), "in_effect": sum(1 for r in recs if r["state"] == "in-effect"),
                    "overdue": sum(1 for r in recs if overdue(dev, r, on))})
    return sorted(out, key=lambda d: _natural(d["number"]))


def overdue_units(svc: UnitService) -> list[dict]:
    """Every unit with a temporary departure still in effect after its end date, and those departures."""
    index = DeviationIndex(svc)
    out = []
    for uid, recs in index.by_unit.items():
        late = []
        for r in recs:
            dev = index.deviations.get(r["deviation"])
            if dev is not None and overdue(dev, r, index.today):
                late.append({"deviation": dev["id"], "number": dev["number"], "title": dev["title"], "until": dev["until"]})
        if late:
            out.append({"unit": uid, "label": _label(svc, uid), "deviations": sorted(late, key=lambda d: _natural(d["number"]))})
    return sorted(out, key=lambda x: _natural(x["label"]))


__all__ = ["AUTO_PREFIX", "DeviationIndex", "add", "all_deviations", "all_devunits", "approve", "clear", "deviation",
           "devunit", "find", "listing", "not_used", "overdue", "overdue_units", "status", "today", "unit_deviations"]
