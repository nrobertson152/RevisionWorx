# SPDX-License-Identifier: GPL-3.0-or-later
"""Handover packs (docs/UNITS.md section 13): one unit's record as a folder anyone can open, with no RevisionWorx and no
network. A page to read and print, the PDFs of the revisions that apply to the unit, the record as JSON, and the SHA-256 of
every file in the format ``sha256sum -c`` reads.

Made from what a read-only handle sees; nothing is written to the workspace. Until the Director decides who vouches for a
pack (gate 4), a pack proves that it is whole, not who made it, and its first page says so.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import atomicio, derivatives
from ..content import ContentStore
from ..workspace import WorkspaceCorruptError, now_iso
from . import query
from .model import UnitError

PACK_FORMAT = "revisionworx-unit-pack"
PACK_VERSION = 1
SUMS = "SHA256SUMS"
RECORD = "unit.json"
PAGE = "index.html"
PDF_DIR = "documents"
_SUM_LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


class PackError(UnitError):
    pass


# ---------------------------------------------------------------------------------------------------------- making
def make(svc, ref: str, dest: Path, *, on: str = "") -> dict:
    """Write the handover pack of unit ``ref`` to the new folder ``dest``, as the unit stands now or ``on`` a date. All of it
    or nothing: it is put together beside ``dest`` and moved into place at the end. Refused if ``dest`` exists and isn't
    an empty folder."""
    unit = svc.resolve(ref)
    detail = _public(query.unit_detail(svc, unit, on=on))
    dest = Path(dest)
    if dest.exists():
        if not dest.is_dir() or any(dest.iterdir()):
            raise PackError(f"{dest} already exists and isn't an empty folder; choose a new one")
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.name}.{secrets.token_hex(6)}.tmp"
    work.mkdir()
    try:
        (work / PDF_DIR).mkdir()
        pdfs = _pdfs(svc.ws, detail["documents"], work / PDF_DIR)
        record = {"format": PACK_FORMAT, "version": PACK_VERSION, "made": now_iso(), "signed": False,
                  "workspace": {"id": svc.ws.id, "name": svc.ws.info.get("name", "")}, "as_of": on or None,
                  "unit": detail, "pdfs": pdfs}
        atomicio.atomic_write_bytes(work / RECORD, (json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False)
                                                    + "\n").encode("utf-8"))
        atomicio.atomic_write_bytes(work / PAGE, render(record).encode("utf-8"))
        if not any((work / PDF_DIR).iterdir()):
            (work / PDF_DIR).rmdir()                 # no PDFs: no empty folder to wonder about
        files = _listing(work)
        atomicio.atomic_write_bytes(work / SUMS, "".join(f"{files[name]}  {name}\n" for name in sorted(files))
                                    .encode("utf-8"))
        if dest.exists():
            dest.rmdir()                             # empty, checked above
        os.replace(work, dest)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    included = [p for p in pdfs if p["file"]]
    return {"path": str(dest), "unit": detail["label"], "files": len(files) + 1, "pdfs": len(included),
            "missing": [p for p in pdfs if not p["file"]], "signed": False}


def _public(detail: dict) -> dict:
    """The record as it leaves the company: who did what stays (a quality record says who verified), the computer's name
    doesn't."""
    def person(actor: Any) -> Any:
        return {k: v for k, v in actor.items() if k != "host"} if isinstance(actor, dict) else actor
    out = dict(detail)
    out["history"] = [{**e, "actor": person(e.get("actor"))} for e in detail.get("history", [])]
    return out


