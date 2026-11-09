# SPDX-License-Identifier: GPL-3.0-or-later
"""Transfers between workspaces (docs/UNITS.md section 16, ADR-031).

A unit sold, handed over to a service company, or moved to another site is recorded from then on in another workspace.
Exactly one workspace keeps a unit at a time. The workspace it leaves makes a **transfer pack**: the unit's next handover pack
(section 13), with ``transfer.json`` and the unit's history added, for the unit and every unit fitted in it. It records the
pack as issued and each of those units as ``transferred``, in one entry; from then on it refuses every action on them, and
keeps a copy of the pack so it can be written out again. The receiving workspace checks the pack (whole, and signed by the key
it was told to expect, if any), then records each unit with the same id, in one entry (``unit.received``).

The Director decided (2026-11-08) that the receiver **refers** to the source's revisions: it keeps each document the unit uses
as a foreign reference (number, title, revision, SHA-256 and the PDF), read-only, never as a document of its own. A revision
the receiving workspace has itself (a unit coming back) is its own again.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from .. import atomicio, derivatives
from ..content import ContentStore
from ..workspace import CommittedNotApplied, WorkspaceCorruptError
from . import history as H, pack as P, query
from .model import (MERGED, STATE_FIELDS, TRANSFERRED, NotFound, ValidationError, actor_record, check_foreign, check_state,
                    clean, is_id, norm)

TRANSFER = "transfer.json"
FORMAT = "revisionworx-unit-transfer"
VERSION = 1
HISTORY_DIR = "history"
HISTORY_FORMAT = "revisionworx-unit-history"
KEPT_FORMAT = "revisionworx-kept-pack"
RECEIVE_DOMAIN = "revisionworx/units/receive/v1"
MAX_JSON = 64 * 1024 * 1024          # a transfer.json or a history this big isn't one RevisionWorx would write


class TransferError(P.PackError):
    pass


# ----------------------------------------------------------------------------------------------------- making one
class TransferDraft(P.Draft):
    """A transfer pack put together beside its folder: the root's handover pack plus the transfer, not yet recorded."""

    def __init__(self, work: Path, dest: Path, units: list[dict], number: int, sums: str, files: int, pdfs: list[dict],
                 key: str, to: str):
        super().__init__(work, dest, units[0]["id"], query.unit_summary(units[0])["label"], units[0]["events"]["count"],
                         number, sums, "", files, pdfs, key)
        self.units, self.to = units, to
        self.counts = {u["id"]: u["events"]["count"] for u in units}


