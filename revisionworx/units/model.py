# SPDX-License-Identifier: GPL-3.0-or-later
"""Units: words, values, validation and the strict record loaders (docs/UNITS.md sections 2 and 3).

Nothing here touches a workspace. Every value written to the journal passes through these functions first, and every record read
back passes through a loader that refuses anything but exactly the documented shape, so a damaged or newer record is never used
partially.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

SCHEMA = 1

CLASSES = ("product", "component", "instrument", "tool")
STATUSES = ("planned", "in-build", "built", "in-service", "out-of-service", "retired", "scrapped")
BASES = ("verified", "reported", "declared", "legacy")
MILESTONES = ("built", "shipped", "installed", "commissioned", "decommissioned")
DISPOSITIONS = ("scrapped", "returned", "repair", "stock", "reused", "unknown")
SCHEMES = ("serial", "tag", "other", "fieldworx")       # identifiers a unit can hold
PERMANENT_SCHEMES = ("serial", "fieldworx")              # retiring one keeps its claim
STANDARD_SCHEME = "standard"                             # a build standard's number (claim owner bst_...)
UPDATABLE = ("class", "name", "model", "project", "customer", "site", "location", "notes")
EVENT_TYPES = ("unit.registered", "unit.updated", "identifier.added", "identifier.retired", "identifier.removed",
               "status.changed", "milestone", "standard.set", "document.set", "document.reset", "component.installed",
               "component.removed", "parent.set", "note", "void")
VOIDABLE = ("unit.updated", "milestone", "standard.set", "document.set", "document.reset", "component.installed",
            "component.removed", "note")

LIMITS = {"name": 200, "model": 100, "customer": 200, "site": 200, "location": 200, "notes": 4000, "value": 100,
          "issuer": 200, "position": 64, "serial": 100, "lot": 100, "reason": 2000, "text": 4000, "evidence": 50,
          "evidence_ref": 200, "evidence_label": 200, "standard_number": 64, "standard_title": 200,
          "standard_documents": 2000}

ID_PATTERNS = {"unit": "unt", "event": "uev", "key": "uky", "standard": "bst", "project": "prj", "document": "doc",
               "revision": "rev", "part": "prt", "change": "eco", "fieldworx": "ast"}
_ID_RES = {k: re.compile(rf"^{p}_[0-9a-f]{{32}}$") for k, p in ID_PATTERNS.items()}
_TXID_RE = re.compile(r"^[0-9a-f]{32}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?Z$")
_LINE_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_TEXT_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
_SPACE_RUN = re.compile(r"\s+")

_KEY_DOMAIN = "revisionworx/units/key/v1"
_EVENT_ID_DOMAIN = "revisionworx/units/event-id/v1"


# ------------------------------------------------------------------------------------------------- errors
class UnitError(Exception):
    """A refusal written for the person who asked. Nothing was changed."""


class ValidationError(UnitError):
    pass


class NotFound(UnitError):
    pass


class Ambiguous(UnitError):
    def __init__(self, message: str, matches: list | None = None):
        super().__init__(message)
        self.matches = matches or []


class DuplicateIdentifier(UnitError):
    def __init__(self, message: str, owner: str = ""):
        super().__init__(message)
        self.owner = owner


class MalformedUnitRecord(UnitError):
    pass


class ReplayRefused(UnitError):
    pass


class ImportProblems(UnitError):
    """An import that would have left something half done. ``problems`` is ``[(row, column, message)]``; nothing was written."""

    def __init__(self, problems: list[tuple[int, str, str]]):
        self.problems = problems
        lines = [f"row {r}{', ' + c if c else ''}: {m}" for r, c, m in problems[:20]]
        more = f"\n... and {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"nothing was imported; {len(problems)} problem(s):\n" + "\n".join(lines) + more)


# ------------------------------------------------------------------------------------------------- values
def is_id(kind: str, value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RES[kind].match(value))


def check_id(kind: str, value: Any, what: str, *, empty_ok: bool = False) -> str:
    if empty_ok and value == "":
        return ""
    if not is_id(kind, value):
        raise ValidationError(f"{what} must be a {ID_PATTERNS[kind]}_ id, not {value!r}")
    return value


def check_txid(txid: str | None) -> None:
    if txid is not None and not (isinstance(txid, str) and _TXID_RE.match(txid)):
        raise ValidationError("txid must be 32 lowercase hex characters")


def _surrogates(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def clean(value: Any, what: str, limit: int, *, multiline: bool = False, required: bool = False) -> str:
    """NFC text, stripped, within its limit and free of control characters (newline and tab allowed when multi-line)."""
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be text")
    if _surrogates(value):
        raise ValidationError(f"{what} contains text that can't be stored (an unpaired surrogate)")
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n") if multiline else value).strip()
    if (_TEXT_CTRL if multiline else _LINE_CTRL).search(text):
        raise ValidationError(f"{what} contains control characters")
    if len(text) > limit:
        raise ValidationError(f"{what} is longer than {limit} characters")
    if required and not text:
        raise ValidationError(f"{what} is required")
    return text


def norm(text: str) -> str:
    """How identifiers are compared: NFKC, case-folded, white space collapsed and stripped."""
    return _SPACE_RUN.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def check_date(value: Any, what: str) -> str:
    """``""`` (unknown), ``YYYY-MM-DD``, or ``YYYY-MM-DDTHH:MM[:SS]Z``, and a real calendar date and time."""
    if value == "":
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be a date such as 2026-03-14")
    try:
        if _DATE_RE.match(value):
            datetime.strptime(value, "%Y-%m-%d")
            return value
        m = _DATETIME_RE.match(value)
        if m:
            datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4) or '00'}", "%Y-%m-%d %H:%M:%S")
            return value
    except ValueError:
        pass
    raise ValidationError(f"{what} must be a date such as 2026-03-14 (or 2026-03-14T09:30Z), not {value!r}")


def day_of(value: str) -> str:
    """The calendar day of a date, a date-time, or a recorded time; ``""`` stays ``""``."""
    return value[:10] if value else ""


def check_choice(value: Any, choices: tuple, what: str) -> str:
    if value not in choices:
        raise ValidationError(f"{what} must be one of {', '.join(choices)}, not {value!r}")
    return value


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"value can't be stored: {exc}") from None


def digest(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + canonical(value)).hexdigest()


def key_id(scheme: str, issuer: str, value: str) -> str:
    h = hashlib.sha256(f"{_KEY_DOMAIN}\0{scheme}\0{norm(issuer)}\0{norm(value)}".encode("utf-8")).hexdigest()
    return f"uky_{h[:32]}"


def event_id(unit_id: str, n: int) -> str:
    h = hashlib.sha256(f"{_EVENT_ID_DOMAIN}\0{unit_id}\0{n}".encode("ascii")).hexdigest()
    return f"uev_{h[:32]}"


def derived_id(prefix: str, txid: str, salt: str) -> str:
    """An id a retry of the same action (same txid) arrives at again."""
    return f"{prefix}_{hashlib.sha256(f'revisionworx/units/{salt}/v1:{txid}'.encode('ascii')).hexdigest()[:32]}"


def fieldworx_asset_id(workspace_id: str, tag_or_serial: str) -> str:
    """The id FieldWorx 0.1 gave an asset in a ``*.fwassets.csv`` register (its bridge's ``asset_id``), so records made
    against it can still find the unit."""
    return "ast_" + hashlib.sha256(f"{workspace_id}\0{tag_or_serial.casefold()}".encode("utf-8")).hexdigest()[:32]


def check_identifier(scheme: Any, value: Any, issuer: Any = "") -> dict:
    scheme = check_choice(scheme, SCHEMES, "identifier scheme")
    value = clean(value, f"{scheme} value", LIMITS["value"], required=True)
    issuer = clean(issuer, f"{scheme} issuer", LIMITS["issuer"])
    if scheme == "fieldworx":
        if not is_id("fieldworx", value):
            raise ValidationError(f"a FieldWorx asset id looks like ast_ and 32 hex digits, not {value!r}")
        if issuer:
            raise ValidationError("a FieldWorx asset id has no issuer")
    return {"scheme": scheme, "issuer": issuer, "value": value}


def check_model(model: Any) -> dict:
    if not isinstance(model, dict) or set(model) != {"part", "number"}:
        raise ValidationError("model must be {part, number}")
    return {"part": check_id("part", model["part"], "model part", empty_ok=True),
            "number": clean(model["number"], "model number", LIMITS["model"])}


def check_position(value: Any) -> str:
    return clean(value, "position", LIMITS["position"], required=True)


def position_key(position: str) -> str:
    return norm(position)


def check_evidence(items: Any) -> list[dict]:
    if not isinstance(items, (list, tuple)) or len(items) > LIMITS["evidence"]:
        raise ValidationError(f"evidence must be a list of at most {LIMITS['evidence']} items")
    out = []
    for e in items:
        if not isinstance(e, dict) or set(e) - {"kind", "ref", "label"} or "kind" not in e:
            raise ValidationError("evidence items are {kind, ref, label}")
        kind = e["kind"]
        if not isinstance(kind, str) or not _EVIDENCE_KIND_RE.match(kind):
            raise ValidationError(f"evidence kind {kind!r} must be lowercase letters, digits and -")
        out.append({"kind": kind, "ref": clean(e.get("ref", ""), "evidence reference", LIMITS["evidence_ref"]),
                     "label": clean(e.get("label", ""), "evidence label", LIMITS["evidence_label"])})
    return out


def actor_record(actor: dict) -> dict:
    """Who did it, as stored: the OS user and host, and in a team workspace the account and its display name."""
    out = {"user": str(actor.get("user", "")), "host": str(actor.get("host", ""))}
    for k in ("account", "name"):
        if actor.get(k):
            out[k] = str(actor[k])
    return out


def unit_label(unit: dict) -> str:
    """What to call a unit: its first active serial, else tag, else other code, else a short id."""
    for scheme in ("serial", "tag", "other"):
        for ident in unit.get("identifiers", ()):
            if ident.get("scheme") == scheme and ident.get("active"):
                return ident["value"]
    return unit.get("id", "")[:12]


# ------------------------------------------------------------------------------------------------- loaders
UNIT_FIELDS = {"schema", "id", "class", "name", "model", "identifiers", "status", "project", "customer", "site", "location",
               "parent", "standard", "documents", "components", "milestones", "notes", "events", "created", "updated"}
EVENT_FIELDS = {"schema", "id", "unit", "n", "type", "effective", "time", "actor", "basis", "evidence", "reason", "change",
                "txid", "input", "body", "prev", "digest"}
KEY_FIELDS = {"schema", "scheme", "issuer", "value", "owner"}
STANDARD_FIELDS = {"schema", "id", "number", "title", "model", "documents", "based_on", "change", "notes", "created",
                   "created_by", "digest"}
COMPONENT_FIELDS = {"position", "part", "number", "unit", "serial", "lot", "since", "event", "basis"}


def _fields(record: Any, want: set, what: str) -> dict:
    if not isinstance(record, dict):
        raise MalformedUnitRecord(f"{what} is not an object")
    got = set(record) - {"_rev"}
    if got != want:
        raise MalformedUnitRecord(f"{what}: fields don't match schema {SCHEMA} (missing {sorted(want - got)}, "
                                  f"unknown {sorted(got - want)})")
    if record.get("schema") != SCHEMA:
        raise MalformedUnitRecord(f"{what} has schema {record.get('schema')!r}; this RevisionWorx reads schema {SCHEMA}. "
                                  "Update RevisionWorx")
    return record


def _str(v: Any, what: str) -> str:
    if not isinstance(v, str):
        raise MalformedUnitRecord(f"{what} must be text")
    return v


def _int(v: Any, what: str, minimum: int = 0) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
        raise MalformedUnitRecord(f"{what} must be a whole number of at least {minimum}")
    return v


def load_unit(record: Any) -> dict:
    """The unit record, refused unless it is exactly schema 1. Returns it without ``_rev``."""
    r = _fields(record, UNIT_FIELDS, "unit")
    what = f"unit {r.get('id')!r}"
    try:
        if not is_id("unit", r["id"]):
            raise MalformedUnitRecord(f"{what}: bad id")
        check_choice(r["class"], CLASSES, "class")
        check_choice(r["status"], STATUSES, "status")
        check_model(r["model"])
        for k in ("name", "customer", "site", "location", "notes", "created", "updated"):
            _str(r[k], f"{what} {k}")
        check_id("project", r["project"], "project", empty_ok=True)
        if not isinstance(r["identifiers"], list):
            raise MalformedUnitRecord(f"{what}: identifiers must be a list")
        for ident in r["identifiers"]:
            if not isinstance(ident, dict) or set(ident) != {"scheme", "issuer", "value", "active", "since"}:
                raise MalformedUnitRecord(f"{what}: an identifier has the wrong fields")
            check_identifier(ident["scheme"], ident["value"], ident["issuer"])
            if not isinstance(ident["active"], bool):
                raise MalformedUnitRecord(f"{what}: identifier 'active' must be true or false")
            _int(ident["since"], f"{what} identifier since", 1)
        parent = r["parent"]
        if parent is not None:
            if not isinstance(parent, dict) or set(parent) != {"unit", "position"} or not is_id("unit", parent["unit"]):
                raise MalformedUnitRecord(f"{what}: parent must be null or {{unit, position}}")
            _str(parent["position"], f"{what} parent position")
        std = r["standard"]
        if std is not None:
            if not isinstance(std, dict) or set(std) != {"id", "event", "basis"} or not is_id("standard", std["id"]):
                raise MalformedUnitRecord(f"{what}: standard must be null or {{id, event, basis}}")
            _int(std["event"], f"{what} standard event", 1)
            check_choice(std["basis"], BASES, "basis")
        if not isinstance(r["documents"], dict):
            raise MalformedUnitRecord(f"{what}: documents must be an object")
        for doc, entry in r["documents"].items():
            if not is_id("document", doc) or not isinstance(entry, dict) or set(entry) != {"revision", "event", "basis"}:
                raise MalformedUnitRecord(f"{what}: a unit difference has the wrong shape")
            check_id("revision", entry["revision"], "revision", empty_ok=True)
            _int(entry["event"], f"{what} document event", 1)
            check_choice(entry["basis"], BASES, "basis")
        if not isinstance(r["components"], dict):
            raise MalformedUnitRecord(f"{what}: components must be an object")
        for key, c in r["components"].items():
            if not isinstance(c, dict) or set(c) != COMPONENT_FIELDS:
                raise MalformedUnitRecord(f"{what}: a component has the wrong fields")
            if position_key(_str(c["position"], "position")) != key:
                raise MalformedUnitRecord(f"{what}: component key {key!r} doesn't match its position")
            check_id("part", c["part"], "component part", empty_ok=True)
            check_id("unit", c["unit"], "component unit", empty_ok=True)
            for k in ("number", "serial", "lot", "since"):
                _str(c[k], f"{what} component {k}")
            _int(c["event"], f"{what} component event", 1)
            check_choice(c["basis"], BASES, "basis")
        if not isinstance(r["milestones"], dict):
            raise MalformedUnitRecord(f"{what}: milestones must be an object")
        for name, m in r["milestones"].items():
            check_choice(name, MILESTONES, "milestone")
            if not isinstance(m, dict) or set(m) != {"date", "event"}:
                raise MalformedUnitRecord(f"{what}: milestone {name} has the wrong fields")
            _str(m["date"], "milestone date")
            _int(m["event"], "milestone event", 1)
        ev = r["events"]
        if not isinstance(ev, dict) or set(ev) != {"count", "head"}:
            raise MalformedUnitRecord(f"{what}: events must be {{count, head}}")
        _int(ev["count"], f"{what} event count", 1)
        if not _HEX64_RE.match(_str(ev["head"], "head")):
            raise MalformedUnitRecord(f"{what}: event head must be a SHA-256")
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


BODY_KEYS = {
    "unit.registered": {"class", "name", "model", "status", "project", "customer", "site", "location", "notes", "identifiers"},
    "unit.updated": {"fields"},
    "identifier.added": {"scheme", "issuer", "value"},
    "identifier.retired": {"scheme", "issuer", "value"},
    "identifier.removed": {"scheme", "issuer", "value"},
    "status.changed": {"from", "to"},
    "milestone": {"milestone"},
    "standard.set": {"standard", "cleared"},
    "document.set": {"document", "revision"},
    "document.reset": {"document"},
    "component.installed": {"position", "part", "number", "unit", "serial", "lot"},
    "component.removed": {"position", "disposition", "unit"},
    "parent.set": {"parent", "position"},
    "note": {"text"},
    "void": {"events"},
}


def _check_body(t: str, b: dict, what: str) -> None:
    if set(b) != BODY_KEYS[t]:
        raise MalformedUnitRecord(f"{what}: a {t} event has the wrong fields")
    if t == "unit.registered":
        if not isinstance(b["identifiers"], list) or not all(
                isinstance(i, dict) and set(i) == {"scheme", "issuer", "value"} for i in b["identifiers"]):
            raise MalformedUnitRecord(f"{what}: identifiers have the wrong shape")
        check_model(b["model"])
    elif t == "unit.updated":
        if not isinstance(b["fields"], dict) or not b["fields"] or set(b["fields"]) - set(UPDATABLE):
            raise MalformedUnitRecord(f"{what}: updated fields have the wrong shape")
    elif t == "standard.set":
        if not isinstance(b["cleared"], list) or not all(is_id("document", d) for d in b["cleared"]):
            raise MalformedUnitRecord(f"{what}: cleared must list document ids")
    elif t == "void":
        if not isinstance(b["events"], list) or not b["events"] or not all(
                isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in b["events"]):
            raise MalformedUnitRecord(f"{what}: a void names event numbers")
    for k, v in b.items():
        if k not in ("identifiers", "model", "fields", "cleared", "events") and not isinstance(v, str):
            raise MalformedUnitRecord(f"{what}: {k} must be text")


def load_event(record: Any) -> dict:
    r = _fields(record, EVENT_FIELDS, "unit event")
    what = f"unit event {r.get('id')!r}"
    try:
        if not is_id("event", r["id"]) or not is_id("unit", r["unit"]):
            raise MalformedUnitRecord(f"{what}: bad id")
        _int(r["n"], f"{what} number", 1)
        check_choice(r["type"], EVENT_TYPES, "event type")
        check_choice(r["basis"], BASES, "basis")
        for k in ("effective", "time", "reason", "prev", "digest", "txid", "input"):
            _str(r[k], f"{what} {k}")
        check_id("change", r["change"], "change", empty_ok=True)
        if not isinstance(r["actor"], dict) or not isinstance(r["evidence"], list) or not isinstance(r["body"], dict):
            raise MalformedUnitRecord(f"{what}: actor, evidence and body must be an object, a list and an object")
        if not _HEX64_RE.match(r["digest"]) or (r["prev"] and not _HEX64_RE.match(r["prev"])):
            raise MalformedUnitRecord(f"{what}: digests must be SHA-256")
        _check_body(r["type"], r["body"], what)
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


def load_key(record: Any) -> dict:
    r = _fields(record, KEY_FIELDS, "identifier claim")
    if r["scheme"] not in SCHEMES + (STANDARD_SCHEME,):
        raise MalformedUnitRecord(f"identifier claim has an unknown scheme {r['scheme']!r}")
    for k in ("issuer", "value", "owner"):
        _str(r[k], f"identifier claim {k}")
    if not (is_id("unit", r["owner"]) or is_id("standard", r["owner"])):
        raise MalformedUnitRecord("identifier claim owner must be a unit or a build standard")
    return {k: v for k, v in r.items() if k != "_rev"}


STANDARD_DOMAIN = "revisionworx/units/standard/v1"


def standard_digest(std: dict) -> str:
    return digest(STANDARD_DOMAIN, {k: v for k, v in std.items() if k not in ("digest", "_rev")})


def load_standard(record: Any) -> dict:
    r = _fields(record, STANDARD_FIELDS, "build standard")
    what = f"build standard {r.get('id')!r}"
    try:
        if not is_id("standard", r["id"]):
            raise MalformedUnitRecord(f"{what}: bad id")
        for k in ("number", "title", "notes", "created", "digest"):
            _str(r[k], f"{what} {k}")
        check_model(r["model"])
        check_id("standard", r["based_on"], "based on", empty_ok=True)
        check_id("change", r["change"], "change", empty_ok=True)
        if not isinstance(r["created_by"], dict):
            raise MalformedUnitRecord(f"{what}: created_by must be an object")
        docs = r["documents"]
        if not isinstance(docs, dict) or not docs:
            raise MalformedUnitRecord(f"{what}: documents must name at least one document")
        for d, rev in docs.items():
            if not is_id("document", d) or not is_id("revision", rev):
                raise MalformedUnitRecord(f"{what}: documents maps document ids to revision ids")
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}
