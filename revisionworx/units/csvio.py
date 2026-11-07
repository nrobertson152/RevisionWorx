# SPDX-License-Identifier: GPL-3.0-or-later
"""Bringing existing serial-number registers in: one CSV row per unit, all of them or none (docs/UNITS.md, migration).

A FieldWorx asset register (``*.fwassets.csv``: tag, serial, name, part, project, location, built, as_built, notes) is read as it
is, and each unit keeps the id FieldWorx gave it as a ``fieldworx`` identifier, so inspections already recorded against it still
attach. Nothing is guessed: every problem in the file is listed, and nothing is written while there is one.
"""
from __future__ import annotations

import csv
import io

from .model import (MILESTONES, ImportProblems, NotFound, ValidationError, UnitError, fieldworx_asset_id, key_id, load_key,
                    norm)
from .service import KIND, UnitService

MAX_ROWS = 20_000
MAX_BYTES = 64 * 1024 * 1024
MAX_PROBLEMS = 200

# column heading (case-insensitive, spaces or underscores) -> field
COLUMNS = {
    "serial": "serial", "serial number": "serial", "s/n": "serial",
    "issuer": "issuer", "serial issuer": "issuer", "maker": "issuer", "manufacturer": "issuer",
    "tag": "tag", "asset tag": "tag", "equipment tag": "tag",
    "tag issuer": "tag_issuer", "tag owner": "tag_issuer",
    "other": "other", "other code": "other",
    "name": "name", "description": "name",
    "model": "model", "part": "model", "part number": "model",
    "class": "class",
    "project": "project",
    "customer": "customer",
    "site": "site",
    "location": "location",
    "status": "status",
    "standard": "standard", "build standard": "standard",
    "documents": "documents", "as built": "documents",
    "notes": "notes",
    **{m: m for m in MILESTONES},
}
FIELDWORX_COLUMNS = {"tag", "serial", "name", "part", "project", "location", "built", "as built", "notes"}


def _heading(h: str) -> str:
    return " ".join((h or "").replace("_", " ").split()).casefold()


def _cell(v: str | None) -> str:
    v = (v or "").strip()
    if len(v) > 1 and v[0] == "'" and v[1] in "=+-@\t\r":       # RevisionWorx's spreadsheet formula guard
        return v[1:]
    return v


def _word(v: str | None) -> str:
    """``In service`` and ``in-service`` are the same status."""
    return "-".join((v or "").casefold().split())


def looks_like_fieldworx_register(headings: list[str]) -> bool:
    got = {_heading(h) for h in headings if h and h.strip()}
    return bool(got) and got <= FIELDWORX_COLUMNS and ("tag" in got or "serial" in got)


