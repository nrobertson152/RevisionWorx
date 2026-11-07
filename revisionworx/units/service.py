# SPDX-License-Identifier: GPL-3.0-or-later
"""The unit register: every action is one journal entry (docs/UNITS.md section 6).

An action reads the records it decides on, checks them, builds the events, and hands the workspace **one** commit holding the
events (each written with ``expect_rev=0``), the unit records (each at the revision the action read), identifier claims (created with
``expect_rev=0`` or deleted at their revision) and ``RecordCheck``s. If anything it read has changed by then, the core refuses the
whole entry with ``ConflictError`` and nothing is written. Every core guarantee for an entry (fencing, crash recovery, signatures in a
team workspace, ``verify``) therefore holds for every unit action.

Reads work on a read-only handle; actions need a writable one.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .. import documents as D
from ..workspace import RecordCheck, RecordDelete, RecordPut, Workspace, new_id, now_iso, register_kind
from . import history as H
from .model import (BASES, CLASSES, DISPOSITIONS, LIMITS, MILESTONES, PERMANENT_SCHEMES, STANDARD_SCHEME, STATUSES, UPDATABLE,
                    VOIDABLE, Ambiguous, DuplicateIdentifier, MalformedUnitRecord, NotFound, ReplayRefused, ValidationError,
                    actor_record, check_choice, check_date, check_evidence, check_id, check_identifier, check_model,
                    check_pattern, check_position, check_txid, clean, day_of, derived_id, digest, event_id, is_id, key_id,
                    load_event, load_key, load_numbering, load_standard, load_unit, norm, numbering_id, pattern_period,
                    position_key, render_pattern, standard_digest, unit_label)

KIND = {"unit": "unit", "event": "unitevt", "key": "unitkey", "standard": "buildstd", "effectivity": "uniteff",
        "compliance": "unitcmp", "numbering": "unitseq"}
for _kind in KIND.values():
    register_kind(_kind)

_INPUT_DOMAIN = "revisionworx/units/input/v1"
MAX_DEPTH = 64                        # how deep units may be fitted inside each other


class _Tx:
    """One action being built: working copies of the units it touches and everything its commit will hold."""

    def __init__(self, svc: "UnitService", txid: str | None, input_digest: str, meta: dict):
        self.svc = svc
        self.txid = txid or ""
        self.input = input_digest
        self.meta = meta
        self.time = svc.clock()
        self.actor = actor_record(svc.actor())
        self.states: dict[str, dict | None] = {}
        self.revs: dict[str, int] = {}
        self.order: list[str] = []
        self.events: list[dict] = []
        self.key_puts: dict[str, dict] = {}
        self.key_deletes: dict[str, int] = {}
        self.checks: dict[tuple[str, str], int] = {}
        self.extra_puts: list[RecordPut] = []
        self.numberings: dict[str, dict] = {}          # working copies of numberings this action issues from
        self.numbering_revs: dict[str, int] = {}

    # -------------------------------------------------------------------------------------------- units
    def unit(self, unit_id: str) -> dict:
        """The working copy of a unit (read once per action, at a revision the commit will insist on)."""
        if unit_id not in self.states:
            rec = self.svc.ws.get(KIND["unit"], unit_id)
            if rec is None:
                raise NotFound(f"no unit {unit_id}")
            state = load_unit(rec)
            history = self.svc.events(unit_id)       # refuses a missing or altered event
            if H.reduce(history) != (state, []):     # fail closed: nothing is added on top of a damaged record
                raise MalformedUnitRecord(f"unit {unit_label(state)}: its record doesn't match its history, so nothing "
                                          "was changed; run 'revisionworx unit verify'")
            self.revs[unit_id] = int(rec.get("_rev", 0))
            self.states[unit_id] = state
            self.order.append(unit_id)
        state = self.states[unit_id]
        if state is None:
            raise NotFound(f"unit {unit_id} is being registered in this action")
        return state

    def new_unit(self, unit_id: str) -> None:
        if unit_id in self.states or self.svc.ws.get(KIND["unit"], unit_id) is not None:
            raise ValidationError(f"unit {unit_id} already exists")
        self.states[unit_id], self.revs[unit_id] = None, 0
        self.order.append(unit_id)

    def emit(self, unit_id: str, type_: str, body: dict, **meta: Any) -> dict:
        state = self.states[unit_id]
        n = 1 if state is None else state["events"]["count"] + 1
        prev = "" if state is None else state["events"]["head"]
        m = {**self.meta, **meta}
        ev = H.make_event(unit_id, n, prev, type_, body, effective=m["effective"], time=self.time, actor=self.actor,
                          basis=m["basis"], evidence=m["evidence"], reason=m["reason"], change=m["change"], txid=self.txid,
                          input_digest=self.input)
        if type_ == "void":                          # a void changes earlier history: compute the record from the start
            new_state, problems = H.reduce(self.svc.events(unit_id) + [e for e in self.events if e["unit"] == unit_id] + [ev])
        else:
            new_state, problems = H.apply(state, ev)
        if problems:
            raise ValidationError("this would leave the unit's history inconsistent: " + "; ".join(problems))
        load_unit(new_state)                         # everything written is readable by the strict loader
        self.states[unit_id] = new_state
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------------------------------------- claims
    def claim(self, scheme: str, issuer: str, value: str, owner: str) -> None:
        kid = key_id(scheme, issuer, value)
        pending = self.key_puts.get(kid)
        if pending is not None:
            if pending["owner"] != owner:
                raise DuplicateIdentifier(f"{scheme} {value} appears twice in this action", pending["owner"])
            return
        if kid in self.key_deletes:
            raise ValidationError(f"{scheme} {value} is released and claimed again in one action")
        rec = self.svc.ws.get(KIND["key"], kid)
        if rec is not None:
            owner_now = load_key(rec)["owner"]
            if owner_now != owner:
                raise DuplicateIdentifier(f"{scheme} {value}{' (' + issuer + ')' if issuer else ''} already belongs to "
                                          f"{self.svc.describe(owner_now)}", owner_now)
            self.checks[(KIND["key"], kid)] = int(rec.get("_rev", 0))
            return
        self.key_puts[kid] = {"schema": 1, "scheme": scheme, "issuer": issuer, "value": value, "owner": owner}

    def release(self, scheme: str, issuer: str, value: str, owner: str) -> None:
        kid = key_id(scheme, issuer, value)
        rec = self.svc.ws.get(KIND["key"], kid)
        if rec is not None and load_key(rec)["owner"] == owner:
            self.checks.pop((KIND["key"], kid), None)
            self.key_deletes[kid] = int(rec.get("_rev", 0))

    def next_serial(self, numbering: str, day: str) -> tuple[str, str]:
        """The next free serial of a numbering for ``day``, and its issuer. The numbering is written at the revision read, so two
        actions can't issue the same number; a number whose serial is already claimed is skipped (docs/UNITS.md section 11)."""
        nid = numbering if is_id("numbering", numbering) else numbering_id(numbering)
        if nid not in self.numberings:
            rec = self.svc.ws.get(KIND["numbering"], nid)
            if rec is None:
                raise NotFound(f"no numbering {numbering!r}")
            self.numberings[nid], self.numbering_revs[nid] = load_numbering(rec), int(rec.get("_rev", 0))
        num = self.numberings[nid]
        period = pattern_period(num["pattern"], day)
        n = num["counters"].get(period, num["start"] - 1)
        for _ in range(100_000):
            n += 1
            value = render_pattern(num["pattern"], n, day)
            kid = key_id("serial", num["issuer"], value)
            if kid not in self.key_puts and self.svc.ws.get(KIND["key"], kid) is None:
                num["counters"][period] = n
                return value, num["issuer"]
        raise ValidationError(f"numbering {num['name']} found no free serial in 100,000 tries")  # pragma: no cover

    def check(self, kind: str, rid: str) -> None:
        rec = self.svc.ws.get(kind, rid)
        if rec is None:
            raise NotFound(f"no {kind} {rid}")
        self.checks[(kind, rid)] = int(rec.get("_rev", 0))

    # ---------------------------------------------------------------------------------------- the commit
    def ops(self) -> list:
        out: list = [RecordPut(KIND["event"], ev["id"], ev, expect_rev=0) for ev in self.events]
        read_only: list[str] = []
        for uid in self.order:                       # every unit the action read is pinned at that revision
            state = self.states[uid]
            if state is not None and (self.revs[uid] == 0 or any(ev["unit"] == uid for ev in self.events)):
                out.append(RecordPut(KIND["unit"], uid, state, expect_rev=self.revs[uid]))
            elif state is not None:
                read_only.append(uid)
        for uid in read_only:
            self.checks.setdefault((KIND["unit"], uid), self.revs[uid])
        out += [RecordPut(KIND["key"], kid, rec, expect_rev=0) for kid, rec in self.key_puts.items()]
        out += [RecordDelete(KIND["key"], kid, expect_rev=rev) for kid, rev in self.key_deletes.items()]
        out += self.extra_puts
        out += [RecordPut(KIND["numbering"], nid, rec, expect_rev=self.numbering_revs[nid])
                for nid, rec in self.numberings.items()]
        written = {(o.kind, o.id) for o in out}
        if len(written) != len(out):
            raise AssertionError("one action wrote the same record twice")  # pragma: no cover - a bug, never data
        out += [RecordCheck(kind, rid, rev) for (kind, rid), rev in self.checks.items() if (kind, rid) not in written]
        return out


