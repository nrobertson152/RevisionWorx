# SPDX-License-Identifier: GPL-3.0-or-later
"""Handover packs (docs/UNITS.md section 13): one unit's record as a folder anyone can open, with no RevisionWorx and no
network. A page to read and print, the PDFs of the revisions that apply to the unit, the record as JSON, and the SHA-256 of
every file in the format ``sha256sum -c`` reads.

A pack is put together from what a read-only handle sees (``build``), then recorded in the unit's history as issued, with its
number and the SHA-256 of its SHA256SUMS, and only then moved into place (``issue``): the workspace is the company's record
of every pack it issued (ADR-030, the Director's decision at gate 4). ``check`` says whether a pack is whole and, given the
workspace, whether that workspace issued it.

Signing is optional and off by default (ADR-030). An administrator turns it on with the company's public key (``set_signing``);
then every pack is signed with ``ssh-keygen -Y sign`` (``SHA256SUMS.sig``), with a software key or one on a security key such
as a YubiKey, and the key's fingerprint is recorded with the pack.
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

from .. import atomicio, derivatives, sshsig
from ..content import ContentStore
from ..workspace import CommittedNotApplied, RecordPut, WorkspaceCorruptError, now_iso
from . import history as H, query
from .model import NotFound, UnitError, actor_record, check_date, day_of, is_id

PACK_FORMAT = "revisionworx-unit-pack"
PACK_VERSION = 2                    # 2: numbered, and recorded in the unit's history as issued (ADR-030)
SUMS = "SHA256SUMS"
SIGNATURE = "SHA256SUMS.sig"
NAMESPACE = "revisionworx-handover-pack"   # what a pack's signature is for (ssh-keygen -n): never valid for anything else
SETTING = "pack-signing"                   # the settings record (admin-only in a team workspace)
RECORD = "unit.json"
PAGE = "index.html"
PDF_DIR = "documents"
_SUM_LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


class PackError(UnitError):
    pass


# ---------------------------------------------------------------------------------------------------------- signing
def signing(ws) -> dict:
    """Whether this workspace signs its handover packs, and with which key: ``{"enabled", "key" (sshsig.PublicKey or None),
    "fingerprint", "changed", "changed_by"}``. Off unless someone turned it on. A setting that can't be read is refused
    (``PackError``): whether packs must be signed isn't known, so none is made."""
    rec = ws.get("settings", SETTING)
    if rec is None:
        return {"enabled": False, "key": None, "fingerprint": "", "changed": "", "changed_by": None}
    rec = {k: v for k, v in rec.items() if k != "_rev"}
    if set(rec) != {"id", "enabled", "key", "changed", "changed_by"} or rec["id"] != SETTING \
            or not isinstance(rec["enabled"], bool) or not isinstance(rec["key"], str) or not isinstance(rec["changed"], str) \
            or not isinstance(rec["changed_by"], dict) or rec["enabled"] != bool(rec["key"]):
        raise PackError("the workspace's pack-signing setting is damaged, so no pack is made; an administrator sets it again "
                        "with 'revisionworx unit pack-signing on --key FILE' or 'off'")
    key = None
    if rec["enabled"]:
        try:
            key = sshsig.parse_public_key(rec["key"])
        except sshsig.SignatureError as exc:
            raise PackError(f"the workspace's pack-signing key can't be read ({exc}), so no pack is made") from None
    return {"enabled": rec["enabled"], "key": key, "fingerprint": key.fingerprint if key else "", "changed": rec["changed"],
            "changed_by": rec["changed_by"]}


def set_signing(ws, key_text: str | None) -> dict:
    """Sign every handover pack from now on with the key whose public half is ``key_text`` (a ``.pub`` file's text), or
    stop signing (``None``). Only an administrator, in a team workspace. Packs already made don't change."""
    if not getattr(ws, "is_admin", True):
        raise PackError("only an administrator can change how handover packs are signed")
    key = None
    if key_text is not None:
        try:
            key = sshsig.parse_public_key(key_text)
        except sshsig.SignatureError as exc:
            raise PackError(f"that key can't sign packs: {exc}") from None
    current = ws.get("settings", SETTING)
    record = {"id": SETTING, "enabled": key is not None, "key": key.openssh() if key else "", "changed": now_iso(),
              "changed_by": actor_record(ws.actor())}
    ws.commit("unit.pack.signing", [RecordPut("settings", SETTING, record,
                                              expect_rev=int(current.get("_rev", 0)) if current else 0)])
    return signing(ws)