def build(svc, ref: str, dest: Path, *, to: str, sign_with: Path | None = None) -> TransferDraft:
    """Put the transfer pack of unit ``ref`` (and of everything fitted in it) together beside the new folder ``dest``, for
    ``to``. Reads only. Refused for a unit fitted in another (transfer that one) and for anything the receiving workspace
    couldn't take whole: a document of the unit's that can't be found, a damaged history."""
    to = clean(to, "who it is transferred to", 200, required=True)
    policy = P.signing(svc.ws)
    if policy["enabled"] and sign_with is None:
        raise TransferError(f"this workspace signs every handover pack, with the key {policy['fingerprint']}: give its key "
                            "file ('revisionworx unit transfer REF FOLDER --to WHO --sign-with KEYFILE')")
    if sign_with is not None and not policy["enabled"]:
        raise TransferError("this workspace doesn't sign handover packs; an administrator turns it on with "
                            "'revisionworx unit pack-signing on --key COMPANY.pub'")
    root = svc.resolve(ref)
    if root["status"] in (MERGED, TRANSFERRED):
        raise TransferError(f"{query.unit_summary(root)['label']} is {root['status']}: there is nothing here to transfer")
    if root["parent"] is not None:
        raise TransferError(f"{query.unit_summary(root)['label']} is fitted in {svc.describe(root['parent']['unit'])}: "
                            "transfer that unit, which takes this one with it, or take it out first")
    members = _tree(svc, root)
    names = query._Names(svc)
    ws_name = svc.ws.info.get("name", "") or svc.ws.id
    number = len(H.packs_in(svc.events(root["id"]))) + 1
    detail = P._public(query.unit_detail(svc, root))
    dest = Path(dest)
    P._free(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.name}.{secrets.token_hex(6)}.tmp"
    work.mkdir()
    try:
        (work / P.PDF_DIR).mkdir()
        (work / HISTORY_DIR).mkdir()
        documents, pdfs = _documents(svc, members, names, work / P.PDF_DIR)
        units = []
        for u in members:
            parent = None if u is root else {"unit": u["parent"]["unit"], "position": u["parent"]["position"]}
            state = _state(svc, u, names, ws_name, parent)
            hist = _history(svc, u, ws_name)
            rel = f"{HISTORY_DIR}/{u['id']}.json"
            data = _dumps(hist)
            atomicio.atomic_write_bytes(work / rel, data)
            units.append({"id": u["id"], "state": state, "history": rel, "history_sha256": hashlib.sha256(data).hexdigest()})
        signed = {"key": policy["fingerprint"], "type": policy["key"].type, "namespace": P.NAMESPACE} if policy["enabled"] \
            else False
        made = svc.clock()
        record = {"format": P.PACK_FORMAT, "version": P.PACK_VERSION, "pack": number, "made": made,
                  "made_by": P._person(actor_record(svc.actor())), "signed": signed,
                  "workspace": {"id": svc.ws.id, "name": svc.ws.info.get("name", "")}, "as_of": None, "unit": detail,
                  "pdfs": [p for p in pdfs if p["unit"] == root["id"]], "transfer": {"to": to, "units": len(members)}}
        record["pdfs"] = [{k: v for k, v in p.items() if k != "unit"} for p in record["pdfs"]]
        transfer = {"format": FORMAT, "version": VERSION, "from": {"workspace": svc.ws.id, "name": ws_name}, "to": to,
                    "made": made, "root": root["id"], "units": units, "documents": documents}
        atomicio.atomic_write_bytes(work / P.RECORD, (json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False)
                                                      + "\n").encode("utf-8"))
        atomicio.atomic_write_bytes(work / TRANSFER, _dumps(transfer))
        atomicio.atomic_write_bytes(work / P.PAGE, P.render(record).encode("utf-8"))
        if not any((work / P.PDF_DIR).iterdir()):
            (work / P.PDF_DIR).rmdir()
        files = P._listing(work)
        listing = "".join(f"{files[name]}  {name}\n" for name in sorted(files)).encode("utf-8")
        atomicio.atomic_write_bytes(work / P.SUMS, listing)
        if policy["enabled"]:
            P._sign(work, listing, Path(sign_with), policy)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return TransferDraft(work, dest, members, number, hashlib.sha256(listing).hexdigest(),
                         len(files) + 1 + bool(policy["enabled"]), [p for p in pdfs if p["unit"] == root["id"]],
                         policy["fingerprint"], to)


def _tree(svc, root: dict) -> list[dict]:
    """The unit and everything fitted in it, the unit first and each unit before what is fitted in it."""
    out, seen, todo = [], set(), [root]
    while todo:
        u = todo.pop(0)
        if u["id"] in seen:
            raise TransferError(f"{query.unit_summary(u)['label']} is fitted twice in what is transferred; run "
                                "'revisionworx unit verify'")
        seen.add(u["id"])
        out.append(u)
        for c in sorted(u["components"].values(), key=lambda c: norm(c["position"])):
            if not c["unit"]:
                continue
            child = svc.unit(c["unit"])
            if (child["parent"] or {}).get("unit") != u["id"] or child["status"] in (MERGED, TRANSFERRED):
                raise TransferError(f"{query.unit_summary(child)['label']}, fitted in {query.unit_summary(u)['label']}, "
                                    "doesn't say so itself; run 'revisionworx unit verify'")
            todo.append(child)
    return out


