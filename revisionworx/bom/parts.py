# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Base parts catalog values: part records and company numbering rules (REQUIREMENTS.md section 6).

* A :class:`Part` has a stable internal id (``prt_<32 hex>``, ADR-002 style) that never changes, and a
  company-visible ``number`` that may be renumbered. Identity is never the number or a file path.
* Numbers are unique case-insensitively after NFC normalisation (:func:`number_key`).
* A :class:`NumberingRule` issues numbers such as ``P-000123-A``; numbers that do not match a rule are
  allowed as explicit manual exceptions (imported or legacy numbers).

These are plain values. Storage is :mod:`revisionworx.bom.store`; editing rules are enforced by
:mod:`revisionworx.bom.catalog_service`.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable

from .catalog import PartInfo, clean_text, number_key
from .errors import StructureError

PART_ID_RE = re.compile(r"prt_[0-9a-f]{32}")
LINE_ID_RE = re.compile(r"bln_[0-9a-f]{32}")


def new_part_id() -> str:
    return "prt_" + uuid.uuid4().hex


def new_line_id() -> str:
    return "bln_" + uuid.uuid4().hex


class PartKind(str, Enum):
    PART = "part"
    ASSEMBLY = "assembly"
    MATERIAL = "material"      # raw/bulk material, usually measured (m, kg, L)
    DOCUMENT = "document"      # reference-only item, e.g. a specification


class PartStatus(str, Enum):
    ACTIVE = "active"
    OBSOLETE = "obsolete"      # still resolvable; new BOM usages are refused


@dataclass(frozen=True)
class ManufacturerRef:
    manufacturer: str
    mpn: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "manufacturer",
                           clean_text(self.manufacturer, what="manufacturer", allow_empty=False, max_len=200))
        object.__setattr__(self, "mpn", clean_text(self.mpn, what="manufacturer part number", allow_empty=False,
                                                   max_len=200))


@dataclass(frozen=True)
class VendorRef:
    vendor: str
    sku: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "vendor", clean_text(self.vendor, what="vendor", allow_empty=False, max_len=200))
        object.__setattr__(self, "sku", clean_text(self.sku, what="vendor SKU", max_len=200))


@dataclass(frozen=True)
class DocumentLink:
    """Explicit association of a part with a core document (optionally one revision). Declared by a user;
    a filename match is never turned into a link."""

    document_id: str
    revision_id: str = ""
    role: str = "drawing"

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", clean_text(self.document_id, what="document id", allow_empty=False,
                                                           max_len=200))
        object.__setattr__(self, "revision_id", clean_text(self.revision_id, what="revision id", max_len=200))
        object.__setattr__(self, "role", clean_text(self.role, what="document role", allow_empty=False, max_len=64))


