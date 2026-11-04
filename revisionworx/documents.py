# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2: document registry, immutable revisions, lifecycle and checkout/check-in.

Design: docs/ARCHITECTURE.md ADR-010..013. Working files stay where they are in the project folder;
revisions are immutable snapshots in the content store. Every state change is one journal entry
whose expectations (``expect_rev``) make concurrent or stale operations fail as conflicts instead
of overwriting each other.
"""
from __future__ import annotations

import csv
import io
import os
import secrets
import shutil
import stat
import time
import uuid
from pathlib import Path

from . import atomicio, paths, projects, revlabels
from . import lifecycle as lc_mod
from .content import ContentStore, hash_file, is_content_name
from .filetypes import classify
from .lifecycle import LifecycleError
from .workspace import (CommittedNotApplied, ConflictError, LockLostError, RecordCheck, RecordDelete, RecordPut, TxidReuseError,
                        Workspace, WorkspaceCorruptError, WorkspaceError, current_actor, new_id, now_iso, register_kind, strip_rev)

for _kind in ("doctype", "document", "revision", "checkout", "lifecycle"):
    register_kind(_kind)

IMMUTABLE_REVISION_FIELDS = ("id", "document_id", "number", "label", "sha256", "size", "source_mtime_ns",
                             "source_rel_path", "description", "author", "created", "previous_rev_id",
                             "restored_from")

DEFAULT_DOCTYPES = {
    "drawing": {"name": "Engineering drawing", "extensions": [".idw", ".slddrw", ".dwg", ".dxf"],
                "revision_scheme": "alpha", "category": "drawing"},
    "cad-part": {"name": "CAD part", "extensions": [".ipt", ".sldprt", ".step", ".stp", ".iges", ".igs"],
                 "revision_scheme": "alpha", "category": "cad"},
    "cad-assembly": {"name": "CAD assembly", "extensions": [".iam", ".sldasm", ".ipn"],
                     "revision_scheme": "alpha", "category": "cad"},
    "schematic": {"name": "Schematic", "extensions": [".schematix", ".osch"],
                  "revision_scheme": "alpha", "category": "schematic"},
    "pdf": {"name": "PDF document", "extensions": [".pdf"], "revision_scheme": "alpha", "category": "document"},
    "manufacturing": {"name": "Manufacturing document", "extensions": [],
                      "revision_scheme": "numeric2", "category": "manufacturing"},
    "general": {"name": "General document", "extensions": [], "revision_scheme": "numeric", "category": "document"},
}


class DocumentError(WorkspaceError):
    pass


class CheckoutConflict(DocumentError):
    def __init__(self, checkout: dict):
        self.checkout = checkout
        o = checkout.get("owner", {})
        super().__init__(f"already checked out by {o.get('user')}@{o.get('host')} since {checkout.get('acquired')}")


class NotCheckedOut(DocumentError):
    pass


class TokenMismatch(DocumentError):
    pass


class NoChanges(DocumentError):
    pass


class MissingFileError(DocumentError):
    """The file is not there. Worth telling apart from other refusals: the user can put it back."""


class ImmutableRevisionError(DocumentError):
    pass


class StaleCheckout(DocumentError):
    """A revision was committed after this checkout was taken (e.g. after an admin release)."""


# ---------------------------------------------------------------------------- reading records
# A journal entry's digest says it was not altered by accident, not that its record has the right shape. Everything here
# reads through _get and _all, so a malformed record is one controlled refusal and not a KeyError deep in an operation.
_TEXT, _OPTIONAL_TEXT, _WHOLE, _OBJECT, _LIST = (str,), (str, type(None)), (int,), (dict,), (list,)
_RECORD_FIELDS = {
    "project": {"id": _TEXT, "name": _TEXT, "mode": _TEXT, "status": _TEXT, "root_id": _TEXT, "rel_path": _TEXT},
    "document": {"id": _TEXT, "project_id": _TEXT, "rel_path": _TEXT, "type_id": _TEXT, "number": _TEXT, "title": _TEXT,
                 "owner": _TEXT, "revision_scheme": _TEXT, "status": _TEXT, "latest_rev_id": _OPTIONAL_TEXT,
                 "released_rev_id": _OPTIONAL_TEXT, "rev_count": _WHOLE},
    "revision": {"id": _TEXT, "document_id": _TEXT, "number": _WHOLE, "label": _TEXT, "state": _TEXT, "sha256": _TEXT,
                 "size": _WHOLE, "source_mtime_ns": _WHOLE, "source_rel_path": _TEXT, "description": _TEXT, "author": _OBJECT,
                 "created": _TEXT, "previous_rev_id": _OPTIONAL_TEXT, "restored_from": _OPTIONAL_TEXT, "transitions": _LIST},
    "checkout": {"document_id": _TEXT, "token": _TEXT, "owner": _OBJECT, "acquired": _TEXT, "base_rev_id": _OPTIONAL_TEXT,
                 "restored_from": _OPTIONAL_TEXT},
}


def _record_problem(kind: str, record: dict) -> str | None:
    for field, types in _RECORD_FIELDS[kind].items():
        value = record.get(field, ...)
        if value is ...:
            return f"{field} is missing"
        if not isinstance(value, types) or isinstance(value, bool):
            return f"{field} has the wrong type"
    if kind == "document" and record["revision_scheme"] not in revlabels.SCHEMES:
        return "revision_scheme is not a known scheme"
    if kind == "revision":
        if not is_content_name(record["sha256"]):
            return "sha256 is not a SHA-256"
        for step in record["transitions"]:
            if not (isinstance(step, dict) and isinstance(step.get("from"), str) and isinstance(step.get("to"), str)
                    and isinstance(step.get("by"), dict)):
                return "transitions has a malformed step"
    return None


def _checked(kind: str, rid: str, record: dict) -> dict:
    problem = _record_problem(kind, record)
    if problem:
        raise WorkspaceCorruptError(f"{kind} {rid} is malformed: {problem}. Nothing was changed; run 'revisionworx doctor'")
    return record


def _get(ws: Workspace, kind: str, rid: str) -> dict | None:
    record = ws.get(kind, rid)
    return None if record is None else _checked(kind, rid, record)


def _all(ws: Workspace, kind: str) -> dict[str, dict]:
    return {rid: _checked(kind, rid, record) for rid, record in ws.list(kind).items()}


# ---------------------------------------------------------------------------- configuration
def list_doctypes(ws: Workspace) -> dict[str, dict]:
    out = {k: {"id": k, **v} for k, v in DEFAULT_DOCTYPES.items()}
    out.update({k: strip_rev(v) for k, v in ws.list("doctype").items()})
    return out


def save_doctype(ws: Workspace, dt: dict) -> str:
    tid = str(dt.get("id", ""))
    if not tid or not tid.replace("-", "").replace("_", "").isalnum() or tid != tid.lower() or len(tid) > 32:
        raise DocumentError("document type id must be lowercase letters, digits, '-' or '_'")
    if dt.get("revision_scheme", "alpha") not in revlabels.SCHEMES:
        raise DocumentError(f"revision_scheme must be one of {revlabels.SCHEMES}")
    exts = [e.lower() if e.startswith(".") else "." + e.lower() for e in dt.get("extensions", [])]
    rec = {"id": tid, "name": str(dt.get("name") or tid), "extensions": exts,
           "revision_scheme": dt.get("revision_scheme", "alpha"), "category": str(dt.get("category", "document"))}
    ws.commit("doctype.save", [RecordPut("doctype", tid, rec)])
    return tid


def suggest_type(ws: Workspace, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    for tid, dt in list_doctypes(ws).items():
        if ext in dt["extensions"]:
            return tid
    return "general"


def get_lifecycle(ws: Workspace) -> dict:
    stored = ws.get("lifecycle", "default")
    return lc_mod.validate_lifecycle(strip_rev(stored)) if stored else lc_mod.DEFAULT_LIFECYCLE


def save_lifecycle(ws: Workspace, lc: dict) -> None:
    valid = lc_mod.validate_lifecycle(lc)
    in_use = {r["state"] for r in _all(ws, "revision").values()}
    missing = in_use - {s["id"] for s in valid["states"]}
    if missing:
        raise LifecycleError(f"revisions are currently in states {sorted(missing)}, which the new lifecycle removes")
    ws.commit("lifecycle.save", [RecordPut("lifecycle", "default", {**valid, "id": "default"})])


# ---------------------------------------------------------------------------- helpers
def _managed_project(ws: Workspace, project_id: str) -> tuple[dict, Path]:
    project = _get(ws, "project", project_id)
    if project is None:
        raise DocumentError(f"unknown project {project_id}")
    if project["mode"] != "managed":
        raise DocumentError(f"project {project['name']!r} is {project['mode']}; documents can only be "
                            "registered in managed projects")
    if project["status"] != "active":
        raise DocumentError(f"project {project['name']!r} is archived")
    return project, projects.project_folder(ws, project)


def working_path(ws: Workspace, doc: dict) -> Path:
    project = _get(ws, "project", doc["project_id"])
    if project is None:
        raise DocumentError(f"document {doc['id']} belongs to a missing project")
    return paths.resolve_under_root(projects.project_folder(ws, project), doc["rel_path"])


def _require_doc(ws: Workspace, doc_id: str) -> dict:
    doc = _get(ws, "document", doc_id)
    if doc is None:
        raise DocumentError(f"unknown document {doc_id}")
    return doc


require_document = _require_doc      # public name for callers outside this module (Claude B's A5)


def _require_rev(ws: Workspace, rev_id: str) -> dict:
    rev = _get(ws, "revision", rev_id)
    if rev is None:
        raise DocumentError(f"unknown revision {rev_id}")
    return rev


def _regular_file(path: Path) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise MissingFileError(f"{path} does not exist") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise DocumentError(f"{path} is not a regular file (links are not followed)")


def _check_number(ws: Workspace, number: str, allow_duplicate: bool, exclude: str | None = None) -> None:
    if not number or allow_duplicate:
        return
    for d in _all(ws, "document").values():
        if d["id"] != exclude and d.get("number", "").casefold() == number.casefold():
            raise DocumentError(f"document number {number!r} is already used by {d['rel_path']!r}")


def revisions_of(ws: Workspace, doc_id: str) -> list[dict]:
    """A document's revisions, oldest first, by walking the ``previous_rev_id`` chain from its
    latest revision: O(revisions of this document), not O(all revisions in the workspace)."""
    doc = _get(ws, "document", doc_id)
    out, rid, seen = [], doc.get("latest_rev_id") if doc else None, set()
    while rid:
        if rid in seen:
            raise WorkspaceError(f"revision chain of document {doc_id} loops at {rid}")
        seen.add(rid)
        rev = _get(ws, "revision", rid)
        if rev is None or rev["document_id"] != doc_id:
            raise WorkspaceError(f"revision chain of document {doc_id} is broken at {rid}; run 'verify'")
        out.append(rev)
        rid = rev.get("previous_rev_id")
    out.reverse()
    if doc and len(out) != doc["rev_count"]:
        raise WorkspaceError(f"document {doc_id} records {doc['rev_count']} revisions but its chain has {len(out)}")
    if [r["number"] for r in out] != list(range(1, len(out) + 1)):
        raise WorkspaceError(f"revision chain of document {doc_id} is out of order; run 'verify'")
    return out


def _next_label(ws: Workspace, doc: dict, number: int, label: str | None) -> str:
    auto = revlabels.label_for(doc["revision_scheme"], number)
    chosen = revlabels.validate_label(label) if label else auto
    if chosen is None:
        raise DocumentError("this document uses manual revision labels; provide a label")
    taken = {r["label"].casefold() for r in revisions_of(ws, doc["id"])}
    if chosen.casefold() in taken:
        raise DocumentError(f"revision label {chosen!r} is already used on this document")
    return chosen


def _new_revision(doc: dict, number: int, label: str, stored, description: str, previous: str | None,
                  initial_state: str, restored_from: str | None = None) -> dict:
    rid = new_id("rev")
    return {"id": rid, "document_id": doc["id"], "number": number, "label": label, "state": initial_state,
            "sha256": stored.sha256, "size": stored.size, "source_mtime_ns": stored.source_mtime_ns,
            "source_rel_path": doc["rel_path"], "description": description, "author": current_actor(),
            "created": now_iso(), "previous_rev_id": previous, "restored_from": restored_from,
            "transitions": []}


# ---------------------------------------------------------------------------- registry
def register_document(ws: Workspace, project_id: str, file_path: Path | str, *, type_id: str | None = None,
                      number: str = "", title: str = "", description: str = "", owner: str = "",
                      revision_scheme: str | None = None, capture: bool = True,
                      revision_description: str = "Initial revision", label: str | None = None,
                      allow_duplicate_number: bool = False) -> tuple[dict, dict | None]:
    """Register a file that already exists in a managed project. The file is never moved or edited.

    With ``capture`` the file's present content becomes revision 1 in the same journal entry."""
    project, folder = _managed_project(ws, project_id)
    fp = Path(file_path)
    if not fp.is_absolute():
        fp = folder.joinpath(*paths.normalize_relative(str(file_path)).split("/"))
    if os.path.islink(fp):  # refuse before resolving, or the link's TARGET would be registered instead
        raise DocumentError(f"{fp} is a symbolic link, not a regular file (links are not followed)")
    rel = paths.relative_to_root(folder, fp)
    key = paths.case_key(rel)  # case-insensitive: such names collide on Windows/SMB clients
    for d in _all(ws, "document").values():
        if d["project_id"] == project_id and paths.case_key(d["rel_path"]) == key:
            raise DocumentError(f"{rel!r} is already registered in this project (as {d['rel_path']!r})")
    target = paths.resolve_under_root(folder, rel)
    _regular_file(target)
    doctypes = list_doctypes(ws)
    type_id = type_id or suggest_type(ws, rel)
    if type_id not in doctypes:
        raise DocumentError(f"unknown document type {type_id!r}")
    scheme = revision_scheme or doctypes[type_id]["revision_scheme"]
    if scheme not in revlabels.SCHEMES:
        raise DocumentError(f"revision scheme must be one of {revlabels.SCHEMES}")
    _check_number(ws, number.strip(), allow_duplicate_number)
    doc = {"id": new_id("doc"), "project_id": project_id, "rel_path": rel, "type_id": type_id,
           "number": number.strip(), "title": title.strip() or Path(rel).stem, "description": description,
           "owner": owner.strip(), "revision_scheme": scheme, "latest_rev_id": None, "released_rev_id": None,
           "rev_count": 0, "status": "active", "file_category": classify(Path(rel)).category,
           "created": now_iso(), "created_by": current_actor()}
    ops: list = []
    rev = None
    if capture:
        lc = get_lifecycle(ws)
        chosen = _next_label(ws, doc, 1, label)
        stored = ContentStore(ws).store_file(target)
        rev = _new_revision(doc, 1, chosen, stored, revision_description.strip() or "Initial revision",
                            None, lc["initial"])
        doc.update(latest_rev_id=rev["id"], rev_count=1)
        ops.append(RecordPut("revision", rev["id"], rev, expect_rev=0))
    ops.insert(0, RecordPut("document", doc["id"], doc, expect_rev=0))
    seq = ws.commit("document.register", ops)
    doc["_rev"] = seq
    if rev:
        rev["_rev"] = seq
    return doc, rev