def allowed_signers(key) -> str:
    """The line a customer puts in an OpenSSH ``allowed_signers`` file to check packs with ``ssh-keygen -Y verify``."""
    return f'supplier namespaces="{NAMESPACE}" {key.openssh()}'


# ---------------------------------------------------------------------------------------------------------- making
class Draft:
    """A pack put together beside its folder and not yet issued. ``issue`` records it and moves it into place; leaving a
    ``with`` block without issuing it removes it, so a pack that wasn't recorded is never left behind."""

    def __init__(self, work: Path, dest: Path, unit_id: str, label: str, count: int, number: int, sums: str, as_of: str,
                 files: int, pdfs: list[dict], key: str = ""):
        self.work, self.dest, self.unit_id, self.label = work, dest, unit_id, label
        self.count, self.number, self.sums, self.as_of = count, number, sums, as_of
        self.files, self.pdfs, self.key = files, pdfs, key
        self.issued = False

    def __enter__(self) -> "Draft":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.discard()
        return False

    def discard(self) -> None:
        if not self.issued:
            shutil.rmtree(self.work, ignore_errors=True)


def make(svc, ref: str, dest: Path, *, on: str = "", sign_with: Path | None = None) -> dict:
    """Make the handover pack of unit ``ref`` in the new folder ``dest``, as the unit stands now or ``on`` a date, and
    record it as issued, on one writable handle. All of it or nothing: see ``build`` and ``issue``."""
    with build(svc, ref, dest, on=on, sign_with=sign_with) as draft:
        return issue(svc, draft)


def build(svc, ref: str, dest: Path, *, on: str = "", sign_with: Path | None = None) -> Draft:
    """Put the handover pack of unit ``ref`` together beside the new folder ``dest``, numbered as the unit's next pack.
    Reads only: nothing is written to the workspace, and nothing is in ``dest`` yet. Refused if ``dest`` exists and isn't an
    empty folder.

    In a workspace that signs its packs, ``sign_with`` is the key file ``ssh-keygen -Y sign -f`` takes (a private key, or the
    handle of a key on a security key, which asks to be touched); the signature must be by the workspace's key. Given a key
    where packs aren't signed, it is refused: nothing is signed by accident."""
    policy = signing(svc.ws)
    if policy["enabled"] and sign_with is None:
        raise PackError(f"this workspace signs every handover pack, with the key {policy['fingerprint']}: give its key file "
                        "('revisionworx unit pack REF FOLDER --sign-with KEYFILE')")
    if sign_with is not None and not policy["enabled"]:
        raise PackError("this workspace doesn't sign handover packs; an administrator turns it on with "
                        "'revisionworx unit pack-signing on --key COMPANY.pub'")
    unit = svc.resolve(ref)
    on = day_of(check_date(on, "date")) if on else ""
    number = len(H.packs_in(svc.events(unit["id"]))) + 1
    detail = _public(query.unit_detail(svc, unit, on=on))
    dest = Path(dest)
    _free(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.name}.{secrets.token_hex(6)}.tmp"
    work.mkdir()
    try:
        (work / PDF_DIR).mkdir()
        pdfs = _pdfs(svc.ws, detail["documents"], work / PDF_DIR)
        signed = {"key": policy["fingerprint"], "type": policy["key"].type, "namespace": NAMESPACE} if policy["enabled"] \
            else False
        record = {"format": PACK_FORMAT, "version": PACK_VERSION, "pack": number, "made": svc.clock(),
                  "made_by": _person(actor_record(svc.actor())), "signed": signed,
                  "workspace": {"id": svc.ws.id, "name": svc.ws.info.get("name", "")}, "as_of": on or None,
                  "unit": detail, "pdfs": pdfs}
        atomicio.atomic_write_bytes(work / RECORD, (json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False)
                                                    + "\n").encode("utf-8"))
        atomicio.atomic_write_bytes(work / PAGE, render(record).encode("utf-8"))
        if not any((work / PDF_DIR).iterdir()):
            (work / PDF_DIR).rmdir()                 # no PDFs: no empty folder to wonder about
        files = _listing(work)
        listing = "".join(f"{files[name]}  {name}\n" for name in sorted(files)).encode("utf-8")
        atomicio.atomic_write_bytes(work / SUMS, listing)
        if policy["enabled"]:
            _sign(work, listing, Path(sign_with), policy)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return Draft(work, dest, unit["id"], detail["label"], unit["events"]["count"], number,
                 hashlib.sha256(listing).hexdigest(), on, len(files) + 1 + bool(policy["enabled"]), pdfs,
                 policy["fingerprint"])


