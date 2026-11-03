# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal, standard-library XLSX (Office Open XML spreadsheet) import and export (REQUIREMENTS.md section 6).

Export writes one worksheet whose cells are all *inline strings*: values stay exact (no floating point) and a
spreadsheet never evaluates them as formulas, so the CSV formula guard is not needed. The output is
deterministic: fixed member order and timestamps, so the same table gives the same bytes.

Import reads the first (or a named) worksheet and hands the rows to the CSV importers in
:mod:`revisionworx.bom.csvio` / :mod:`revisionworx.bom.parts_csv`, so validation, errors (with the real
worksheet row numbers) and all-or-nothing behaviour are identical. It refuses, with a reason:

* formula cells (``<f>``) - paste values only, so the file says what the engineer saw;
* error cells (``#N/A``...);
* archives over the size/member limits, members that expand beyond the limit (zip bombs), encrypted or
  non-ZIP files, and XML with a ``DOCTYPE`` (entity expansion).

Numbers are taken as the exact text stored in the file (Excel writes ``2.5``, not a binary float), then
parsed by the same strict quantity rules as CSV.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from . import csvio, parts_csv
from .catalog import CatalogReader
from .errors import CsvImportError, CsvProblem
from .model import Provenance, Structure
from .parts import Part

MAX_XLSX_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 2000
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_REF = re.compile(r"([A-Z]{1,3})([0-9]{1,7})")
_STAMP = (1980, 1, 1, 0, 0, 0)


# ----------------------------------------------------------------------------------------- writing
def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def table_to_xlsx(header: Sequence[str], rows: Iterable[Sequence[str]], *, sheet: str = "BOM") -> bytes:
    """One worksheet of inline-string cells. Deterministic bytes for the same input."""
    if not re.fullmatch(r"[^\[\]:*?/\\]{1,31}", sheet):
        raise ValueError("sheet name must be 1-31 characters without [ ] : * ? / \\")
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              f'<worksheet xmlns="{_NS["m"]}"><sheetData>')
    for r, values in enumerate([list(header)] + [list(x) for x in rows], start=1):
        out.write(f'<row r="{r}">')
        for c, v in enumerate(values):
            text = "" if v is None else str(v)
            if text == "":
                continue
            out.write(f'<c r="{_col(c)}{r}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        out.write("</row>")
    out.write("</sheetData></worksheet>")
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{_NS["pr"]}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<workbook xmlns="{_NS["m"]}" xmlns:r="{_NS["r"]}"><sheets>'
            f'<sheet name={quoteattr(sheet)} sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{_NS["pr"]}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": out.getvalue(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, text.encode("utf-8"))
    return buf.getvalue()


def csv_to_xlsx(csv_bytes: bytes, *, sheet: str = "BOM") -> bytes:
    """Any CSV this package exports (reports included) as a worksheet. The formula guard is removed:
    inline strings are never evaluated."""
    import csv
    text = csv_bytes.decode("utf-8-sig")
    rows = [[csvio.unguard(c) for c in r] for r in csv.reader(io.StringIO(text, newline=""))]
    return table_to_xlsx(rows[0] if rows else [], rows[1:], sheet=sheet)


def structure_to_xlsx(structure: Structure, *, sheet: str = "BOM") -> bytes:
    return csv_to_xlsx(csvio.structure_to_csv(structure, formula_guard=False), sheet=sheet)


def parts_to_xlsx(parts: Iterable[Part], *, sheet: str = "Parts") -> bytes:
    return csv_to_xlsx(parts_csv.parts_to_csv(parts, formula_guard=False), sheet=sheet)


# ----------------------------------------------------------------------------------------- reading
def _fail(message: str, row: int = 0, column: str = "") -> CsvImportError:
    return CsvImportError([CsvProblem(row, column, message)])


def _member(zf: zipfile.ZipFile, name: str, budget: list[int]) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise _fail(f"the workbook has no {name}") from None
    if info.flag_bits & 0x1:
        raise _fail("the workbook is encrypted; save an unencrypted copy")
    if info.file_size > MAX_MEMBER_BYTES or info.file_size > budget[0]:
        raise _fail(f"{name} expands to {info.file_size} bytes, over the limit")
    with zf.open(info) as fh:
        data = fh.read(min(MAX_MEMBER_BYTES, budget[0]) + 1)
    if len(data) > MAX_MEMBER_BYTES or len(data) > budget[0]:
        raise _fail(f"{name} expands beyond the size limit")
    budget[0] -= len(data)
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data.upper():
        raise _fail(f"{name} contains a DOCTYPE or entity declaration, which is not allowed")
    return data


def _xml(data: bytes, name: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise _fail(f"{name} is not valid XML: {exc}") from None


def _resolve(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.rsplit("/", 1)[0].split("/") + target.split("/")
    out: list[str] = []
    for p in parts:
        if p == "..":
            if out:
                out.pop()
        elif p and p != ".":
            out.append(p)
    return "/".join(out)


def read_rows(source: bytes | str | Path, *, sheet: str | None = None,
              max_bytes: int = MAX_XLSX_BYTES) -> list[tuple[int, list[str]]]:
    """``[(worksheet row number, [cell text, ...]), ...]`` of one worksheet (default: the first)."""
    data = csvio.read_file(source, max_bytes) if isinstance(source, (str, Path)) else bytes(source)
    if len(data) > max_bytes:
        raise _fail(f"larger than the {max_bytes}-byte limit")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _fail("not an .xlsx workbook (not a ZIP archive); for .xls or .csv use another import") from None
    with zf:
        if len(zf.infolist()) > MAX_MEMBERS:
            raise _fail("the workbook has too many parts")
        budget = [MAX_TOTAL_BYTES]
        wb = _xml(_member(zf, "xl/workbook.xml", budget), "workbook.xml")
        sheets = wb.findall("m:sheets/m:sheet", _NS)
        if not sheets:
            raise _fail("the workbook has no worksheets")
        chosen = sheets[0] if sheet is None else next((s for s in sheets if s.get("name") == sheet), None)
        if chosen is None:
            raise _fail(f"no worksheet named {sheet!r}; found {', '.join(s.get('name', '') for s in sheets)}")
        rid = chosen.get(f"{{{_NS['r']}}}id")
        rels = _xml(_member(zf, "xl/_rels/workbook.xml.rels", budget), "workbook.xml.rels")
        target = next((r.get("Target") for r in rels.findall("pr:Relationship", _NS) if r.get("Id") == rid), None)
        if not target:
            raise _fail("the worksheet relationship is missing")
        sheet_xml = _xml(_member(zf, _resolve("xl/workbook.xml", target), budget), "worksheet")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sst = _xml(_member(zf, "xl/sharedStrings.xml", budget), "sharedStrings.xml")
            for si in sst.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    rows: list[tuple[int, list[str]]] = []
    problems: list[CsvProblem] = []
    for row in sheet_xml.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, str] = {}
        rnum = int(row.get("r", len(rows) + 1))
        next_col = 0
        for c in row.findall("m:c", _NS):
            ref = c.get("r", "")
            m = _REF.fullmatch(ref)
            col = next_col
            if m:
                col = 0
                for ch in m.group(1):
                    col = col * 26 + ord(ch) - 64
                col -= 1
                rnum = int(m.group(2))
            next_col = col + 1
            cname = _col(col)
            if c.find("m:f", _NS) is not None:
                problems.append(CsvProblem(rnum, cname, "formula cell; paste values only so the file holds what "
                                                         "you see"))
                continue
            t = c.get("t", "n")
            v = c.find("m:v", _NS)
            if t == "inlineStr":
                value = "".join(x.text or "" for x in c.iter(f"{{{_NS['m']}}}t"))
            elif t == "s":
                try:
                    value = shared[int(v.text or "")] if v is not None else ""
                except (ValueError, IndexError):
                    problems.append(CsvProblem(rnum, cname, "broken shared-string reference"))
                    continue
            elif t == "e":
                problems.append(CsvProblem(rnum, cname, f"error value {v.text if v is not None else ''}"))
                continue
            elif t == "b":
                value = "TRUE" if (v is not None and v.text == "1") else "FALSE"
            else:   # n, str, d
                value = v.text if v is not None and v.text is not None else ""
            if value != "":
                cells[col] = value
        if cells:
            width = max(cells) + 1
            rows.append((rnum, [cells.get(i, "") for i in range(width)]))
    if problems:
        raise CsvImportError(problems[:csvio.MAX_PROBLEMS], truncated=len(problems) > csvio.MAX_PROBLEMS)
    if not rows:
        raise _fail("the worksheet is empty")
    return sorted(rows)


def structure_from_xlsx(source: bytes | str | Path, *, sheet: str | None = None, catalog: CatalogReader | None = None,
                        provenance: str = Provenance.IMPORTED.value) -> Structure:
    """Parent-child layout from a worksheet (same columns and rules as :func:`csvio.structure_from_csv`)."""
    return csvio.structure_from_csv(read_rows(source, sheet=sheet), catalog=catalog, formula_guard=False,
                                    provenance=provenance)


def structure_from_indented_xlsx(source: bytes | str | Path, *, sheet: str | None = None, root_number: str = "",
                                 catalog: CatalogReader | None = None) -> tuple[Structure, str]:
    return csvio.structure_from_indented_csv(read_rows(source, sheet=sheet), root_number=root_number,
                                             catalog=catalog, formula_guard=False)


def parts_from_xlsx(source: bytes | str | Path, *, sheet: str | None = None):
    return parts_csv.parts_from_csv(read_rows(source, sheet=sheet), formula_guard=False)