def update_document(ws: Workspace, doc_id: str, expected_rev: int | None = None, **changes) -> dict:
    allowed = {"number", "title", "description", "owner", "type_id", "status"}
    bad = set(changes) - allowed
    if bad:
        raise DocumentError(f"cannot change {sorted(bad)} (paths and revisions are not editable)")
    doc = _require_doc(ws, doc_id)
    if "type_id" in changes and changes["type_id"] not in list_doctypes(ws):
        raise DocumentError(f"unknown document type {changes['type_id']!r}")
    if "status" in changes and changes["status"] not in ("active", "archived"):
        raise DocumentError("status must be 'active' or 'archived'")
    if changes.get("number"):
        _check_number(ws, changes["number"], False, exclude=doc_id)
    new = {**strip_rev(doc), **changes, "modified": now_iso()}
    rev = doc["_rev"] if expected_rev is None else expected_rev
    seq = ws.commit("document.update", [RecordPut("document", doc_id, new, expect_rev=rev)])
    return {**new, "_rev": seq}


def list_documents(ws: Workspace, project_id: str | None = None) -> list[dict]:
    docs = [d for d in _all(ws, "document").values() if project_id is None or d["project_id"] == project_id]
    return sorted(docs, key=lambda d: (d["project_id"], d["rel_path"].casefold()))


