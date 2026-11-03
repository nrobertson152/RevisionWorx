# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only seam to the parts catalog.

The parts catalog (stable part identities, company part numbers, numbering rules, manufacturer data and
their persistence) is owned by issue #15. This module only defines the *read* interface the structure
engine needs (:class:`CatalogReader`) and the value it returns (:class:`PartInfo`).

:class:`InMemoryCatalog` is a stand-in for tests and standalone file work. It has no numbering rules, no
persistence and no editing workflow, and it is not a replacement for the #15 catalog.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from .errors import StructureError

_ID_RE = re.compile(r"[^\x00-\x1f\x7f]{1,200}")

#: Prefix of the key given to a part number that could not be resolved to a catalog identity.
UNRESOLVED_PREFIX = "pn:"


def clean_text(value: str, *, what: str, allow_empty: bool = True, max_len: int = 4000) -> str:
    """NFC-normalise and strip text; refuse control characters other than tab and newline."""
    if not isinstance(value, str):
        raise StructureError(f"{what} must be text, got {type(value).__name__}")
    v = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not v:
        raise StructureError(f"{what} must not be empty")
    if len(v) > max_len:
        raise StructureError(f"{what} is longer than {max_len} characters")
    if any((ord(c) < 32 and c not in "\t\n") or ord(c) == 0x7F for c in v):
        raise StructureError(f"{what} {v[:40]!r} contains control characters")
    return v


def check_id(value: str, *, what: str = "item id") -> str:
    v = clean_text(value, what=what, allow_empty=False, max_len=200)
    if not _ID_RE.fullmatch(v) or "\t" in v or "\n" in v:
        raise StructureError(f"{what} {v!r} is not a valid identifier")
    return v


def number_key(number: str) -> str:
    """Comparison key for a company part number: NFC, stripped, case-insensitive."""
    return unicodedata.normalize("NFC", number).strip().casefold()


def unresolved_id(number: str) -> str:
    """Deterministic placeholder identity for a part number with no catalog identity."""
    key = number_key(number)
    if not key:
        raise StructureError("a part needs a part number or an id")
    return UNRESOLVED_PREFIX + key


@dataclass(frozen=True)
class PartInfo:
    """What the structure engine needs to know about one item (part or assembly).

    ``resolved`` is ``False`` when the identity is a placeholder derived from a part number that no
    catalog has confirmed (see PR #2 decision 5: unresolved candidates stay unresolved)."""

    part_id: str
    number: str = ""
    description: str = ""
    unit: str = "EA"
    revision: str = ""
    kind: str = ""
    resolved: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "part_id", check_id(self.part_id, what="part id"))
        object.__setattr__(self, "number", clean_text(self.number, what="part number", max_len=200))
        object.__setattr__(self, "description", clean_text(self.description, what="description"))
        object.__setattr__(self, "unit", clean_text(self.unit, what="unit", allow_empty=False, max_len=32))
        object.__setattr__(self, "revision", clean_text(self.revision, what="revision", max_len=64))
        object.__setattr__(self, "kind", clean_text(self.kind, what="kind", max_len=64))
        if not isinstance(self.resolved, bool):
            raise StructureError("resolved must be a boolean")

    @property
    def display_number(self) -> str:
        return self.number or self.part_id


@runtime_checkable
class CatalogReader(Protocol):
    """Read-only catalog interface. Issue #15's catalog is expected to provide this."""

    def get(self, part_id: str) -> PartInfo | None:
        """The part with this stable id, or ``None``."""

    def find_by_number(self, number: str) -> list[PartInfo]:
        """Parts whose number matches case-insensitively (NFC). More than one means ambiguous."""


class InMemoryCatalog:
    """A :class:`CatalogReader` over a fixed set of parts. Test and standalone use only."""

    def __init__(self, parts: Iterable[PartInfo] = ()):
        self._by_id: dict[str, PartInfo] = {}
        self._by_number: dict[str, list[PartInfo]] = {}
        for p in parts:
            self.add(p)

    def add(self, part: PartInfo) -> None:
        if part.part_id in self._by_id:
            raise StructureError(f"duplicate part id {part.part_id!r}")
        self._by_id[part.part_id] = part
        if part.number:
            self._by_number.setdefault(number_key(part.number), []).append(part)

    def get(self, part_id: str) -> PartInfo | None:
        return self._by_id.get(part_id)

    def find_by_number(self, number: str) -> list[PartInfo]:
        return sorted(self._by_number.get(number_key(number), []), key=lambda p: p.part_id)

    def __iter__(self):
        return iter(sorted(self._by_id.values(), key=lambda p: p.part_id))

    def __len__(self) -> int:
        return len(self._by_id)
