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
from typing import Any, Iterable

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
               "component.removed", "parent.set", "note", "verified", "void")
VOIDABLE = ("unit.updated", "milestone", "standard.set", "document.set", "document.reset", "component.installed",
            "component.removed", "note", "verified")

# Engineering changes reaching units (docs/UNITS.md section 10)
EFFECTIVITY_KINDS = ("production", "retrofit-required", "retrofit-optional", "unit", "documentation")
REQUIRED_KINDS = ("retrofit-required", "unit")          # a unit in scope is out of compliance until verified
SCOPE_RULES = ("candidates", "standard", "serial-range", "customer", "units", "none")
COMPLIANCE_STATES = ("pending", "offered", "accepted", "scheduled", "deferred", "declined", "installed", "verified",
                     "not-applicable", "reversed")
_AFTER_INSTALL = ("installed", "verified")
TRANSITIONS = {
    True: {"pending": ("scheduled", "deferred", "not-applicable") + _AFTER_INSTALL,
           "scheduled": ("pending", "deferred", "not-applicable") + _AFTER_INSTALL,
           "deferred": ("pending", "scheduled", "not-applicable") + _AFTER_INSTALL,
           "installed": ("verified", "reversed"), "verified": ("reversed",)},
    False: {"offered": ("accepted", "declined", "not-applicable") + _AFTER_INSTALL,
            "accepted": ("scheduled", "declined") + _AFTER_INSTALL,
            "scheduled": ("accepted", "deferred", "not-applicable") + _AFTER_INSTALL,
            "deferred": ("accepted", "scheduled", "not-applicable") + _AFTER_INSTALL,
            "declined": ("accepted",),
            "installed": ("verified", "reversed"), "verified": ("reversed",)},
}
NEEDS_REASON = ("deferred", "declined", "not-applicable", "reversed")
SIGNED_EVIDENCE = ("field-signature", "field-signer")  # added only by link --field-signature, after checking (ADR-028)
DEVIATION_KINDS = ("deviation", "concession")       # permission before the work; acceptance of units already made
DEVIATION_STATES = ("in-effect", "cleared", "not-used")
DEVIATION_TRANSITIONS = {"in-effect": ("cleared", "not-used")}   # cleared: a temporary one, the unit conforms again
DONE_STATES = ("verified", "not-applicable")            # a required change in any other state is outstanding

LIMITS = {"name": 200, "model": 100, "customer": 200, "site": 200, "location": 200, "notes": 4000, "value": 100,
          "issuer": 200, "position": 64, "serial": 100, "lot": 100, "reason": 2000, "text": 4000, "evidence": 50,
          "evidence_ref": 200, "evidence_label": 200, "standard_number": 64, "standard_title": 200,
          "standard_documents": 2000, "deviation_number": 64, "deviation_title": 200, "departure": 4000,
          "deviation_documents": 200, "deviation_units": 10000}

ID_PATTERNS = {"unit": "unt", "event": "uev", "key": "uky", "standard": "bst", "project": "prj", "document": "doc",
               "revision": "rev", "part": "prt", "change": "eco", "fieldworx": "ast", "effectivity": "uef",
               "compliance": "ucp", "numbering": "usq", "deviation": "udv", "devunit": "udu"}
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


class Changed(UnitError):
    """Something was recorded on the unit since the caller read it, and the caller asked to act only on what it read."""


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


def effectivity_id(change_id: str) -> str:
    """One effectivity decision per change order: its id comes from the order's."""
    return "uef_" + hashlib.sha256(f"revisionworx/units/effectivity/v1\0{change_id}".encode("ascii")).hexdigest()[:32]


def compliance_id(change_id: str, unit_id: str) -> str:
    """One compliance record per change order and unit."""
    h = hashlib.sha256(f"revisionworx/units/compliance/v1\0{change_id}\0{unit_id}".encode("ascii")).hexdigest()
    return f"ucp_{h[:32]}"


def deviation_id(number: str) -> str:
    """A deviation's or concession's id comes from its number, so two can't share one."""
    return "udv_" + hashlib.sha256(f"revisionworx/units/deviation-id/v1\0{norm(number)}".encode("utf-8")).hexdigest()[:32]


def devunit_id(deviation: str, unit_id: str) -> str:
    """One record per deviation and unit it covers."""
    h = hashlib.sha256(f"revisionworx/units/deviation-unit/v1\0{deviation}\0{unit_id}".encode("ascii")).hexdigest()
    return f"udu_{h[:32]}"


def numbering_id(name: str) -> str:
    """A numbering's id comes from its name, so two can't share one."""
    return "usq_" + hashlib.sha256(f"revisionworx/units/numbering/v1\0{norm(name)}".encode("utf-8")).hexdigest()[:32]


_TOKEN_RE = re.compile(r"\{(seq(?::(\d{1,2}))?|yyyy|yy|mm)\}")