# ---------------------------------------------------------------------------- checkout / check-in
def checkout(ws: Workspace, doc_id: str, note: str = "", expected_doc_rev: int | None = None) -> dict:
    doc = _require_doc(ws, doc_id)
    if doc["status"] != "active":
        raise DocumentError("archived documents cannot be checked out")
    existing = _get(ws, "checkout", doc_id)
    if existing is not None:
        raise CheckoutConflict(existing)
    path = working_path(ws, doc)
    base_sha = hash_file(path)[0] if path.is_file() else None
    co = {"document_id": doc_id, "token": secrets.token_hex(16), "owner": current_actor(),
          "acquired": now_iso(), "base_rev_id": doc["latest_rev_id"], "base_sha256": base_sha,
          "note": note, "restored_from": None}
    try:
        seq = ws.commit("document.checkout", [
            RecordPut("checkout", doc_id, co, expect_rev=0),
            RecordCheck("document", doc_id, doc["_rev"] if expected_doc_rev is None else expected_doc_rev)])
    except ConflictError as exc:
        if exc.kind == "checkout":
            raise CheckoutConflict(_get(ws, "checkout", doc_id) or {}) from exc
        raise
    except CommittedNotApplied as exc:
        exc.result = {**co, "_rev": exc.seq}     # saved: without its token only an administrator could release it
        raise
    return {**co, "_rev": seq}


