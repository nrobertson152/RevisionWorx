# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CSV import and export of engineering BOM structures and reports.

Two structure layouts:

* **Parent-child** (:func:`structure_to_csv` / :func:`structure_from_csv`) - lossless. Item rows (empty
  parent) declare parts; line rows declare usages. A shared subassembly is defined once. Round-trips
  exactly: ``structure_from_csv(structure_to_csv(s)) == s``.
* **Indented** (:func:`structure_from_indented_csv`) - the level-numbered layout most CAD systems export.
  A subassembly listed twice must have the same children both times.

Reports (indented explosion, summarized rollup, comparison, validation, impact) export only.

Safety:

* **All or nothing.** Import parses and checks the whole file and raises :class:`CsvImportError` listing
  the problems (row and column) - nothing is half-imported. Export builds the whole file in memory and
  writes it with :func:`revisionworx.bom.fileio.write_file` - the destination is never left partial.
* **Unicode.** UTF-8 with or without a byte-order mark is read; other encodings must be named. Export
  writes UTF-8 with a byte-order mark by default so spreadsheet programs detect it.
* **Spreadsheet formulas.** Exported text cells that start with ``= + - @``, tab or carriage return are
  prefixed with ``'`` (OWASP CSV injection guidance). Import reverses exactly that escaping, so the
  round-trip is lossless. Disable with ``formula_guard=False`` on both sides for machine exchange.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .catalog import CatalogReader, PartInfo, number_key, unresolved_id
from .diff import BomDiff, QuantityDelta
from .errors import BomError, CsvImportError, CsvProblem
from .fileio import read_file, write_file
from .graph import Explosion, Rollup
from .impact import ChangeImpactReport, RevisionImpactReport
from .model import BomLine, Provenance, Structure
from .units import canonical_text, parse_quantity
from .validate import ValidationReport

MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_PROBLEMS = 200
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
_GUARD_RE = re.compile(r"^'+[=+\-@\t\r]")
_ATTR_PREFIX = "attr:"

STRUCTURE_COLUMNS = ("parent_number", "parent_id", "find_number", "child_number", "child_id",
                     "child_revision", "quantity", "unit", "reference_designators", "description", "part_unit",
                     "kind", "resolved", "provenance", "line_id", "note")

_ALIASES = {
    # parent-child layout
    "parent": "parent_number", "parentnumber": "parent_number", "parentpartnumber": "parent_number",
    "parentpn": "parent_number", "assembly": "parent_number", "parentid": "parent_id",
    "child": "child_number", "childnumber": "child_number", "childpartnumber": "child_number",
    "component": "child_number", "childid": "child_id", "childrevision": "child_revision",
    "childrev": "child_revision",
    # shared
    "partnumber": "number", "partno": "number", "pn": "number", "number": "number", "itemnumber": "number",
    "findnumber": "find_number", "find": "find_number", "findno": "find_number", "item": "item",
    "itemno": "item", "balloon": "find_number", "quantity": "quantity", "qty": "quantity", "unitqty": "quantity",
    "unit": "unit", "uom": "unit", "units": "unit", "unitofmeasure": "unit",
    "referencedesignators": "reference_designators", "refdes": "reference_designators",
    "reference": "reference_designators", "designators": "reference_designators",
    "description": "description", "desc": "description", "revision": "revision", "rev": "revision",
    "level": "level", "partunit": "part_unit", "kind": "kind", "type": "kind", "resolved": "resolved",
    "provenance": "provenance", "source": "provenance", "lineid": "line_id", "note": "note", "notes": "note",
    "remarks": "note", "id": "id", "partid": "id",
}


# ------------------------------------------------------------------------------------- low-level
def guard(text: str) -> str:
    """Escape a cell a spreadsheet would run as a formula: ``'`` is added before ``'*[=+-@\\t\\r]``."""
    return "'" + text if text.lstrip("'")[:1] in _DANGEROUS else text


def unguard(text: str) -> str:
    """Exact inverse of :func:`guard`."""
    return text[1:] if _GUARD_RE.match(text) else text


