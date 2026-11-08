# SPDX-License-Identifier: GPL-3.0-or-later
"""A unit's history: events written once, chained by SHA-256, and the pure function that turns them into the unit record.

``apply`` is the only place an event changes a unit. The service applies each new event to the record it read, and ``reduce``
applies every event from the first, so ``unit verify`` can check that the stored record is exactly what the history says.
Nothing here touches a workspace (docs/UNITS.md sections 3.2, 4 and 5).
"""
from __future__ import annotations

import copy
from typing import Callable

from .model import (EVENT_TYPES, MalformedUnitRecord, SCHEMA, day_of, digest, event_id, load_event, norm, position_key,
                    unit_label)

EVENT_DOMAIN = "revisionworx/units/event/v1"


def event_digest(event: dict) -> str:
    return digest(EVENT_DOMAIN, {k: v for k, v in event.items() if k not in ("digest", "_rev")})


def make_event(unit_id: str, n: int, prev: str, type_: str, body: dict, *, effective: str, time: str, actor: dict, basis: str,
               evidence: list, reason: str, change: str, txid: str, input_digest: str) -> dict:
    if type_ not in EVENT_TYPES:
        raise ValueError(f"unknown unit event type {type_!r}")
    ev = {"schema": SCHEMA, "id": event_id(unit_id, n), "unit": unit_id, "n": n, "type": type_, "effective": effective,
          "time": time, "actor": dict(actor), "basis": basis, "evidence": list(evidence), "reason": reason, "change": change,
          "txid": txid, "input": input_digest, "body": copy.deepcopy(body), "prev": prev}
    ev["digest"] = event_digest(ev)
    return ev


# --------------------------------------------------------------------------------------------- the reduction
def _fresh(ev: dict) -> dict:
    b = ev["body"]
    return {"schema": SCHEMA, "id": ev["unit"], "class": b["class"], "name": b["name"], "model": dict(b["model"]),
            "identifiers": [{"scheme": i["scheme"], "issuer": i["issuer"], "value": i["value"], "active": True,
                             "since": ev["n"]} for i in b["identifiers"]],
            "status": b["status"], "project": b["project"], "customer": b["customer"], "site": b["site"],
            "location": b["location"], "parent": None, "standard": None, "documents": {}, "components": {},
            "milestones": {}, "notes": b["notes"], "events": {"count": 0, "head": ""}, "created": ev["time"],
            "updated": ev["time"]}


def _same_identifier(a: dict, b: dict) -> bool:
    return a["scheme"] == b["scheme"] and norm(a["issuer"]) == norm(b["issuer"]) and norm(a["value"]) == norm(b["value"])


def apply(state: dict | None, ev: dict, voided: frozenset | set = frozenset()) -> tuple[dict | None, list[str]]:
    """The unit record after ``ev``. A voided event, or a void, changes nothing but the event count. Returns the new state
    (a copy) and any problems the event raises against the state (an empty position removed, for example); a problem never
    stops the reduction."""
    problems: list[str] = []
    n, t = ev["n"], ev["type"]
    if t == "unit.registered":
        if state is not None:
            problems.append(f"event {n} registers a unit that is already registered")
        state = _fresh(ev)
    elif state is None:
        problems.append(f"event {n} comes before the unit was registered")
        return None, problems
    else:
        state = copy.deepcopy(state)
        if n not in voided:
            problems += _apply_change(state, ev)
    state["events"] = {"count": n, "head": ev["digest"]}
    state["updated"] = ev["time"]
    return state, problems


