# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only workspace integrity check: ``revisionworx doctor`` (backlog #28 item 11).

It looks at everything and changes nothing: no lock, no recovery, no repair, no index, no temp file. Unlike ``verify`` it keeps
going past damage, so one bad journal entry does not hide what else is wrong. Each finding has a stable ``code`` for scripts,
a ``severity`` and an ``action`` a person can follow:

* ``error``: something is wrong and needs a decision;
* ``warning``: the next writer may refuse or will clean this up; worth knowing about;
* ``info``: true and harmless, such as a change that is saved but not yet materialised.

It reports what the files say right now. On a workspace in use a finding can be out of date by the time it is read, and it
cannot tell a live lock holder from a dead one. Codes are listed in docs/CORE_API.md.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import atomicio, documents
from .content import ContentStore, hash_file
from .workspace import FORMAT_NAME, FORMAT_VERSION, Workspace, WorkspaceCorruptError, WorkspaceError

ERROR, WARNING, INFO = "error", "warning", "info"
_PASSES = 5
_SEQ_NAME = re.compile(r"^\d{20}\.json$")
_EVIDENCE = ("writer.lock.revoked-", "writer.lock.broken-", "writer.lock.displaced-", "writer.lock.orphaned-")


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    subject: str
    message: str
    action: str = ""


class _Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.checked: list[str] = []
        self.skipped: list[str] = []

    def add(self, code: str, severity: str, subject: str, message: str, action: str = "") -> None:
        self.findings.append(Finding(code, severity, subject, message, action))

    def result(self, location: Path) -> dict:
        order = {ERROR: 0, WARNING: 1, INFO: 2}
        found = sorted(self.findings, key=lambda f: (order[f.severity], f.code, f.subject))
        return {"workspace": str(location), "ok": not any(f.severity != INFO for f in found),
                "errors": sum(f.severity == ERROR for f in found), "warnings": sum(f.severity == WARNING for f in found),
                "checked": self.checked, "skipped": self.skipped, "findings": [asdict(f) for f in found]}


_REPAIR = "run 'revisionworx verify --repair' (journal is the authority; replaced files go to quarantine/)"
_RESTORE = "restore this file from a backup; nothing can rebuild it"


def _check_identity(meta: Path, out: _Report) -> dict | None:
    path = meta / "workspace.json"
    if not path.is_file():
        out.add("W001", ERROR, "workspace.json", "there is no workspace here", "check the folder")
        return None
    try:
        info = atomicio.read_json(path)
    except (OSError, ValueError) as exc:
        out.add("W002", ERROR, "workspace.json", f"cannot be read: {exc}", _RESTORE)
        return None
    if not isinstance(info, dict) or info.get("format") != FORMAT_NAME:
        out.add("W002", ERROR, "workspace.json", "is not a RevisionWorx workspace file", _RESTORE)
        return None
    version = info.get("format_version", 0)
    if not isinstance(version, int):
        out.add("W002", ERROR, "workspace.json", f"has an invalid format version {version!r}", _RESTORE)
        return None
    if version > FORMAT_VERSION:
        out.add("W003", ERROR, "workspace.json", f"format {info.get('format_version')} is newer than this RevisionWorx",
                "update RevisionWorx")
        return None
    return info