def check_pattern(pattern: Any) -> str:
    """A serial pattern: exactly one ``{seq}``/``{seq:N}`` (N 1-12), any of ``{yyyy}``, ``{yy}``, ``{mm}``, no other braces, and
    never more than 100 characters once filled."""
    p = clean(pattern, "pattern", 200, required=True)
    seqs = [m for m in _TOKEN_RE.finditer(p) if m.group(1).startswith("seq")]
    if len(seqs) != 1:
        raise ValidationError("a pattern has exactly one {seq} or {seq:N}, e.g. NH-{yy}-{seq:4}")
    width = int(seqs[0].group(2) or 1)
    if not 1 <= width <= 12:
        raise ValidationError("{seq:N} pads to 1 to 12 digits")
    rest = _TOKEN_RE.sub("", p)
    if "{" in rest or "}" in rest:
        raise ValidationError("a pattern's only braces are {seq}, {seq:N}, {yyyy}, {yy} and {mm}")
    longest = len(_TOKEN_RE.sub(lambda m: "x" * {"yyyy": 4, "yy": 2, "mm": 2}.get(m.group(1), 12), p))
    if longest > LIMITS["value"]:
        raise ValidationError(f"this pattern can give serials longer than {LIMITS['value']} characters")
    return p


def pattern_period(pattern: str, day: str) -> str:
    """Which run a number belongs to: the year (and month) the pattern shows, or one run."""
    tokens = {m.group(1) for m in _TOKEN_RE.finditer(pattern)}
    year = day[:4] if tokens & {"yyyy", "yy"} else ""
    return year + (f"-{day[5:7]}" if year and "mm" in tokens else "")


def render_pattern(pattern: str, n: int, day: str) -> str:
    def fill(m: re.Match) -> str:
        tok = m.group(1)
        if tok.startswith("seq"):
            return str(n).zfill(int(m.group(2) or 1))
        return {"yyyy": day[:4], "yy": day[2:4], "mm": day[5:7]}[tok]
    return _TOKEN_RE.sub(fill, pattern)


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


def parse_evidence(items: Iterable[str]) -> list[dict]:
    """``KIND:REF[=LABEL]`` as typed on a command line (``inspection:ins_...``, ``work-order:WO-00012=first article``) as
    evidence items; ``check_evidence`` then checks them."""
    evidence = []
    for e in items:
        kind, sep, rest = e.partition(":")
        if not sep:
            raise ValidationError(f"evidence {e!r} should look like KIND:REF, e.g. inspection:ins_...")
        ref, _, label = rest.partition("=")
        evidence.append({"kind": kind.strip(), "ref": ref.strip(), "label": label.strip()})
    return evidence


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
    "verified": {"events"},
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
    elif t in ("void", "verified"):
        if not isinstance(b["events"], list) or not b["events"] or not all(
                isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in b["events"]):
            raise MalformedUnitRecord(f"{what}: a {t} event names event numbers")
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


EFFECTIVITY_DOMAIN = "revisionworx/units/effectivity/v1"
EFFECTIVITY_FIELDS = {"schema", "id", "change", "kind", "standard", "documents", "scope", "units", "reason", "decided",
                      "decided_by", "txid", "input", "digest"}
COMPLIANCE_FIELDS = {"schema", "id", "change", "unit", "required", "state", "steps", "created", "updated"}
STEP_FIELDS = {"state", "time", "effective", "actor", "basis", "reason", "events", "txid", "input"}


def effectivity_digest(eff: dict) -> str:
    return digest(EFFECTIVITY_DOMAIN, {k: v for k, v in eff.items() if k not in ("digest", "_rev")})


def load_effectivity(record: Any) -> dict:
    """An effectivity decision, refused unless it has exactly the documented shape (its digest is checked by verify)."""
    r = _fields(record, EFFECTIVITY_FIELDS, "effectivity")
    what = f"effectivity {r.get('id')!r}"
    try:
        if not is_id("effectivity", r["id"]) or not is_id("change", r["change"]):
            raise MalformedUnitRecord(f"{what}: bad id")
        if r["id"] != effectivity_id(r["change"]):
            raise MalformedUnitRecord(f"{what}: its id isn't the one its change order gives")
        check_choice(r["kind"], EFFECTIVITY_KINDS, "kind")
        check_id("standard", r["standard"], "standard", empty_ok=True)
        if not isinstance(r["documents"], list):
            raise MalformedUnitRecord(f"{what}: documents must be a list")
        for row in r["documents"]:
            if not isinstance(row, dict) or set(row) != {"document", "from", "to"}:
                raise MalformedUnitRecord(f"{what}: a document row has the wrong fields")
            check_id("document", row["document"], "document")
            check_id("revision", row["from"], "from revision")
            check_id("revision", row["to"], "to revision")
        scope = r["scope"]
        if not isinstance(scope, dict) or set(scope) != {"rule", "value"}:
            raise MalformedUnitRecord(f"{what}: scope must be {{rule, value}}")
        check_choice(scope["rule"], SCOPE_RULES, "scope rule")
        _str(scope["value"], f"{what} scope value")
        if not isinstance(r["units"], list) or not all(is_id("unit", u) for u in r["units"]):
            raise MalformedUnitRecord(f"{what}: units must list unit ids")
        for k in ("reason", "decided", "txid", "input", "digest"):
            _str(r[k], f"{what} {k}")
        if not isinstance(r["decided_by"], dict):
            raise MalformedUnitRecord(f"{what}: decided_by must be an object")
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