#: Parts-catalog columns (parts_csv), matched case-insensitively like every other column.
PARTS_COLUMNS = ("part_id", "number", "description", "unit", "kind", "status", "revision", "manual_number")
_PARTS_GROUP = re.compile(r"(manufacturer|mpn|vendor|sku|document_id|revision_id|role)_\d+")


def _canon_header(raw: str) -> str:
    h = unicodedata.normalize("NFKC", raw).strip()
    if h.casefold().startswith(_ATTR_PREFIX):
        return _ATTR_PREFIX + h[len(_ATTR_PREFIX):].strip()   # attribute names keep their spelling
    low = h.casefold().replace(" ", "_")
    if low in PARTS_COLUMNS[2:] or _PARTS_GROUP.fullmatch(low):
        return low
    key = re.sub(r"[\s_.\-#/()]+", "", h.casefold())
    if h.casefold().replace(" ", "_") in STRUCTURE_COLUMNS:
        return h.casefold().replace(" ", "_")
    return _ALIASES.get(key, h)


class _Rows:
    """Decoded CSV rows with physical line numbers and canonical headers."""

    def __init__(self, data, *, encoding: str, max_rows: int, formula_guard: bool):
        """``data`` is CSV bytes, or already-parsed ``[(row number, [cells]), ...]`` (the XLSX reader)."""
        self.problems: list[CsvProblem] = []
        self.formula_guard = formula_guard
        self.rows: list[tuple[int, list[str]]] = []
        if isinstance(data, list):
            if len(data) > max_rows + 1:
                raise CsvImportError([CsvProblem(data[max_rows + 1][0], "", f"more than {max_rows} rows")])
            self.rows = [(n, list(r)) for n, r in data if any(c.strip() for c in r)]
        else:
            self._parse_csv(data, encoding, max_rows)
        if not self.rows:
            raise CsvImportError([CsvProblem(0, "", "the file is empty")])
        hline, header = self.rows.pop(0)
        self.header_line = hline
        self.columns = [_canon_header(h) for h in header]
        seen: dict[str, int] = {}
        for i, c in enumerate(self.columns):
            if not c:
                self.problem(hline, f"#{i + 1}", "empty column header")
            elif c.casefold() in seen:
                self.problem(hline, c, "duplicate column")
            seen[c.casefold()] = i
        self.index = {c: i for i, c in enumerate(self.columns) if c}
        for line, record in self.rows:
            if len(record) > len(self.columns) and any(x.strip() for x in record[len(self.columns):]):
                self.problem(line, "", f"{len(record)} values but only {len(self.columns)} column headers")

    def _parse_csv(self, data: bytes, encoding: str, max_rows: int) -> None:
        if b"\x00" in data:
            line = data[:data.index(b"\x00")].count(b"\n") + 1
            raise CsvImportError([CsvProblem(line, "", "file contains NUL bytes; it is not a text CSV file")])
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as exc:
            line = data[:exc.start].count(b"\n") + 1
            raise CsvImportError([CsvProblem(line, "", f"not valid {encoding} text at byte {exc.start}; save the "
                                             "file as UTF-8 or name its encoding")]) from None
        except LookupError:
            raise CsvImportError([CsvProblem(0, "", f"unknown encoding {encoding!r}")]) from None
        if text.startswith("\ufeff"):
            text = text[1:]
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        start = 1
        try:
            for record in reader:
                if len(self.rows) > max_rows:
                    raise CsvImportError([CsvProblem(start, "", f"more than {max_rows} rows")])
                if any(c.strip() for c in record):
                    self.rows.append((start, record))
                start = reader.line_num + 1
        except csv.Error as exc:
            raise CsvImportError([CsvProblem(reader.line_num or start, "", f"malformed CSV: {exc}")]) from None

    def problem(self, row: int, column: str, message: str) -> None:
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(CsvProblem(row, column, message))

    def has(self, column: str) -> bool:
        return column in self.index

    def get(self, record: list[str], column: str) -> str:
        i = self.index.get(column)
        if i is None or i >= len(record):
            return ""
        v = record[i]
        if self.formula_guard:
            v = unguard(v)
        return unicodedata.normalize("NFC", v).strip()

    def attributes(self, record: list[str]) -> list[tuple[str, str]]:
        out = []
        for c, i in self.index.items():
            if c.startswith(_ATTR_PREFIX) and i < len(record):
                v = self.get(record, c)
                if v:
                    out.append((c[len(_ATTR_PREFIX):], v))
        return out

    def fail_if_problems(self) -> None:
        if self.problems:
            raise CsvImportError(sorted(self.problems, key=lambda p: (p.row, p.column)),
                                 truncated=len(self.problems) >= MAX_PROBLEMS)