def _documents(svc, members: list[dict], names, folder: Path) -> tuple[list[dict], list[dict]]:
    """What the receiver keeps of each document the units use (``transfer.json`` ``documents``), and the PDFs, written to
    ``folder``: each revision's own PDF (ADR-026), or the revision itself when it is a PDF, or the PDF this workspace kept of
    one that came from elsewhere. Never another revision's."""
    store = ContentStore(svc.ws)
    ws_name = svc.ws.info.get("name", "") or svc.ws.id
    entries: dict[tuple[str, str], dict] = {}
    pdfs, used, files = [], set(), {}
    for u in members:
        docs = query.applicable_documents(svc, u, names)
        if docs["standard_problem"]:
            raise TransferError(f"{query.unit_summary(u)['label']}: {docs['standard_problem']}; nothing was transferred")
        for row in docs["rows"]:
            doc_id, rev = row["document"]["id"], row["revision"]
            rid = rev["id"] if rev else ""
            if "missing" in row["flags"]:
                raise TransferError(f"{query.unit_summary(u)['label']} names {row['document']['number'] or doc_id}, which "
                                    "can't be found here; run 'revisionworx unit verify'. Nothing was transferred")
            if (doc_id, rid) in entries:
                continue
            if row.get("foreign"):
                f = row["foreign"]
                meta = {"workspace": f["workspace"], "workspace_name": f["name"], "number": row["document"]["number"],
                        "title": row["document"]["title"], "revision": rid, "label": rev["label"] if rev else "",
                        "state": rev["state"] if rev else "", "state_name": rev["state_name"] if rev else "",
                        "sha256": rev["sha256"] if rev else "", "size": f["size"], "pdf": f["pdf"]}
            else:
                rec = svc.ws.get("revision", rid) or {} if rid else {}
                derivative = derivatives.get(svc.ws, rid) if rid else None
                pdf = (derivative["sha256"] if derivative else
                       (rec.get("sha256", "") if rid and derivatives.native_is_pdf(svc.ws, rec) else ""))
                meta = {"workspace": svc.ws.id, "workspace_name": ws_name, "number": row["document"]["number"],
                        "title": row["document"]["title"], "revision": rid, "label": rev["label"] if rev else "",
                        "state": rev["state"] if rev else "", "state_name": rev["state_name"] if rev else "",
                        "sha256": rev["sha256"] if rev else "", "size": int(rec.get("size", 0) or 0), "pdf": pdf or ""}
            entry = {"document": doc_id, **meta, "pdf_file": ""}
            if meta["pdf"]:
                if meta["pdf"] not in files:
                    name = P._file_name(f"{meta['number'] or doc_id} rev {meta['label']}", used)
                    try:
                        store.export(meta["pdf"], folder / name)
                    except (OSError, WorkspaceCorruptError) as exc:
                        raise TransferError(f"the PDF of {meta['number'] or doc_id} rev {meta['label']} can't be read from "
                                            f"this workspace ({exc}); run 'revisionworx doc verify'. Nothing was "
                                            "transferred") from None
                    files[meta["pdf"]] = f"{P.PDF_DIR}/{name}"
                entry["pdf_file"] = files[meta["pdf"]]
            entries[(doc_id, rid)] = entry
            if rid:
                pdfs.append({"unit": u["id"], "document": meta["number"] or doc_id, "title": meta["title"],
                             "revision": meta["label"], "revision_id": rid, "file": entry["pdf_file"] or None,
                             "sha256": meta["pdf"] or None,
                             "missing": "" if meta["pdf"] else "no PDF is attached to this revision"})
    return sorted(entries.values(), key=lambda e: (e["document"], e["revision"])), pdfs


