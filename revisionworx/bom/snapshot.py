# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable, revision-aware BOM snapshots and their canonical JSON form.

A :class:`BomSnapshot` freezes the structure of one top-level item at a point in its history, and says
where it came from (:class:`SourceRef`, e.g. a workspace document revision with its SHA-256). Its
``digest`` is the SHA-256 of the canonical JSON of the structure, so "same BOM content" is a byte
comparison, independent of line order, Unicode composition or how the file was written.

Canonical JSON: UTF-8, sorted keys, no insignificant whitespace in the digested form, quantities as
canonical decimal text (never JSON numbers, which readers may turn into floats).

Loading is strict and fails closed: an unknown format, a newer version, unknown fields or invalid
values raise :class:`BomFormatError` and nothing is returned.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .catalog import PartInfo
from .errors import BomError, BomFormatError
from .model import BomLine, Structure
from .units import canonical_text, parse_quantity

FORMAT = "revisionworx.bom"
VERSION = 1
MAX_JSON_BYTES = 256 * 1024 * 1024

_LINE_FIELDS = ("parent_id", "child_id", "quantity", "unit", "find_number", "reference_designators",
                "child_revision", "line_id", "provenance", "note", "attributes")
_PART_FIELDS = ("part_id", "number", "description", "unit", "revision", "kind", "resolved")
_SOURCE_FIELDS = ("kind", "document_id", "revision_id", "revision_label", "revision_number", "sha256",
                  "path", "state")


@dataclass(frozen=True)
class SourceRef:
    """Where a snapshot came from. For a workspace revision: ``kind='workspace-revision'`` with the
    document id, revision id, label, number, lifecycle state and the content SHA-256."""

    kind: str = "manual"
    document_id: str = ""
    revision_id: str = ""
    revision_label: str = ""
    revision_number: int = 0
    sha256: str = ""
    path: str = ""
    state: str = ""

    def __post_init__(self) -> None:
        for name in _SOURCE_FIELDS:
            v = getattr(self, name)
            if name == "revision_number":
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    raise BomFormatError("source.revision_number must be a non-negative integer")
            elif not isinstance(v, str):
                raise BomFormatError(f"source.{name} must be text")

    @property
    def released(self) -> bool:
        return self.state == "released"


@dataclass(frozen=True)
class BomSnapshot:
    """The structure of ``root_id`` frozen at one revision. ``label`` is a human name ("Rev B")."""

    structure: Structure
    root_id: str
    label: str = ""
    source: SourceRef = field(default_factory=SourceRef)

    def __post_init__(self) -> None:
        if self.root_id not in self.structure.item_ids:
            raise BomError(f"snapshot root {self.root_id!r} is not in its structure")

    @property
    def digest(self) -> str:
        return structure_digest(self.structure)

    @property
    def released(self) -> bool:
        return self.source.released

    @property
    def display_name(self) -> str:
        num = self.structure.info(self.root_id).display_number
        rev = self.label or self.source.revision_label
        return f"{num} {rev}".strip()


# ------------------------------------------------------------------------------------------ encoding
def _line_dict(ln: BomLine) -> dict[str, Any]:
    return {"parent_id": ln.parent_id, "child_id": ln.child_id, "quantity": canonical_text(ln.quantity),
            "unit": ln.unit, "find_number": ln.find_number, "reference_designators": list(ln.reference_designators),
            "child_revision": ln.child_revision, "line_id": ln.line_id, "provenance": ln.provenance,
            "note": ln.note, "attributes": {k: v for k, v in ln.attributes}}


def _part_dict(p: PartInfo) -> dict[str, Any]:
    return {"part_id": p.part_id, "number": p.number, "description": p.description, "unit": p.unit,
            "revision": p.revision, "kind": p.kind, "resolved": p.resolved}


def structure_to_dict(structure: Structure) -> dict[str, Any]:
    return {"lines": [_line_dict(ln) for ln in structure.lines],
            "parts": [_part_dict(p) for p in structure.parts]}


def _dumps(obj: Any, pretty: bool) -> bytes:
    if pretty:
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    else:
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def structure_digest(structure: Structure) -> str:
    """SHA-256 of the canonical (compact) JSON of the structure."""
    return hashlib.sha256(_dumps({"format": FORMAT, "version": VERSION, "structure": structure_to_dict(structure)},
                                 pretty=False)).hexdigest()


def structure_to_json(structure: Structure, *, pretty: bool = True) -> bytes:
    return _dumps({"format": FORMAT, "version": VERSION, "kind": "structure",
                   "structure": structure_to_dict(structure)}, pretty)


def snapshot_to_json(snapshot: BomSnapshot, *, pretty: bool = True) -> bytes:
    src = snapshot.source
    return _dumps({"format": FORMAT, "version": VERSION, "kind": "snapshot", "root_id": snapshot.root_id,
                   "label": snapshot.label, "digest": snapshot.digest,
                   "source": {k: getattr(src, k) for k in _SOURCE_FIELDS},
                   "structure": structure_to_dict(snapshot.structure)}, pretty)


# ------------------------------------------------------------------------------------------ decoding
def _expect_keys(obj: Any, allowed: tuple[str, ...], where: str, required: tuple[str, ...] = ()) -> dict:
    if not isinstance(obj, dict):
        raise BomFormatError(f"{where} must be an object")
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise BomFormatError(f"{where}: unknown field(s) {', '.join(unknown)}")
    missing = [k for k in required if k not in obj]
    if missing:
        raise BomFormatError(f"{where}: missing field(s) {', '.join(missing)}")
    return obj