def _require_checkout(ws: Workspace, doc_id: str, token: str) -> dict:
    co = _get(ws, "checkout", doc_id)
    if co is None:
        raise NotCheckedOut("this document is not checked out")
    if not token or co["token"] != token:
        raise TokenMismatch("this checkout belongs to another session "
                            f"({co['owner'].get('user')}@{co['owner'].get('host')}); it cannot be used here")
    return co


def _earlier_checkin(ws: Workspace, doc_id: str, txid: str, description: str, label: str | None) -> dict | None:
    """What an earlier attempt with this txid already did (CORE_API 4), or None if nothing happened yet.

    A check-in cannot lean on commit()'s own retry check: every attempt makes a new revision id and time, so
    no two attempts are ever the same request, and after a success the checkout it needs is gone. The
    journal answers instead: the original revision for the same request, and a refusal for anything else."""
    outcome = ws.transaction_outcome(txid)
    if outcome["state"] == "not-found":
        return None
    if outcome["state"] == "fenced":
        raise LockLostError(f"check-in attempt {txid} (entry {outcome['seq']}) never took effect, because its "
                            "session had lost the writer lock. Nothing changed. Start a new attempt with a new txid")
    entry = ws.read_entry(outcome["seq"])
    revs = [i["data"] for i in entry["records"] if i["kind"] == "revision" and not i.get("delete")]
    same = (entry.get("op") == "document.checkin" and len(revs) == 1 and revs[0]["document_id"] == doc_id
            and revs[0]["description"] == description.strip() and (label is None or revs[0]["label"] == label.strip()))
    if not same:
        raise TxidReuseError(f"txid {txid} was already used for a different change (entry {outcome['seq']})")
    return {**revs[0], "_rev": outcome["seq"]}