def _state(svc, u: dict, names, ws_name: str, parent: dict | None) -> dict:
    """The unit as the receiving workspace starts from it: nothing in it is an id of this workspace's own records but
    documents, revisions and units. Serials and tags this organization issued name it."""
    events = svc.events(u["id"])
    docs = query.applicable_documents(svc, u, names)["rows"]
    aliases = {m["unit"] for m in H.merged_from(events)}
    for r in H.receipts(events):
        aliases.update(r["aliases"])
    state = {
        "class": u["class"], "name": u["name"],
        "model": {"part": "", "number": u["model"]["number"] or (names("part", u["model"]["part"]) if u["model"]["part"]
                                                                 else "")},
        "identifiers": [{"scheme": i["scheme"],
                         "issuer": ws_name[:200] if i["scheme"] in ("serial", "tag") and not i["issuer"] else i["issuer"],
                         "value": i["value"], "active": i["active"]} for i in u["identifiers"]],
        "status": u["status"], "customer": u["customer"], "site": u["site"], "location": u["location"], "notes": u["notes"],
        "parent": parent,
        "documents": {r["document"]["id"]: {"revision": r["revision"]["id"] if r["revision"] else "", "basis": r["basis"]}
                      for r in docs},
        "components": [{"position": c["position"], "number": c["number"] or (names("part", c["part"]) if c["part"] else ""),
                        "unit": c["unit"], "serial": c["serial"], "lot": c["lot"], "since": c["since"], "basis": c["basis"]}
                       for c in sorted(u["components"].values(), key=lambda c: norm(c["position"]))],
        "milestones": {m: v["date"] for m, v in u["milestones"].items()},
        "lots": [{k: x[k] for k in ("number", "lot", "quantity", "units", "since", "basis")} for x in u.get("lots", [])],
        "aliases": sorted(aliases - {u["id"]}),
    }
    try:
        return check_state(state)
    except ValidationError as exc:
        raise TransferError(f"{query.unit_summary(u)['label']} can't be handed over as it is recorded ({exc}); nothing was "
                            "transferred") from None


def _history(svc, u: dict, ws_name: str) -> dict:
    """The unit's history in every workspace that has kept it, oldest first: the one this workspace received with it, then its
    own, without computer names (they stay behind, as in a handover pack)."""
    custodians = []
    receipts = H.receipts(svc.events(u["id"]))
    if receipts:                                     # this workspace's own earlier record is part of its history here
        earlier = read_history(svc.ws, receipts[-1]["history"])
        custodians = [c for c in earlier["custodians"] if c["workspace"] != svc.ws.id]
    own = [{**e, "actor": P._person(e.get("actor"))} for e in query.unit_detail(svc, u)["history"]]
    custodians.append({"workspace": svc.ws.id, "name": ws_name, "history": own})
    return {"format": HISTORY_FORMAT, "version": 1, "unit": u["id"], "custodians": custodians}


def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def issue(svc, draft: TransferDraft) -> dict:
    """Keep a copy of the pack, record the transfer (the pack issued, each unit transferred) in one entry, then move the pack
    into place. Recorded only if nothing was recorded on any of the units since the pack was put together; otherwise nothing
    is recorded and the pack is removed."""
    if draft.issued:
        raise TransferError("this transfer has already been recorded")
    pending = False
    try:
        P._free(draft.dest)
        kept = _keep(svc.ws, draft.work)
        with svc.expecting(draft.counts):
            svc.transfer([u["id"] for u in draft.units], draft.to, draft.number, draft.sums, draft.key, kept)
    except CommittedNotApplied:
        pending = True
    except BaseException:
        draft.discard()
        raise
    draft.issued = True
    try:
        if draft.dest.exists():
            draft.dest.rmdir()
        os.replace(draft.work, draft.dest)
    except OSError as exc:
        raise TransferError(f"the transfer of {draft.label} is recorded, but its pack couldn't be moved to {draft.dest} ({exc}). "
                            f"It is complete in {draft.work}: rename that folder, or write it again with 'revisionworx unit "
                            "transfer-pack'") from exc
    return {"path": str(draft.dest), "unit": draft.label, "to": draft.to, "units": [query.unit_summary(u)["label"]
                                                                                     for u in draft.units],
            "pack": draft.number, "files": draft.files, "pdfs": len([p for p in draft.pdfs if p["file"]]),
            "missing": [p for p in draft.pdfs if not p["file"]], "signed": bool(draft.key), "key": draft.key,
            "recorded": True, "pending": pending}


def make(svc, ref: str, dest: Path, *, to: str, sign_with: Path | None = None) -> dict:
    """Make the transfer pack and record the transfer, on one writable handle."""
    with build(svc, ref, dest, to=to, sign_with=sign_with) as draft:
        return issue(svc, draft)