def _apply_change(s: dict, ev: dict) -> list[str]:
    n, t, b = ev["n"], ev["type"], ev["body"]
    if t == "unit.updated":
        for k, v in b["fields"].items():
            s[k] = copy.deepcopy(v)
    elif t == "identifier.added":
        for ident in s["identifiers"]:
            if _same_identifier(ident, b):
                ident.update(active=True, since=n, issuer=b["issuer"], value=b["value"])
                break
        else:
            s["identifiers"].append({"scheme": b["scheme"], "issuer": b["issuer"], "value": b["value"], "active": True,
                                     "since": n})
    elif t in ("identifier.retired", "identifier.removed"):
        hit = [i for i in s["identifiers"] if _same_identifier(i, b)]
        if not hit:
            return [f"event {n} {t.split('.')[1]} identifier {b['value']!r}, which the unit doesn't have"]
        if t == "identifier.retired":
            hit[0]["active"] = False
        else:
            s["identifiers"].remove(hit[0])
    elif t == "status.changed":
        s["status"] = b["to"]
    elif t == "milestone":
        s["milestones"][b["milestone"]] = {"date": ev["effective"], "event": n}
    elif t == "standard.set":
        s["standard"] = {"id": b["standard"], "event": n, "basis": ev["basis"]} if b["standard"] else None
        for doc in b["cleared"]:
            s["documents"].pop(doc, None)
    elif t == "document.set":
        s["documents"][b["document"]] = {"revision": b["revision"], "event": n, "basis": ev["basis"]}
    elif t == "document.reset":
        if s["documents"].pop(b["document"], None) is None:
            return [f"event {n} resets document {b['document']}, which had no unit difference"]
    elif t == "component.installed":
        key = position_key(b["position"])
        problem = [f"event {n} fits something at {b['position']}, which wasn't empty"] if key in s["components"] else []
        s["components"][key] = {"position": b["position"], "part": b["part"], "number": b["number"], "unit": b["unit"],
                                "serial": b["serial"], "lot": b["lot"], "since": ev["effective"], "event": n,
                                "basis": ev["basis"]}
        return problem
    elif t == "component.removed":
        if s["components"].pop(position_key(b["position"]), None) is None:
            return [f"event {n} removes something from {b['position']}, which was empty"]
    elif t == "parent.set":
        s["parent"] = {"unit": b["parent"], "position": b["position"]} if b["parent"] else None
    elif t == "verified":                               # a reported change confirmed: its facts become verified
        named, hit = set(b["events"]), False
        for entry in list(s["documents"].values()) + list(s["components"].values()):
            if entry["event"] in named:
                entry["basis"], hit = "verified", True
        if s["standard"] is not None and s["standard"]["event"] in named:
            s["standard"]["basis"], hit = "verified", True
        if not hit:
            return [f"event {n} verifies events {sorted(named)}, which set nothing the unit still has"]
    return []                                           # note, void, pack.*: history only


def voided_by(events: list[dict]) -> set[int]:
    out: set[int] = set()
    for ev in events:
        if ev["type"] == "void":
            out.update(ev["body"]["events"])
    return out


def reduce(events: list[dict], voided: set[int] | None = None) -> tuple[dict | None, list[str]]:
    """The unit record its events describe, and any problems. ``voided`` defaults to the voids among ``events``."""
    voids = voided_by(events) if voided is None else voided
    state, problems = None, []
    for ev in events:
        state, p = apply(state, ev, voids)
        problems += p
    return state, problems


def packs_in(events: list[dict]) -> list[dict]:
    """The handover packs a history records as issued, in the order they were issued (docs/UNITS.md section 13): number,
    event, time, who, the date it shows the unit on, the SHA-256 of its SHA256SUMS, the key that signed it, and its
    withdrawal if there is one. ``voided`` is true for one a void names: no RevisionWorx writes that (a pack is withdrawn,
    never voided), so it is never vouched for."""
    voids = voided_by(events)
    out: list[dict] = []
    for e in events:
        b = e["body"]
        if e["type"] == "pack.issued":
            out.append({"pack": b["pack"], "event": e["n"], "time": e["time"], "actor": e["actor"], "as_of": b["as_of"],
                        "sums": b["sums"], "key": b["key"], "withdrawn": None, "voided": e["n"] in voids})
        elif e["type"] == "pack.withdrawn":
            for p in out:
                if p["pack"] == b["pack"] and p["withdrawn"] is None:
                    p["withdrawn"] = {"event": e["n"], "time": e["time"], "actor": e["actor"], "reason": e["reason"]}
    return out


def pack_problems(events: list[dict]) -> list[str]:
    """What is wrong with the packs a history records: they are numbered 1, 2, 3 in the order issued, a withdrawal names a
    pack issued before it and not yet withdrawn, and no void names a pack event. RevisionWorx never writes any of these."""
    problems: list[str] = []
    issued: set[int] = set()
    withdrawn: set[int] = set()
    by_n = {e["n"]: e for e in events}
    for e in events:
        t, b = e["type"], e["body"]
        if t == "pack.issued":
            if b["pack"] != len(issued) + 1 or b["pack"] in issued:
                problems.append(f"event {e['n']} issues handover pack {b['pack']}, but the next pack is {len(issued) + 1}")
            issued.add(b["pack"])
        elif t == "pack.withdrawn":
            if b["pack"] not in issued or b["pack"] in withdrawn:
                problems.append(f"event {e['n']} withdraws handover pack {b['pack']}, which isn't issued and in effect")
            withdrawn.add(b["pack"])
        elif t == "void":
            for n in b["events"]:
                if by_n.get(n, {}).get("type", "").startswith("pack."):
                    problems.append(f"event {e['n']} voids event {n}, a handover pack event, which is never voided")
    return problems


def after(events: list[dict], n: int) -> dict | None:
    """The record as it stood once event ``n`` was recorded (voids recorded later don't apply)."""
    return reduce([e for e in events if e["n"] <= n])[0]


def event_day(ev: dict) -> str:
    return day_of(ev["effective"]) or day_of(ev["time"])