def checkin(ws: Workspace, doc_id: str, token: str, description: str, label: str | None = None,
            allow_unchanged: bool = False, txid: str | None = None) -> dict:
    """Snapshot the working file as a new immutable revision and release the checkout atomically.

    With a ``txid``, a retry returns the original revision and captures nothing new (CORE_API 4)."""
    if not description.strip():
        raise DocumentError("a change description is required")
    if txid is None:
        return _checkin(ws, doc_id, token, description, label, allow_unchanged, None)
    earlier = _earlier_checkin(ws, doc_id, txid, description, label)
    if earlier is not None:
        return earlier
    try:
        return _checkin(ws, doc_id, token, description, label, allow_unchanged, txid)
    except (NotCheckedOut, TokenMismatch, StaleCheckout, ConflictError, TxidReuseError):
        # Another attempt with this txid may have got there while this one was stopped: same answer as a retry.
        earlier = _earlier_checkin(ws, doc_id, txid, description, label)
        if earlier is not None:
            return earlier
        raise


def _checkin(ws: Workspace, doc_id: str, token: str, description: str, label: str | None, allow_unchanged: bool,
             txid: str | None) -> dict:
    doc = _require_doc(ws, doc_id)
    co = _require_checkout(ws, doc_id, token)
    if doc["latest_rev_id"] != co["base_rev_id"]:
        raise StaleCheckout("a newer revision was committed after this checkout was taken; nothing was "
                            "checked in. Save your file elsewhere, check out again and merge your changes")
    path = working_path(ws, doc)
    _regular_file(path)
    latest = _get(ws, "revision", doc["latest_rev_id"]) if doc["latest_rev_id"] else None
    number = doc["rev_count"] + 1
    chosen = _next_label(ws, doc, number, label)
    stored = ContentStore(ws).store_file(path)
    if latest and latest["sha256"] == stored.sha256 and not allow_unchanged:
        raise NoChanges("the file is identical to the latest revision; cancel the checkout instead, "
                        "or check in anyway with 'allow unchanged'")
    rev = _new_revision(doc, number, chosen, stored, description.strip(), doc["latest_rev_id"],
                        get_lifecycle(ws)["initial"], co.get("restored_from"))
    new_doc = {**strip_rev(doc), "latest_rev_id": rev["id"], "rev_count": number, "modified": now_iso()}
    try:
        seq = ws.commit("document.checkin", [
            RecordPut("revision", rev["id"], rev, expect_rev=0),
            RecordPut("document", doc_id, new_doc, expect_rev=doc["_rev"]),
            RecordDelete("checkout", doc_id, expect_rev=co["_rev"])], txid=txid)
    except CommittedNotApplied as exc:
        exc.result = {**rev, "_rev": exc.seq}
        raise
    return {**rev, "_rev": seq}


def _sweep_working_temps(path: Path) -> None:
    """Remove temp files a crash left beside this working file, and only those: it is the company's folder. A young
    one may belong to a writer that is still at work."""
    cutoff = time.time() - atomicio.ORPHAN_TEMP_MIN_AGE_SECONDS
    for entry in path.parent.iterdir():
        if entry.name.endswith(".tmp") and entry.name.startswith((f".{path.name}.rwx-", f"..{path.name}.rwx-")):
            try:
                if entry.stat().st_mtime < cutoff:
                    atomicio.remove_quietly(entry)
            except OSError:
                pass


def _write_working_from_store(ws: Workspace, sha256: str, path: Path) -> None:
    """Replace the working file with a stored revision. Nothing can fence this and nothing can undo it, so a handle that
    may not write stops here, not at the commit that follows, by which time the user's edits are gone."""
    ws.require_authority()
    if not path.parent.is_dir():
        raise MissingFileError(f"the folder {path.parent} does not exist, so the file cannot be written; restore the "
                               "folder, then try again")
    _sweep_working_temps(path)
    tmp = path.with_name(f".{path.name}.rwx-{uuid.uuid4().hex[:8]}.tmp")
    try:
        ContentStore(ws).export(sha256, tmp)
        if path.is_file():
            shutil.copymode(path, tmp)           # a replaced file keeps the permissions the company gave it
        atomicio.retry_sharing(lambda: os.replace(tmp, path))
        atomicio._fsync_dir(path.parent)
    finally:
        atomicio.remove_quietly(tmp)     # a CAD program holding the file must not leave copies in the project


