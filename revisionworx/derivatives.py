# SPDX-License-Identifier: GPL-3.0-or-later
"""PDF derivatives: a PDF tied to the exact native revision it prints (ADR-026).

A derivative's content is stored like revision content (by SHA-256, never changed). Its record names one revision and that
revision's SHA-256 when it was attached; its id comes from the revision's, so a revision has one current PDF, and a newer
revision without one has none (the older one is never shown in its place). Every write is one journal entry through CORE_API
section 8, so it is fenced, recovered after a crash and signed in a team workspace.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import documents as D
from .content import ContentStore
from .workspace import RecordCheck, RecordPut, Workspace, WorkspaceError, now_iso, register_kind

KIND = "derivative"
register_kind(KIND)

FORMATS = {"pdf": "application/pdf"}
FIELDS = {"schema", "id", "revision", "document", "format", "sha256", "size", "name", "tool", "source_sha256", "created",
          "created_by", "replaced"}
OLD_FIELDS = {"sha256", "size", "name", "tool", "created", "created_by", "reason"}
TOOL_LIMIT, NAME_LIMIT, REASON_LIMIT = 200, 255, 2000


class DerivativeError(WorkspaceError):
    """A derivative was refused; nothing was written."""


class MalformedDerivative(DerivativeError):
    """A derivative record doesn't have the documented shape; it is never used partially."""


def derivative_id(revision_id: str, fmt: str = "pdf") -> str:
    return "drv_" + hashlib.sha256(f"revisionworx/derivative/v1\0{revision_id}\0{fmt}".encode("ascii")).hexdigest()[:32]


def _text(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str):
        raise DerivativeError(f"{what} must be text")
    value = value.strip()
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise DerivativeError(f"{what} must be one line of at most {limit} characters")
    return value


def load(record: Any) -> dict:
    """A derivative record, refused unless it has exactly the documented shape."""
    if not isinstance(record, dict) or set(record) - {"_rev"} != FIELDS or record.get("schema") != 1:
        raise MalformedDerivative("a derivative record doesn't match schema 1; update RevisionWorx or run "
                                  "'revisionworx doc verify'")
    r = {k: v for k, v in record.items() if k != "_rev"}
    ok = (r["format"] in FORMATS and r["id"] == derivative_id(str(r["revision"]), r["format"])
          and all(isinstance(r[k], str) for k in ("revision", "document", "sha256", "name", "tool", "source_sha256",
                                                  "created"))
          and isinstance(r["size"], int) and not isinstance(r["size"], bool) and r["size"] > 0
          and len(r["sha256"]) == 64 and isinstance(r["created_by"], dict) and isinstance(r["replaced"], list)
          and all(isinstance(o, dict) and set(o) == OLD_FIELDS for o in r["replaced"]))
    if not ok:
        raise MalformedDerivative(f"derivative {r.get('id')!r} has the wrong shape")
    return r


def get(ws: Workspace, revision_id: str, fmt: str = "pdf") -> dict | None:
    """The current derivative of a revision, or None."""
    rec = ws.get(KIND, derivative_id(revision_id, fmt))
    return None if rec is None else {**load(rec), "_rev": rec.get("_rev", 0)}


def of_document(ws: Workspace, doc_id: str, fmt: str = "pdf") -> dict[str, dict]:
    """``{revision id: derivative}`` for one document's revisions."""
    out = {}
    for rev in D.history(ws, doc_id):
        d = get(ws, rev["id"], fmt)
        if d is not None:
            out[rev["id"]] = d
    return out


def _is_pdf(path: Path) -> bool:
    """A PDF has its ``%PDF-`` header within its first 1024 bytes (ISO 32000-1, 7.5.2)."""
    with open(path, "rb") as fh:
        return b"%PDF-" in fh.read(1024)


def native_is_pdf(ws: Workspace, rev: dict) -> bool:
    doc = ws.get("document", rev["document_id"]) or {}
    name = rev.get("source_rel_path") or doc.get("rel_path") or ""
    return name.casefold().endswith(".pdf")


def attach(ws: Workspace, revision_id: str, path: Path, *, tool: str = "", replace: bool = False, reason: str = "") -> dict:
    """Store ``path`` as the PDF of one revision, in one journal entry. ``replace`` (with a reason) puts a corrected PDF in
    place of the current one, which stays listed under ``replaced``."""
    rev = ws.get("revision", revision_id)
    if rev is None:
        raise DerivativeError(f"no revision {revision_id}")
    tool = _text(tool, "tool", TOOL_LIMIT)
    reason = _text(reason, "reason", REASON_LIMIT)
    path = Path(path)
    if not path.is_file():
        raise DerivativeError(f"{path} isn't a file")
    if native_is_pdf(ws, rev):
        raise DerivativeError("that revision is a PDF already; it needs no PDF derivative")
    if not _is_pdf(path):
        raise DerivativeError(f"{path.name} isn't a PDF (it doesn't start with %PDF-)")
    did = derivative_id(revision_id)
    current = get(ws, revision_id)
    if current is not None and not replace:
        raise DerivativeError("this revision already has a PDF; replace it with --replace and a reason")
    if replace and current is None:
        raise DerivativeError("this revision has no PDF to replace")
    if replace and not reason:
        raise DerivativeError("replacing a PDF needs a reason")
    stored = ContentStore(ws).store_file(path)
    if current is not None and stored.sha256 == current["sha256"]:
        raise DerivativeError("that is the PDF this revision already has")
    actor, time = ws.actor(), now_iso()
    rec = {"schema": 1, "id": did, "revision": revision_id, "document": rev["document_id"], "format": "pdf",
           "sha256": stored.sha256, "size": stored.size, "name": _text(path.name, "name", NAME_LIMIT), "tool": tool,
           "source_sha256": rev["sha256"], "created": time, "created_by": actor, "replaced": []}
    if current is not None:
        rec["replaced"] = current["replaced"] + [{"sha256": current["sha256"], "size": current["size"], "name": current["name"],
                                                  "tool": current["tool"], "created": current["created"],
                                                  "created_by": current["created_by"], "reason": reason}]
    load(rec)
    ops = [RecordPut(KIND, did, rec, expect_rev=current["_rev"] if current else 0),
           RecordCheck("revision", revision_id, int(rev.get("_rev", 0)))]
    ws.commit("derivative.replace" if current else "derivative.attach", ops)
    return get(ws, revision_id)