class UnitService:
    """The unit register on an open workspace handle. ``actor`` and ``clock`` exist for tests."""

    def __init__(self, ws: Workspace, actor: Callable[[], dict] | None = None, clock: Callable[[], str] | None = None):
        self.ws = ws
        self.actor = actor or ws.actor
        self.clock = clock or now_iso
        self.last_replayed = False

    # ================================================================================================ reading
    def unit(self, unit_id: str) -> dict:
        """A unit record (with ``_rev``), checked against its schema."""
        rec = self.ws.get(KIND["unit"], unit_id) if is_id("unit", unit_id) else None
        if rec is None:
            raise NotFound(f"no unit {unit_id}")
        return {**load_unit(rec), "_rev": rec.get("_rev", 0)}

    def scan(self) -> tuple[list[dict], list[dict]]:
        """Every readable unit, by label, and the ones that couldn't be read (never used partially)."""
        units, problems = [], []
        for uid, rec in self.ws.list(KIND["unit"]).items():
            try:
                units.append({**load_unit(rec), "_rev": rec.get("_rev", 0)})
            except MalformedUnitRecord as exc:
                problems.append({"id": uid, "detail": str(exc)})
        units.sort(key=lambda u: (norm(unit_label(u)), u["id"]))
        return units, problems

    def units(self) -> list[dict]:
        return self.scan()[0]

    def events(self, unit_id: str) -> list[dict]:
        """A unit's history, oldest first. Refuses (``MalformedUnitRecord``) if it isn't complete and intact."""
        unit = self.ws.get(KIND["unit"], unit_id)
        if unit is None:
            raise NotFound(f"no unit {unit_id}")
        count = int(load_unit(unit)["events"]["count"])
        out = []
        for n in range(1, count + 1):
            rec = self.ws.get(KIND["event"], event_id(unit_id, n))
            if rec is None:
                raise MalformedUnitRecord(f"unit {unit_id}: event {n} is missing; run 'revisionworx unit verify'")
            ev = load_event(rec)
            if ev["digest"] != H.event_digest(ev) or ev["n"] != n or ev["unit"] != unit_id or \
                    ev["prev"] != (out[-1]["digest"] if out else ""):
                raise MalformedUnitRecord(f"unit {unit_id}: event {n} has been altered; run 'revisionworx unit verify'")
            out.append(ev)
        return out

    def standard(self, standard_id: str) -> dict:
        rec = self.ws.get(KIND["standard"], standard_id) if is_id("standard", standard_id) else None
        if rec is None:
            raise NotFound(f"no build standard {standard_id}")
        std = load_standard(rec)
        if std["digest"] != standard_digest(std):
            raise MalformedUnitRecord(f"build standard {std['number']} has been altered; run 'revisionworx unit verify'")
        return std

    def standards(self) -> list[dict]:
        out = []
        for sid in self.ws.list(KIND["standard"]):
            try:
                out.append(self.standard(sid))
            except MalformedUnitRecord:
                continue
        return sorted(out, key=lambda s: norm(s["number"]))

    def find(self, text: str) -> list[dict]:
        """Units an exact code names: a unit id, or any identifier's value (active or retired), compared as identifiers are.
        Every match is returned, each with why it matched; nothing is guessed."""
        q = text.strip()
        if is_id("unit", q):
            try:
                return [{"unit": self.unit(q), "why": "unit id"}]
            except NotFound:
                return []
        nq = norm(q)
        hits = []
        for u in self.units():
            for ident in u["identifiers"]:
                if norm(ident["value"]) == nq:
                    why = ident["scheme"] + ("" if ident["active"] else " (former)")
                    hits.append({"unit": u, "why": why})
                    break
        return hits

    def resolve(self, ref: str) -> dict:
        """One unit for a reference a person typed, or ``NotFound``/``Ambiguous`` naming the candidates."""
        hits = self.find(ref)
        if not hits:
            raise NotFound(f"no unit {ref!r}")
        if len(hits) > 1:
            names = ", ".join(f"{unit_label(h['unit'])} ({h['unit']['id']})" for h in hits[:10])
            raise Ambiguous(f"{ref!r} names {len(hits)} units: {names}; use the unit id", [h["unit"] for h in hits])
        return hits[0]["unit"]

    def resolve_standard(self, ref: str) -> dict:
        ref = ref.strip()
        if is_id("standard", ref):
            return self.standard(ref)
        hits = [s for s in self.standards() if norm(s["number"]) == norm(ref)]
        if not hits:
            raise NotFound(f"no build standard {ref!r}")
        return hits[0]

    def numbering(self, ref: str) -> dict:
        """A numbering by name or id."""
        ref = (ref or "").strip()
        nid = ref if is_id("numbering", ref) else numbering_id(ref)
        rec = self.ws.get(KIND["numbering"], nid) if ref else None
        if rec is None:
            raise NotFound(f"no numbering {ref!r}")
        return {**load_numbering(rec), "_rev": rec.get("_rev", 0)}

    def numberings(self) -> list[dict]:
        out = []
        for rec in self.ws.list(KIND["numbering"]).values():
            try:
                out.append(load_numbering(rec))
            except MalformedUnitRecord:
                continue
        return sorted(out, key=lambda n: norm(n["name"]))

    def next_serial_preview(self, ref: str, day: str = "") -> str:
        """The serial a registration would get now, without taking it."""
        tx = _Tx(self, None, "", {})
        return tx.next_serial(self.numbering(ref)["id"], day_of(check_date(day, "date")) if day else self.clock()[:10])[0]

    def describe(self, rid: str) -> str:
        """A short name for an id, for messages."""
        try:
            if is_id("unit", rid):
                u = self.unit(rid)
                return f"unit {unit_label(u)}" + ("" if unit_label(u) == rid[:12] else f" ({rid})")
            if is_id("standard", rid):
                return f"build standard {self.standard(rid)['number']}"
        except (NotFound, MalformedUnitRecord):
            pass
        return rid

    # ========================================================================================== engineering refs
    def project_id(self, ref: str) -> str:
        """A project by id, number or name (exactly one)."""
        ref = (ref or "").strip()
        if not ref:
            return ""
        projects = self.ws.list("project")
        if ref in projects:
            return ref
        hits = [pid for pid, p in projects.items()
                if norm(ref) in (norm(str(p.get("number") or "")), norm(str(p.get("name") or "")))]
        if not hits:
            raise ValidationError(f"no project {ref!r}")
        if len(hits) > 1:
            raise ValidationError(f"{ref!r} names {len(hits)} projects; use the project id")
        return hits[0]

    def document_id(self, ref: str) -> str:
        """A document by id or number (exactly one in the workspace)."""
        ref = (ref or "").strip()
        if is_id("document", ref):
            if self.ws.get("document", ref) is None:
                raise ValidationError(f"no document {ref}")
            return ref
        hits = [d["id"] for d in D.list_documents(self.ws) if d.get("number") and norm(d["number"]) == norm(ref)]
        if not hits:
            raise ValidationError(f"no document numbered {ref!r}")
        if len(hits) > 1:
            raise ValidationError(f"{ref!r} is the number of {len(hits)} documents; use the document id")
        return hits[0]

    def revision_id(self, document_id: str, ref: str) -> str:
        """A revision of a document, by id or label."""
        ref = (ref or "").strip()
        if is_id("revision", ref):
            self.check_revision(document_id, ref)
            return ref
        for r in D.revisions_of(self.ws, document_id):
            if norm(r["label"]) == norm(ref):
                return r["id"]
        doc = self.ws.get("document", document_id) or {}
        raise ValidationError(f"{doc.get('number') or document_id} has no revision {ref!r}")

    def doc_rev(self, ref: str) -> tuple[str, str]:
        """``DOC@REV`` (number or id, label or id) as (document id, revision id)."""
        doc, sep, rev = ref.rpartition("@")
        if not sep or not doc.strip() or not rev.strip():
            raise ValidationError(f"{ref!r} should look like DWG-100@B (a document and one of its revisions)")
        did = self.document_id(doc)
        return did, self.revision_id(did, rev)

    def check_revision(self, document_id: str, revision_id: str) -> dict:
        if self.ws.get("document", document_id) is None:
            raise ValidationError(f"no document {document_id}")
        rev = self.ws.get("revision", revision_id)
        if rev is None:
            raise ValidationError(f"no revision {revision_id}")
        if rev.get("document_id") != document_id:
            raise ValidationError(f"revision {revision_id} belongs to another document, not {document_id}")
        return rev

    def _part(self, part_id: str) -> str:
        if part_id and self.ws.get("part", part_id) is None:
            raise ValidationError(f"no part {part_id} in the parts catalog")
        return part_id

    def _change(self, change_id: str) -> str:
        check_id("change", change_id, "change", empty_ok=True)
        if change_id and self.ws.get("changeord", change_id) is None:
            raise ValidationError(f"no change order {change_id}")
        return change_id

    # ================================================================================================ actions
    def _meta(self, effective: str = "", basis: str = "declared", evidence: Iterable = (), reason: str = "",
              change: str = "") -> dict:
        return {"effective": check_date(effective, "effective date"), "basis": check_choice(basis, BASES, "basis"),
                "evidence": check_evidence(list(evidence)), "reason": clean(reason, "reason", LIMITS["reason"], multiline=True),
                "change": self._change(change)}

    def _run(self, op: str, txid: str | None, args: dict, replay: list[str], build: Callable[[_Tx], Any],
             meta: dict) -> Any:
        check_txid(txid)
        inp = digest(_INPUT_DOMAIN, {"op": op, "args": args}) if txid else ""
        self.last_replayed = False
        if txid is not None:
            state = self.ws.transaction_outcome(txid)["state"]
            if state == "fenced":
                raise ReplayRefused(f"attempt {txid} never took effect because its session lost the writer lock; nothing "
                                    "changed. Start again with a new txid")
            if state == "committed":
                self._check_replay(txid, inp, replay)
                self.last_replayed = True
                return None
        tx = _Tx(self, txid, inp, meta)
        result = build(tx)
        self.ws.commit(op, tx.ops(), txid=txid)
        return result

    def _check_replay(self, txid: str, inp: str, replay: list[str]) -> None:
        for rid in replay:
            if is_id("standard", rid):
                continue                                # a standard's id comes from the txid; its content was checked
            kind = next((k for k in ("effectivity", "compliance", "numbering") if is_id(k, rid)), "")
            if kind:
                rec = self.ws.get(KIND[kind], rid) or {}
                mine = [rec] if rec.get("txid") == txid else [s for s in rec.get("steps", ()) if s.get("txid") == txid]
                if not mine:
                    raise ReplayRefused(f"txid {txid} was already used for a different action")
                if any(m.get("input") != inp for m in mine):
                    raise ReplayRefused(f"txid {txid} was already used for this change with different input; nothing was "
                                        "changed. Start again with a new txid")
                continue
            try:
                mine = [e for e in self.events(rid) if e["txid"] == txid]
            except NotFound:
                mine = []
            if not mine:
                raise ReplayRefused(f"txid {txid} was already used for a different action")
            if any(e["input"] != inp for e in mine):
                raise ReplayRefused(f"txid {txid} was already used for this unit with different input; nothing was changed. "
                                    "Start again with a new txid")

    # ------------------------------------------------------------------------------------------- register
    def _spec(self, spec: dict, where: str) -> dict:
        allowed = {"identifiers", "class", "name", "model", "status", "project", "customer", "site", "location", "notes",
                   "standard", "documents", "milestones", "serial_from"}
        unknown = set(spec) - allowed
        if unknown:
            raise ValidationError(f"{where}: unknown fields {sorted(unknown)}")
        idents = []
        for i in spec.get("identifiers", []):
            ident = check_identifier(i.get("scheme"), i.get("value"), i.get("issuer", ""))
            if any(norm(x["value"]) == norm(ident["value"]) and x["scheme"] == ident["scheme"]
                   and norm(x["issuer"]) == norm(ident["issuer"]) for x in idents):
                raise ValidationError(f"{where}: {ident['scheme']} {ident['value']} is given twice")
            idents.append(ident)
        model = spec.get("model") or {"part": "", "number": ""}
        if not isinstance(model, dict):
            raise ValidationError(f"{where}: model is {{part, number}}")
        model = check_model({"part": model.get("part", ""), "number": model.get("number", "")})
        self._part(model["part"])
        standard = spec.get("standard", "") or ""
        if standard:
            standard = self.standard(standard)["id"]
        docs = {}
        for doc, rev in (spec.get("documents") or {}).items():
            check_id("document", doc, "document")
            if rev:
                self.check_revision(doc, rev)
            elif self.ws.get("document", doc) is None:
                raise ValidationError(f"{where}: no document {doc}")
            docs[doc] = rev
        milestones = {}
        for name, date in (spec.get("milestones") or {}).items():
            milestones[check_choice(name, MILESTONES, "milestone")] = check_date(date, f"{name} date")
        project = spec.get("project", "") or ""
        if project:
            check_id("project", project, "project")
            if self.ws.get("project", project) is None:
                raise ValidationError(f"{where}: no project {project}")
        serial_from = spec.get("serial_from", "") or ""
        if serial_from:
            num = self.numbering(serial_from)
            if any(i["scheme"] == "serial" and norm(i["issuer"]) == norm(num["issuer"]) for i in idents):
                raise ValidationError(f"{where}: give a serial or a numbering to issue one, not both")
            serial_from = num["id"]
        return {"identifiers": idents, "class": check_choice(spec.get("class", "product"), CLASSES, "class"),
                "name": clean(spec.get("name", ""), "name", LIMITS["name"]), "model": model,
                "status": check_choice(spec.get("status", "planned"), STATUSES, "status"), "project": project,
                "customer": clean(spec.get("customer", ""), "customer", LIMITS["customer"]),
                "site": clean(spec.get("site", ""), "site", LIMITS["site"]),
                "location": clean(spec.get("location", ""), "location", LIMITS["location"]),
                "notes": clean(spec.get("notes", ""), "notes", LIMITS["notes"], multiline=True),
                "standard": standard, "documents": docs, "milestones": milestones, "serial_from": serial_from}

    def register(self, txid: str | None = None, **spec: Any) -> dict:
        """Register one unit. ``spec``: identifiers ``[{scheme, value, issuer}]``, ``class``, name, model ``{part, number}``,
        status, project (id), customer, site, location, notes, standard (bst id), documents ``{doc id: rev id or ""}``,
        milestones ``{name: date}``; plus effective, basis, evidence, reason, change."""
        meta = {k: spec.pop(k) for k in ("effective", "basis", "evidence", "reason", "change") if k in spec}
        if "cls" in spec:                            # ``class`` can't be a Python keyword argument
            spec["class"] = spec.pop("cls")
        return self.register_many([spec], txid=txid, **meta)[0]

    def register_many(self, specs: list[dict], txid: str | None = None, *, effective: str = "", basis: str = "declared",
                      evidence: Iterable = (), reason: str = "", change: str = "", op: str = "unit.register") -> list[dict]:
        """Register several units in one entry: all of them or none."""
        if not specs:
            raise ValidationError("nothing to register")
        meta = self._meta(effective, basis, evidence, reason, change)
        clean_specs = [self._spec(s, f"unit {i + 1}") for i, s in enumerate(specs)]
        ids = [derived_id("unt", txid, f"unit:{i}") if txid else new_id("unt") for i in range(len(specs))]

        def build(tx: _Tx) -> list[str]:
            for uid, s in zip(ids, clean_specs):
                tx.new_unit(uid)
                body = {k: s[k] for k in ("class", "name", "model", "status", "project", "customer", "site", "location",
                                          "notes")}
                body["identifiers"] = list(s["identifiers"])
                if s["serial_from"]:
                    day = day_of(meta["effective"] or s["milestones"].get("built", "") or tx.time)
                    value, issuer = tx.next_serial(s["serial_from"], day)
                    body["identifiers"].insert(0, {"scheme": "serial", "issuer": issuer, "value": value})
                tx.emit(uid, "unit.registered", body)
                for ident in body["identifiers"]:
                    tx.claim(ident["scheme"], ident["issuer"], ident["value"], uid)
                # The configuration given at registration is the one it was built to: it dates from the build, when known.
                as_built = meta["effective"] or s["milestones"].get("built", "")
                if s["standard"]:
                    tx.emit(uid, "standard.set", {"standard": s["standard"], "cleared": []}, effective=as_built)
                for doc in sorted(s["documents"]):
                    tx.emit(uid, "document.set", {"document": doc, "revision": s["documents"][doc]}, effective=as_built)
                for name in MILESTONES:
                    if name in s["milestones"]:
                        tx.emit(uid, "milestone", {"milestone": name}, effective=s["milestones"][name])
            return ids

        args = {"specs": clean_specs, "meta": meta}
        self._run(op, txid, args, ids, build, meta)
        return [self.unit(uid) for uid in ids]

    # --------------------------------------------------------------------------------------------- identity
    def update(self, unit_id: str, txid: str | None = None, *, effective: str = "", basis: str = "declared",
               evidence: Iterable = (), reason: str = "", change: str = "", **fields: Any) -> dict:
        """Change descriptive fields: ``class`` (or ``cls``), name, model, project, customer, site, location, notes."""
        if "cls" in fields:
            fields["class"] = fields.pop("cls")
        unknown = set(fields) - set(UPDATABLE)
        if unknown:
            raise ValidationError(f"can't update {sorted(unknown)}; identifiers, status and configuration have their own "
                                  "actions")
        meta = self._meta(effective, basis, evidence, reason, change)
        new: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "class":
                new[k] = check_choice(v, CLASSES, "class")
            elif k == "model":
                if not isinstance(v, dict):
                    raise ValidationError("model is {part, number}")
                new[k] = check_model({"part": v.get("part", ""), "number": v.get("number", "")})
                self._part(new[k]["part"])
            elif k == "project":
                new[k] = self.project_id(v) if v else ""
            elif k == "notes":
                new[k] = clean(v, "notes", LIMITS["notes"], multiline=True)
            else:
                new[k] = clean(v, k, LIMITS[k])

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            changed = {k: v for k, v in new.items() if u[k] != v}
            if not changed:
                raise ValidationError("nothing to change")
            tx.emit(unit_id, "unit.updated", {"fields": changed})

        self._run("unit.update", txid, {"unit": unit_id, "fields": new, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def add_identifier(self, unit_id: str, scheme: str, value: str, issuer: str = "", txid: str | None = None,
                       **meta_args: Any) -> dict:
        ident = check_identifier(scheme, value, issuer)
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            for x in u["identifiers"]:
                if x["scheme"] == ident["scheme"] and norm(x["issuer"]) == norm(ident["issuer"]) \
                        and norm(x["value"]) == norm(ident["value"]) and x["active"]:
                    raise ValidationError(f"{unit_label(u)} already has {scheme} {value}")
            tx.claim(ident["scheme"], ident["issuer"], ident["value"], unit_id)
            tx.emit(unit_id, "identifier.added", ident)

        self._run("unit.identifier", txid, {"unit": unit_id, "add": ident, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def retire_identifier(self, unit_id: str, scheme: str, value: str, issuer: str = "", txid: str | None = None,
                          **meta_args: Any) -> dict:
        """No longer current (re-serialized, re-tagged). A serial keeps its claim; a tag is free for another unit."""
        return self._drop_identifier("retired", unit_id, scheme, value, issuer, txid, meta_args)

    def remove_identifier(self, unit_id: str, scheme: str, value: str, issuer: str = "", txid: str | None = None,
                          **meta_args: Any) -> dict:
        """Entered by mistake: taken out, and its claim released. Needs a reason."""
        return self._drop_identifier("removed", unit_id, scheme, value, issuer, txid, meta_args)

    def _drop_identifier(self, how: str, unit_id: str, scheme: str, value: str, issuer: str, txid: str | None,
                         meta_args: dict) -> dict:
        ident = check_identifier(scheme, value, issuer)
        meta = self._meta(**meta_args)
        if how == "removed" and not meta["reason"]:
            raise ValidationError("say why the identifier was entered by mistake (a reason is required)")

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            hit = [x for x in u["identifiers"] if x["scheme"] == ident["scheme"] and norm(x["issuer"]) == norm(ident["issuer"])
                   and norm(x["value"]) == norm(ident["value"])]
            if not hit or (how == "retired" and not hit[0]["active"]):
                raise ValidationError(f"{unit_label(u)} has no {'current ' if how == 'retired' else ''}{scheme} {value}")
            stored = {"scheme": hit[0]["scheme"], "issuer": hit[0]["issuer"], "value": hit[0]["value"]}
            if how == "removed" or stored["scheme"] not in PERMANENT_SCHEMES:
                tx.release(stored["scheme"], stored["issuer"], stored["value"], unit_id)
            tx.emit(unit_id, f"identifier.{how}", stored)

        self._run("unit.identifier", txid, {"unit": unit_id, how: ident, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def set_status(self, unit_id: str, status: str, txid: str | None = None, **meta_args: Any) -> dict:
        to = check_choice(status, STATUSES, "status")
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            if u["status"] == to:
                raise ValidationError(f"{unit_label(u)} is already {to}")
            if u["status"] == "scrapped":
                raise ValidationError(f"{unit_label(u)} is scrapped; nothing follows that")
            if u["status"] == "retired" and not meta["reason"]:
                raise ValidationError("say why a retired unit is coming back (a reason is required)")
            tx.emit(unit_id, "status.changed", {"from": u["status"], "to": to})

        self._run("unit.status", txid, {"unit": unit_id, "to": to, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def milestone(self, unit_id: str, name: str, date: str, txid: str | None = None, **meta_args: Any) -> dict:
        """Built, shipped, installed, commissioned or decommissioned, on ``date`` (``""`` when unknown)."""
        name = check_choice(name, MILESTONES, "milestone")
        meta_args["effective"] = check_date(date, f"{name} date")
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            tx.unit(unit_id)
            tx.emit(unit_id, "milestone", {"milestone": name})

        self._run("unit.milestone", txid, {"unit": unit_id, "milestone": name, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def note(self, unit_id: str, text: str, txid: str | None = None, **meta_args: Any) -> dict:
        text = clean(text, "note", LIMITS["text"], multiline=True, required=True)
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            tx.unit(unit_id)
            tx.emit(unit_id, "note", {"text": text})

        self._run("unit.note", txid, {"unit": unit_id, "text": text, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    # ---------------------------------------------------------------------------------------- build standards
    def create_numbering(self, name: str, pattern: str, *, issuer: str = "", start: int = 1, txid: str | None = None) -> dict:
        """A numbering that issues serials (docs/UNITS.md section 11). Its name is unique."""
        name = clean(name, "name", 64, required=True)
        pattern = check_pattern(pattern)
        issuer = clean(issuer, "issuer", LIMITS["issuer"])
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ValidationError("start must be a whole number, 0 or more")
        nid = numbering_id(name)
        replaying = txid is not None and self.ws.transaction_outcome(txid)["state"] == "committed"
        if not replaying and self.ws.get(KIND["numbering"], nid) is not None:
            raise ValidationError(f"there is already a numbering {name!r}")
        meta = self._meta()

        def build(tx: _Tx) -> dict:
            rec = {"schema": 1, "id": nid, "name": name, "pattern": pattern, "issuer": issuer, "start": start, "counters": {},
                   "created": tx.time, "created_by": tx.actor, "txid": tx.txid, "input": tx.input}
            load_numbering(rec)
            tx.extra_puts.append(RecordPut(KIND["numbering"], nid, rec, expect_rev=0))
            return rec

        self._run("unit.numbering.new", txid, {"name": name, "pattern": pattern, "issuer": issuer, "start": start}, [nid],
                  build, meta)
        return self.numbering(nid)

    def create_standard(self, number: str, title: str, documents: dict[str, str], *, model: dict | None = None,
                        based_on: str = "", change: str = "", notes: str = "", allow_unreleased: bool = False,
                        txid: str | None = None) -> dict:
        """A build standard: a named set of document revisions, written once. ``documents`` maps document ids to revision
        ids. Revisions that aren't released or superseded need ``allow_unreleased``."""
        check_txid(txid)
        number = clean(number, "build standard number", LIMITS["standard_number"], required=True)
        title = clean(title, "title", LIMITS["standard_title"])
        model = check_model({"part": (model or {}).get("part", ""), "number": (model or {}).get("number", "")})
        self._part(model["part"])
        notes = clean(notes, "notes", LIMITS["notes"], multiline=True)
        if based_on:
            based_on = self.standard(based_on)["id"]
        change = self._change(change)
        if not documents:
            raise ValidationError("a build standard names at least one document revision")
        if len(documents) > LIMITS["standard_documents"]:
            raise ValidationError(f"a build standard names at most {LIMITS['standard_documents']} documents")
        lc = D.get_lifecycle(self.ws)
        fine = {lc["released"], lc["superseded"]}
        docs = {}
        for doc, rev_id in documents.items():
            check_id("document", doc, "document")
            check_id("revision", rev_id, "revision")
            rev = self.check_revision(doc, rev_id)
            if rev.get("state") not in fine and not allow_unreleased:
                d = self.ws.get("document", doc) or {}
                raise ValidationError(f"{d.get('number') or doc} rev {rev.get('label')} is {rev.get('state')}, not released; "
                                      "build standards normally name released revisions (allow unreleased to go ahead)")
            docs[doc] = rev_id
        sid = derived_id("bst", txid, "standard") if txid else new_id("bst")
        fields = {"number": number, "title": title, "model": model, "documents": docs, "based_on": based_on,
                  "change": change, "notes": notes}
        self.last_replayed = False
        if txid is not None:
            state = self.ws.transaction_outcome(txid)["state"]
            if state == "fenced":
                raise ReplayRefused(f"attempt {txid} never took effect; start again with a new txid")
            if state == "committed":
                existing = self.standard(sid) if self.ws.get(KIND["standard"], sid) else None
                if existing is None or any(existing[k] != v for k, v in fields.items()):
                    raise ReplayRefused(f"txid {txid} was already used for a different action")
                self.last_replayed = True
                return existing
        record = {"schema": 1, "id": sid, **fields, "created": self.clock(), "created_by": actor_record(self.actor())}
        record["digest"] = standard_digest(record)
        load_standard(record)
        tx = _Tx(self, txid, "", {"effective": "", "basis": "declared", "evidence": [], "reason": "", "change": ""})
        if self.ws.get(KIND["standard"], sid) is not None:
            raise ValidationError(f"build standard {sid} already exists")
        tx.claim(STANDARD_SCHEME, "", number, sid)
        tx.extra_puts.append(RecordPut(KIND["standard"], sid, record, expect_rev=0))
        self.ws.commit("unit.standard.create", tx.ops(), txid=txid)
        return self.standard(sid)

    def set_standard(self, unit_ids: list[str], standard_id: str, *, clear_differences: bool = False,
                     txid: str | None = None, **meta_args: Any) -> list[dict]:
        """Record that units conform to a build standard (``""``: to none). With ``clear_differences``, unit differences for
        documents the standard names are dropped in the same entry. One entry for every unit listed."""
        if not unit_ids:
            raise ValidationError("name at least one unit")
        if len(set(unit_ids)) != len(unit_ids):
            raise ValidationError("a unit is listed twice")
        std = self.standard(standard_id) if standard_id else None
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            if std is not None:
                tx.check(KIND["standard"], std["id"])
            for uid in unit_ids:
                u = tx.unit(uid)
                cleared = sorted(d for d in u["documents"] if std is not None and d in std["documents"]) \
                    if clear_differences else []
                current = (u["standard"] or {}).get("id", "")
                if current == (std["id"] if std else "") and not cleared:
                    raise ValidationError(f"{unit_label(u)} already conforms to "
                                          f"{std['number'] if std else 'no build standard'}")
                tx.emit(uid, "standard.set", {"standard": std["id"] if std else "", "cleared": cleared})

        self._run("unit.standard.set", txid, {"units": unit_ids, "standard": standard_id, "clear": clear_differences,
                                              "meta": meta}, list(unit_ids), build, meta)
        return [self.unit(uid) for uid in unit_ids]

    # -------------------------------------------------------------------------------------- unit differences
    def set_document(self, unit_id: str, document_id: str, revision_id: str, txid: str | None = None,
                     **meta_args: Any) -> dict:
        """This unit uses ``revision_id`` of the document instead of its standard's (``""``: the document doesn't apply)."""
        check_id("document", document_id, "document")
        if revision_id:
            check_id("revision", revision_id, "revision")
            self.check_revision(document_id, revision_id)
        elif self.ws.get("document", document_id) is None:
            raise ValidationError(f"no document {document_id}")
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            have = u["documents"].get(document_id)
            if have is not None and have["revision"] == revision_id:
                raise ValidationError("the unit already records that")
            tx.emit(unit_id, "document.set", {"document": document_id, "revision": revision_id})

        self._run("unit.document", txid, {"unit": unit_id, "document": document_id, "revision": revision_id,
                                          "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def reset_document(self, unit_id: str, document_id: str, txid: str | None = None, **meta_args: Any) -> dict:
        """Drop the unit difference: the build standard applies to that document again."""
        check_id("document", document_id, "document")
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            if document_id not in u["documents"]:
                raise ValidationError(f"{unit_label(u)} has no difference for that document")
            tx.emit(unit_id, "document.reset", {"document": document_id})

        self._run("unit.document", txid, {"unit": unit_id, "reset": document_id, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    # ------------------------------------------------------------------------------------------- components
    def _ancestors(self, tx: _Tx, unit_id: str) -> list[str]:
        out, cur = [], tx.unit(unit_id)
        while cur["parent"] is not None:
            pid = cur["parent"]["unit"]
            if pid in out or len(out) > MAX_DEPTH:
                raise ValidationError(f"units are fitted in each other in a loop at {pid}; run 'revisionworx unit verify'")
            out.append(pid)
            cur = tx.unit(pid)
        return out

    def _remove_in(self, tx: _Tx, unit_id: str, key: str, disposition: str) -> None:
        u = tx.unit(unit_id)
        c = u["components"][key]
        tx.emit(unit_id, "component.removed", {"position": c["position"], "disposition": disposition, "unit": c["unit"]})
        if c["unit"]:
            comp = tx.unit(c["unit"])
            if comp["parent"] != {"unit": unit_id, "position": c["position"]}:
                raise ValidationError(f"{unit_label(comp)} doesn't record being fitted at {c['position']}; run "
                                      "'revisionworx unit verify'")
            tx.emit(c["unit"], "parent.set", {"parent": "", "position": ""})

    def install(self, unit_id: str, position: str, *, part: str = "", number: str = "", component: str = "",
                serial: str = "", lot: str = "", replace: bool = False, disposition: str = "unknown",
                txid: str | None = None, **meta_args: Any) -> dict:
        """Fit something at a position. ``component`` is a unit fitted in its own right; otherwise give the part (catalog id)
        or its number, and a serial or lot if it has one. ``replace`` removes what is there first (``disposition``)."""
        position = check_position(position)
        key = position_key(position)
        part = self._part(check_id("part", part, "part", empty_ok=True))
        number = clean(number, "part number", LIMITS["model"])
        serial = clean(serial, "serial", LIMITS["serial"])
        lot = clean(lot, "lot", LIMITS["lot"])
        disposition = check_choice(disposition, DISPOSITIONS, "disposition")
        check_id("unit", component, "component", empty_ok=True)
        if not (part or number or component):
            raise ValidationError("say what is fitted: a part number, a catalog part or a component unit")
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            if key in u["components"]:
                if not replace:
                    raise ValidationError(f"{position} on {unit_label(u)} already holds "
                                          f"{_component_words(self, u['components'][key])}; replace it instead")
                self._remove_in(tx, unit_id, key, disposition)
            comp_number = number
            if component:
                if component == unit_id:
                    raise ValidationError("a unit can't be fitted in itself")
                comp = tx.unit(component)
                if comp["parent"] is not None:
                    raise ValidationError(f"{unit_label(comp)} is fitted in {self.describe(comp['parent']['unit'])} at "
                                          f"{comp['parent']['position']}; take it out there first")
                if component in self._ancestors(tx, unit_id):
                    raise ValidationError(f"{unit_label(comp)} contains {unit_label(u)}; it can't also be fitted inside it")
                comp_number = number or comp["model"]["number"]
            tx.emit(unit_id, "component.installed", {"position": position, "part": part, "number": comp_number,
                                                      "unit": component, "serial": serial, "lot": lot})
            if component:
                tx.emit(component, "parent.set", {"parent": unit_id, "position": position})

        self._run("unit.install", txid, {"unit": unit_id, "position": position, "part": part, "number": number,
                                         "component": component, "serial": serial, "lot": lot, "replace": replace,
                                         "disposition": disposition, "meta": meta},
                  [unit_id] + ([component] if component else []), build, meta)
        return self.unit(unit_id)

    def remove(self, unit_id: str, position: str, disposition: str = "unknown", txid: str | None = None,
               **meta_args: Any) -> dict:
        position = check_position(position)
        key = position_key(position)
        disposition = check_choice(disposition, DISPOSITIONS, "disposition")
        meta = self._meta(**meta_args)

        def build(tx: _Tx) -> None:
            u = tx.unit(unit_id)
            if key not in u["components"]:
                raise ValidationError(f"nothing is recorded at {position} on {unit_label(u)}")
            self._remove_in(tx, unit_id, key, disposition)

        before = self.unit(unit_id)["components"].get(key, {}).get("unit", "")
        self._run("unit.remove", txid, {"unit": unit_id, "position": position, "disposition": disposition, "meta": meta},
                  [unit_id] + ([before] if before else []), build, meta)
        return self.unit(unit_id)

    # -------------------------------------------------------------------------------------------------- void
    def void(self, unit_id: str, events: list[int], reason: str, txid: str | None = None, **meta_args: Any) -> dict:
        """Mark recorded events as mistakes (a reason is required). The events stay in the history; the unit record is
        computed without them. Component events take their matching event on the component unit with them."""
        meta_args["reason"] = reason
        meta = self._meta(**meta_args)
        if not meta["reason"]:
            raise ValidationError("say why these events are mistakes (a reason is required)")
        targets = sorted(set(events))
        if not targets or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in targets):
            raise ValidationError("name the events to void by number")
        mirrors: dict[str, list[int]] = {}

        def build(tx: _Tx) -> None:
            tx.unit(unit_id)
            history = self.events(unit_id)
            already = H.voided_by(history)
            by_n = {e["n"]: e for e in history}
            for n in targets:
                ev = by_n.get(n)
                if ev is None:
                    raise ValidationError(f"there is no event {n}")
                if ev["type"] not in VOIDABLE:
                    raise ValidationError(f"event {n} ({ev['type']}) can't be voided; "
                                          + ("remove the identifier instead" if ev["type"].startswith("identifier.")
                                             else "record what is right instead"))
                if n in already:
                    raise ValidationError(f"event {n} is already void")
                if ev["type"].startswith("component."):
                    self._void_component(tx, unit_id, ev, history, set(targets) | already, mirrors)
            for comp, ns in mirrors.items():
                tx.emit(comp, "void", {"events": sorted(ns)})
            tx.emit(unit_id, "void", {"events": targets})

        self._run("unit.void", txid, {"unit": unit_id, "events": targets, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def _void_component(self, tx: _Tx, unit_id: str, ev: dict, history: list[dict], ignore: set[int],
                        mirrors: dict[str, list[int]]) -> None:
        key = position_key(ev["body"]["position"])
        for later in history:
            if later["n"] > ev["n"] and later["n"] not in ignore and later["type"].startswith("component.") \
                    and position_key(later["body"]["position"]) == key:
                raise ValidationError(f"event {later['n']} also records {ev['body']['position']}; void it too, or record what "
                                      "is right instead")
        comp = ev["body"]["unit"]
        if not comp:
            return
        tx.unit(comp)
        comp_history = self.events(comp)
        comp_void = H.voided_by(comp_history) | set(mirrors.get(comp, []))
        sets = [e for e in comp_history if e["type"] == "parent.set" and e["n"] not in comp_void]
        want = {"parent": unit_id, "position": ev["body"]["position"]} if ev["type"] == "component.installed" else \
            {"parent": "", "position": ""}
        if not sets or {"parent": sets[-1]["body"]["parent"], "position": sets[-1]["body"]["position"]} != want:
            raise ValidationError(f"{unit_label(tx.unit(comp))} has moved since; record what is right instead")
        mirrors.setdefault(comp, []).append(sets[-1]["n"])


def _component_words(svc: UnitService, c: dict) -> str:
    if c["unit"]:
        return svc.describe(c["unit"])
    words = c["number"] or c["part"] or "something"
    return words + "".join(f" {k} {c[k]}" for k in ("serial", "lot") if c[k])


def service_for(ws: Workspace, **kwargs: Any) -> UnitService:
    return UnitService(ws, **kwargs)