def cancel_checkout(ws: Workspace, doc_id: str, token: str, revert: bool = False) -> None:
    """Release a checkout. With ``revert`` the working file is restored to the latest revision
    first (explicit, since it discards local edits)."""
    doc = _require_doc(ws, doc_id)
    co = _require_checkout(ws, doc_id, token)
    if revert:
        if not doc["latest_rev_id"]:
            raise DocumentError("there is no revision to revert to")
        latest = _require_rev(ws, doc["latest_rev_id"])
        _write_working_from_store(ws, latest["sha256"], working_path(ws, doc))
    ws.commit("document.checkout.cancel", [RecordDelete("checkout", doc_id, expect_rev=co["_rev"])])


def admin_release_checkout(ws: Workspace, doc_id: str, expected_token: str, reason: str) -> dict:
    """Administrative recovery when the owner's session or token is lost. Audited; the working file
    is left untouched."""
    if not reason.strip():
        raise DocumentError("a reason is required")
    co = _get(ws, "checkout", doc_id)
    if co is None:
        raise NotCheckedOut("this document is not checked out")
    if co["token"] != expected_token:
        raise DocumentError("the checkout changed since it was inspected; inspect it again")
    ws.commit("document.checkout.admin_release", [
        RecordDelete("checkout", doc_id, expect_rev=co["_rev"]),
        RecordPut("audit", new_id("aud"), {"event": "checkout-admin-release", "document_id": doc_id,
                                          "previous_checkout": strip_rev(co), "reason": reason,
                                          "time": now_iso(), "by": current_actor()})])
    return co


def restore_to_working(ws: Workspace, doc_id: str, token: str, rev_id: str) -> None:
    """While checked out: replace the working file with an earlier revision's content. The next
    check-in records a NEW revision with ``restored_from`` set; history is never rewritten."""
    doc = _require_doc(ws, doc_id)
    co = _require_checkout(ws, doc_id, token)
    rev = _require_rev(ws, rev_id)
    if rev["document_id"] != doc_id:
        raise DocumentError("that revision belongs to another document")
    _write_working_from_store(ws, rev["sha256"], working_path(ws, doc))
    ws.commit("document.restore_to_working", [
        RecordPut("checkout", doc_id, {**strip_rev(co), "restored_from": rev_id}, expect_rev=co["_rev"])])


# ---------------------------------------------------------------------------- lifecycle
def transition(ws: Workspace, rev_id: str, to_state: str, comment: str = "",
               expected_rev: int | None = None) -> dict:
    lc = get_lifecycle(ws)
    rev = _require_rev(ws, rev_id)
    doc = _require_doc(ws, rev["document_id"])
    frm = rev["state"]
    if [frm, to_state] not in lc["transitions"]:
        allowed = ", ".join(lc_mod.allowed_targets(lc, frm)) or "none"
        raise LifecycleError(f"cannot move from {lc_mod.state_name(lc, frm)} to "
                             f"{lc_mod.state_name(lc, to_state)}; allowed: {allowed}")
    actor = current_actor()
    if to_state == lc["approved"] and lc["require_distinct_approver"] and \
            actor["user"] == rev["author"].get("user"):
        raise LifecycleError("policy requires a different person to approve this revision")
    now = now_iso()
    ops: list = []
    new_doc = None
    if to_state == lc["released"]:
        revisions_of(ws, doc["id"])              # a revision in a broken chain is not one to release
        if not ContentStore(ws).verify(rev["sha256"]):
            raise WorkspaceError(f"stored content of revision {rev['label']} is missing or damaged; "
                                 "it cannot be released")
        if doc.get("released_rev_id"):
            current = _require_rev(ws, doc["released_rev_id"])
            if current["number"] > rev["number"]:
                raise LifecycleError(f"revision {current['label']} (newer) is already released")
            sup = {**strip_rev(current), "state": lc["superseded"],
                   "transitions": current["transitions"] + [
                       {"from": current["state"], "to": lc["superseded"], "by": actor, "time": now,
                        "comment": f"superseded by {rev['label']}", "system": True}]}
            ops.append(RecordPut("revision", current["id"], sup, expect_rev=current["_rev"]))
        new_doc = {**strip_rev(doc), "released_rev_id": rev["id"], "modified": now}
    elif frm == lc["released"] and doc.get("released_rev_id") == rev_id:
        new_doc = {**strip_rev(doc), "released_rev_id": None, "modified": now}
    updated = {**strip_rev(rev), "state": to_state,
               "transitions": rev["transitions"] + [{"from": frm, "to": to_state, "by": actor, "time": now,
                                                     "comment": comment}]}
    _guard_immutable(rev, updated)
    ops.insert(0, RecordPut("revision", rev_id, updated,
                            expect_rev=rev["_rev"] if expected_rev is None else expected_rev))
    if new_doc is not None:
        ops.append(RecordPut("document", doc["id"], new_doc, expect_rev=doc["_rev"]))
    seq = ws.commit(f"revision.transition.{to_state}", ops)
    return {**updated, "_rev": seq}