class _Identities:
    """Resolves part numbers to ids (catalog, else unresolved placeholders) and collects PartInfo."""

    def __init__(self, rows: _Rows, catalog: CatalogReader | None):
        self.rows = rows
        self.catalog = catalog
        self.parts: dict[str, PartInfo] = {}
        self.number_of: dict[str, str] = {}

    def resolve(self, line: int, column: str, part_id: str, number: str) -> str | None:
        if part_id:
            if self.catalog is not None and self.catalog.get(part_id) is None:
                self.rows.problem(line, column, f"part id {part_id!r} is not in the parts catalog")
                return None
            if number:
                known = self.number_of.setdefault(part_id, number)
                if number_key(known) != number_key(number):
                    self.rows.problem(line, column, f"part id {part_id} has number {known!r} on an earlier row "
                                      f"and {number!r} here")
            return part_id
        if not number:
            self.rows.problem(line, column, "a part number or part id is required")
            return None
        if self.catalog is not None:
            found = self.catalog.find_by_number(number)
            if len(found) == 1:
                self.parts.setdefault(found[0].part_id, found[0])
                return found[0].part_id
            if len(found) > 1:
                self.rows.problem(line, column, f"part number {number!r} matches {len(found)} catalog parts: "
                                  f"{', '.join(p.part_id for p in found)}; give the part id")
                return None
        pid = unresolved_id(number)
        self.parts.setdefault(pid, PartInfo(pid, number=number, resolved=False))
        return pid

    def declare(self, line: int, info: PartInfo) -> None:
        old = self.parts.get(info.part_id)
        if old is not None and old != info and not (not old.resolved and old.number == info.number
                                                     and not old.description):
            if old.resolved and self.catalog is not None and self.catalog.get(info.part_id) == old:
                return  # the catalog record wins over a file's copy of it
            self.rows.problem(line, "", f"part {info.display_number} is described differently on an earlier row")
            return
        self.parts[info.part_id] = info


def _quantity(rows: _Rows, line: int, text: str) -> Decimal | None:
    try:
        return parse_quantity(text)
    except BomError as exc:
        rows.problem(line, "quantity", str(exc))
        return None


def _make_line(rows: _Rows, line: int, **kw) -> BomLine | None:
    try:
        return BomLine(**kw)
    except BomError as exc:
        rows.problem(line, "", str(exc))
        return None


def _read(source, encoding: str, max_bytes: int):
    if isinstance(source, list):   # pre-parsed rows from the XLSX reader
        return source
    if isinstance(source, (str, Path)):
        return read_file(source, max_bytes)
    data = bytes(source)
    if len(data) > max_bytes:
        raise CsvImportError([CsvProblem(0, "", f"larger than the {max_bytes}-byte limit")])
    return data


# ---------------------------------------------------------------------------- parent-child layout
def structure_to_csv(structure: Structure, *, formula_guard: bool = True, excel: bool = True) -> bytes:
    """Lossless parent-child CSV: item rows first (by number), then line rows in structure order."""
    attr_names = sorted({k for ln in structure.lines for k, _ in ln.attributes})
    header = list(STRUCTURE_COLUMNS) + [_ATTR_PREFIX + a for a in attr_names]
    rows: list[list[str]] = []
    for p in sorted(structure.parts, key=lambda p: (number_key(p.display_number), p.part_id)):
        rows.append(["", "", "", p.number, p.part_id, p.revision, "", "", "", p.description, p.unit, p.kind,
                     "yes" if p.resolved else "no", "", "", ""] + [""] * len(attr_names))
    for ln in structure.lines:
        attrs = dict(ln.attributes)
        rows.append([structure.info(ln.parent_id).number, ln.parent_id, ln.find_number,
                     structure.info(ln.child_id).number, ln.child_id, ln.child_revision, ln.quantity_text, ln.unit,
                     " ".join(ln.reference_designators), "", "", "", "", ln.provenance, ln.line_id, ln.note]
                    + [attrs.get(a, "") for a in attr_names])
    return _encode(header, rows, formula_guard=formula_guard, excel=excel)