def on(events: list[dict], day: str) -> tuple[dict | None, list[int]]:
    """As known now, the record for calendar day ``day`` (``YYYY-MM-DD``): the registration, then every event that happened
    (or, when that's unknown, was recorded) on or before it, with every void applied. Also returns the events that were
    recorded after something that happened later than them: their order is then a guess."""
    voids = voided_by(events)
    chosen = [e for e in events if e["n"] == 1 or (e["type"] != "void" and event_day(e) <= day)]
    late, latest = [], ""
    for e in chosen:
        if e["n"] == 1 or e["n"] in voids:
            continue
        d = event_day(e)
        if d < latest:
            late.append(e["n"])
        latest = max(latest, d)
    state = None
    for e in chosen:
        state, _ = apply(state, e, voids)
    return state, late


# ------------------------------------------------------------------------------------------------- integrity
def verify_chain(unit_id: str, count: int, head: str, fetch: Callable[[int], dict | None]) -> list[dict]:
    """Problems with a unit's history; an empty list means it is intact and complete."""
    problems: list[dict] = []
    prev: str | None = ""
    for n in range(1, count + 1):
        raw = fetch(n)
        if raw is None:
            problems.append({"problem": "missing-event", "n": n})
            prev = None
            continue
        try:
            ev = load_event(raw)
        except MalformedUnitRecord as exc:
            problems.append({"problem": "malformed", "n": n, "detail": str(exc)})
            prev = None
            continue
        if ev["id"] != event_id(unit_id, n) or ev["unit"] != unit_id or ev["n"] != n:
            problems.append({"problem": "misplaced-event", "n": n})
        if ev["digest"] != event_digest(ev):
            problems.append({"problem": "altered-event", "n": n})
        if prev is not None and ev["prev"] != prev:
            problems.append({"problem": "broken-link", "n": n})
        prev = ev["digest"]
    if count and prev is not None and prev != head:
        problems.append({"problem": "head-mismatch", "n": count})
    if fetch(count + 1) is not None:
        problems.append({"problem": "unrecorded-event", "n": count + 1})
    return problems


# --------------------------------------------------------------------------------------------------- display
def summary(ev: dict, name: Callable[[str, str], str] = lambda kind, rid: rid) -> str:
    """One line a person can read. ``name(kind, id)`` turns ids into numbers and labels where the caller can."""
    b, t = ev["body"], ev["type"]
    if t == "unit.registered":
        ids = ", ".join(f"{i['scheme']} {i['value']}" for i in b["identifiers"]) or "no identifiers"
        return f"registered ({ids})"
    if t == "unit.updated":
        parts = []
        for k, v in b["fields"].items():
            if k == "model":
                v = v.get("number") or name("part", v.get("part", ""))
            elif k == "project":
                v = name("project", v) if v else "none"
            parts.append(f"{k} {v!s}" if v != "" else f"{k} cleared")
        return "updated: " + "; ".join(parts)
    if t.startswith("identifier."):
        who = f" ({b['issuer']})" if b["issuer"] else ""
        return f"{b['scheme']} {b['value']}{who} {t.split('.')[1]}"
    if t == "status.changed":
        return f"status {b['from']} -> {b['to']}"
    if t == "milestone":
        return f"{b['milestone']}" + (f" on {ev['effective']}" if ev["effective"] else ", date unknown")
    if t == "standard.set":
        return f"conforms to {name('standard', b['standard'])}" if b["standard"] else "no build standard"
    if t == "document.set":
        if not b["revision"]:
            return f"{name('document', b['document'])} doesn't apply to this unit"
        return f"{name('document', b['document'])} rev {name('revision', b['revision'])}"
    if t == "document.reset":
        return f"{name('document', b['document'])} follows the build standard again"
    if t == "component.installed":
        what = name("unit", b["unit"]) if b["unit"] else (b["number"] or "item")
        extra = "".join(f" {k} {b[k]}" for k in ("serial", "lot") if b[k])
        return f"fitted {what}{extra} at {b['position']}"
    if t == "component.removed":
        what = f" {name('unit', b['unit'])}" if b["unit"] else ""
        return f"removed{what} from {b['position']} ({b['disposition']})"
    if t == "parent.set":
        return f"fitted in {name('unit', b['parent'])} at {b['position']}" if b["parent"] else "taken out of its unit"
    if t == "note":
        return b["text"].splitlines()[0][:120] if b["text"] else "note"
    if t == "verified":
        cited = f" ({name('change', ev['change'])})" if ev.get("change") else ""
        return "verified event " + ", ".join(str(n) for n in b["events"]) + cited
    if t == "void":
        return "voided event " + ", ".join(str(n) for n in b["events"])
    if t == "pack.issued":
        return (f"handover pack {b['pack']} issued" + (f", as known on {b['as_of']}" if b["as_of"] else "")
                + (f", signed with key {b['key']}" if b["key"] else ""))
    if t == "pack.withdrawn":
        return f"handover pack {b['pack']} withdrawn"
    return t  # pragma: no cover


__all__ = ["EVENT_DOMAIN", "after", "apply", "event_day", "event_digest", "make_event", "on", "pack_problems", "packs_in",
           "reduce", "summary", "unit_label", "verify_chain", "voided_by"]