@dataclass(frozen=True)
class Part:
    part_id: str
    number: str
    description: str = ""
    unit: str = "EA"
    kind: str = PartKind.PART.value
    status: str = PartStatus.ACTIVE.value
    revision: str = ""
    manufacturers: tuple[ManufacturerRef, ...] = ()
    vendors: tuple[VendorRef, ...] = ()
    documents: tuple[DocumentLink, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()
    manual_number: bool = False   # True when the number is a deliberate exception to the numbering rule

    def __post_init__(self) -> None:
        s = object.__setattr__
        if not isinstance(self.part_id, str) or not PART_ID_RE.fullmatch(self.part_id):
            raise StructureError(f"part id {self.part_id!r} must look like prt_<32 lowercase hex>")
        s(self, "number", clean_text(self.number, what="part number", allow_empty=False, max_len=100))
        if "\n" in self.number or "\t" in self.number:
            raise StructureError("part number must be a single line")
        s(self, "description", clean_text(self.description, what="description"))
        s(self, "unit", clean_text(self.unit, what="unit", allow_empty=False, max_len=32))
        for name, enum in (("kind", PartKind), ("status", PartStatus)):
            v = getattr(self, name)
            v = v.value if isinstance(v, Enum) else v
            try:
                s(self, name, enum(v).value)
            except ValueError:
                raise StructureError(f"unknown {name} {v!r}; use one of {', '.join(e.value for e in enum)}") from None
        s(self, "revision", clean_text(self.revision, what="revision", max_len=64))
        s(self, "manufacturers", _unique(self.manufacturers, ManufacturerRef,
                                         lambda m: (number_key(m.manufacturer), number_key(m.mpn)), "manufacturer"))
        s(self, "vendors", _unique(self.vendors, VendorRef, lambda v: (number_key(v.vendor), number_key(v.sku)),
                                   "vendor"))
        s(self, "documents", _unique(self.documents, DocumentLink, lambda d: (d.document_id, d.revision_id, d.role),
                                     "document link"))
        attrs = dict(self.attributes.items() if isinstance(self.attributes, dict) else self.attributes)
        clean = {}
        for k, v in attrs.items():
            key = clean_text(k, what="attribute name", allow_empty=False, max_len=64)
            if key in clean:
                raise StructureError(f"duplicate attribute {key!r}")
            clean[key] = clean_text(v, what=f"attribute {key!r}")
        s(self, "attributes", tuple(sorted(clean.items())))
        if not isinstance(self.manual_number, bool):
            raise StructureError("manual_number must be a boolean")

    @property
    def key(self) -> str:
        return number_key(self.number)

    @property
    def obsolete(self) -> bool:
        return self.status == PartStatus.OBSOLETE.value

    def info(self) -> PartInfo:
        return PartInfo(self.part_id, number=self.number, description=self.description, unit=self.unit,
                        revision=self.revision, kind=self.kind, resolved=True)

    def with_changes(self, **changes: Any) -> "Part":
        return replace(self, **changes)

    # ----------------------------------------------------------------- record form (JSON-safe)
    def to_record(self) -> dict[str, Any]:
        return {"id": self.part_id, "number": self.number, "description": self.description, "unit": self.unit,
                "kind": self.kind, "status": self.status, "revision": self.revision,
                "manufacturers": [{"manufacturer": m.manufacturer, "mpn": m.mpn} for m in self.manufacturers],
                "vendors": [{"vendor": v.vendor, "sku": v.sku} for v in self.vendors],
                "documents": [{"document_id": d.document_id, "revision_id": d.revision_id, "role": d.role}
                              for d in self.documents],
                "attributes": dict(self.attributes), "manual_number": self.manual_number}

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "Part":
        try:
            return cls(part_id=rec["id"], number=rec["number"], description=rec.get("description", ""),
                       unit=rec.get("unit", "EA"), kind=rec.get("kind", "part"), status=rec.get("status", "active"),
                       revision=rec.get("revision", ""),
                       manufacturers=tuple(ManufacturerRef(**m) for m in rec.get("manufacturers", [])),
                       vendors=tuple(VendorRef(**v) for v in rec.get("vendors", [])),
                       documents=tuple(DocumentLink(**d) for d in rec.get("documents", [])),
                       attributes=tuple(rec.get("attributes", {}).items()),
                       manual_number=rec.get("manual_number", False))
        except (KeyError, TypeError, AttributeError) as exc:
            raise StructureError(f"malformed part record {rec.get('id', '?') if isinstance(rec, dict) else rec!r}: "
                                 f"{exc}") from None


def _unique(items: Iterable, typ: type, key, what: str) -> tuple:
    out, seen = [], set()
    for it in items:
        if not isinstance(it, typ):
            raise StructureError(f"{what} must be a {typ.__name__}")
        k = key(it)
        if k in seen:
            raise StructureError(f"duplicate {what} {it}")
        seen.add(k)
        out.append(it)
    return tuple(sorted(out, key=key))


# ------------------------------------------------------------------------------------- numbering
@dataclass(frozen=True)
class NumberingRule:
    """``prefix`` + zero-padded sequence (``digits``) + ``suffix``; e.g. ``P-`` 6 ``-A`` -> ``P-000042-A``.

    ``reserved`` ranges (inclusive) are never issued automatically. The rule ``rule_id`` names its counter."""

    rule_id: str
    prefix: str = ""
    digits: int = 6
    suffix: str = ""
    start: int = 1
    reserved: tuple[tuple[int, int], ...] = field(default=())

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", self.rule_id or ""):
            raise StructureError(f"rule id {self.rule_id!r} must be lowercase letters, digits, '.', '_' or '-'")
        object.__setattr__(self, "prefix", clean_text(self.prefix, what="prefix", max_len=32))
        object.__setattr__(self, "suffix", clean_text(self.suffix, what="suffix", max_len=32))
        if not isinstance(self.digits, int) or not 1 <= self.digits <= 12:
            raise StructureError("digits must be 1..12")
        if not isinstance(self.start, int) or self.start < 0:
            raise StructureError("start must be a non-negative integer")
        ranges = []
        for lo, hi in self.reserved:
            if not (isinstance(lo, int) and isinstance(hi, int)) or lo > hi or lo < 0:
                raise StructureError(f"bad reserved range {lo}..{hi}")
            ranges.append((lo, hi))
        object.__setattr__(self, "reserved", tuple(sorted(ranges)))

    @property
    def capacity(self) -> int:
        return 10 ** self.digits - 1

    def format(self, n: int) -> str:
        if n > self.capacity:
            raise StructureError(f"numbering rule {self.rule_id} is exhausted ({self.digits} digits)")
        return f"{self.prefix}{n:0{self.digits}d}{self.suffix}"

    def pattern(self) -> re.Pattern:
        return re.compile(re.escape(self.prefix) + rf"(\d{{{self.digits}}})" + re.escape(self.suffix), re.IGNORECASE)

    def matches(self, number: str) -> bool:
        return self.pattern().fullmatch(number.strip()) is not None

    def sequence_of(self, number: str) -> int | None:
        m = self.pattern().fullmatch(number.strip())
        return int(m.group(1)) if m else None

    def is_reserved(self, n: int) -> bool:
        return any(lo <= n <= hi for lo, hi in self.reserved)

    def next_free(self, after: int, taken: set[str]) -> tuple[int, str]:
        """The first sequence > ``after`` (and >= start) that is neither reserved nor already used."""
        n = max(after + 1, self.start)
        while True:
            if n > self.capacity:
                raise StructureError(f"numbering rule {self.rule_id} is exhausted ({self.digits} digits)")
            if self.is_reserved(n):
                n = next(hi for lo, hi in self.reserved if lo <= n <= hi) + 1
                continue
            candidate = self.format(n)
            if number_key(candidate) not in taken:
                return n, candidate
            n += 1

    def to_record(self) -> dict[str, Any]:
        return {"id": self.rule_id, "prefix": self.prefix, "digits": self.digits, "suffix": self.suffix,
                "start": self.start, "reserved": [list(r) for r in self.reserved]}

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "NumberingRule":
        return cls(rec["id"], rec.get("prefix", ""), rec.get("digits", 6), rec.get("suffix", ""), rec.get("start", 1),
                   tuple(tuple(r) for r in rec.get("reserved", [])))
