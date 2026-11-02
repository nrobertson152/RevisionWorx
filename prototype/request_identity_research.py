"""Research-only ADR-017 full-command identity proposal; NOT a production serializer.

Canonical JSON is sorted by mapping key, NOT by operations order. RecordCheck
inputs remain in the digest even though they are not replayed as mutations.
This is an executable proposal requiring Claude's approval and versioned
migration. It does not authenticate a caller or fence a lease.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

_DOMAIN = b"RevisionWorx.CommitAuthority.request.v2\x00"
_MAX_PAYLOAD = 1_048_576
_MAX_DEPTH = 32


def _validate_json(value: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("nested payload exceeds research limit")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON numbers are forbidden")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            _validate_json(item, depth + 1)
        return
    raise ValueError("unsupported JSON input type")


def canonical_request(op: str, operations: list[dict],
                      blob_refs: list[str] | None = None) -> bytes:
    """Return an unambiguous UTF-8 encoding of the ENTIRE submitted command.

    - Ordered operations are significant, including checks and their positions.
    - Put/Delete absent expectation is explicit JSON null, different from 0.
    - Keys inside JSON objects are sorted; names and values retain Unicode.
    - The derived top-level record _rev is stripped from put data.
    - Blob refs are ordered; an absent list is normalized to [].
    - Empty write sets are representable (check-only commands).
    """
    if not isinstance(op, str) or not op:
        raise ValueError("nonempty operation name required")
    if not isinstance(operations, list) or not operations:
        raise ValueError("nonempty operation list required")
    if blob_refs is None:
        blob_refs = []
    if not isinstance(blob_refs, list) or any(
            not isinstance(ref, str) or not ref for ref in blob_refs):
        raise ValueError("blob_refs must be a list of nonempty strings")
    normalized = []
    for item in operations:
        if not isinstance(item, dict):
            raise ValueError("each operation must be a mapping")
        kind, rid, typ = item.get("kind"), item.get("id"), item.get("type")
        if not isinstance(kind, str) or not kind or not isinstance(rid, str) or not rid:
            raise ValueError("record kind and id are required")
        if typ not in ("put", "delete", "check"):
            raise ValueError("operation type must be put, delete or check")
        required = {"type", "kind", "id", "expect_rev"}
        if set(item) != (required | ({"data"} if typ == "put" else set())):
            raise ValueError("operation fields are incomplete or unexpected")
        expected = item["expect_rev"]
        if expected is not None and (
                type(expected) is not int or expected < 0):
            raise ValueError("expect_rev must be nonnegative int or null")
        if typ == "check" and expected is None:
            raise ValueError("RecordCheck must have a concrete expected version")
        converted = {"type": typ, "kind": kind, "id": rid, "expect_rev": expected}
        if typ == "put":
            if not isinstance(item["data"], dict):
                raise ValueError("RecordPut must contain JSON object data")
            converted["data"] = {k: v for k, v in item["data"].items() if k != "_rev"}
        normalized.append(converted)
    request = {"version": 2, "op": op, "operations": normalized, "blob_refs": blob_refs}
    _validate_json(request)
    payload = json.dumps(request, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > _MAX_PAYLOAD:
        raise ValueError("canonical request exceeds research size limit")
    return payload


def request_digest(op: str, operations: list[dict],
                   blob_refs: list[str] | None = None) -> str:
    """Domain-separated SHA-256 of canonical command (not a security MAC)."""
    return hashlib.sha256(_DOMAIN + canonical_request(op, operations, blob_refs)).hexdigest()