def _pdfs(ws, rows: list[dict], folder: Path) -> list[dict]:
    """The PDF of each revision that applies: its own PDF (ADR-026), or the revision itself when it is a PDF. Never another
    revision's. One that can't be included is listed with why, and the page says so."""
    out, used = [], set()
    store = ContentStore(ws)
    for row in rows:
        rev = row.get("revision")
        if not row.get("applies") or rev is None:
            continue
        item = {"document": row["document"]["number"] or row["document"]["id"], "title": row["document"]["title"],
                "revision": rev["label"], "revision_id": rev["id"], "file": None, "sha256": None, "missing": ""}
        derivative = derivatives.get(ws, rev["id"])
        rec = ws.get("revision", rev["id"]) or {}
        sha = derivative["sha256"] if derivative else (rec.get("sha256") if derivatives.native_is_pdf(ws, rec) else None)
        if not sha:
            item["missing"] = "no PDF is attached to this revision"
        else:
            name = _file_name(f"{item['document']} rev {item['revision']}", used)
            try:
                store.export(sha, folder / name)
            except (OSError, WorkspaceCorruptError) as exc:
                item["missing"] = f"its PDF couldn't be read from the workspace ({exc}); run 'revisionworx doc verify'"
            else:
                item["file"], item["sha256"] = f"{PDF_DIR}/{name}", sha
        out.append(item)
    return out


def _file_name(stem: str, used: set) -> str:
    """A file name every system accepts, unique in the pack."""
    stem = unicodedata.normalize("NFC", stem)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "document"
    stem = stem[:120]
    name, n = f"{stem}.pdf", 1
    while name.casefold() in used:
        n += 1
        name = f"{stem} ({n}).pdf"
    used.add(name.casefold())
    return name


def _listing(folder: Path) -> dict[str, str]:
    """``{relative path: SHA-256}`` of every file in the pack, but the list itself."""
    out = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            rel = path.relative_to(folder).as_posix()
            if rel != SUMS:
                out[rel] = _sha256(path)
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------------------- checking
def check(folder: Path) -> dict:
    """Is the pack whole? Every file listed in SHA256SUMS is there with that SHA-256, nothing else is, and the record is one
    this version can read. It can't say who made the pack: packs aren't signed yet."""
    folder = Path(folder)
    problems: list[str] = []
    try:
        lines = (folder / SUMS).read_text("utf-8").splitlines()
    except FileNotFoundError:
        return {"ok": False, "problems": [f"there's no {SUMS} here: this isn't a handover pack, or it's incomplete"],
                "unit": None, "files": 0, "signed": False}
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "problems": [f"{SUMS} can't be read: {exc}"], "unit": None, "files": 0, "signed": False}
    listed: dict[str, str] = {}
    for n, line in enumerate(lines, 1):
        m = _SUM_LINE.match(line)
        rel = m.group(2) if m else ""
        if not m or rel.startswith(("/", "\\")) or ".." in rel.replace("\\", "/").split("/") or rel in listed:
            problems.append(f"{SUMS} line {n} isn't a checksum line this pack would have")
            continue
        listed[rel] = m.group(1)
    present = _listing(folder)
    for rel, want in listed.items():
        got = present.get(rel)
        if got is None:
            problems.append(f"{rel} is missing")
        elif got != want:
            problems.append(f"{rel} has been changed since the pack was made")
    for rel in sorted(set(present) - set(listed)):
        problems.append(f"{rel} isn't part of the pack")
    label = None
    try:
        record = json.loads((folder / RECORD).read_text("utf-8"))
        if not isinstance(record, dict) or record.get("format") != PACK_FORMAT:
            problems.append(f"{RECORD} isn't a handover pack's record")
        elif not isinstance(record.get("version"), int) or record["version"] > PACK_VERSION:
            problems.append(f"{RECORD} was made by a newer RevisionWorx (pack version {record.get('version')!r}); "
                            "update RevisionWorx to check it")
        else:
            label = (record.get("unit") or {}).get("label")
    except (OSError, ValueError, RecursionError) as exc:
        problems.append(f"{RECORD} can't be read: {exc}")
    return {"ok": not problems, "problems": problems, "unit": label, "files": len(listed) + 1, "signed": False}