def structure_from_csv(source: bytes | str | Path, *, catalog: CatalogReader | None = None,
                       encoding: str = "utf-8-sig", formula_guard: bool = True,
                       provenance: str = Provenance.IMPORTED.value, max_bytes: int = MAX_CSV_BYTES,
                       max_rows: int = MAX_ROWS) -> Structure:
    """Import the parent-child layout. Lines take ``provenance`` unless the file has a provenance column.

    Without a ``catalog``, rows that give only a part number get an unresolved placeholder identity
    (``pn:<number>``), reported as ``unresolved-identity`` by validation."""
    rows = _Rows(_read(source, encoding, max_bytes), encoding=encoding, max_rows=max_rows,
                 formula_guard=formula_guard)
    if not (rows.has("child_number") or rows.has("child_id")) or not rows.has("quantity"):
        if rows.has("level") or rows.has("item"):
            rows.problem(rows.header_line, "", "this looks like an indented BOM; use the indented import")
        else:
            rows.problem(rows.header_line, "", "required columns: child_number (or child_id) and quantity; found "
                         + ", ".join(c for c in rows.columns if c))
        rows.fail_if_problems()
    ids = _Identities(rows, catalog)
    lines: list[BomLine] = []
    for line, rec in rows.rows:
        g = lambda c: rows.get(rec, c)  # noqa: E731
        child = ids.resolve(line, "child_number", g("child_id"), g("child_number"))
        if child is None:
            continue
        if not g("parent_id") and not g("parent_number"):  # item row
            if g("quantity"):
                rows.problem(line, "quantity", "an item row (no parent) must not have a quantity")
                continue
            resolved = g("resolved").casefold()
            if resolved not in ("", "yes", "no", "true", "false"):
                rows.problem(line, "resolved", "use yes or no")
                continue
            try:
                ids.declare(line, PartInfo(child, number=g("child_number"), description=g("description"),
                                           unit=g("part_unit") or "EA", revision=g("child_revision"),
                                           kind=g("kind"), resolved=(resolved in ("yes", "true")) if resolved
                                           else not child.startswith("pn:")))
            except BomError as exc:
                rows.problem(line, "", str(exc))
            continue
        parent = ids.resolve(line, "parent_number", g("parent_id"), g("parent_number"))
        q = _quantity(rows, line, g("quantity"))
        if parent is None or q is None:
            continue
        if g("description") and child not in ids.parts:
            try:
                ids.declare(line, PartInfo(child, number=g("child_number"), description=g("description"),
                                           resolved=not child.startswith("pn:")))
            except BomError as exc:
                rows.problem(line, "description", str(exc))
        ln = _make_line(rows, line, parent_id=parent, child_id=child, quantity=q, unit=g("unit") or "EA",
                        find_number=g("find_number"), reference_designators=g("reference_designators"),
                        child_revision=g("child_revision"), line_id=g("line_id"),
                        provenance=g("provenance") or provenance, note=g("note"),
                        attributes=tuple(rows.attributes(rec)))
        if ln is not None:
            lines.append(ln)
    rows.fail_if_problems()
    # An unresolved part with no item row only knows its number; take its unit from the first line that
    # uses it, so "2.5 m of HOSE" does not turn HOSE into a count item. Declared or catalog parts keep theirs.
    for ln in sorted(lines, key=BomLine.sort_key):
        info = ids.parts.get(ln.child_id)
        if info is not None and not info.resolved and info == PartInfo(info.part_id, number=info.number,
                                                                      resolved=False):
            ids.parts[ln.child_id] = PartInfo(info.part_id, number=info.number, unit=ln.unit, resolved=False)
    return _finish(rows, lines, ids)