def _check_journal(ws: Workspace, out: _Report) -> bool:
    """Read every entry, whatever the others look like. Returns whether the journal is whole."""
    out.checked.append("journal")
    whole = True
    journal = ws.meta_dir / "journal"
    for path in sorted(journal.iterdir()) if journal.is_dir() else []:
        if not (_SEQ_NAME.match(path.name) or path.name.endswith(".tmp")):
            out.add("J004", WARNING, f"journal/{path.name}", "a file that is not a journal entry", "move it out of the journal")
    seqs = ws.journal_seqs()
    missing = sorted(set(range(1, (seqs[-1] if seqs else 0) + 1)) - set(seqs))
    for seq in missing:
        whole = False
        out.add("J001", ERROR, f"journal entry {seq}", "is missing, so history has a gap; writes and opens are refused", _RESTORE)
    for seq in seqs:
        try:
            ws.read_entry(seq)
        except (WorkspaceCorruptError, OSError) as exc:
            whole = False
            out.add("J002", ERROR, f"journal entry {seq}", str(exc), _RESTORE)
    if whole and seqs:
        try:
            for seq in seqs:
                entry = ws.read_entry(seq)
                if "digest" not in entry:
                    out.add("J003", WARNING, f"journal entry {seq}", "has no digest (written by an old version), so changes to "
                            "it could not be detected", "keep a backup; this does not stop anything working")
                if ws.is_fenced(entry):
                    out.add("J005", INFO, f"journal entry {seq}", "was published after its writer lost the lock, so it never took "
                            "effect. This is fencing having worked")
        except (WorkspaceCorruptError, OSError) as exc:
            whole = False
            out.add("J002", ERROR, "journal", str(exc), _RESTORE)
    head = seqs[-1] if seqs else 0
    try:
        applied = int(atomicio.read_json(ws.meta_dir / "state.json")["applied_seq"])
    except FileNotFoundError:
        applied = 0
        if head:
            out.add("S001", INFO, "state.json", "is missing. It is only a cache", "the next writer rebuilds it")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        applied = 0
        out.add("S001", WARNING, "state.json", f"cannot be read: {exc}. It is only a cache", "the next writer rebuilds it")
    if applied > head:
        out.add("S002", ERROR, "state.json", f"says entry {applied} was applied but the journal ends at {head}: the journal is "
                "older than the rest of the workspace (a partial restore?)", "restore the whole workspace from one backup")
    elif applied < head:
        out.add("S003", INFO, "state.json", f"{head - applied} saved change(s) are not materialised yet",
                "none: the next writer finishes this")
    return whole


def _check_locks(ws: Workspace, out: _Report, journal_whole: bool) -> None:
    out.checked.append("locks")
    locks = ws.meta_dir / "locks"
    revoked: set[str] = set()
    if journal_whole:
        revoked = Workspace._revoked_tokens(ws.meta_dir)
    else:
        out.skipped.append("whether lock tokens are revoked: the journal is damaged")
    holder = Workspace.read_writer_lock(ws.meta_dir)
    if holder and holder.get("unreadable"):
        out.add("L003", ERROR, "locks/writer.lock", "cannot be read, so its writer is unknown and everyone is locked out",
                "confirm no writer is running, then 'revisionworx lock break --token ? --reason ...' and follow the L005 advice")
    elif holder:
        who = f"{holder.get('user')}@{holder.get('host')} (pid {holder.get('pid')}, since {holder.get('acquired')})"
        if holder.get("token") in revoked:
            out.add("L002", INFO, "locks/writer.lock", f"held by {who}, whose lock was revoked", "none: the next writer clears it")
        else:
            out.add("L001", INFO, "locks/writer.lock", f"held by {who}. This cannot tell a running writer from a dead one",
                    "if that writer is gone: 'revisionworx lock show', then 'revisionworx lock break --token ... --reason ...'")
    for path in sorted(locks.iterdir()) if locks.is_dir() else []:
        name = path.name
        if name.startswith((".releasing-", ".fencing-")):
            try:
                token = atomicio.read_json(path).get("token")
            except (OSError, ValueError, AttributeError):
                token = None
            if token:
                out.add("L004", WARNING, f"locks/{name}", "a displaced lock, or a release interrupted partway",
                        "none: the next writer revokes it and files it as evidence")
            else:
                out.add("L005", ERROR, f"locks/{name}", "unreadable, so its writer cannot be revoked; writers are refused",
                        f"confirm that writer is stopped, then 'revisionworx lock clear-orphan {name} --reason ...'")
        elif name.startswith(".clearing-"):
            out.add("L006", WARNING, f"locks/{name}", "an administrative clear did not finish; writers wait for it",
                    "none: the next writer or a repeat of 'lock clear-orphan' finishes it")
        elif name.startswith(_EVIDENCE) and journal_whole:
            try:
                token = atomicio.read_json(path).get("token")
            except (OSError, ValueError, AttributeError):
                continue                             # evidence from an audited clear of an unreadable file
            if token and token not in revoked:
                out.add("L007", ERROR, f"locks/{name}", f"archived while its token {token} is not revoked: that writer was never "
                        "fenced and could still publish", "stop all writers, then report this: it should be impossible")