def _sign(work: Path, listing: bytes, key_file: Path, policy: dict) -> None:
    """Sign SHA256SUMS with ssh-keygen, then check the signature here: it must hold, be by the workspace's key, and (on a
    security key) say the key was touched. Otherwise ``PackError``, and the pack is removed."""
    try:
        sig = sshsig.sign_file(work / SUMS, key_file, NAMESPACE)
        info = sshsig.verify(sig, listing, NAMESPACE)
    except sshsig.SignatureError as exc:
        raise PackError(f"the pack couldn't be signed: {exc}") from None
    if info["fingerprint"] != policy["fingerprint"]:
        raise PackError(f"{key_file} is the key {info['fingerprint']}, but this workspace signs its packs with "
                        f"{policy['fingerprint']}: sign with that key")


def issue(svc, draft: Draft) -> dict:
    """Record ``draft`` in its unit's history as issued (``pack.issued``: its number and the SHA-256 of its SHA256SUMS),
    then move it into place. Recorded only if nothing has been recorded on the unit since the pack was put together, so a
    pack always shows the unit as it stood when it was issued; otherwise ``Changed``. If it isn't recorded, it is removed."""
    if draft.issued:
        raise PackError("this pack has already been issued")
    pending = False
    try:
        _free(draft.dest)
        with svc.expecting({draft.unit_id: draft.count}):
            svc.issue_pack(draft.unit_id, draft.number, draft.sums, as_of=draft.as_of, key=draft.key)
    except CommittedNotApplied:
        pending = True                               # recorded: the workspace finishes applying it when next opened
    except BaseException:
        draft.discard()
        raise
    draft.issued = True
    try:
        if draft.dest.exists():
            draft.dest.rmdir()                       # empty, checked above
        os.replace(draft.work, draft.dest)
    except OSError as exc:
        raise PackError(f"handover pack {draft.number} of {draft.label} is recorded as issued, but it couldn't be moved to "
                        f"{draft.dest} ({exc}). It is complete in {draft.work}: rename that folder") from exc
    included = [p for p in draft.pdfs if p["file"]]
    return {"path": str(draft.dest), "unit": draft.label, "pack": draft.number, "files": draft.files,
            "pdfs": len(included), "missing": [p for p in draft.pdfs if not p["file"]], "signed": bool(draft.key),
            "key": draft.key, "recorded": True, "pending": pending}


def _free(dest: Path) -> None:
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise PackError(f"{dest} already exists and isn't an empty folder; choose a new one")


def _person(actor: Any) -> Any:
    """Who, as it leaves the company: the person, not the computer they used."""
    return {k: v for k, v in actor.items() if k != "host"} if isinstance(actor, dict) else actor


def _who(actor: Any) -> str:
    if not isinstance(actor, dict):
        return ""
    return str(actor.get("name") or actor.get("account") or actor.get("user") or "")