def _keep(ws, folder: Path) -> str:
    """Put every file of the pack in the content store, and a list of them: its SHA-256 is what ``unit.transferred``
    records, so the pack can be written out again exactly (``write_again``)."""
    store = ContentStore(ws)
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            files[path.relative_to(folder).as_posix()] = store.store_file(path).sha256
    tmp = folder.parent / f".{folder.name}.kept.{secrets.token_hex(4)}"
    try:
        atomicio.atomic_write_bytes(tmp, _dumps({"format": KEPT_FORMAT, "version": 1, "files": files}))
        return store.store_file(tmp).sha256
    finally:
        atomicio.remove_quietly(tmp)


def _kept_files(ws, kept: str) -> dict[str, str]:
    data = _read_json(ContentStore(ws), kept, "the copy of the transfer pack")
    files = data.get("files") if isinstance(data, dict) and data.get("format") == KEPT_FORMAT else None
    if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        raise TransferError("the copy of the transfer pack this workspace kept isn't one RevisionWorx wrote; run "
                            "'revisionworx unit verify'")
    return files


def write_again(svc, ref: str, dest: Path) -> dict:
    """Write a transferred unit's transfer pack again, exactly as it was issued (a pack lost on the way, say)."""
    unit = svc.resolve(ref)
    gone = H.transferred(svc.events(unit["id"]))
    if gone is None:
        raise TransferError(f"{query.unit_summary(unit)['label']} hasn't been transferred from this workspace")
    root = gone["units"][0]
    if root != unit["id"]:
        raise TransferError(f"{query.unit_summary(unit)['label']} was transferred fitted in {svc.describe(root)}: write that "
                            "unit's transfer pack")
    t = next(e for e in reversed(svc.events(unit["id"])) if e["type"] == "unit.transferred")
    files = _kept_files(svc.ws, t["body"]["kept"])
    dest = Path(dest)
    P._free(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.name}.{secrets.token_hex(6)}.tmp"
    work.mkdir()
    store = ContentStore(svc.ws)
    try:
        for rel, sha in files.items():
            if rel.startswith(("/", "\\")) or ".." in rel.split("/"):
                raise TransferError("the copy of the transfer pack names a file outside it; run 'revisionworx unit verify'")
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            store.export(sha, work / rel)
        if hashlib.sha256((work / P.SUMS).read_bytes()).hexdigest() != t["body"]["pack"]:
            raise TransferError("the copy of the transfer pack isn't the pack that was issued; run 'revisionworx unit verify'")
        if dest.exists():
            dest.rmdir()
        os.replace(work, dest)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return {"path": str(dest), "unit": query.unit_summary(unit)["label"], "to": gone["to"], "files": len(files)}


# --------------------------------------------------------------------------------------------------- receiving one
def receive(svc, folder: Path, *, key: str = "") -> dict:
    """Receive the units of the transfer pack in ``folder``, on a writable handle. The pack must be whole and, if it is
    signed, its signature must hold; given ``key`` (a fingerprint) it must be signed with that key. Then the PDFs and the
    histories it brings are kept in the content store, and each unit is recorded with the same id, in one entry. The same
    pack received again records nothing more."""
    folder = Path(folder)
    report = P.check(folder, key=key)
    if not report["ok"]:
        raise TransferError("the transfer pack can't be received: " + "; ".join(report["problems"]))
    t = load(folder)
    if t["from"]["workspace"] == svc.ws.id:
        raise TransferError("this transfer pack was made by this workspace: it is received by the workspace it was made for")
    sums = hashlib.sha256((folder / P.SUMS).read_bytes()).hexdigest()
    signer = report["signed"]["key"] if report["signed"] else ""
    store = ContentStore(svc.ws)
    kept_pdf: dict[str, str] = {}
    for d in t["documents"]:
        if d["pdf"] and d["pdf"] not in kept_pdf:
            got = store.store_file(folder / d["pdf_file"]).sha256
            if got != d["pdf"]:
                raise TransferError(f"{d['pdf_file']} isn't the PDF transfer.json says it is; nothing was received")
            kept_pdf[d["pdf"]] = got
    by_doc = {(d["document"], d["revision"]): d for d in t["documents"]}
    me = norm(svc.ws.info.get("name", "") or "")
    units = []
    for m in t["units"]:
        state = copy.deepcopy(m["state"])
        for i in state["identifiers"]:              # what this organization issued is its own again when a unit comes back
            if me and i["scheme"] in ("serial", "tag") and norm(i["issuer"]) == me:
                i["issuer"] = ""
        foreign = {}
        for doc, d in state["documents"].items():
            entry = by_doc.get((doc, d["revision"]))
            if entry is None:
                raise TransferError(f"transfer.json doesn't describe document {doc} that {m['id']} uses; nothing was received")
            if _ours(svc.ws, doc, d["revision"]):
                continue                            # a document of this workspace's own: it refers to it as before
            foreign[doc] = {k: entry[k] for k in ("workspace", "workspace_name", "number", "title", "revision", "label",
                                                  "state", "state_name", "sha256", "size", "pdf")}
        history = store.store_file(folder / m["history"]).sha256
        if history != m["history_sha256"]:
            raise TransferError(f"{m['history']} isn't the history transfer.json says it is; nothing was received")
        units.append({"id": m["id"], "state": state, "foreign": foreign, "history": history})
    txid = hashlib.sha256(f"{RECEIVE_DOMAIN}\0{sums}".encode()).hexdigest()[:32]
    pending = False
    try:
        got = svc.receive(units, t["from"], sums, signer, txid=txid)
    except CommittedNotApplied:
        pending, got = True, []
    labels = [query.unit_summary(u)["label"] for u in got] if got else [m["id"] for m in t["units"]]
    return {"units": labels, "from": t["from"], "to": t["to"], "signed": bool(signer), "key": signer,
            "already": svc.last_replayed, "pending": pending}


