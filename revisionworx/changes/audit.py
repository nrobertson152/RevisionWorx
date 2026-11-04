# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable, hash-chained audit events for changes.

Every state change writes exactly one new event in the same commit as the change record. Event *n* of a
change has a deterministic id derived from (change id, n) and is written with ``expect_rev=0`` ("must not
exist"), so it can be written once and never replaced through this API, and two writers cannot both append
event *n*. Each event carries the SHA-256 of the previous one; the change record carries the count and the
digest of the last. :func:`verify_chain` detects a missing, altered, reordered or truncated history,
including in an exported package.

Scope: this is tamper *evidence* for local workflow records, layered on the core journal (itself
digest-checked). It is not a cryptographic signature: anyone with write access to the shared folder could
rewrite everything consistently. See docs/CHANGE_CONTROL.md.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable

from .model import MalformedRecord, canonical_json, check_time, digest_of

EVENT_SCHEMA = 1
_EVENT_DOMAIN = "revisionworx/changes/event/v1"


def event_id(change_id: str, n: int) -> str:
    h = hashlib.sha256(f"revisionworx/changes/event-id/v1\x00{change_id}\x00{n}".encode()).hexdigest()
    return f"chev_{h[:32]}"


def event_digest(event: dict) -> str:
    return digest_of(_EVENT_DOMAIN, {k: v for k, v in event.items() if k not in ("digest", "_rev")})


def make_event(change_id: str, change_kind: str, n: int, prev_digest: str, event: str, actor: dict,
               time: str, *, from_status: str | None = None, to_status: str | None = None,
               comment: str = "", details: dict | None = None) -> dict:
    ev = {"schema": EVENT_SCHEMA, "id": event_id(change_id, n), "change_id": change_id,
          "change_kind": change_kind, "n": n, "event": event, "from": from_status, "to": to_status,
          "actor": dict(actor), "time": time, "comment": comment, "details": details or {},
          "prev": prev_digest}
    canonical_json(ev)  # refuses non-finite numbers and unencodable text before anything is written
    ev["digest"] = event_digest(ev)
    return ev


def check_event(ev: Any) -> dict:
    if not isinstance(ev, dict):
        raise MalformedRecord("audit event must be an object")
    for key in ("id", "change_id", "change_kind", "event", "prev", "digest"):
        if not isinstance(ev.get(key), str):
            raise MalformedRecord(f"audit event field {key!r} must be text")
    if not isinstance(ev.get("n"), int) or ev["n"] < 1:
        raise MalformedRecord("audit event number must be a positive integer")
    check_time(ev.get("time"), "audit event time")
    return ev


def verify_chain(change_id: str, expected_count: int, expected_head: str,
                 fetch: Callable[[int], dict | None]) -> list[dict]:
    """Problems with a change's history; an empty list means the chain is intact and complete."""
    problems: list[dict] = []
    prev = ""
    for n in range(1, expected_count + 1):
        ev = fetch(n)
        if ev is None:
            problems.append({"problem": "missing-event", "n": n})
            prev = None
            continue
        try:
            check_event(ev)
        except MalformedRecord as exc:
            problems.append({"problem": "malformed-event", "n": n, "detail": str(exc)})
            prev = None
            continue
        if ev["id"] != event_id(change_id, n) or ev["change_id"] != change_id or ev["n"] != n:
            problems.append({"problem": "misplaced-event", "n": n})
        if ev["digest"] != event_digest(ev):
            problems.append({"problem": "altered-event", "n": n})
        if prev is not None and ev["prev"] != prev:
            problems.append({"problem": "broken-link", "n": n})
        prev = ev["digest"]
    if expected_count and prev is not None and prev != expected_head:
        problems.append({"problem": "head-mismatch", "n": expected_count})
    extra = fetch(expected_count + 1)
    if extra is not None:
        problems.append({"problem": "unrecorded-event", "n": expected_count + 1})
    return problems