def plan_import(svc: UnitService, text: str, *, fieldworx_ids: bool | None = None, status: str = "in-service",
                cls: str = "product") -> list[dict]:
    """The units a register would create, as ``register_many`` specs. Raises :class:`ImportProblems` listing every problem
    (row, column, message); nothing is written either way. ``fieldworx_ids`` defaults to yes for a FieldWorx asset register."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportProblems([(0, "", f"the file is larger than {MAX_BYTES // (1024 * 1024)} MiB")])
    if "\x00" in text:
        raise ImportProblems([(0, "", "the file contains NUL bytes; is it really CSV text?")])
    try:
        reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""), strict=True)
        rows = list(reader)
    except csv.Error as exc:
        raise ImportProblems([(0, "", f"the file isn't valid CSV: {exc}")]) from None
    if not rows:
        raise ImportProblems([(0, "", "the file is empty")])
    header = rows[0]
    problems: list[tuple[int, str, str]] = []
    fields: list[str] = []
    for h in header:
        f = COLUMNS.get(_heading(h))
        if f is None:
            problems.append((1, h, "unknown column (see docs/UNITS.md for the columns a register can have)"))
        elif f in fields:
            problems.append((1, h, "column given twice"))
        fields.append(f or "")
    if not {"serial", "tag", "other"} & set(fields):
        problems.append((1, "", "a register needs a serial, tag or other column"))
    if problems:
        raise ImportProblems(problems)
    if len(rows) - 1 > MAX_ROWS:
        raise ImportProblems([(0, "", f"more than {MAX_ROWS} rows; split the file")])
    if fieldworx_ids is None:
        fieldworx_ids = looks_like_fieldworx_register(header)

    specs: list[dict] = []
    seen: dict[str, int] = {}
    doc_cache: dict[str, tuple[str, str]] = {}

    def note(row: int, col: str, msg: str) -> None:
        if len(problems) < MAX_PROBLEMS:
            problems.append((row, col, msg))

    def claimed(scheme: str, issuer: str, value: str, row: int, col: str) -> None:
        kid = key_id(scheme, issuer, value)
        if kid in seen:
            note(row, col, f"{value} is also in row {seen[kid]}")
            return
        seen[kid] = row
        rec = svc.ws.get(KIND["key"], kid)
        if rec is not None:
            note(row, col, f"{value} already belongs to {svc.describe(load_key(rec)['owner'])}")

    for number, raw in enumerate(rows[1:], start=2):
        if len(raw) != len(fields):
            if any(c.strip() for c in raw):
                note(number, "", f"{len(raw)} cells where the heading has {len(fields)}")
            continue
        row = {f: _cell(v) for f, v in zip(fields, raw) if f}
        if not any(row.values()):
            continue
        idents = []
        for scheme, value_field, issuer_field in (("serial", "serial", "issuer"), ("tag", "tag", "tag_issuer"),
                                                  ("other", "other", "")):
            value = row.get(value_field, "")
            if value:
                issuer = row.get(issuer_field, "") if issuer_field else ""
                idents.append({"scheme": scheme, "value": value, "issuer": issuer})
                claimed(scheme, issuer, value, number, value_field)
        if not idents:
            note(number, "", "a unit needs a serial, a tag or another code")
            continue
        if fieldworx_ids:
            alias = fieldworx_asset_id(svc.ws.id, row.get("tag") or row.get("serial") or "")
            idents.append({"scheme": "fieldworx", "value": alias, "issuer": ""})
            claimed("fieldworx", "", alias, number, "tag" if row.get("tag") else "serial")
        spec: dict = {"identifiers": idents, "class": _word(row.get("class")) or cls, "status": _word(row.get("status")) or status,
                      "name": row.get("name", ""), "model": {"part": "", "number": row.get("model", "")},
                      "customer": row.get("customer", ""), "site": row.get("site", ""),
                      "location": row.get("location", ""), "notes": row.get("notes", "")}
        try:
            spec["project"] = svc.project_id(row["project"]) if row.get("project") else ""
        except UnitError as exc:
            note(number, "project", str(exc))
        if row.get("standard"):
            try:
                spec["standard"] = svc.resolve_standard(row["standard"])["id"]
            except UnitError as exc:
                note(number, "standard", str(exc))
        docs: dict[str, str] = {}
        for ref in [r.strip() for r in row.get("documents", "").split(";") if r.strip()]:
            try:
                if ref.casefold() not in doc_cache:
                    doc_cache[ref.casefold()] = svc.doc_rev(ref)
                did, rid = doc_cache[ref.casefold()]
                if did in docs and docs[did] != rid:
                    note(number, "documents", f"names two revisions of one document ({ref})")
                docs[did] = rid
            except (UnitError, ValueError) as exc:
                note(number, "documents", str(exc))
        spec["documents"] = docs
        spec["milestones"] = {m: row[m] for m in MILESTONES if row.get(m)}
        try:
            svc._spec(spec, f"row {number}")          # every value checked now, so the report is complete
        except NotFound as exc:
            note(number, "", str(exc))
        except ValidationError as exc:
            note(number, "", str(exc))
        specs.append(spec)
    if problems:
        raise ImportProblems(problems)
    if not specs:
        raise ImportProblems([(0, "", "the file has no units in it")])
    return specs


def import_register(svc: UnitService, text: str, *, txid: str | None = None, fieldworx_ids: bool | None = None,
                    status: str = "in-service", reason: str = "") -> list[dict]:
    """Create every unit in a register in one journal entry, with basis ``legacy``. All or nothing."""
    specs = plan_import(svc, text, fieldworx_ids=fieldworx_ids, status=status)
    return svc.register_many(specs, txid=txid, basis="legacy", reason=reason or "imported from a register",
                             op="unit.import")


def units_to_csv(svc: UnitService) -> str:
    """The register as a spreadsheet for people and other programs: one row per unit, its current serial and tag, and its
    permanent ``id``. (A report: ``plan_import`` registers new units and refuses the ``id`` column.)"""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    cols = ["serial", "issuer", "tag", "tag_issuer", "name", "model", "class", "status", "project", "customer", "site",
            "location", "standard", "built", "notes", "id"]
    w.writerow(cols)
    projects = svc.ws.list("project")
    standards = {s["id"]: s["number"] for s in svc.standards()}

    def guard(v: str) -> str:
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    for u in svc.units():
        def first(scheme: str) -> dict:
            return next((i for i in u["identifiers"] if i["scheme"] == scheme and i["active"]), {"value": "", "issuer": ""})
        s, t = first("serial"), first("tag")
        proj = projects.get(u["project"], {})
        w.writerow([guard(x) for x in (
            s["value"], s["issuer"], t["value"], t["issuer"], u["name"], u["model"]["number"], u["class"], u["status"],
            str(proj.get("number") or proj.get("name") or ""), u["customer"], u["site"], u["location"],
            standards.get((u["standard"] or {}).get("id", ""), ""), u["milestones"].get("built", {}).get("date", ""),
            u["notes"], u["id"])])
    return out.getvalue()


__all__ = ["COLUMNS", "import_register", "looks_like_fieldworx_register", "norm", "plan_import", "units_to_csv"]