def _guard_immutable(old: dict, new: dict) -> None:
    changed = [f for f in IMMUTABLE_REVISION_FIELDS if old.get(f) != new.get(f)]
    if changed:
        raise ImmutableRevisionError(f"revision fields {changed} are immutable")
    if len(new["transitions"]) < len(old["transitions"]) or \
            new["transitions"][:len(old["transitions"])] != old["transitions"]:
        raise ImmutableRevisionError("revision history can only be appended to")


# ---------------------------------------------------------------------------- inspection
def document_status(ws: Workspace, doc_id: str, verify_hash: bool = False) -> dict:
    """Compare the working file with the latest revision; detect edits made outside RevisionWorx."""
    doc = _require_doc(ws, doc_id)
    co = _get(ws, "checkout", doc_id)
    path = working_path(ws, doc)
    out = {"document_id": doc_id, "path": str(path), "checked_out": bool(co),
           "checkout_owner": co["owner"] if co else None, "method": "hash" if verify_hash else "metadata"}
    latest = _get(ws, "revision", doc["latest_rev_id"]) if doc["latest_rev_id"] else None
    if not path.exists():
        return {**out, "state": "missing"}
    if latest is None:
        return {**out, "state": "unrevisioned"}
    st = os.stat(path)
    if verify_hash:
        same = hash_file(path)[0] == latest["sha256"]
    else:
        same = st.st_size == latest["size"] and st.st_mtime_ns == latest["source_mtime_ns"]
        if not same and st.st_size == latest["size"]:
            same = hash_file(path)[0] == latest["sha256"]  # touched but identical
            out["method"] = "hash"
    if same:
        return {**out, "state": "unchanged"}
    return {**out, "state": "modified" if co else "modified-externally"}


def read_revision_bytes(ws: Workspace, rev_id: str, max_bytes: int) -> tuple[dict, bytes]:
    """Read-only, verified access to a revision's immutable content for integrations (ADR-018).

    Returns ``(revision, data)``; the bytes are checked against ``revision.sha256`` and
    ``revision.size``. The original extension is ``Path(revision["source_rel_path"]).suffix``."""
    rev = _require_rev(ws, rev_id)
    if _get(ws, "document", rev["document_id"]) is None:
        raise DocumentError(f"revision {rev_id} belongs to a missing document")
    data = ContentStore(ws).read_verified(rev["sha256"], max_bytes, expected_size=rev["size"])
    return rev, data


def export_revision(ws: Workspace, rev_id: str, dest: Path, overwrite: bool = False) -> Path:
    rev = _require_rev(ws, rev_id)
    return ContentStore(ws).export(rev["sha256"], Path(dest), overwrite=overwrite)


def verify_revisions(ws: Workspace, doc_id: str | None = None) -> list[dict]:
    store = ContentStore(ws)
    problems = []
    for rev in _all(ws, "revision").values():
        if doc_id and rev["document_id"] != doc_id:
            continue
        if not store.verify(rev["sha256"]):
            problems.append({"revision_id": rev["id"], "document_id": rev["document_id"],
                             "label": rev["label"], "problem": "content missing or damaged"})
    problems.extend(p for p in audit_history(ws) if not doc_id or p["document_id"] == doc_id)
    return problems


def audit_history(ws: Workspace) -> list[dict]:
    """Check from the journal that no published revision ever changed. ``commit`` is generic, so only the code calling it
    keeps revisions immutable; this is what would notice if it ever failed to. Reads every effective entry."""
    seen: dict[str, dict] = {}
    problems: list[dict] = []

    def report(problem: str, rid: str, seq: int, **details) -> None:
        problems.append({"problem": problem, "revision_id": rid, "seq": seq,
                         "document_id": seen.get(rid, {}).get("document_id"), **details})
    for entry in ws.entries_after(0):
        for item in entry["records"]:
            if item["kind"] != "revision":
                continue
            rid, seq = item["id"], entry["seq"]
            if item.get("delete"):
                report("revision-deleted", rid, seq)
                continue
            old, new = seen.get(rid), item["data"]
            if old is not None:
                changed = [f for f in IMMUTABLE_REVISION_FIELDS if old.get(f) != new.get(f)]
                if changed:
                    report("immutable-field-changed", rid, seq, fields=changed)
                before, after = old.get("transitions", []), new.get("transitions", [])
                if not isinstance(before, list) or not isinstance(after, list) or after[:len(before)] != before:
                    report("transitions-rewritten", rid, seq)
            seen[rid] = new
    return problems