def _public(detail: dict) -> dict:
    """The record as it leaves the company: who did what stays (a quality record says who verified), the computer's name
    doesn't."""
    out = dict(detail)
    out["history"] = [{**e, "actor": _person(e.get("actor"))} for e in detail.get("history", [])]
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
    """``{relative path: SHA-256}`` of every file in the pack, but the list itself and its signature."""
    out = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            rel = path.relative_to(folder).as_posix()
            if rel not in (SUMS, SIGNATURE):
                out[rel] = _sha256(path)
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------------------- checking
def check(folder: Path, svc=None, key: str = "") -> dict:
    """Is the pack whole? Every file listed in SHA256SUMS is there with that SHA-256, nothing else is, and the record is one
    this version can read. Given ``svc`` (the workspace the pack says it came from), also: did that workspace issue it, and
    is it still in effect? ``register`` answers that, or is ``None`` when no workspace was given.

    A signed pack's signature must hold and be by the key the pack names; ``signed`` is then the key, or ``False``. Given
    ``key`` (a fingerprint, as ``ssh-keygen -l`` prints it), the signature must be by that key. A key named by the pack alone
    proves nothing: anyone can make a key."""
    folder = Path(folder)
    problems: list[str] = []
    report: dict = {"ok": False, "problems": problems, "unit": None, "pack": None, "files": 0, "signed": False,
                    "register": None}
    try:
        raw = (folder / SUMS).read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except FileNotFoundError:
        problems.append(f"there's no {SUMS} here: this isn't a handover pack, or it's incomplete")
        return report
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{SUMS} can't be read: {exc}")
        return report
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
    report["files"] = len(listed) + 1 + (folder / SIGNATURE).is_file()
    record = None
    try:
        record = json.loads((folder / RECORD).read_text("utf-8"))
        if not isinstance(record, dict) or record.get("format") != PACK_FORMAT:
            problems.append(f"{RECORD} isn't a handover pack's record")
            record = None
        elif isinstance(record.get("version"), bool) or not isinstance(record.get("version"), int) \
                or record["version"] > PACK_VERSION:
            problems.append(f"{RECORD} was made by a newer RevisionWorx (pack version {record.get('version')!r}); "
                            "update RevisionWorx to check it")
            record = None
        elif record["version"] >= 2 and (isinstance(record.get("pack"), bool) or not isinstance(record.get("pack"), int)
                                         or record["pack"] < 1):
            problems.append(f"{RECORD} isn't a handover pack's record (it has no pack number)")
            record = None
        else:
            report["unit"] = (record.get("unit") or {}).get("label")
            report["pack"] = record.get("pack")
    except (OSError, ValueError, RecursionError) as exc:
        problems.append(f"{RECORD} can't be read: {exc}")
    report["signed"] = _signature(folder, raw, record, key, problems)
    if svc is not None:
        report["register"] = reg = _in_register(svc, record, hashlib.sha256(raw).hexdigest())
        if reg["state"] != "issued":
            problems.append(reg["message"])
        if reg["state"] in ("issued", "withdrawn"):
            have = report["signed"]["key"] if report["signed"] else ""
            if reg["key"] != have:
                problems.append(f"{reg['workspace']} issued this pack " + (f"signed with the key {reg['key']}"
                                if reg["key"] else "unsigned") + ", but here it is " + (f"signed with the key {have}"
                                                                                       if have else "not signed"))
    report["ok"] = not problems
    return report


def _signature(folder: Path, raw: bytes, record: dict | None, key: str, problems: list[str]) -> dict | bool:
    """The pack's signature, checked: ``{"key", "type", "security_key", "touched"}``, or ``False`` if there is none (or it
    doesn't hold, which is then one of ``problems``)."""
    says = record.get("signed") if isinstance(record, dict) else None
    named = says.get("key") if isinstance(says, dict) else ""
    path = folder / SIGNATURE
    if not path.is_file():
        if named:
            problems.append(f"the pack says it is signed with the key {named}, but {SIGNATURE} is missing")
        elif key:
            problems.append(f"the pack isn't signed, so it isn't signed with the key {key}")
        return False
    try:
        if path.stat().st_size > sshsig.MAX_SIGNATURE:
            raise sshsig.SignatureError("it is far too big to be a signature")
        info = sshsig.verify(path.read_bytes(), raw, NAMESPACE)
    except (OSError, sshsig.SignatureError) as exc:
        problems.append(f"{SIGNATURE} doesn't hold: {exc}")
        return False
    found = {"key": info["fingerprint"], "type": info["type"], "security_key": info["key"].security_key,
             "touched": info["touched"]}
    if record is not None and not named:
        problems.append(f"{SIGNATURE} isn't part of the pack: the pack says it isn't signed")
    elif named and named != info["fingerprint"]:
        problems.append(f"{SIGNATURE} is by the key {info['fingerprint']}, but the pack says it is signed with {named}")
    if key and key != info["fingerprint"]:
        problems.append(f"the pack is signed with the key {info['fingerprint']}, not with the key you gave ({key})")
    return found