def load_compliance(record: Any) -> dict:
    """One unit's compliance with one change order. Its steps must be the allowed transitions and its state the last
    step's; anything else is refused, so nothing is ever added on top of a damaged record."""
    r = _fields(record, COMPLIANCE_FIELDS, "compliance")
    what = f"compliance {r.get('id')!r}"
    try:
        if not is_id("compliance", r["id"]) or not is_id("change", r["change"]) or not is_id("unit", r["unit"]):
            raise MalformedUnitRecord(f"{what}: bad id")
        if r["id"] != compliance_id(r["change"], r["unit"]):
            raise MalformedUnitRecord(f"{what}: its id isn't the one its change order and unit give")
        if not isinstance(r["required"], bool):
            raise MalformedUnitRecord(f"{what}: required must be true or false")
        check_choice(r["state"], COMPLIANCE_STATES, "state")
        _str(r["created"], f"{what} created"); _str(r["updated"], f"{what} updated")
        steps = r["steps"]
        if not isinstance(steps, list) or not steps:
            raise MalformedUnitRecord(f"{what}: steps must list at least one step")
        for i, s in enumerate(steps):
            if not isinstance(s, dict) or set(s) != STEP_FIELDS:
                raise MalformedUnitRecord(f"{what}: step {i + 1} has the wrong fields")
            check_choice(s["state"], COMPLIANCE_STATES, "state")
            check_choice(s["basis"], BASES, "basis")
            for k in ("time", "effective", "reason", "txid", "input"):
                _str(s[k], f"{what} step {k}")
            if not isinstance(s["actor"], dict):
                raise MalformedUnitRecord(f"{what}: step actor must be an object")
            if not isinstance(s["events"], list) or not all(is_id("event", e) for e in s["events"]):
                raise MalformedUnitRecord(f"{what}: step events must list unit event ids")
        first = "pending" if r["required"] else "offered"
        if steps[0]["state"] != first:
            raise MalformedUnitRecord(f"{what}: a {'required' if r['required'] else 'optional'} change starts {first}")
        for a, b in zip(steps, steps[1:]):
            if b["state"] not in TRANSITIONS[r["required"]].get(a["state"], ()):
                raise MalformedUnitRecord(f"{what}: {a['state']} can't become {b['state']}")
        if r["state"] != steps[-1]["state"]:
            raise MalformedUnitRecord(f"{what}: its state isn't its last step's")
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


DEVIATION_DOMAIN = "revisionworx/units/deviation/v1"
DEVIATION_FIELDS = {"schema", "id", "number", "kind", "title", "departure", "documents", "units", "temporary", "until",
                    "reason", "approved", "approved_by", "txid", "input", "digest"}
DEVUNIT_FIELDS = {"schema", "id", "deviation", "unit", "state", "steps", "created", "updated"}


def deviation_digest(dev: dict) -> str:
    return digest(DEVIATION_DOMAIN, {k: v for k, v in dev.items() if k not in ("digest", "_rev")})