def _check_materialised(ws: Workspace, out: _Report) -> None:
    out.checked.append("records")
    codes = {"mismatch": ("R001", "differs from the journal"), "unreadable": ("R005", "cannot be read"),
             "missing": ("R002", "is missing"), "not-in-journal": ("R003", "was never written by the journal"),
             "deleted-but-present": ("R004", "should have been deleted")}
    for problem in ws.verify()["problems"]:
        kind = problem["problem"]
        if kind in codes:
            code, text = codes[kind]
            out.add(code, WARNING, f"records/{problem['kind']}/{problem['id']}", f"{text}. Reads use the journal; only the cache "
                    "is wrong", _REPAIR)
        elif kind == "missing-blob":
            out.add("B001", ERROR, f"blobs/{problem['blob']}", f"journal entry {problem['seq']} refers to it, and it is missing", _RESTORE)
        elif kind in ("orphan-evidence-missing", "orphan-evidence-altered"):
            out.add("L008", ERROR, f"locks/{problem['evidence']}", f"audited evidence is {kind.rsplit('-', 1)[1]}", _RESTORE)


def _check_documents(ws: Workspace, out: _Report) -> dict[str, list[str]]:
    """Returns which revisions use each content hash."""
    out.checked.append("documents")
    docs, revisions = ws.list("document"), ws.list("revision")
    states = {s["id"] for s in documents.get_lifecycle(ws)["states"]}
    used: dict[str, list[str]] = {}
    for rid, rev in revisions.items():
        used.setdefault(rev["sha256"], []).append(rid)
        if rev["document_id"] not in docs:
            out.add("D004", ERROR, f"revision {rid}", f"belongs to document {rev['document_id']}, which does not exist", _RESTORE)
        if rev.get("state") not in states:
            out.add("D007", WARNING, f"revision {rid}", f"is in state {rev.get('state')!r}, which the lifecycle does not have",
                    "add the state back to the lifecycle")
    projects_known = ws.list("project")
    for did, doc in docs.items():
        if doc["project_id"] not in projects_known:
            out.add("D005", ERROR, f"document {did}", f"belongs to project {doc['project_id']}, which does not exist", _RESTORE)
        try:
            documents.revisions_of(ws, did)
        except WorkspaceError as exc:
            out.add("D001", ERROR, f"document {did}", str(exc), _RESTORE)
        released = doc.get("released_rev_id")
        if released and released not in revisions:
            out.add("D006", ERROR, f"document {did}", f"names released revision {released}, which does not exist", _RESTORE)
    for did in ws.list("checkout"):
        if did not in docs:
            out.add("D003", ERROR, f"checkout {did}", "is for a document that does not exist",
                    "an administrator can release it with 'revisionworx doc release-checkout'")
    out.checked.append("history")
    codes = {"immutable-field-changed": "D010", "transitions-rewritten": "D011", "revision-deleted": "D012"}
    for problem in documents.audit_history(ws):
        detail = f" ({', '.join(problem['fields'])})" if problem.get("fields") else ""
        out.add(codes[problem["problem"]], ERROR, f"revision {problem['revision_id']}",
                f"{problem['problem']}{detail} in journal entry {problem['seq']}: a published revision must never change",
                "stop and investigate: the journal records exactly what happened")
    return used