def _ours(ws, doc: str, rev: str) -> bool:
    d = ws.get("document", doc)
    if d is None:
        return False
    if not rev:
        return True
    r = ws.get("revision", rev)
    return r is not None and r.get("document_id") == doc


def load(folder: Path) -> dict:
    """``transfer.json``, refused unless it is exactly what this version writes."""
    try:
        raw = (Path(folder) / TRANSFER).read_bytes()
        if len(raw) > MAX_JSON:
            raise ValueError("it is far too big")
        t = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise TransferError(f"there's no {TRANSFER} here: this is a handover pack, not a transfer pack") from None
    except (OSError, ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise TransferError(f"{TRANSFER} can't be read: {exc}") from None
    if not isinstance(t, dict) or t.get("format") != FORMAT:
        raise TransferError(f"{TRANSFER} isn't a transfer pack's")
    if isinstance(t.get("version"), bool) or not isinstance(t.get("version"), int) or t["version"] > VERSION:
        raise TransferError(f"{TRANSFER} was made by a newer RevisionWorx (version {t.get('version')!r}); update RevisionWorx "
                            "to receive it")
    try:
        if set(t) != {"format", "version", "from", "to", "made", "root", "units", "documents"}:
            raise ValidationError("it has the wrong fields")
        src = t["from"]
        if not isinstance(src, dict) or set(src) != {"workspace", "name"} or not all(isinstance(v, str) for v in src.values()):
            raise ValidationError("it doesn't say which workspace it came from")
        if not isinstance(t["to"], str) or not isinstance(t["made"], str) or not is_id("unit", t["root"]):
            raise ValidationError("it doesn't say what was transferred, and to whom")
        if not isinstance(t["units"], list) or not t["units"] or t["units"][0].get("id") != t["root"]:
            raise ValidationError("its units don't start with the unit transferred")
        ids = set()
        for m in t["units"]:
            if not isinstance(m, dict) or set(m) != {"id", "state", "history", "history_sha256"} or not is_id("unit", m["id"]) \
                    or m["id"] in ids or not isinstance(m["history"], str) or m["history"] != f"{HISTORY_DIR}/{m['id']}.json" \
                    or not isinstance(m["history_sha256"], str) or len(m["history_sha256"]) != 64:
                raise ValidationError("one of its units has the wrong fields")
            ids.add(m["id"])
            check_state(m["state"])
            if set(m["state"]) != STATE_FIELDS:
                raise ValidationError("one of its units has the wrong fields")
        for m in t["units"][1:]:                    # everything but the unit transferred is fitted in something transferred
            if m["state"]["parent"] is None or m["state"]["parent"]["unit"] not in ids:
                raise ValidationError("one of its units isn't fitted in what was transferred")
        if t["units"][0]["state"]["parent"] is not None:
            raise ValidationError("the unit transferred is fitted in something that wasn't")
        for m in t["units"]:
            for c in m["state"]["components"]:
                if c["unit"] and c["unit"] not in ids:
                    raise ValidationError("one of its units has a unit fitted that wasn't transferred with it")
        if not isinstance(t["documents"], list):
            raise ValidationError("its documents are a list")
        for d in t["documents"]:
            if not isinstance(d, dict) or set(d) != {"document", "pdf_file"} | set(check_foreign(d.get("document", ""), {
                    k: v for k, v in d.items() if k not in ("document", "pdf_file")})):
                raise ValidationError("one of its documents has the wrong fields")
            if bool(d["pdf"]) != bool(d["pdf_file"]) or (d["pdf_file"] and (
                    not isinstance(d["pdf_file"], str) or not d["pdf_file"].startswith(P.PDF_DIR + "/")
                    or ".." in d["pdf_file"].split("/"))):
                raise ValidationError("one of its documents names its PDF wrongly")
    except ValidationError as exc:
        raise TransferError(f"{TRANSFER} isn't one this version can receive: {exc}") from None
    return t


# ------------------------------------------------------------------------------------------------------ histories
def read_history(ws, sha: str) -> dict:
    """A unit's history before it came here, as kept in the content store: ``{format, version, unit, custodians}``."""
    h = _read_json(ContentStore(ws), sha, "a received history")
    ok = isinstance(h, dict) and h.get("format") == HISTORY_FORMAT and h.get("version") == 1 \
        and isinstance(h.get("custodians"), list) and all(
            isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("workspace"), str)
            and isinstance(c.get("history"), list) for c in h["custodians"])
    if not ok:
        raise TransferError("a history this workspace received isn't one RevisionWorx wrote; run 'revisionworx unit verify'")
    return h