def load_deviation(record: Any) -> dict:
    """A deviation or concession, refused unless it has exactly the documented shape (its digest is checked by verify)."""
    r = _fields(record, DEVIATION_FIELDS, "deviation")
    what = f"deviation {r.get('number') or r.get('id')!r}"
    try:
        if not is_id("deviation", r["id"]) or r["id"] != deviation_id(_str(r["number"], f"{what} number")):
            raise MalformedUnitRecord(f"{what}: its id isn't the one its number gives")
        clean(r["number"], "number", LIMITS["deviation_number"], required=True)
        check_choice(r["kind"], DEVIATION_KINDS, "kind")
        clean(r["title"], "title", LIMITS["deviation_title"], required=True)
        clean(r["departure"], "departure", LIMITS["departure"], multiline=True, required=True)
        clean(r["reason"], "reason", LIMITS["reason"], multiline=True, required=True)
        docs = r["documents"]
        if not isinstance(docs, list) or len(docs) > LIMITS["deviation_documents"]:
            raise MalformedUnitRecord(f"{what}: documents must be a list of at most {LIMITS['deviation_documents']}")
        for row in docs:
            if not isinstance(row, dict) or set(row) != {"document", "revision"}:
                raise MalformedUnitRecord(f"{what}: a document row has the wrong fields")
            check_id("document", row["document"], "document")
            check_id("revision", row["revision"], "revision")
        if len({row["document"] for row in docs}) != len(docs):
            raise MalformedUnitRecord(f"{what}: a document is named twice")
        units = r["units"]
        if not isinstance(units, list) or not units or len(units) > LIMITS["deviation_units"] \
                or not all(is_id("unit", u) for u in units) or len(set(units)) != len(units):
            raise MalformedUnitRecord(f"{what}: units must list the unit ids it covers, each once")
        if not isinstance(r["temporary"], bool):
            raise MalformedUnitRecord(f"{what}: temporary must be true or false")
        until = _str(r["until"], f"{what} until")
        if until and (not r["temporary"] or not _DATE_RE.match(until)):
            raise MalformedUnitRecord(f"{what}: only a temporary one has an end date, as YYYY-MM-DD")
        for k in ("approved", "txid", "input", "digest"):
            _str(r[k], f"{what} {k}")
        if not isinstance(r["approved_by"], dict):
            raise MalformedUnitRecord(f"{what}: approved_by must be an object")
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


def load_devunit(record: Any) -> dict:
    """Whether one deviation is in effect on one unit. Its steps must be allowed transitions and its state the last step's."""
    r = _fields(record, DEVUNIT_FIELDS, "deviation on a unit")
    what = f"deviation record {r.get('id')!r}"
    try:
        if not is_id("devunit", r["id"]) or not is_id("deviation", r["deviation"]) or not is_id("unit", r["unit"]):
            raise MalformedUnitRecord(f"{what}: bad id")
        if r["id"] != devunit_id(r["deviation"], r["unit"]):
            raise MalformedUnitRecord(f"{what}: its id isn't the one its deviation and unit give")
        check_choice(r["state"], DEVIATION_STATES, "state")
        _str(r["created"], f"{what} created"); _str(r["updated"], f"{what} updated")
        steps = r["steps"]
        if not isinstance(steps, list) or not steps:
            raise MalformedUnitRecord(f"{what}: steps must list at least one step")
        for i, s in enumerate(steps):
            if not isinstance(s, dict) or set(s) != STEP_FIELDS:
                raise MalformedUnitRecord(f"{what}: step {i + 1} has the wrong fields")
            check_choice(s["state"], DEVIATION_STATES, "state")
            check_choice(s["basis"], BASES, "basis")
            for k in ("time", "effective", "reason", "txid", "input"):
                _str(s[k], f"{what} step {k}")
            if not isinstance(s["actor"], dict):
                raise MalformedUnitRecord(f"{what}: step actor must be an object")
            if not isinstance(s["events"], list) or not all(is_id("event", e) for e in s["events"]):
                raise MalformedUnitRecord(f"{what}: step events must list unit event ids")
        if steps[0]["state"] != "in-effect":
            raise MalformedUnitRecord(f"{what}: it starts in effect")
        for a, b in zip(steps, steps[1:]):
            if b["state"] not in DEVIATION_TRANSITIONS.get(a["state"], ()):
                raise MalformedUnitRecord(f"{what}: {a['state']} can't become {b['state']}")
        if r["state"] != steps[-1]["state"]:
            raise MalformedUnitRecord(f"{what}: its state isn't its last step's")
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


NUMBERING_FIELDS = {"schema", "id", "name", "pattern", "issuer", "start", "counters", "created", "created_by", "txid",
                    "input"}


def load_numbering(record: Any) -> dict:
    r = _fields(record, NUMBERING_FIELDS, "numbering")
    what = f"numbering {r.get('name')!r}"
    try:
        if not is_id("numbering", r["id"]) or r["id"] != numbering_id(_str(r["name"], f"{what} name")):
            raise MalformedUnitRecord(f"{what}: its id isn't the one its name gives")
        check_pattern(r["pattern"])
        for k in ("issuer", "created", "txid", "input"):
            _str(r[k], f"{what} {k}")
        _int(r["start"], f"{what} start", 0)
        if not isinstance(r["created_by"], dict):
            raise MalformedUnitRecord(f"{what}: created_by must be an object")
        if not isinstance(r["counters"], dict):
            raise MalformedUnitRecord(f"{what}: counters must be an object")
        for period, n in r["counters"].items():
            if not re.match(r"^(\d{4}(-\d{2})?)?$", period):
                raise MalformedUnitRecord(f"{what}: counter period {period!r} isn't a year or a month")
            _int(n, f"{what} counter", 0)
    except ValidationError as exc:
        raise MalformedUnitRecord(f"{what}: {exc}") from None
    return {k: v for k, v in r.items() if k != "_rev"}


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