def _finish(rows: _Rows, lines: list[BomLine], ids: _Identities) -> Structure:
    for pid in {ln.parent_id for ln in lines} | {ln.child_id for ln in lines}:
        if pid not in ids.parts and pid.startswith("pn:"):  # pragma: no cover - resolve() declares these
            ids.parts[pid] = PartInfo(pid, number=pid[3:], resolved=False)
    try:
        return Structure.build(lines, ids.parts.values())
    except BomError as exc:
        raise CsvImportError([CsvProblem(0, "", str(exc))]) from None


# -------------------------------------------------------------------------------- indented layout
def structure_from_indented_csv(source: bytes | str | Path, *, root_number: str = "", root_id: str = "",
                                catalog: CatalogReader | None = None, encoding: str = "utf-8-sig",
                                formula_guard: bool = True, provenance: str = Provenance.IMPORTED.value,
                                max_bytes: int = MAX_CSV_BYTES, max_rows: int = MAX_ROWS,
                                ) -> tuple[Structure, str]:
    """Import a level-numbered (indented) BOM. Returns ``(structure, root_id)``.

    Levels come from a ``level`` column (0 = the top item, 1 = its children...) or from dotted item numbers
    (``1``, ``1.1``, ``1.2.3``). Without a level-0 row, ``root_number`` or ``root_id`` names the top item."""
    rows = _Rows(_read(source, encoding, max_bytes), encoding=encoding, max_rows=max_rows,
                 formula_guard=formula_guard)
    if not (rows.has("number") or rows.has("id")) or not (rows.has("level") or rows.has("item")):
        rows.problem(rows.header_line, "", "required columns: level (or dotted item numbers) and part number; "
                     "found " + ", ".join(c for c in rows.columns if c))
        rows.fail_if_problems()
    ids = _Identities(rows, catalog)
    root: str | None = None
    if root_id or root_number:
        root = ids.resolve(rows.header_line, "", root_id, root_number)
    # occurrence index -> (item id, [lines])
    occurrences: list[tuple[str, int, list[BomLine]]] = []
    stack: list[int] = []   # occurrence index per level (stack[0] = root occurrence)
    if root is not None:
        occurrences.append((root, rows.header_line, []))
        stack = [0]
    for line, rec in rows.rows:
        g = lambda c: rows.get(rec, c)  # noqa: E731
        level = _level(rows, line, g("level"), g("item"))
        if level is None:
            continue
        item = ids.resolve(line, "number", g("id"), g("number"))
        if item is None:
            continue
        unit = g("unit") or "EA"
        existing = ids.parts.get(item)
        if existing is None or existing == PartInfo(item, number=existing.number, resolved=existing.resolved):
            try:  # first full description of an item wins; later rows only add usages
                ids.parts[item] = PartInfo(item, number=g("number") or (existing.number if existing else ""),
                                           description=g("description"), unit=unit, revision=g("revision"),
                                           resolved=existing.resolved if existing else not item.startswith("pn:"))
            except BomError as exc:
                rows.problem(line, "", str(exc))
        elif g("description") and existing.description and g("description") != existing.description:
            rows.problem(line, "description", f"{existing.display_number} has a different description on an "
                         "earlier row")
        if level == 0:
            if stack:
                rows.problem(line, "level", "only one level-0 (top) row is allowed, and it must come first")
                continue
            occurrences.append((item, line, []))
            stack = [len(occurrences) - 1]
            continue
        if not stack:
            rows.problem(line, "level", "no top item: add a level-0 row or name the root item")
            continue
        if level > len(stack):
            rows.problem(line, "level", f"level {level} follows level {len(stack) - 1}; levels may only "
                         "increase by one")
            continue
        del stack[level:]
        parent_occ = occurrences[stack[-1]]
        q = _quantity(rows, line, g("quantity"))
        if q is None:
            continue
        ln = _make_line(rows, line, parent_id=parent_occ[0], child_id=item, quantity=q, unit=unit,
                        find_number=g("find_number") or _last_item_part(g("item")),
                        reference_designators=g("reference_designators"), child_revision=g("revision"),
                        provenance=g("provenance") or provenance, note=g("note"),
                        attributes=tuple(rows.attributes(rec)))
        if ln is None:
            continue
        parent_occ[2].append(ln)
        occurrences.append((item, line, []))
        stack.append(len(occurrences) - 1)
    if not occurrences:
        rows.problem(0, "", "no rows")
    rows.fail_if_problems()
    definitions: dict[str, tuple[int, tuple[BomLine, ...]]] = {}
    for item, line, lines in occurrences:
        if not lines:
            continue
        key = tuple(sorted(lines, key=BomLine.sort_key))
        prev = definitions.get(item)
        if prev is None:
            definitions[item] = (line, key)
        elif prev[1] != key:
            rows.problem(line, "", f"{ids.parts.get(item, PartInfo(item)).display_number} is listed with different "
                         f"children here and on row {prev[0]}")
    rows.fail_if_problems()
    all_lines = [ln for _, lines in definitions.values() for ln in lines]
    return _finish(rows, all_lines, ids), occurrences[0][0]


