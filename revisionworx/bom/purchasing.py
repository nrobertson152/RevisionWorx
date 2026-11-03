# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derived purchasing (buy) list - kept distinct from the engineering BOM (REQUIREMENTS.md section 6).

The engineering BOM says what a product *is made of*. A buy list says what to *order*. This module derives a
simple buy list from an engineering structure and the parts catalog, and never changes either:

* only leaf items are bought (an item with its own BOM is made); ``document`` items are reference-only
  and excluded;
* required quantities are exact totals for the requested number of products, in each part's unit;
* count units (``EA``) are ordered in whole numbers: a fractional requirement is rounded **up** and the row
  is flagged ``rounded-up`` - the engineering total is still shown unchanged;
* rows carry the catalog's manufacturer and vendor references, and are flagged ``no-source``,
  ``obsolete``, ``unresolved`` or ``needs-verification`` where that applies.

Not modelled (and not claimed): scrap/yield, stock on hand, lead times, prices, alternates or a
manufacturing routing. It is a starting point for purchasing, not an MRP result.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Mapping

from .graph import rollup
from .model import Structure, natural_key
from .parts import Part, PartKind
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, UnitRegistry, canonical_text

NOTE = ("Derived buy list: leaf items of the engineering BOM with exact required totals. Count units are "
        "rounded up to whole numbers to order. Scrap, stock, lead times, prices and alternates are not included.")


@dataclass(frozen=True)
class BuyLine:
    part_id: str
    number: str
    description: str
    required: Decimal            # exact engineering total
    order_quantity: Decimal      # what to order (count units rounded up)
    unit: str
    manufacturers: tuple[str, ...]
    vendors: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class PurchasingList:
    root_id: str
    quantity: Decimal
    lines: tuple[BuyLine, ...]
    excluded: tuple[str, ...]    # reference-only items left out
    note: str = NOTE

    @property
    def unsourced(self) -> tuple[BuyLine, ...]:
        return tuple(b for b in self.lines if "no-source" in b.flags)


def purchasing_list(structure: Structure, root_id: str, quantity: Decimal = Decimal(1), *,
                    parts: Mapping[str, Part] | None = None, units: UnitRegistry = DEFAULT_UNITS,
                    limits: Limits = DEFAULT_LIMITS) -> PurchasingList:
    """Buy list for ``quantity`` of ``root_id``. ``parts`` (catalog records by id) supplies sourcing data;
    without it every row is flagged ``no-source``."""
    parts = parts or {}
    ro = rollup(structure, root_id, quantity, leaves_only=True, limits=limits, units=units)
    lines, excluded = [], []
    for r in ro.rows:
        info = structure.info(r.item_id)
        part = parts.get(r.item_id)
        kind = part.kind if part else info.kind
        if kind == PartKind.DOCUMENT.value:
            excluded.append(r.item_id)
            continue
        flags = []
        order = r.total
        if units.known(r.unit) and units.get(r.unit).integral and r.total != r.total.to_integral_value():
            order = r.total.to_integral_value(rounding=ROUND_CEILING)
            flags.append("rounded-up")
        mans = tuple(f"{m.manufacturer} {m.mpn}" for m in part.manufacturers) if part else ()
        vens = tuple(f"{v.vendor} {v.sku}".strip() for v in part.vendors) if part else ()
        if not mans and not vens:
            flags.append("no-source")
        if part and part.obsolete:
            flags.append("obsolete")
        if not info.resolved:
            flags.append("unresolved")
        if r.needs_verification:
            flags.append("needs-verification")
        lines.append(BuyLine(r.item_id, (part.number if part else info.number) or r.item_id,
                             part.description if part else info.description, r.total, order, r.unit, mans, vens,
                             tuple(flags)))
    lines.sort(key=lambda b: (natural_key(b.number), b.part_id))
    return PurchasingList(root_id, quantity, tuple(lines), tuple(sorted(excluded)))


def purchasing_to_csv(plist: PurchasingList, **kw) -> bytes:
    from .csvio import _encode, _opts
    header = ["part_number", "part_id", "description", "required", "order_quantity", "unit", "manufacturers",
              "vendors", "flags"]
    rows = [[b.number, b.part_id, b.description, canonical_text(b.required), canonical_text(b.order_quantity),
             b.unit, "; ".join(b.manufacturers), "; ".join(b.vendors), " ".join(b.flags)] for b in plist.lines]
    rows += [["EXCLUDED", pid, "reference-only item", "", "", "", "", "", ""] for pid in plist.excluded]
    rows.append(["NOTE", plist.note, "", "", "", "", "", "", ""])
    return _encode(header, rows, **_opts(kw))