def _structure_from(obj: Any) -> Structure:
    obj = _expect_keys(obj, ("lines", "parts"), "structure", ("lines", "parts"))
    if not isinstance(obj["lines"], list) or not isinstance(obj["parts"], list):
        raise BomFormatError("structure.lines and structure.parts must be lists")
    lines, parts = [], []
    for i, raw in enumerate(obj["lines"]):
        where = f"structure.lines[{i}]"
        raw = _expect_keys(raw, _LINE_FIELDS, where, ("parent_id", "child_id", "quantity", "unit"))
        try:
            if not isinstance(raw["quantity"], str):
                raise BomFormatError(f"{where}.quantity must be decimal text, not a JSON number")
            attrs = raw.get("attributes", {})
            if not isinstance(attrs, dict):
                raise BomFormatError(f"{where}.attributes must be an object")
            refs = raw.get("reference_designators", [])
            if not isinstance(refs, list):
                raise BomFormatError(f"{where}.reference_designators must be a list")
            lines.append(BomLine(
                parent_id=raw["parent_id"], child_id=raw["child_id"],
                quantity=parse_quantity(raw["quantity"], what=f"{where}.quantity"), unit=raw["unit"],
                find_number=raw.get("find_number", ""), reference_designators=tuple(refs),
                child_revision=raw.get("child_revision", ""), line_id=raw.get("line_id", ""),
                provenance=raw.get("provenance", "declared"), note=raw.get("note", ""),
                attributes=tuple(attrs.items())))
        except BomFormatError:
            raise
        except (BomError, TypeError, ValueError) as exc:
            raise BomFormatError(f"{where}: {exc}") from None
    for i, raw in enumerate(obj["parts"]):
        where = f"structure.parts[{i}]"
        raw = _expect_keys(raw, _PART_FIELDS, where, ("part_id",))
        try:
            parts.append(PartInfo(**raw))
        except (BomError, TypeError, ValueError) as exc:
            raise BomFormatError(f"{where}: {exc}") from None
    try:
        return Structure.build(lines, parts)
    except BomError as exc:
        raise BomFormatError(f"structure: {exc}") from None


def _load(data: bytes | str) -> dict:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise BomFormatError(f"BOM JSON is larger than {MAX_JSON_BYTES} bytes")
    try:
        obj = json.loads(data.decode("utf-8-sig"), parse_float=_no_float, parse_constant=_no_constant)
    except UnicodeDecodeError as exc:
        raise BomFormatError(f"BOM JSON is not valid UTF-8 (byte {exc.start})") from None
    except json.JSONDecodeError as exc:
        raise BomFormatError(f"BOM JSON is malformed: {exc.msg} at line {exc.lineno} column {exc.colno}") from None
    if not isinstance(obj, dict) or obj.get("format") != FORMAT:
        raise BomFormatError(f"not a RevisionWorx BOM file (expected format {FORMAT!r})")
    version = obj.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BomFormatError("BOM JSON has no integer version")
    if version > VERSION:
        raise BomFormatError(f"BOM JSON version {version} is newer than this program supports ({VERSION}); "
                             "upgrade RevisionWorx")
    if version < 1:
        raise BomFormatError(f"unsupported BOM JSON version {version}")
    return obj


def _no_float(text: str) -> Decimal:
    raise BomFormatError(f"JSON number {text} found; quantities must be decimal text")


def _no_constant(text: str) -> Any:
    raise BomFormatError(f"JSON constant {text} is not allowed")


def structure_from_json(data: bytes | str) -> Structure:
    obj = _load(data)
    _expect_keys(obj, ("format", "version", "kind", "structure"), "document", ("kind", "structure"))
    if obj["kind"] != "structure":
        raise BomFormatError(f"expected a structure, found {obj['kind']!r}")
    return _structure_from(obj["structure"])


def snapshot_from_json(data: bytes | str) -> BomSnapshot:
    obj = _load(data)
    _expect_keys(obj, ("format", "version", "kind", "root_id", "label", "digest", "source", "structure"),
                 "document", ("kind", "root_id", "structure"))
    if obj["kind"] != "snapshot":
        raise BomFormatError(f"expected a snapshot, found {obj['kind']!r}")
    structure = _structure_from(obj["structure"])
    src_raw = _expect_keys(obj.get("source", {}), _SOURCE_FIELDS, "source")
    try:
        source = SourceRef(**src_raw)
        if not isinstance(obj.get("label", ""), str) or not isinstance(obj["root_id"], str):
            raise BomFormatError("root_id and label must be text")
        snap = BomSnapshot(structure, obj["root_id"], obj.get("label", ""), source)
    except BomFormatError:
        raise
    except (BomError, TypeError) as exc:
        raise BomFormatError(str(exc)) from None
    if "digest" in obj and obj["digest"] != snap.digest:
        raise BomFormatError("snapshot digest does not match its content; the file was altered or damaged")
    return snap


def load_any(data: bytes | str) -> Structure | BomSnapshot:
    """A structure or a snapshot, whichever the document holds."""
    kind = _load(data).get("kind")
    if kind == "snapshot":
        return snapshot_from_json(data)
    if kind == "structure":
        return structure_from_json(data)
    raise BomFormatError(f"unknown BOM document kind {kind!r}")