def _last_item_part(item: str) -> str:
    return item.rsplit(".", 1)[-1] if item else ""


def _level(rows: _Rows, line: int, level_text: str, item_text: str) -> int | None:
    if level_text:
        t = level_text.strip().lstrip(".")
        if not t.isdigit():
            rows.problem(line, "level", f"level {level_text!r} is not a whole number")
            return None
        return int(t)
    if item_text:
        parts = item_text.split(".")
        if not all(p.strip().isdigit() for p in parts):
            rows.problem(line, "item", f"item number {item_text!r} is not a dotted number such as 1.2.3")
            return None
        return len(parts)
    rows.problem(line, "level", "missing level")
    return None


# ------------------------------------------------------------------------------------------ reports
def _encode(header: Sequence[str], rows: Iterable[Sequence[str]], *, formula_guard: bool, excel: bool) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(header)
    for r in rows:
        w.writerow([guard(c) if formula_guard and isinstance(c, str) else c for c in r])
    return (("﻿" if excel else "") + buf.getvalue()).encode("utf-8")


def _q(q: Decimal | None) -> str:
    return "" if q is None else canonical_text(q)


def explosion_to_csv(explosion: Explosion, structure: Structure, **kw) -> bytes:
    header = ["level", "path", "find_number", "part_number", "part_id", "description", "revision", "quantity",
              "unit", "extended_quantity", "extended_unit", "reference_designators", "provenance"]
    rows = []
    for r in explosion.rows:
        info = structure.info(r.item_id)
        rows.append([str(r.level), " > ".join(structure.info(p).display_number for p in r.path),
                     r.line.find_number, info.number, r.item_id, info.description, r.line.child_revision,
                     r.line.quantity_text, r.line.unit, _q(r.extended_quantity), r.unit,
                     " ".join(r.line.reference_designators), r.line.provenance])
    return _encode(header, rows, **_opts(kw))


def rollup_to_csv(rollup: Rollup, structure: Structure, **kw) -> bytes:
    header = ["part_number", "part_id", "description", "total_quantity", "unit", "occurrences", "assembly",
              "min_level", "needs_verification"]
    rows = [[structure.info(r.item_id).number, r.item_id, structure.info(r.item_id).description, _q(r.total),
             r.unit, str(r.occurrences), "yes" if r.is_assembly else "no", str(r.min_level),
             "yes" if r.needs_verification else "no"] for r in rollup.rows]
    return _encode(header, rows, **_opts(kw))