# ---------------------------------------------------------------------------------------------------------- the page
_FLAGS = {"not-released": "not released", "obsolete": "obsolete"}
_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1d1d1f;margin:2rem auto;max-width:60rem;
padding:0 1rem;line-height:1.45}
h1{font-size:1.6rem;margin-bottom:.2rem}h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #d0d0d4;padding-bottom:.2rem}
table{border-collapse:collapse;width:100%;font-size:.92rem}th,td{text-align:left;vertical-align:top;padding:.3rem .5rem;
border-bottom:1px solid #e4e4e8}th{font-weight:600}.muted{color:#5f5f66}.note{border:1px solid #d0d0d4;padding:.6rem .8rem;
border-radius:6px;background:#f6f6f8}.flag{font-weight:600}@media print{a{color:inherit;text-decoration:none}}
"""


def render(record: dict) -> str:
    """The pack's first page: plain HTML and CSS, no scripts, nothing fetched from anywhere."""
    u = record["unit"]
    e = html.escape
    out = ['<!DOCTYPE html>', '<html lang="en"><head><meta charset="utf-8">',
           f'<title>{e(u["label"])}: handover pack</title><style>{_CSS}</style></head><body>']
    model = " ".join(x for x in (u["model"].get("number", ""), u["name"]) if x)
    out.append(f'<h1>{e(u["label"])}</h1><p class="muted">{e(model)}{" · " if model else ""}{e(u["class"])} · '
               f'status: {e(u["status"])}</p>')
    as_of = f' It shows the unit as known on {e(record["as_of"])}.' if record.get("as_of") else ""
    out.append(f'<p class="note">Handover pack made on {e(record["made"][:10])} from the RevisionWorx workspace '
               f'{e(record["workspace"]["name"])}.{as_of} <b>{SUMS}</b> lists the SHA-256 of every file, so you can '
               f'check that nothing is missing or changed (<code>sha256sum -c {SUMS}</code>, or '
               f'<code>revisionworx unit check-pack FOLDER</code>). The pack is not signed: it shows that it is whole, '
               f'not who made it.</p>')

    out.append("<h2>Identity</h2><table><tr><th>Kind</th><th>Value</th><th>Issued by</th><th></th></tr>")
    for i in u["identifiers"]:
        out.append(f'<tr><td>{e(i["scheme"])}</td><td>{e(i["value"])}</td><td>{e(i.get("issuer", ""))}</td>'
                   f'<td class="muted">{"" if i.get("active", True) else "retired"}</td></tr>')
    out.append(f'<tr><td>record id</td><td><code>{e(u["id"])}</code></td><td>RevisionWorx</td><td></td></tr></table>')

    where = [("Customer", u["customer"]), ("Site", u["site"]), ("Location", u["location"]),
             ("Project", f'{u["project"]["number"]} {u["project"]["name"]}'.strip() if u.get("project") else ""),
             ("Fitted in", f'{u["parent"]["label"]} at {u["parent"]["position"]}' if u.get("parent") else "")]
    rows = [(k, v) for k, v in where if v]
    if rows:
        out.append("<h2>Where it is</h2><table>")
        out += [f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows]
        out.append("</table>")
    basis = {h["n"]: h["basis"] for h in u.get("history", [])}
    if u["milestones"]:
        out.append("<h2>Milestones</h2><table><tr><th>Milestone</th><th>Date</th><th>Basis</th></tr>")
        for name, m in u["milestones"].items():
            out.append(f'<tr><td>{e(name)}</td><td>{e(m.get("date") or "")}</td>'
                       f'<td>{e(basis.get(m.get("event"), ""))}</td></tr>')
        out.append("</table>")

    pdfs = {p["revision_id"]: p for p in record["pdfs"]}
    out.append("<h2>Documents that apply to this unit</h2>")
    if u.get("standard"):
        out.append(f'<p>Built to standard {e(u["standard"]["number"])} ({e(u["standard"]["title"])}), with the '
                   "differences below.</p>")
    if not u["documents"]:
        out.append("<p>No documents are recorded for this unit.</p>")
    else:
        out.append("<table><tr><th>Document</th><th>Title</th><th>Revision</th><th>PDF</th><th>Notes</th></tr>")
        for r in u["documents"]:
            rev = r.get("revision")
            if not r.get("applies") or rev is None:
                out.append(f'<tr><td>{e(r["document"]["number"])}</td><td>{e(r["document"]["title"])}</td>'
                           '<td colspan="3" class="muted">doesn\'t apply to this unit</td></tr>')
                continue
            p = pdfs.get(rev["id"], {})
            pdf = (f'<a href="{quote(p["file"])}">open</a>' if p.get("file")
                   else f'<span class="flag">not included</span>: {e(p.get("missing", ""))}')
            notes = [_FLAGS[f] for f in r.get("flags", []) if f in _FLAGS]
            if "newer-released" in r.get("flags", []) and r.get("released"):
                notes.append(f'revision {r["released"]["label"]} is released; this unit has {rev["label"]}')
            out.append(f'<tr><td>{e(r["document"]["number"])}</td><td>{e(r["document"]["title"])}</td>'
                       f'<td>{e(rev["label"])}</td><td>{pdf}</td><td class="flag">{e("; ".join(notes))}</td></tr>')
        out.append("</table>")

    if u["components"]:
        out.append("<h2>Fitted</h2><table><tr><th>Position</th><th>Part</th><th>Serial</th><th>Lot</th><th>Since</th></tr>")
        for c in u["components"]:
            out.append(f'<tr><td>{e(c.get("position", ""))}</td><td>{e(c.get("number") or c.get("label") or "")}</td>'
                       f'<td>{e(c.get("serial") or "")}</td><td>{e(c.get("lot") or "")}</td>'
                       f'<td>{e(c.get("since") or "")}</td></tr>')
        out.append("</table>")
    if u.get("deviations"):
        out.append("<h2>Approved departures from the documents</h2><table><tr><th>Number</th><th>Kind</th><th>What</th>"
                   "<th>Where it stands</th></tr>")
        for d in u["deviations"]:
            until = f' until {d["until"]}' if d.get("temporary") and d.get("until") else ""
            out.append(f'<tr><td>{e(d["number"])}</td><td>{e(d["kind"])}</td><td>{e(d["title"])}<br>'
                       f'<span class="muted">{e(d["departure"])}</span></td><td>{e(d["state"])}{e(until)}'
                       f'{" (past its end date)" if d.get("overdue") else ""}</td></tr>')
        out.append("</table>")
    if u.get("changes"):
        out.append("<h2>Engineering changes that reach this unit</h2><table><tr><th>Change</th><th>Title</th>"
                   "<th>Kind</th><th>Where it stands</th><th>Since</th></tr>")
        for c in u["changes"]:
            out.append(f'<tr><td>{e(c["number"])}</td><td>{e(c["title"])}</td><td>{e(c["kind"])}'
                       f'{" (required)" if c.get("required") else ""}</td><td>{e(c["state"])}</td>'
                       f'<td>{e(c.get("since", ""))}</td></tr>')
        out.append("</table>")
    if u.get("history"):
        out.append("<h2>History</h2><table><tr><th>#</th><th>When</th><th>What</th><th>Basis</th><th>By</th></tr>")
        for h in u["history"]:
            who = h.get("actor") or {}
            by = who.get("name") or who.get("account") or who.get("user") or "" if isinstance(who, dict) else ""
            what = e(h["summary"]) + (' <span class="flag">(recorded in error)</span>' if h.get("voided") else "")
            out.append(f'<tr><td>{h["n"]}</td><td>{e(h.get("effective") or h["time"][:10])}</td><td>{what}</td>'
                       f'<td>{e(h["basis"])}</td><td>{e(by)}</td></tr>')
        out.append("</table>")
    if u["notes"]:
        out.append(f'<h2>Notes</h2><p>{e(u["notes"])}</p>')
    out.append("</body></html>\n")
    return "\n".join(out)
