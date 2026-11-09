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

from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator

from .. import documents as D
from ..workspace import RecordCheck, RecordDelete, RecordPut, Workspace, new_id, now_iso, register_kind
from . import history as H
from .model import (BASES, CLASSES, DISPOSITIONS, KEY_FINGERPRINT_RE, LIMITS, MAX_TRANSFER_UNITS, MERGED, MILESTONES,
                    PERMANENT_SCHEMES, SIGNED_EVIDENCE, TRANSFERRED, WORKSPACE_ID_RE, check_foreign, check_state,
                    STANDARD_SCHEME, STATUSES, UPDATABLE, VOIDABLE, Ambiguous, Changed, DuplicateIdentifier, MalformedUnitRecord,
                    NotFound,
                    ReplayRefused, ValidationError, actor_record, check_choice, check_date, check_evidence, check_id,
                    check_identifier, check_lots, check_model, check_pattern, check_position, check_txid, clean, day_of,
                    derived_id, digest,
                    event_id, is_id, key_id, load_event, load_key, load_numbering, load_standard, load_unit, norm, numbering_id,
                    pattern_period, position_key, render_pattern, standard_digest, unit_label)

KIND = {"unit": "unit", "event": "unitevt", "key": "unitkey", "standard": "buildstd", "effectivity": "uniteff",
        "compliance": "unitcmp", "numbering": "unitseq", "deviation": "unitdev", "devunit": "unitdvu"}
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
        self.key_moves: dict[str, tuple[dict, int]] = {}   # claims given to another owner (a merge), at their revision
        self.merged_ok: set[str] = set()               # merged units this action may touch: only a void of their merge
        self.transferred_ok: set[str] = set()          # transferred units this action may touch: only receiving them back

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
            expected = self.svc.expected.get(unit_id)
            if expected is not None and state["events"]["count"] != expected:
                raise Changed(f"{unit_label(state)} has {state['events']['count']} events in its history, not the {expected} it "
                              "had when this was decided: something was recorded on it since. Nothing was changed; look at it "
                              "again")
            if state["status"] == MERGED and unit_id not in self.merged_ok:
                into = H.merged_into(history)
                raise ValidationError(f"{unit_label(state)} was merged into "
                                      f"{self.svc.describe(into['unit']) if into else 'another unit'}: record it there")
            if state["status"] == TRANSFERRED and unit_id not in self.transferred_ok:
                gone = H.transferred(history)
                raise ValidationError(f"{unit_label(state)} was transferred to "
                                      f"{gone['to'] + ' on ' + gone['time'][:10] if gone else 'another workspace'}: it is "
                                      "recorded in that workspace now")
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

    def move_claim(self, scheme: str, issuer: str, value: str, owner: str, to: str) -> None:
        """Give an identifier's claim to another unit (a merge, or voiding one), at the revision read. Nothing if there is no
        claim (a retired identifier that isn't permanent has none); refused if someone else holds it."""
        kid = key_id(scheme, issuer, value)
        rec = self.svc.ws.get(KIND["key"], kid)
        if rec is None:
            return
        claim = load_key(rec)
        if claim["owner"] != owner:
            raise DuplicateIdentifier(f"{scheme} {value} belongs to {self.svc.describe(claim['owner'])}", claim["owner"])
        self.checks.pop((KIND["key"], kid), None)
        self.key_moves[kid] = ({**claim, "owner": to}, int(rec.get("_rev", 0)))

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
        out += [RecordPut(KIND["key"], kid, rec, expect_rev=rev) for kid, (rec, rev) in self.key_moves.items()]
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
        self.expected: dict[str, int] = {}            # unit id -> its event count when the caller decided (``expecting``)
        self.signed_evidence: list[dict] = []         # field-signature evidence link has checked (devices.signature_evidence)

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
        """Every readable unit but those merged into another: a merged one is history, and its identifiers are the other's."""
        return [u for u in self.scan()[0] if u["status"] != MERGED]

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

    def resolve(self, ref: str, follow: bool = False) -> dict:
        """One unit for a reference a person typed, or ``NotFound``/``Ambiguous`` naming the candidates. With ``follow``, a
        unit merged into another answers as that one (other programs naming an earlier id: docs/UNITS.md section 15)."""
        hits = self.find(ref)
        if not hits:
            raise NotFound(f"no unit {ref!r}")
        if len(hits) > 1:
            names = ", ".join(f"{unit_label(h['unit'])} ({h['unit']['id']})" for h in hits[:10])
            raise Ambiguous(f"{ref!r} names {len(hits)} units: {names}; use the unit id", [h["unit"] for h in hits])
        unit = hits[0]["unit"]
        for _ in range(MAX_DEPTH):
            if not follow or unit["status"] != MERGED:
                return unit
            into = H.merged_into(self.events(unit["id"]))
            if into is None:
                raise MalformedUnitRecord(f"unit {unit['id']} is merged but its history doesn't say into what; run "
                                          "'revisionworx unit verify'")
            unit = self.unit(into["unit"])
        raise MalformedUnitRecord(f"unit {ref!r} is merged into units merged into others {MAX_DEPTH} deep; run "
                                  "'revisionworx unit verify'")

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
        items = check_evidence(list(evidence))
        if any(e["kind"] in SIGNED_EVIDENCE and e not in self.signed_evidence for e in items):
            raise ValidationError("a field signature is added only by 'revisionworx link ... --field-signature', which checks "
                                  "it against the phone enrolled to the account")
        return {"effective": check_date(effective, "effective date"), "basis": check_choice(basis, BASES, "basis"),
                "evidence": items, "reason": clean(reason, "reason", LIMITS["reason"], multiline=True),
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
        unread = sorted(uid for uid in self.expected if tx.states.get(uid) is None)
        if unread:                                   # fail closed: an expectation this action never checked is a mistake
            raise ValidationError(f"unit {unread[0]} was named as unchanged, but this action doesn't concern it")
        self.ws.commit(op, tx.ops(), txid=txid)
        return result

    @contextmanager
    def expecting(self, counts: dict[str, int]) -> Iterator[None]:
        """Act only if each named unit still has the number of events it had when the caller decided: otherwise ``Changed``,
        and nothing is written. The count is compared when the action reads the unit, at the revision its commit insists on, so
        nothing recorded in between can slip past. A retry of an attempt already recorded (same ``txid``) isn't checked: it
        answers what was recorded."""
        for uid, n in counts.items():
            check_id("unit", uid, "unit")
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                raise ValidationError("an expected event count is a whole number from 1")
        before, self.expected = self.expected, dict(counts)
        try:
            yield
        finally:
            self.expected = before

    def _check_replay(self, txid: str, inp: str, replay: list[str]) -> None:
        for rid in replay:
            if is_id("standard", rid):
                continue                                # a standard's id comes from the txid; its content was checked
            kind = next((k for k in ("effectivity", "compliance", "numbering", "deviation", "devunit") if is_id(k, rid)), "")
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
                   "standard", "documents", "milestones", "serial_from", "lots"}
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
                "standard": standard, "documents": docs, "milestones": milestones, "serial_from": serial_from,
                # only when given: a registration without lots asks for exactly what it asked for before, so its retry
                # (the same txid) is still recognised
                **({"lots": check_lots(spec["lots"])} if spec.get("lots") else {})}

    def register(self, txid: str | None = None, **spec: Any) -> dict:
        """Register one unit. ``spec``: identifiers ``[{scheme, value, issuer}]``, ``class``, name, model ``{part, number}``,
        status, project (id), customer, site, location, notes, standard (bst id), documents ``{doc id: rev id or ""}``,
        milestones ``{name: date}``, lots ``[{number, lot, quantity}]`` (the lots it was built from); plus effective, basis,
        evidence, reason, change."""
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
        # units registered together with the same lots were built with them together (a completion of a work order)
        shared = [sum(1 for t in clean_specs if t.get("lots") == s["lots"]) if s.get("lots") else 0 for s in clean_specs]

        def build(tx: _Tx) -> list[str]:
            for uid, s, together in zip(ids, clean_specs, shared):
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
                if s.get("lots"):
                    tx.emit(uid, "lots.used", {"lots": s["lots"], "units": together}, effective=as_built)
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

    # ---------------------------------------------------------------------------------------- handover packs
    def packs(self, unit_id: str) -> list[dict]:
        """The handover packs issued for a unit, in the order issued (docs/UNITS.md section 13)."""
        return H.packs_in(self.events(unit_id))

    def issue_pack(self, unit_id: str, number: int, sums: str, *, as_of: str = "", key: str = "",
                   txid: str | None = None) -> dict:
        """Record that handover pack ``number`` of a unit was issued: ``sums`` is the SHA-256 of its SHA256SUMS, ``as_of`` the
        date it shows the unit on (``""``: as the unit stood when it was made) and ``key`` the fingerprint of the key that
        signed it (``""``: not signed). ``number`` must be the unit's next. ``pack.issue`` calls this inside ``expecting``
        the event count the pack was made from, so a pack is recorded only if it shows the unit as it still stands."""
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValidationError("a handover pack's number is a whole number from 1")
        if not isinstance(sums, str) or len(sums) != 64 or any(ch not in "0123456789abcdef" for ch in sums):
            raise ValidationError("a handover pack is recorded by the SHA-256 of its SHA256SUMS (64 lowercase hex)")
        as_of = check_date(as_of, "the date the pack shows the unit on")
        if as_of and len(as_of) != 10:
            raise ValidationError("the date a pack shows the unit on is a day, such as 2026-11-08")
        if key and not KEY_FINGERPRINT_RE.match(key):
            raise ValidationError(f"{key!r} isn't a key fingerprint (SHA256: and 43 characters, as ssh-keygen -l shows it)")
        meta = self._meta()

        def build(tx: _Tx) -> None:
            unit = tx.unit(unit_id)
            following = len(H.packs_in(self.events(unit_id))) + 1
            if number != following:
                raise Changed(f"the next handover pack of {unit_label(unit)} is {following}, not {number}: another was "
                              "issued since. Nothing was recorded; make the pack again")
            tx.emit(unit_id, "pack.issued", {"pack": number, "sums": sums, "as_of": as_of, "key": key})

        self._run("unit.pack.issue", txid, {"unit": unit_id, "pack": number, "sums": sums, "as_of": as_of, "key": key,
                                            "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    # ------------------------------------------------------------------------------------- transfers (section 16)
    def transfer(self, unit_ids: list[str], to: str, number: int, pack: str, key: str, kept: str,
                 txid: str | None = None) -> list[dict]:
        """Record that ``unit_ids`` (the unit transferred, then each unit fitted in it) were handed over to another workspace,
        in one entry. The first unit's handover pack ``number`` is the transfer pack: ``pack`` is the SHA-256 of its
        SHA256SUMS, ``key`` the key that signed it (``""``: not signed) and ``kept`` the copy this workspace keeps. Each unit
        becomes ``transferred`` and takes no more actions. ``transfer.issue`` calls this inside ``expecting`` the event counts
        the pack was made from."""
        to = clean(to, "who it is transferred to", 200, required=True)
        for what, value in (("pack", pack), ("kept", kept)):
            if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValidationError(f"a transfer's {what} is a SHA-256 (64 lowercase hex)")
        if key and not KEY_FINGERPRINT_RE.match(key):
            raise ValidationError(f"{key!r} isn't a key fingerprint (SHA256: and 43 characters, as ssh-keygen -l shows it)")
        ids = list(unit_ids)
        if not 1 <= len(ids) <= MAX_TRANSFER_UNITS or len(set(ids)) != len(ids) or not all(is_id("unit", u) for u in ids):
            raise ValidationError(f"a transfer names from 1 to {MAX_TRANSFER_UNITS} different units")
        meta = self._meta()

        def build(tx: _Tx) -> None:
            root = tx.unit(ids[0])
            following = len(H.packs_in(self.events(ids[0]))) + 1
            if number != following:
                raise Changed(f"the next handover pack of {unit_label(root)} is {following}, not {number}: another was "
                              "issued since. Nothing was recorded; make the transfer again")
            tx.emit(ids[0], "pack.issued", {"pack": number, "sums": pack, "as_of": "", "key": key})
            for uid in ids:
                tx.unit(uid)
                tx.emit(uid, "unit.transferred", {"to": to, "pack": pack, "key": key, "kept": kept, "units": ids})

        self._run("unit.transfer", txid, {"units": ids, "to": to, "pack": number, "sums": pack, "key": key, "kept": kept,
                                          "meta": meta}, ids, build, meta)
        return [self.unit(uid) for uid in ids]

    def receive(self, units: list[dict], source: dict, pack: str, key: str, txid: str | None = None) -> list[dict]:
        """Record units another workspace transferred here, in one entry: ``units`` are ``{id, state, foreign, history}`` as
        ``transfer.receive`` prepared them (``foreign``: what is kept of the documents that aren't this workspace's;
        ``history``: the SHA-256 of their history before, kept in the content store). A unit new here starts with
        ``unit.received``; one this workspace transferred away is received back. Identifiers are claimed as at registration;
        one another unit here holds refuses the whole receipt."""
        if not isinstance(source, dict) or set(source) != {"workspace", "name"} \
                or not WORKSPACE_ID_RE.match(str(source.get("workspace", ""))):
            raise ValidationError("a receipt names the workspace the units came from")
        if source["workspace"] == self.ws.id:
            raise ValidationError("these units were transferred from this workspace: they can't be received here")
        if not isinstance(pack, str) or len(pack) != 64 or any(ch not in "0123456789abcdef" for ch in pack):
            raise ValidationError("a receipt names its transfer pack by the SHA-256 of its SHA256SUMS")
        if key and not KEY_FINGERPRINT_RE.match(key):
            raise ValidationError(f"{key!r} isn't a key fingerprint")
        if not 1 <= len(units) <= MAX_TRANSFER_UNITS or len({u["id"] for u in units}) != len(units):
            raise ValidationError(f"a receipt has from 1 to {MAX_TRANSFER_UNITS} different units")
        for u in units:
            check_id("unit", u["id"], "unit")
            check_state(u["state"])
            for doc, f in u["foreign"].items():
                check_foreign(doc, f)
        meta = self._meta()

        def build(tx: _Tx) -> list[str]:
            for u in units:
                uid, before = u["id"], None
                if self.ws.get(KIND["unit"], uid) is not None:
                    tx.transferred_ok.add(uid)
                    before = tx.unit(uid)
                    if before["status"] != TRANSFERRED:
                        raise ValidationError(f"this workspace has its own record of {unit_label(before)}, with the same id, "
                                              "so it can't be received as well; nothing was recorded")
                else:
                    tx.new_unit(uid)
                tx.emit(uid, "unit.received", {"from": dict(source), "pack": pack, "key": key, "state": u["state"],
                                               "foreign": u["foreign"], "history": u["history"]})
                held = set()
                for i in u["state"]["identifiers"]:
                    if i["active"] or i["scheme"] in PERMANENT_SCHEMES:
                        tx.claim(i["scheme"], i["issuer"], i["value"], uid)
                        held.add(key_id(i["scheme"], i["issuer"], i["value"]))
                for i in (before or {}).get("identifiers", []):
                    if key_id(i["scheme"], i["issuer"], i["value"]) not in held:
                        tx.release(i["scheme"], i["issuer"], i["value"], uid)
            return [u["id"] for u in units]

        self._run("unit.receive", txid, {"units": units, "from": source, "pack": pack, "key": key, "meta": meta},
                  [u["id"] for u in units], build, meta)
        return [self.unit(u["id"]) for u in units]

    def withdraw_pack(self, unit_id: str, number: int, reason: str, txid: str | None = None, **meta_args: Any) -> dict:
        """Record that handover pack ``number`` is withdrawn: it was issued, and is not to be relied on now. A reason is
        required. The pack stays in the register; checking it says it was withdrawn, when, by whom and why."""
        meta_args["reason"] = reason
        meta = self._meta(**meta_args)
        if not meta["reason"]:
            raise ValidationError("say why the pack is withdrawn (a reason is required)")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValidationError("name the handover pack by its number")

        def build(tx: _Tx) -> None:
            unit = tx.unit(unit_id)
            mine = [p for p in H.packs_in(self.events(unit_id)) if p["pack"] == number]
            if not mine:
                raise ValidationError(f"{unit_label(unit)} has no handover pack {number}")
            if mine[-1]["withdrawn"] is not None:
                raise ValidationError(f"handover pack {number} of {unit_label(unit)} is already withdrawn")
            tx.emit(unit_id, "pack.withdrawn", {"pack": number})

        self._run("unit.pack.withdraw", txid, {"unit": unit_id, "pack": number, "meta": meta}, [unit_id], build, meta)
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
            history = self.events(unit_id)
            by_n = {e["n"]: e for e in history}
            if all(by_n.get(n, {}).get("type") == "unit.merged" for n in targets):
                tx.merged_ok.add(unit_id)                # undoing its merge is the one thing a merged unit takes
            tx.unit(unit_id)
            already = H.voided_by(history)
            for n in targets:
                ev = by_n.get(n)
                if ev is None:
                    raise ValidationError(f"there is no event {n}")
                if ev["type"] not in VOIDABLE:
                    raise ValidationError(f"event {n} ({ev['type']}) can't be voided; "
                                          + ("remove the identifier instead" if ev["type"].startswith("identifier.")
                                             else "withdraw the pack instead ('revisionworx unit withdraw-pack')"
                                             if ev["type"] == "pack.issued" else "record what is right instead"))
                if n in already:
                    raise ValidationError(f"event {n} is already void")
                if ev["type"].startswith("component."):
                    self._void_component(tx, unit_id, ev, history, set(targets) | already, mirrors)
                elif ev["type"] in ("unit.merged", "unit.absorbed"):
                    self._void_merge(tx, unit_id, ev, history, mirrors)
            for comp, ns in mirrors.items():
                tx.emit(comp, "void", {"events": sorted(ns)})
            tx.emit(unit_id, "void", {"events": targets})

        self._run("unit.void", txid, {"unit": unit_id, "events": targets, "meta": meta}, [unit_id], build, meta)
        return self.unit(unit_id)

    def _void_merge(self, tx: _Tx, unit_id: str, ev: dict, history: list[dict], mirrors: dict[str, list[int]]) -> None:
        """Undo a merge: its other half is voided in the same entry, and the identifiers and their claims go back."""
        if ev["type"] == "unit.merged":
            dup, survivor = unit_id, ev["body"]["into"]
        else:
            dup, survivor = ev["body"]["unit"], unit_id
        other = survivor if dup == unit_id else dup
        tx.merged_ok.add(dup)
        tx.unit(other)
        theirs = self.events(other)
        voids = H.voided_by(theirs) | set(mirrors.get(other, []))
        want = "unit.absorbed" if other == survivor else "unit.merged"
        pair = [e for e in theirs if e["type"] == want and e["n"] not in voids
                and e["body"]["unit" if want == "unit.absorbed" else "into"] == (dup if other == survivor else survivor)]
        if not pair:
            raise ValidationError(f"{self.describe(other)} doesn't record this merge; run 'revisionworx unit verify'")
        absorbed = pair[-1] if other == survivor else ev
        later = [e for e in (theirs if other == survivor else history) if e["n"] > absorbed["n"]
                 and e["type"].startswith(("identifier.", "unit.merged", "unit.absorbed"))]
        if later:
            raise ValidationError(f"{self.describe(survivor)} has recorded identifier changes or merges since (event "
                                  f"{later[0]['n']}): undo those first, or record what is right instead")
        mirrors.setdefault(other, []).append(pair[-1]["n"])
        for i in absorbed["body"]["identifiers"]:
            tx.move_claim(i["scheme"], i["issuer"], i["value"], survivor, dup)

    # -------------------------------------------------------------------------------------------- merging
    def merge(self, duplicate: str, into: str, reason: str, txid: str | None = None, **meta_args: Any) -> dict:
        """Record that ``duplicate`` is the same physical unit as ``into`` (docs/UNITS.md section 15), in one entry: the
        duplicate's identifiers, and their claims, go to ``into``; the duplicate becomes ``merged`` and takes no more
        actions. Each keeps its own history. A reason is required. Refused if the duplicate has anything fitted, is fitted
        in something, has engineering-change or deviation records, or if the two hold different serials or tags from the
        same issuer: a person settles those first."""
        meta_args["reason"] = reason
        meta = self._meta(**meta_args)
        if not meta["reason"]:
            raise ValidationError("say why these are the same unit (a reason is required)")
        if duplicate == into:
            raise ValidationError("a unit can't be merged into itself")
        from .deviation import all_devunits
        from .effect import all_compliance

        def build(tx: _Tx) -> None:
            dup, keep = tx.unit(duplicate), tx.unit(into)
            label, other = unit_label(dup), unit_label(keep)
            if dup["components"]:
                raise ValidationError(f"{label} has something fitted: take it out first, or record it on {other}")
            if dup["parent"] is not None:
                raise ValidationError(f"{label} is fitted in {self.describe(dup['parent']['unit'])}: take it out first")
            if any(r["unit"] == duplicate for r in all_compliance(self)[0]):
                raise ValidationError(f"{label} has engineering change records: settle them on it, or record them on {other}, "
                                      "before merging")
            if any(r["unit"] == duplicate for r in all_devunits(self)[0]):
                raise ValidationError(f"{label} is covered by a deviation or concession: record it on {other} first")
            for a in dup["identifiers"]:
                for b in keep["identifiers"]:
                    same_kind = a["scheme"] == b["scheme"] and norm(a["issuer"]) == norm(b["issuer"])
                    if same_kind and norm(a["value"]) == norm(b["value"]):
                        raise ValidationError(f"both units hold {a['scheme']} {a['value']}: remove it from one of them first")
                    if same_kind and a["scheme"] in ("serial", "tag") and a["active"] and b["active"]:
                        raise ValidationError(f"{label} has {a['scheme']} {a['value']} and {other} has {b['scheme']} "
                                              f"{b['value']}{' from ' + a['issuer'] if a['issuer'] else ''}: retire the wrong "
                                              "one first")
            moved = [{"scheme": i["scheme"], "issuer": i["issuer"], "value": i["value"]} for i in dup["identifiers"]]
            tx.emit(duplicate, "unit.merged", {"into": into, "identifiers": moved})
            tx.emit(into, "unit.absorbed", {"unit": duplicate, "identifiers": [
                {**m, "active": i["active"]} for m, i in zip(moved, dup["identifiers"])]})
            for i in moved:
                tx.move_claim(i["scheme"], i["issuer"], i["value"], duplicate, into)

        self._run("unit.merge", txid, {"duplicate": duplicate, "into": into, "meta": meta}, [duplicate, into], build, meta)
        return self.unit(into)

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