def compare_revisions(ws: Workspace, rev_a: str, rev_b: str) -> dict:
    a, b = _require_rev(ws, rev_a), _require_rev(ws, rev_b)
    if a["document_id"] != b["document_id"]:
        raise DocumentError("revisions belong to different documents")
    return {"document_id": a["document_id"], "from": a["label"], "to": b["label"],
            "same_content": a["sha256"] == b["sha256"], "size_delta": b["size"] - a["size"],
            "states": [a["state"], b["state"]], "authors": [a["author"].get("user"), b["author"].get("user")],
            "created": [a["created"], b["created"]], "path_changed": a["source_rel_path"] != b["source_rel_path"],
            "descriptions": [a["description"], b["description"]],
            "restored_from": b.get("restored_from"),
            "note": "Metadata comparison only; native CAD content is not diffed."}


def history(ws: Workspace, doc_id: str) -> list[dict]:
    _require_doc(ws, doc_id)
    return revisions_of(ws, doc_id)


def _transition_actor(rev: dict, state: str) -> str:
    for t in reversed(rev.get("transitions", [])):
        if t["to"] == state:
            return t["by"].get("user", "")
    return ""


def drawing_register(ws: Workspace, project_id: str) -> list[dict]:
    """Rows for the engineering drawing register of one project."""
    lc = get_lifecycle(ws)
    project = _get(ws, "project", project_id)
    doctypes = list_doctypes(ws)
    rows = []
    for doc in list_documents(ws, project_id):
        if doctypes.get(doc["type_id"], {}).get("category") != "drawing":
            continue
        latest = _get(ws, "revision", doc["latest_rev_id"]) if doc["latest_rev_id"] else None
        released = _get(ws, "revision", doc["released_rev_id"]) if doc["released_rev_id"] else None
        rows.append({
            "number": doc["number"], "title": doc["title"], "project": project["name"] if project else "",
            "customer": project.get("customer", "") if project else "",
            "revision": latest["label"] if latest else "", "state": lc_mod.state_name(lc, latest["state"]) if latest else "Unrevisioned",
            "released_revision": released["label"] if released else "",
            "owner": doc["owner"], "author": latest["author"].get("user", "") if latest else "",
            # checker: who approved the latest revision; approver: who released it
            "checker": _transition_actor(latest, lc["approved"]) if latest else "",
            "approver": _transition_actor(latest, lc["released"]) if latest else "",
            "date": (latest or {}).get("created", ""), "source_file": doc["rel_path"]})
    return rows


def drawing_register_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols = ["number", "title", "project", "customer", "revision", "state", "released_revision", "owner",
            "author", "checker", "approver", "date", "source_file"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


# ---------------------------------------------------------------------------- content GC
def gc_content(ws: Workspace, apply: bool = False, min_age_seconds: float = 3600.0) -> dict:
    """Find (and with ``apply`` remove) content objects no revision references.

    Orphans arise when a check-in stored content but failed before its journal entry. Safety:
    requires the writer lock (no check-in can be storing concurrently); every revision record,
    including unapplied journal entries, is a reference; objects younger than ``min_age_seconds`` are
    kept; dry-run by default; removals are recorded in the journal as an audit record."""
    ws._require_writable()
    store = ContentStore(ws)
    referenced = {r["sha256"] for r in _all(ws, "revision").values()}
    now = time.time()
    orphans, unrecognised, kept_young = [], [], 0
    if store.root.is_dir():
        for sub in sorted(store.root.iterdir()):
            if not sub.is_dir():
                continue
            for obj in sorted(sub.iterdir()):
                if obj.name.startswith(".") or obj.name in referenced:
                    continue
                if not is_content_name(obj.name) or obj.name[:2] != sub.name or not obj.is_file():
                    unrecognised.append(obj.relative_to(store.root).as_posix())   # not ours to delete
                    continue
                st = obj.stat()
                if now - st.st_mtime < min_age_seconds:
                    kept_young += 1
                    continue
                orphans.append({"sha256": obj.name, "size": st.st_size})
    report = {"referenced": len(referenced), "orphans": orphans, "unrecognised": unrecognised, "kept_young": kept_young,
              "bytes": sum(o["size"] for o in orphans), "removed": False}
    if apply and orphans:
        ws.commit("content.gc", [RecordPut("audit", new_id("aud"), {
            "event": "content-gc", "removed": orphans, "time": now_iso(), "by": current_actor()})])
        for o in orphans:  # audit first: a crash leaves a record of intent, never an unlogged deletion
            ws.require_authority()        # a deletion cannot be fenced, but it can stop once authority is gone
            if o["sha256"] not in {r["sha256"] for r in _all(ws, "revision").values()}:
                store.path_for(o["sha256"]).unlink(missing_ok=True)
        report["removed"] = True
    return report