def _in_register(svc, record: dict | None, sums: str) -> dict:
    """What the workspace's register says about the pack whose SHA256SUMS has SHA-256 ``sums``. The pack's own record only
    says which unit to look at: the register decides. ``state`` is ``issued``, ``withdrawn``, ``not-issued`` or
    ``unknown``."""
    name = svc.ws.info.get("name", "") or "this workspace"
    if record is None:
        return {"state": "unknown", "workspace": name,
                "message": f"the pack's record can't be read, so it can't be looked up in the register of {name}"}
    if record["version"] < 2:
        return {"state": "not-issued", "workspace": name,
                "message": f"this pack was made before RevisionWorx recorded the packs it issues, so {name} can't confirm "
                           "that it issued it"}
    unit_id = (record.get("unit") or {}).get("id")
    try:
        unit = svc.unit(unit_id) if isinstance(unit_id, str) and is_id("unit", unit_id) else None
    except NotFound:
        unit = None
    if unit is None:
        return {"state": "not-issued", "workspace": name,
                "message": f"{name} has no unit {unit_id!s}, so it didn't issue this pack"}
    label = query.unit_summary(unit)["label"]
    try:
        packs = H.packs_in(svc.events(unit["id"]))
    except UnitError as exc:
        return {"state": "unknown", "workspace": name,
                "message": f"the history of {label} in {name} can't be read, so the pack can't be confirmed ({exc})"}
    hits = [p for p in packs if p["sums"] == sums]
    if not hits:
        return {"state": "not-issued", "workspace": name,
                "message": f"{name} has no record of issuing this pack: no pack of {label} it issued has this {SUMS}"}
    p = hits[-1]
    out = {"workspace": name, "unit": label, "pack": p["pack"], "event": p["event"], "issued": p["time"],
           "by": _who(p["actor"]), "key": p["key"], "withdrawn": None, "newer": None}
    said = f"{label} handover pack {p['pack']}, issued by {name} on {p['time'][:10]}" + (f" by {out['by']}" if out["by"] else "")
    if p["voided"] or p["pack"] != record.get("pack"):
        return {**out, "state": "unknown",
                "message": f"{said}, but its register entry isn't one RevisionWorx writes; run 'revisionworx unit verify'"}
    if p["withdrawn"] is not None:
        w = p["withdrawn"]
        out["withdrawn"] = {"time": w["time"], "by": _who(w["actor"]), "reason": w["reason"], "event": w["event"]}
        return {**out, "state": "withdrawn",
                "message": f"{said}, was withdrawn on {w['time'][:10]}" + (f" by {_who(w['actor'])}" if _who(w["actor"]) else "")
                           + (f": {w['reason']}" if w["reason"] else "")}
    later = [q for q in packs if q["pack"] > p["pack"] and q["withdrawn"] is None and not q["voided"]]
    if later:
        out["newer"] = {"pack": later[-1]["pack"], "issued": later[-1]["time"]}
        said += f"; pack {later[-1]['pack']} has been issued since ({later[-1]['time'][:10]})"
    return {**out, "state": "issued", "message": said}


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
    n = record.get("pack")
    pack_name = f"handover pack {n}" if n else "handover pack"
    out = ['<!DOCTYPE html>', '<html lang="en"><head><meta charset="utf-8">',
           f'<title>{e(u["label"])}: {pack_name}</title><style>{_CSS}</style></head><body>']
    model = " ".join(x for x in (u["model"].get("number", ""), u["name"]) if x)
    out.append(f'<h1>{e(u["label"])}</h1><p class="muted">{e(model)}{" · " if model else ""}{e(u["class"])} · '
               f'status: {e(u["status"])}</p>')
    as_of = f' It shows the unit as known on {e(record["as_of"])}.' if record.get("as_of") else ""
    company = e(record["workspace"]["name"]) or "the workspace"
    by = _who(record.get("made_by"))
    signed = record.get("signed")
    if isinstance(signed, dict):
        sig = (f' It is signed with {company}\'s key <code>{e(signed["key"])}</code>: <b>{SIGNATURE}</b> is the signature of '
               f'<b>{SUMS}</b>. Check it with OpenSSH, <code>ssh-keygen -Y check-novalidate -n {NAMESPACE} -s {SIGNATURE} '
               f'&lt; {SUMS}</code>, and compare the key it names with the one {company} gave you, not with this page: anyone '
               'can write a page.')
    else:
        sig = " The pack is not signed."
    out.append(f'<p class="note">{e(pack_name.capitalize())} of {e(u["label"])}, made on {e(record["made"][:10])}'
               f'{" by " + e(by) if by else ""} from the RevisionWorx workspace {company}, which records every pack it '
               f'issues.{as_of} <b>{SUMS}</b> lists the SHA-256 of every file, so anyone can check that nothing is missing '
               f'or changed (<code>sha256sum -c {SUMS}</code>, or <code>revisionworx unit check-pack FOLDER</code>), and '
               f'{company} can confirm from its record that it issued this pack and that it is still in effect.{sig}</p>')

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
            by = _who(h.get("actor"))
            what = e(h["summary"]) + (' <span class="flag">(recorded in error)</span>' if h.get("voided") else "")
            out.append(f'<tr><td>{h["n"]}</td><td>{e(h.get("effective") or h["time"][:10])}</td><td>{what}</td>'
                       f'<td>{e(h["basis"])}</td><td>{e(by)}</td></tr>')
        out.append("</table>")
    if u["notes"]:
        out.append(f'<h2>Notes</h2><p>{e(u["notes"])}</p>')
    out.append("</body></html>\n")
    return "\n".join(out)
