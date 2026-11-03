# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CSV export and all-or-nothing import of the parts catalog.

One row per part. Manufacturer, vendor and document references use numbered column groups
(``manufacturer_1``, ``mpn_1``, ``manufacturer_2`` ...), so a spreadsheet user can edit them without a
mini-language. ``attr:<Name>`` columns hold attributes. Export -> import is lossless. The same safety rules
as :mod:`revisionworx.bom.csvio` apply: UTF-8 (BOM optional), formula guard, row/column errors, size limits.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .catalog_service import CatalogError, PartsCatalogService
from .csvio import MAX_CSV_BYTES, MAX_ROWS, _encode, _read, _Rows
from .errors import BomError, CsvImportError, CsvProblem
from .parts import DocumentLink, ManufacturerRef, Part, VendorRef

BASE = ("part_id", "number", "description", "unit", "kind", "status", "revision", "manual_number")
_GROUP = re.compile(r"(manufacturer|mpn|vendor|sku|document_id|revision_id|role)_(\d+)$")


def parts_to_csv(parts: Iterable[Part], *, formula_guard: bool = True, excel: bool = True) -> bytes:
    parts = sorted(parts, key=lambda p: (p.key, p.part_id))
    nm = max((len(p.manufacturers) for p in parts), default=0)
    nv = max((len(p.vendors) for p in parts), default=0)
    nd = max((len(p.documents) for p in parts), default=0)
    attrs = sorted({k for p in parts for k, _ in p.attributes})
    header = list(BASE)
    for i in range(1, nm + 1):
        header += [f"manufacturer_{i}", f"mpn_{i}"]
    for i in range(1, nv + 1):
        header += [f"vendor_{i}", f"sku_{i}"]
    for i in range(1, nd + 1):
        header += [f"document_id_{i}", f"revision_id_{i}", f"role_{i}"]
    header += [f"attr:{a}" for a in attrs]
    rows = []
    for p in parts:
        r = [p.part_id, p.number, p.description, p.unit, p.kind, p.status, p.revision, "yes" if p.manual_number else "no"]
        for i in range(nm):
            m = p.manufacturers[i] if i < len(p.manufacturers) else None
            r += [m.manufacturer, m.mpn] if m else ["", ""]
        for i in range(nv):
            v = p.vendors[i] if i < len(p.vendors) else None
            r += [v.vendor, v.sku] if v else ["", ""]
        for i in range(nd):
            d = p.documents[i] if i < len(p.documents) else None
            r += [d.document_id, d.revision_id, d.role] if d else ["", "", ""]
        a = dict(p.attributes)
        r += [a.get(k, "") for k in attrs]
        rows.append(r)
    return _encode(header, rows, formula_guard=formula_guard, excel=excel)


def parts_from_csv(source: bytes | str | Path, *, encoding: str = "utf-8-sig", formula_guard: bool = True,
                   max_bytes: int = MAX_CSV_BYTES, max_rows: int = MAX_ROWS) -> list[tuple[int, Part | None, dict]]:
    """Parse rows into ``(row, Part or None, fields)``: a Part when the row has a ``part_id``, otherwise the
    fields to create one. Raises :class:`CsvImportError` listing every problem."""
    rows = _Rows(_read(source, encoding, max_bytes), encoding=encoding, max_rows=max_rows, formula_guard=formula_guard)
    if not rows.has("number"):
        rows.problem(rows.header_line, "", "required column: number")
        rows.fail_if_problems()
    known = set(BASE) | {"id"}
    for col in rows.index:
        if col not in known and not _GROUP.fullmatch(col) and not col.startswith("attr:"):
            rows.problem(rows.header_line, col, "unknown column (use attr:<Name> for custom attributes); "
                         "nothing was imported")
    rows.fail_if_problems()
    out = []
    numbers: dict[str, int] = {}
    for line, rec in rows.rows:
        g = lambda c: rows.get(rec, c)  # noqa: E731
        groups: dict[str, dict[int, str]] = {}
        for col in rows.index:
            m = _GROUP.fullmatch(col)
            if m and g(col):
                groups.setdefault(m.group(1), {})[int(m.group(2))] = g(col)
        try:
            mans = tuple(ManufacturerRef(groups.get("manufacturer", {}).get(i, ""), groups.get("mpn", {}).get(i, ""))
                         for i in sorted(set(groups.get("manufacturer", {})) | set(groups.get("mpn", {}))))
            vens = tuple(VendorRef(groups.get("vendor", {}).get(i, ""), groups.get("sku", {}).get(i, ""))
                         for i in sorted(set(groups.get("vendor", {})) | set(groups.get("sku", {}))))
            docs = tuple(DocumentLink(groups.get("document_id", {}).get(i, ""), groups.get("revision_id", {}).get(i, ""),
                                      groups.get("role", {}).get(i, "") or "drawing")
                         for i in sorted(set(groups.get("document_id", {})) | set(groups.get("revision_id", {}))))
            manual = g("manual_number").casefold()
            if manual not in ("", "yes", "no", "true", "false"):
                raise CatalogError("manual_number: use yes or no")
            fields = dict(number=g("number"), description=g("description"), unit=g("unit") or "EA",
                          kind=g("kind") or "part", status=g("status") or "active", revision=g("revision"),
                          manufacturers=mans, vendors=vens, documents=docs, attributes=tuple(rows.attributes(rec)),
                          manual_number=manual in ("yes", "true"))
            key = fields["number"].casefold()
            if key in numbers:
                raise CatalogError(f"part number {fields['number']} repeats row {numbers[key]}")
            numbers[key] = line
            pid = g("part_id") or g("id")   # the header "part_id" is canonicalised to "id"
            part = Part(pid, **fields) if pid else None
            if part is None:
                Part("prt_" + "0" * 32, **fields)   # validate the fields now, with a dummy id
            out.append((line, part, fields))
        except BomError as exc:
            rows.problem(line, "", str(exc))
    rows.fail_if_problems()
    return out


def import_parts(service: PartsCatalogService, source: bytes | str | Path, *, update_existing: bool = False,
                 **kw) -> dict[str, int]:
    """Create (and with ``update_existing`` update) catalog parts from CSV in **one** store commit.

    Rows are matched by ``part_id``, else by number. Nothing is written if any row is refused."""
    txid = kw.pop("txid", None)
    return service.import_part_rows(parts_from_csv(source, **kw), update_existing=update_existing, txid=txid)


def export_parts(service: PartsCatalogService, **kw) -> bytes:
    return parts_to_csv(service.parts(), **kw)


__all__ = ["parts_to_csv", "parts_from_csv", "import_parts", "export_parts", "CsvImportError", "CsvProblem"]