def diff_to_csv(diff: BomDiff, before: Structure, after: Structure, **kw) -> bytes:
    header = ["change", "parent_number", "child_number", "find_number", "field", "before", "after"]
    rows = []
    num = lambda pid: (after.part(pid) or before.part(pid) or PartInfo(pid)).display_number  # noqa: E731
    for c in diff.changes:
        ln = c.after or c.before
        if c.kind == "changed":
            for f in c.fields:
                rows.append([c.kind, num(c.parent_id), num(c.child_id), ln.find_number, f.field, f.before, f.after])
        else:
            q = f"{ln.quantity_text} {ln.unit}"
            rows.append([c.kind, num(c.parent_id), num(c.child_id), ln.find_number, "quantity",
                         q if c.kind == "removed" else "", q if c.kind == "added" else ""])
    for p in diff.part_changes:
        for f in p.fields or ((),):
            if f == ():
                rows.append([f"part-{p.kind}", "", num(p.part_id), "", "", "", ""])
            else:
                rows.append([f"part-{p.kind}", "", num(p.part_id), "", f.field, f.before, f.after])
    return _encode(header, rows, **_opts(kw))


def quantity_deltas_to_csv(deltas: Sequence[QuantityDelta], number_of: Callable[[str], str], **kw) -> bytes:
    header = ["part_number", "part_id", "before", "after", "change", "unit"]
    rows = [[number_of(d.item_id), d.item_id, _q(d.before), _q(d.after), _q(d.delta), d.unit] for d in deltas]
    return _encode(header, rows, **_opts(kw))


def issues_to_csv(report: ValidationReport, **kw) -> bytes:
    header = ["severity", "code", "message", "parent_id", "child_id", "line_id"]
    rows = [[i.severity, i.code, i.message, i.parent_id, i.child_id, i.line_id] for i in report.issues]
    return _encode(header, rows, **_opts(kw))


def revision_impact_to_csv(report: RevisionImpactReport, **kw) -> bytes:
    header = ["item", "snapshot", "assembly", "top_level", "relation", "distance", "referenced_revisions",
              "quantity_per_top_level", "unit", "classification", "notes"]
    rows = [[report.item_number, e.snapshot, e.assembly_number, e.top_level_id, e.relation, str(e.distance),
             " ".join(r or "(latest)" for r in e.referenced_revisions), _q(e.quantity_per_top_level), e.unit,
             " ".join(e.classifications), "; ".join(e.notes)] for e in report.entries]
    rows += [["COVERAGE", c.snapshot, c.document_id, c.revision_label, "searched" if c.searched else "NOT searched",
              "", "", "", "", "released" if c.released else "", c.problem] for c in report.coverage]
    rows.append(["NOTE", report.coverage_note, "", "", "", "", "", "", "", "", ""])
    return _encode(header, rows, **_opts(kw))


def change_impact_to_csv(report: ChangeImpactReport, **kw) -> bytes:
    header = ["snapshot", "top_level", "released", "part_id", "before", "after", "change", "unit", "problem"]
    rows = []
    for a in report.affected:
        if not a.deltas:
            rows.append([a.snapshot, a.top_level_number, "yes" if a.released else "no", "", "", "", "", "",
                         a.problem or "no quantity change"])
        for d in a.deltas:
            rows.append([a.snapshot, a.top_level_number, "yes" if a.released else "no", d.item_id, _q(d.before),
                         _q(d.after), _q(d.delta), d.unit, a.problem])
    rows += [["COVERAGE", c.snapshot, "yes" if c.released else "no", "", "", "", "", "", c.problem]
             for c in report.coverage]
    rows.append(["NOTE", report.coverage_note, "", "", "", "", "", "", ""])
    return _encode(header, rows, **_opts(kw))


def _opts(kw: dict) -> dict:
    return {"formula_guard": kw.get("formula_guard", True), "excel": kw.get("excel", True)}


def save(data: bytes, path: Path | str, *, overwrite: bool = False) -> Path:
    """Write export bytes all-or-nothing (see :mod:`revisionworx.bom.fileio`)."""
    return write_file(path, data, overwrite=overwrite)


@dataclass(frozen=True)
class ImportSummary:
    lines: int
    parts: int
    unresolved: int


def summarize(structure: Structure) -> ImportSummary:
    return ImportSummary(len(structure.lines), len(structure.parts),
                         sum(1 for p in structure.parts if not p.resolved))