def _check_content(ws: Workspace, used: dict[str, list[str]], out: _Report, hash_content: bool) -> None:
    out.checked.append("content (hashed)" if hash_content else "content (present only)")
    store = ContentStore(ws)
    if not hash_content:
        out.skipped.append("hashing content objects: --quick")
    for sha, revisions in sorted(used.items()):
        try:
            path = store.path_for(sha)
        except ValueError:
            out.add("C001", ERROR, f"revision {revisions[0]}", f"records an invalid hash {sha!r}", _RESTORE)
            continue
        if not path.is_file():
            out.add("C001", ERROR, f"content {sha}", f"is missing; needed by revision(s) {', '.join(sorted(revisions))}", _RESTORE)
        elif hash_content:
            try:
                good = hash_file(path)[0] == sha
            except OSError as exc:
                out.add("C003", WARNING, f"content {sha}", f"could not be read: {exc}", "check file permissions, then run this again")
                continue
            if not good:
                out.add("C002", ERROR, f"content {sha}", f"does not match its hash; needed by revision(s) {', '.join(sorted(revisions))}",
                        _RESTORE)
    if not store.root.is_dir():
        return
    for sub in sorted(store.root.iterdir()):
        if not sub.is_dir():
            if sub.name.endswith(".tmp"):
                out.add("C005", INFO, f"content/{sub.name}", "left by an interrupted store", "none: the next writer removes it once it is old")
            continue
        for obj in sorted(sub.iterdir()):
            if obj.name not in used and not obj.name.startswith("."):
                out.add("C004", INFO, f"content {obj.name}", "no revision uses it", "'revisionworx doc gc' can remove it")


def _attempt(out: _Report, name: str, check, *args):
    """A check that cannot finish is a finding, and what it did not cover is said. It never stops the others."""
    try:
        return check(*args), True
    except (OSError, WorkspaceError, ValueError, KeyError, TypeError, AttributeError) as exc:
        out.add("X001", ERROR, name, f"could not be checked: {exc!r}", "check file permissions; if that is not it, report this")
        out.skipped.append(name)
        return None, False


def _examine_once(meta: Path, info: dict, hash_content: bool) -> _Report:
    out = _Report()
    out.checked.append("identity")
    ws = Workspace(meta, info, False, None)
    whole, ok = _attempt(out, "journal", _check_journal, ws, out)
    whole = bool(ok and whole)
    _attempt(out, "locks", _check_locks, ws, out, whole)
    if not whole:
        out.skipped.append("records, documents, history and content: they are judged against the journal, which is damaged")
        return out
    _attempt(out, "records", _check_materialised, ws, out)
    used, ok = _attempt(out, "documents", _check_documents, ws, out)
    if ok:
        _attempt(out, "content", _check_content, ws, used, out, hash_content)
    else:
        out.skipped.append("content: the records that use it could not be read")
    return out


def _head(meta: Path) -> tuple[str, ...]:
    """Changes whenever anything is published, including a lock being revoked."""
    try:
        return tuple(sorted(p.name for p in (meta / "journal").iterdir()))
    except OSError:
        return ()


def examine(location: Path, hash_content: bool = True) -> dict:
    """Everything that can be established about a workspace without changing it.

    The checks are not one instant. A writer publishing between two of them could make healthy data look broken, such as a
    revision whose new document was read before it existed. So a pass only counts if nothing was published during it. Every
    change that could cause a false alarm is published first (a lock is revoked before its file is archived), so a quiet journal
    means the pass was consistent."""
    meta = Workspace.meta_dir_for(Path(location))
    out = _Report()
    info = _check_identity(meta, out)
    if info is None:
        out.skipped.append("everything else: the workspace could not be identified")
        return out.result(meta)
    for _ in range(_PASSES):
        before = _head(meta)
        out = _examine_once(meta, info, hash_content)
        if _head(meta) == before:
            return out.result(meta)
    out.skipped.append("a consistent view: writers kept publishing throughout, so findings here may be momentary; run this again")
    return out.result(meta)


def render(report: dict) -> str:
    lines = [f"{report['workspace']}: " + ("healthy" if report["ok"] else f"{report['errors']} error(s), {report['warnings']} warning(s)")]
    for f in report["findings"]:
        lines.append(f"  {f['severity'].upper():<7} {f['code']} {f['subject']}: {f['message']}")
        if f["action"]:
            lines.append(f"          -> {f['action']}")
    lines.append(f"  Checked: {', '.join(report['checked'])}.")
    if report["skipped"]:
        lines.append(f"  Not checked: {'; '.join(report['skipped'])}.")
    return "\n".join(lines)


__all__ = ["examine", "render", "Finding", "ERROR", "WARNING", "INFO"]