def find_revision(ws: Workspace, ref: str) -> dict:
    """A revision by ``DOC@REV`` (document number or id, revision label) or by its id."""
    ref = (ref or "").strip()
    if ref.startswith("rev_"):
        rev = ws.get("revision", ref)
        if rev is None:
            raise DerivativeError(f"no revision {ref}")
        return rev
    doc_ref, sep, label = ref.rpartition("@")
    if not sep or not doc_ref or not label:
        raise DerivativeError(f"give DOC@REV, e.g. FRM-200@B, not {ref!r}")
    doc = ws.get("document", doc_ref)
    if doc is None:
        hits = [d for d in ws.list("document").values() if (d.get("number") or "").strip().casefold() == doc_ref.casefold()]
        if len(hits) > 1:
            raise DerivativeError(f"document number {doc_ref!r} is used in several projects; give the document id")
        doc = hits[0] if hits else None
    if doc is None:
        raise DerivativeError(f"no document {doc_ref!r}")
    revs = [r for r in D.history(ws, doc["id"]) if r["label"].casefold() == label.casefold()]
    if not revs:
        raise DerivativeError(f"{doc.get('number') or doc['id']} has no revision {label!r}")
    return revs[-1]


def export(ws: Workspace, revision_id: str, dest: Path, *, overwrite: bool = False) -> Path:
    """Copy a revision's PDF to ``dest``, verified."""
    d = get(ws, revision_id)
    if d is None:
        raise DerivativeError("that revision has no PDF")
    return ContentStore(ws).export(d["sha256"], Path(dest), overwrite=overwrite)


def missing(ws: Workspace, project_id: str | None = None) -> list[dict]:
    """Released revisions of documents that aren't PDFs and have no PDF: the missing-export warning."""
    released = D.get_lifecycle(ws)["released"]
    out = []
    for doc in D.list_documents(ws, project_id):
        hist = D.history(ws, doc["id"])
        rel = next((r for r in reversed(hist) if r["state"] == released), None)
        if rel is None or native_is_pdf(ws, rel) or get(ws, rel["id"]) is not None:
            continue
        out.append({"document": doc["id"], "number": doc.get("number", ""), "title": doc.get("title", ""),
                    "revision": rel["id"], "label": rel["label"]})
    return sorted(out, key=lambda m: (m["number"].casefold(), m["document"]))


def referenced(ws: Workspace) -> set[str]:
    """Every content object a derivative names, current or replaced (content clean-up keeps them)."""
    out: set[str] = set()
    for rec in ws.list(KIND).values():
        try:
            d = load(rec)
        except MalformedDerivative:
            out.update(o.get("sha256", "") for o in rec.get("replaced", []) if isinstance(o, dict))
            out.add(str(rec.get("sha256", "")))
            continue
        out.add(d["sha256"])
        out.update(o["sha256"] for o in d["replaced"])
    return {s for s in out if s}


def verify(ws: Workspace) -> list[dict]:
    """Problems with derivatives: malformed records, a revision that doesn't exist or whose content isn't the one the PDF was
    attached to, missing or damaged content. Nothing is repaired."""
    ws.check_from_journal()
    store, out = ContentStore(ws), []
    for did, rec in ws.list(KIND).items():
        try:
            d = load(rec)
        except MalformedDerivative as exc:
            out.append({"id": did, "problem": "malformed", "detail": str(exc)})
            continue
        rev = ws.get("revision", d["revision"])
        if rev is None or rev.get("document_id") != d["document"]:
            out.append({"id": did, "problem": "revision missing", "detail": d["revision"]})
        elif rev.get("sha256") != d["source_sha256"]:
            out.append({"id": did, "problem": "attached to other content", "detail": d["revision"]})
        for sha in [d["sha256"]] + [o["sha256"] for o in d["replaced"]]:
            if not store.verify(sha):
                out.append({"id": did, "problem": "content missing or damaged", "detail": sha})
    return out


__all__ = ["DerivativeError", "FORMATS", "KIND", "MalformedDerivative", "attach", "derivative_id", "export", "find_revision",
           "get", "load", "missing", "native_is_pdf", "of_document", "referenced", "verify"]