def _read_json(store: ContentStore, sha: str, what: str) -> Any:
    try:
        return json.loads(store.read_verified(sha, MAX_JSON).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError, WorkspaceCorruptError) as exc:
        raise TransferError(f"{what} can't be read from the content store ({exc}); run 'revisionworx unit verify'") from None


def find_foreign(svc, doc: str, label: str = "") -> list[dict]:
    """What this workspace kept of document ``doc`` from another workspace, one entry per revision some unit here uses
    (``label``: only that revision), as each receipt kept it: ``{workspace, workspace_name, number, title, revision, label,
    state, state_name, sha256, size, pdf}``."""
    if not is_id("document", doc) or svc.ws.get("document", doc) is not None:
        return []
    names = query._Names(svc)
    found: dict[str, dict] = {}
    for u in svc.scan()[0]:
        d = u["documents"].get(doc)
        f = names.foreign(u["id"], d["event"], doc) if d else None
        if f and f["revision"] and f["revision"] == d["revision"] \
                and (not label or f["label"].casefold() == label.strip().casefold()):
            found.setdefault(f["revision"], f)
    return list(found.values())


def content_references(ws) -> set[str]:
    """Every content object a transfer or a receipt keeps (content clean-up keeps them): the copy of each transfer pack and
    every file in it, and each history and PDF a receipt brought."""
    out: set[str] = set()
    store = ContentStore(ws)
    for rec in ws.list("unitevt").values():
        body = rec.get("body") if isinstance(rec, dict) else None
        if not isinstance(body, dict):
            continue
        if rec.get("type") == "unit.transferred" and isinstance(body.get("kept"), str):
            out.add(body["kept"])
            try:
                out.update(_kept_files(ws, body["kept"]).values())
            except TransferError:
                pass                                 # verify reports it; the list itself is kept
        elif rec.get("type") == "unit.received":
            if isinstance(body.get("history"), str):
                out.add(body["history"])
            for f in (body.get("foreign") or {}).values():
                if isinstance(f, dict) and isinstance(f.get("pdf"), str):
                    out.add(f["pdf"])
    del store
    return {s for s in out if s}


__all__ = ["TransferDraft", "TransferError", "build", "content_references", "find_foreign", "issue", "load", "make",
           "read_history", "receive", "write_again"]
