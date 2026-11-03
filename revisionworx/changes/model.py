# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed data model for engineering change requests (ECR) and change orders (ECO).

Everything that is persisted passes through ``to_dict``/``from_dict`` here. ``from_dict`` is strict: a
record that does not match the schema raises :class:`MalformedRecord` instead of being half-loaded, so
damaged or hand-edited data fails closed. Text is NFC-normalised, control characters are refused and
lengths are bounded. Nothing here touches storage.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------- errors
class ChangeError(Exception):
    """Base class. Every subclass is raised before anything is written unless stated otherwise."""


class ValidationError(ChangeError):
    """Input is malformed (missing reason, bad text, unknown item type ...). Nothing was written."""


class MalformedRecord(ChangeError):
    """A stored or imported record does not match the schema. It is never partially used."""


class TransitionError(ChangeError):
    """The requested workflow step is not allowed from the current state. Nothing was written."""


class DuplicateNumber(ChangeError):
    """The company-visible change number is already used (case- and width-insensitive)."""


class NotFound(ChangeError):
    pass


class StaleBaseline(ChangeError):
    """An affected document was revised after the change recorded its baseline revision."""

    def __init__(self, message: str, items: list[str]):
        super().__init__(message)
        self.items = items


class DependencyError(ChangeError):
    """A dependency is unknown, circular, or not yet complete."""


class SignoffError(ChangeError):
    """A signoff is not allowed (duplicate signer, requester approving own change under policy ...)."""


class ReplayRefused(ChangeError):
    """An earlier attempt with this txid never took effect (its session lost the writer lock), or the txid
    belongs to a different change. Nothing changed; start again with a new txid."""


# ---------------------------------------------------------------------------- enums
class StrEnum(str, Enum):  # enum.StrEnum is 3.11+; the project supports 3.10
    def __str__(self) -> str:
        return self.value


class ChangeKind(StrEnum):
    ECR = "ecr"
    ECO = "eco"


class ECRStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CONVERTED = "converted"     # linked to an active change order
    CLOSED = "closed"           # its change order was closed
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ECOStatus(StrEnum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ItemType(StrEnum):
    DOCUMENT = "document"       # a registered RevisionWorx document; baseline = a revision id
    PROJECT = "project"
    PART = "part"               # declared until a parts registry adapter exists (issues #15/#18)
    ASSEMBLY = "assembly"
    BOM = "bom"
    OTHER = "other"


class ProposedAction(StrEnum):
    REVISE = "revise"
    CREATE = "create"
    OBSOLETE = "obsolete"
    REVIEW = "review"           # check for knock-on effect; may end with no change
    REFERENCE = "reference"     # listed for context only


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class SignoffStage(StrEnum):
    ECR_REVIEW = "ecr-review"
    ECO_RELEASE = "eco-release"     # plan approved, work may start
    ECO_CLOSURE = "eco-closure"     # implementation verified, order may close


class DependencyType(StrEnum):
    REQUIRES = "requires"       # the other change must be closed first (enforced)
    RELATED = "related"         # traceability only


class ActionStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    WAIVED = "waived"


class ImplementationStatus(StrEnum):
    PENDING = "pending"
    IMPLEMENTED = "implemented"
    NOT_REQUIRED = "not_required"


# ---------------------------------------------------------------------------- text validation
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONTROL_SINGLE = re.compile(r"[\x00-\x1f\x7f]")
_TXID = re.compile(r"^[0-9a-f]{32}$")
_INTERNAL_ID = re.compile(r"^[a-z]{2,8}_[0-9a-f]{32}$")
_FREE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")

LIMITS = {"title": 200, "number": 64, "label": 200, "reason": 10_000, "description": 50_000,
          "comment": 10_000, "role": 100, "category": 40, "note": 10_000, "assignee": 200}


def clean_text(value: Any, what: str, *, required: bool = False, multiline: bool = False,
               limit: str | None = None) -> str:
    """NFC-normalise and validate user text. Raises :class:`ValidationError`."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{what} must be text, not {type(value).__name__}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValidationError(f"{what} contains invalid Unicode (an unpaired surrogate)") from None
    text = unicodedata.normalize("NFC", value).strip()
    if multiline:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    bad = (_CONTROL if multiline else _CONTROL_SINGLE).search(text)
    if bad:
        raise ValidationError(f"{what} contains a control character (code {ord(bad.group()):#04x})")
    cap = LIMITS[limit or what] if (limit or what) in LIMITS else 10_000
    if len(text) > cap:
        raise ValidationError(f"{what} is too long ({len(text)} characters; the limit is {cap})")
    if required and not text:
        raise ValidationError(f"{what} is required")
    return text


def number_key(number: str) -> str:
    """Uniqueness key for company-visible numbers: NFKC + casefold, so 'ECR-0001', 'ecr-0001' and the
    full-width 'ＥＣＲ－０００１' collide instead of becoming confusable duplicates."""
    return unicodedata.normalize("NFKC", number).casefold().replace("‐", "-").replace("−", "-")


def check_txid(txid: str | None) -> str | None:
    if txid is not None and (not isinstance(txid, str) or not _TXID.match(txid)):
        raise ValidationError("txid must be 32 lowercase hexadecimal characters")
    return txid


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest_of(domain: str, obj: Any) -> str:
    return hashlib.sha256(domain.encode() + b"\x00" + canonical_json(obj)).hexdigest()


def check_time(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise MalformedRecord(f"{what} must be an ISO 8601 time")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise MalformedRecord(f"{what} {value!r} is not an ISO 8601 time") from None
    return value


# ---------------------------------------------------------------------------- strict loading helpers
def _enum(cls, value, what: str):
    try:
        return cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in cls)
        raise MalformedRecord(f"{what} {value!r} is not one of: {allowed}") from None


def _str(d: dict, key: str, *, optional: bool = False) -> str | None:
    v = d.get(key)
    if v is None and optional:
        return None
    if not isinstance(v, str):
        raise MalformedRecord(f"field {key!r} must be text")
    return v


def _int(d: dict, key: str, minimum: int = 0) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
        raise MalformedRecord(f"field {key!r} must be an integer >= {minimum}")
    return v


def _list(d: dict, key: str) -> list:
    v = d.get(key, [])
    if not isinstance(v, list):
        raise MalformedRecord(f"field {key!r} must be a list")
    return v


def _actor(v: Any, what: str) -> dict:
    if not isinstance(v, dict) or not isinstance(v.get("user"), str):
        raise MalformedRecord(f"{what} must be an actor with a 'user'")
    out = {"user": v["user"], "host": str(v.get("host", ""))}
    if "pid" in v:
        out["pid"] = v["pid"]
    return out


def actor_key(actor: dict) -> str:
    """Two signoffs by the same OS account count once. Accounts are not authenticated (see docs)."""
    return unicodedata.normalize("NFKC", actor.get("user", "")).casefold()


def actor_label(actor: dict | None) -> str:
    if not actor:
        return "?"
    host = actor.get("host")
    return f"{actor.get('user', '?')}@{host}" if host else str(actor.get("user", "?"))


# ---------------------------------------------------------------------------- value objects
@dataclass(frozen=True)
class AffectedItem:
    """One artefact a change touches. ``key`` is unique within a change."""
    item_type: ItemType
    label: str                          # human-visible: document number/title, part number ...
    item_id: str | None = None          # internal stable id (doc_..., part id ...) when known
    baseline_rev_id: str | None = None  # documents: the revision the change is based on
    baseline_label: str = ""
    action: ProposedAction = ProposedAction.REVISE
    note: str = ""

    @property
    def key(self) -> str:
        ident = self.item_id if self.item_id else "label:" + number_key(self.label)
        return f"{self.item_type.value}:{ident}"

    @classmethod
    def create(cls, item_type: Any, label: Any = "", item_id: Any = None, *, baseline_rev_id: Any = None,
               baseline_label: Any = "", action: Any = ProposedAction.REVISE, note: Any = "") -> "AffectedItem":
        try:
            itype = ItemType(item_type)
        except ValueError:
            raise ValidationError(f"unknown affected item type {item_type!r}; use one of "
                                  f"{', '.join(t.value for t in ItemType)}") from None
        try:
            act = ProposedAction(action)
        except ValueError:
            raise ValidationError(f"unknown proposed action {action!r}") from None
        if item_id is not None and (not isinstance(item_id, str) or not _FREE_ID.match(item_id)):
            raise ValidationError(f"affected item id {item_id!r} is not a valid identifier")
        if baseline_rev_id is not None:
            if itype is not ItemType.DOCUMENT:
                raise ValidationError("a baseline revision id applies to documents only; use baseline_label")
            if not isinstance(baseline_rev_id, str) or not _INTERNAL_ID.match(baseline_rev_id):
                raise ValidationError(f"baseline revision id {baseline_rev_id!r} is not a revision id")
        if itype is ItemType.DOCUMENT and not item_id:
            raise ValidationError("a document item needs the document's id")
        lab = clean_text(label, "label", required=not item_id)
        return cls(itype, lab, item_id, baseline_rev_id,
                   clean_text(baseline_label, "baseline label", limit="label"), act,
                   clean_text(note, "note", multiline=True))

    def to_dict(self) -> dict:
        return {"item_type": self.item_type.value, "label": self.label, "item_id": self.item_id,
                "baseline_rev_id": self.baseline_rev_id, "baseline_label": self.baseline_label,
                "action": self.action.value, "note": self.note}

    @classmethod
    def from_dict(cls, d: Any) -> "AffectedItem":
        if not isinstance(d, dict):
            raise MalformedRecord("affected item must be an object")
        try:
            return cls.create(d.get("item_type"), d.get("label", ""), d.get("item_id"),
                              baseline_rev_id=d.get("baseline_rev_id"),
                              baseline_label=d.get("baseline_label", ""), action=d.get("action", "revise"),
                              note=d.get("note", ""))
        except ValidationError as exc:
            raise MalformedRecord(str(exc)) from None


@dataclass(frozen=True)
class Dependency:
    change_id: str
    dep_type: DependencyType = DependencyType.REQUIRES

    def to_dict(self) -> dict:
        return {"change_id": self.change_id, "type": self.dep_type.value}

    @classmethod
    def from_dict(cls, d: Any) -> "Dependency":
        if not isinstance(d, dict) or not isinstance(d.get("change_id"), str) \
                or not _INTERNAL_ID.match(d["change_id"]):
            raise MalformedRecord("dependency must name a change id")
        return cls(d["change_id"], _enum(DependencyType, d.get("type", "requires"), "dependency type"))


@dataclass(frozen=True)
class Signoff:
    """Local workflow evidence: which OS account recorded which decision on which content, when.
    It is not an electronic signature; RevisionWorx has no authentication (docs/CHANGE_CONTROL.md)."""
    id: str
    stage: SignoffStage
    decision: Decision
    actor: dict
    time: str
    content_digest: str
    role: str = ""
    comment: str = ""
    round: int = 0                      # review round of the change when recorded

    def to_dict(self) -> dict:
        return {"id": self.id, "stage": self.stage.value, "decision": self.decision.value,
                "actor": dict(self.actor), "time": self.time, "content_digest": self.content_digest,
                "role": self.role, "comment": self.comment, "round": self.round}

    @classmethod
    def from_dict(cls, d: Any) -> "Signoff":
        if not isinstance(d, dict):
            raise MalformedRecord("signoff must be an object")
        return cls(_str(d, "id"), _enum(SignoffStage, d.get("stage"), "signoff stage"),
                   _enum(Decision, d.get("decision"), "decision"), _actor(d.get("actor"), "signoff actor"),
                   check_time(d.get("time"), "signoff time"), _str(d, "content_digest"),
                   _str(d, "role", optional=True) or "", _str(d, "comment", optional=True) or "",
                   _int(d, "round") if "round" in d else 0)


@dataclass(frozen=True)
class OrderAction:
    """A task on a change order: update drawing, notify supplier, rework stock ..."""
    id: str
    title: str
    required: bool = True
    status: ActionStatus = ActionStatus.OPEN
    assignee: str = ""
    item_key: str | None = None
    note: str = ""
    completed_by: dict | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "required": self.required, "status": self.status.value,
                "assignee": self.assignee, "item_key": self.item_key, "note": self.note,
                "completed_by": self.completed_by, "completed_at": self.completed_at}

    @classmethod
    def from_dict(cls, d: Any) -> "OrderAction":
        if not isinstance(d, dict) or not isinstance(d.get("required", True), bool):
            raise MalformedRecord("order action must be an object with a boolean 'required'")
        return cls(_str(d, "id"), _str(d, "title"), d.get("required", True),
                   _enum(ActionStatus, d.get("status", "open"), "action status"),
                   _str(d, "assignee", optional=True) or "", _str(d, "item_key", optional=True),
                   _str(d, "note", optional=True) or "",
                   _actor(d["completed_by"], "completed_by") if d.get("completed_by") else None,
                   check_time(d["completed_at"], "completed_at") if d.get("completed_at") else None)


@dataclass(frozen=True)
class Implementation:
    """How a change order dealt with one affected item: which revision implements it."""
    item_key: str
    status: ImplementationStatus = ImplementationStatus.PENDING
    rev_id: str | None = None
    rev_label: str = ""
    intervening: tuple[str, ...] = ()   # revisions between baseline and rev_id, acknowledged by the user
    note: str = ""
    recorded_by: dict | None = None
    recorded_at: str | None = None

    def to_dict(self) -> dict:
        return {"item_key": self.item_key, "status": self.status.value, "rev_id": self.rev_id,
                "rev_label": self.rev_label, "intervening": list(self.intervening), "note": self.note,
                "recorded_by": self.recorded_by, "recorded_at": self.recorded_at}

    @classmethod
    def from_dict(cls, d: Any) -> "Implementation":
        if not isinstance(d, dict):
            raise MalformedRecord("implementation must be an object")
        inter = _list(d, "intervening")
        if not all(isinstance(x, str) for x in inter):
            raise MalformedRecord("intervening revisions must be ids")
        return cls(_str(d, "item_key"), _enum(ImplementationStatus, d.get("status", "pending"), "status"),
                   _str(d, "rev_id", optional=True), _str(d, "rev_label", optional=True) or "",
                   tuple(inter), _str(d, "note", optional=True) or "",
                   _actor(d["recorded_by"], "recorded_by") if d.get("recorded_by") else None,
                   check_time(d["recorded_at"], "recorded_at") if d.get("recorded_at") else None)


# ---------------------------------------------------------------------------- change records
@dataclass(frozen=True)
class _ChangeBase:
    id: str
    number: str
    title: str
    status: Any
    created: str
    created_by: dict
    modified: str
    description: str = ""
    priority: Priority = Priority.NORMAL
    category: str = "design"
    project_ids: tuple[str, ...] = ()
    affected: tuple[AffectedItem, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    signoffs: tuple[Signoff, ...] = ()
    event_count: int = 0
    event_head: str = ""
    round: int = 0                      # ECR: submissions so far; ECO: implementation rounds so far
    rev: int = 0                        # storage revision (_rev); 0 = not stored yet

    kind: Any = None                    # overridden as a ClassVar-like constant in subclasses

    def item(self, key: str) -> AffectedItem:
        for it in self.affected:
            if it.key == key:
                return it
        raise NotFound(f"{self.number} has no affected item {key!r}")

    def requires(self) -> list[str]:
        return [d.change_id for d in self.dependencies if d.dep_type is DependencyType.REQUIRES]

    def _base_dict(self) -> dict:
        return {"schema": SCHEMA_VERSION, "kind": self.kind.value, "id": self.id, "number": self.number,
                "title": self.title, "status": self.status.value, "created": self.created,
                "created_by": dict(self.created_by), "modified": self.modified, "description": self.description,
                "priority": self.priority.value, "category": self.category,
                "project_ids": list(self.project_ids), "affected": [a.to_dict() for a in self.affected],
                "dependencies": [d.to_dict() for d in self.dependencies],
                "signoffs": [s.to_dict() for s in self.signoffs], "event_count": self.event_count,
                "event_head": self.event_head, "round": self.round}

    @staticmethod
    def _base_kwargs(d: dict, status_cls, kind: ChangeKind) -> dict:
        if not isinstance(d, dict):
            raise MalformedRecord("change record must be an object")
        if d.get("schema") != SCHEMA_VERSION:
            raise MalformedRecord(f"unsupported change record schema {d.get('schema')!r}")
        if d.get("kind") != kind.value:
            raise MalformedRecord(f"record kind {d.get('kind')!r} is not {kind.value!r}")
        rid = _str(d, "id")
        if not _INTERNAL_ID.match(rid) or not rid.startswith(kind.value + "_"):
            raise MalformedRecord(f"invalid {kind.value} id {rid!r}")
        pids = _list(d, "project_ids")
        if not all(isinstance(p, str) for p in pids):
            raise MalformedRecord("project_ids must be ids")
        affected = tuple(AffectedItem.from_dict(a) for a in _list(d, "affected"))
        if len({a.key for a in affected}) != len(affected):
            raise MalformedRecord("duplicate affected item")
        try:
            title = clean_text(d.get("title"), "title", required=True)
            number = clean_text(d.get("number"), "number", required=True)
            description = clean_text(d.get("description", ""), "description", multiline=True)
        except ValidationError as exc:
            raise MalformedRecord(str(exc)) from None
        rev = d.get("_rev", 0)
        return dict(id=rid, number=number, title=title, status=_enum(status_cls, d.get("status"), "status"),
                    created=check_time(d.get("created"), "created"),
                    created_by=_actor(d.get("created_by"), "created_by"),
                    modified=check_time(d.get("modified"), "modified"), description=description,
                    priority=_enum(Priority, d.get("priority", "normal"), "priority"),
                    category=_str(d, "category"), project_ids=tuple(pids), affected=affected,
                    dependencies=tuple(Dependency.from_dict(x) for x in _list(d, "dependencies")),
                    signoffs=tuple(Signoff.from_dict(x) for x in _list(d, "signoffs")),
                    event_count=_int(d, "event_count"), event_head=_str(d, "event_head"),
                    round=_int(d, "round") if "round" in d else 0, rev=rev if isinstance(rev, int) else 0)


@dataclass(frozen=True)
class ChangeRequest(_ChangeBase):
    reason: str = ""
    eco_id: str | None = None
    disposition: str = ""               # why it was rejected/cancelled/returned

    kind: ChangeKind = ChangeKind.ECR

    def content(self) -> dict:
        """What reviewers approve. Signoffs bind to its digest, so any later edit makes them stale."""
        return {"title": self.title, "reason": self.reason, "description": self.description,
                "priority": self.priority.value, "category": self.category,
                "project_ids": list(self.project_ids), "affected": [a.to_dict() for a in self.affected],
                "dependencies": [d.to_dict() for d in self.dependencies]}

    def content_digest(self) -> str:
        return digest_of("revisionworx/changes/ecr-content/v1", self.content())

    def to_dict(self) -> dict:
        return {**self._base_dict(), "reason": self.reason, "eco_id": self.eco_id,
                "disposition": self.disposition}

    @classmethod
    def from_dict(cls, d: Any) -> "ChangeRequest":
        kw = cls._base_kwargs(d, ECRStatus, ChangeKind.ECR)
        try:
            reason = clean_text(d.get("reason"), "reason", required=True, multiline=True)
        except ValidationError as exc:
            raise MalformedRecord(str(exc)) from None
        return cls(**kw, reason=reason, eco_id=_str(d, "eco_id", optional=True),
                   disposition=_str(d, "disposition", optional=True) or "")


@dataclass(frozen=True)
class ChangeOrder(_ChangeBase):
    request_ids: tuple[str, ...] = ()
    actions: tuple[OrderAction, ...] = ()
    implementations: tuple[Implementation, ...] = ()
    disposition: str = ""

    kind: ChangeKind = ChangeKind.ECO

    def content(self) -> dict:
        return {"title": self.title, "description": self.description, "request_ids": list(self.request_ids),
                "affected": [a.to_dict() for a in self.affected],
                "actions": [{"id": a.id, "title": a.title, "required": a.required} for a in self.actions]}

    def content_digest(self) -> str:
        return digest_of("revisionworx/changes/eco-content/v1", self.content())

    def action(self, action_id: str) -> OrderAction:
        for a in self.actions:
            if a.id == action_id:
                return a
        raise NotFound(f"{self.number} has no action {action_id!r}")

    def implementation(self, item_key: str) -> Implementation:
        for i in self.implementations:
            if i.item_key == item_key:
                return i
        return Implementation(item_key)

    def to_dict(self) -> dict:
        return {**self._base_dict(), "request_ids": list(self.request_ids),
                "actions": [a.to_dict() for a in self.actions],
                "implementations": [i.to_dict() for i in self.implementations], "disposition": self.disposition}

    @classmethod
    def from_dict(cls, d: Any) -> "ChangeOrder":
        kw = cls._base_kwargs(d, ECOStatus, ChangeKind.ECO)
        reqs = _list(d, "request_ids")
        if not all(isinstance(r, str) and _INTERNAL_ID.match(r) for r in reqs):
            raise MalformedRecord("request_ids must be change request ids")
        actions = tuple(OrderAction.from_dict(a) for a in _list(d, "actions"))
        if len({a.id for a in actions}) != len(actions):
            raise MalformedRecord("duplicate action id")
        return cls(**kw, request_ids=tuple(reqs), actions=actions,
                   implementations=tuple(Implementation.from_dict(i) for i in _list(d, "implementations")),
                   disposition=_str(d, "disposition", optional=True) or "")


Change = ChangeRequest | ChangeOrder


def load_change(d: Any) -> Change:
    kind = d.get("kind") if isinstance(d, dict) else None
    if kind == ChangeKind.ECR.value:
        return ChangeRequest.from_dict(d)
    if kind == ChangeKind.ECO.value:
        return ChangeOrder.from_dict(d)
    raise MalformedRecord(f"unknown change kind {kind!r}")


def field_diff(old: Change, new: Change, names: Iterable[str]) -> dict:
    """Old/new values of changed fields, JSON-safe, for the audit trail (no silent rewriting)."""
    out = {}
    for n in names:
        a, b = getattr(old, n), getattr(new, n)
        if a != b:
            out[n] = [_jsonable(a), _jsonable(b)]
    return out


def _jsonable(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return v

