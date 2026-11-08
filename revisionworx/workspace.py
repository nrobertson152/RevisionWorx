# SPDX-License-Identifier: GPL-3.0-or-later
"""The authoritative ``.revisionworx`` workspace store.

Layout (format version 1)::

    .revisionworx/
      workspace.json                 identity + format version (written once)
      journal/<seq:020d>.json        ordered, immutable change entries (source of truth)
      records/<kind>/<id>.json       materialised current records (derived from the journal)
      blobs/<kind>/<id>.json         large immutable payloads referenced by entries (baselines)
      state.json                     {"applied_seq": N} checkpoint of materialisation
      locks/writer.lock              single-writer lease for this workspace

Commit protocol (see docs/ARCHITECTURE.md, ADR-004):
  1. Writer holds ``locks/writer.lock`` (exclusive publish; never silently stolen).
  2. Any blob is written first under a fresh unique name (an orphan blob is harmless).
  3. Journal entry ``seq = applied_seq + 1`` is published with exclusive create. This is the commit
     point: once it exists the change has happened.
  4. Records named in the entry are rewritten atomically (idempotent "put" semantics).
  5. ``state.json`` is advanced.
A crash between 3 and 5 is repaired by replaying entries above ``applied_seq`` on next open.
Readers overlay unapplied entries in memory so they never see a state older than the journal.

Additional guards (Phase 1 reliability review, ADR-008):
  * every materialised record carries ``_rev`` = sequence of the entry that last wrote it; a
    put/delete may state ``expect_rev`` (0 = must not exist) and the whole entry is rejected with
    :class:`ConflictError` if any expectation fails (optimistic concurrency, no lost updates);
  * before publishing, the writer re-reads the lock and refuses with :class:`LockLostError` if its
    token is gone (administrative break); each entry records ``writer_token``;
  * each entry carries a SHA-256 ``digest`` of its canonical content, checked on every read;
  * a failure after the commit point raises :class:`CommittedNotApplied`; callers may supply a
    ``txid`` and later ask :meth:`Workspace.transaction_outcome`.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import atomicio
from .atomicio import ExclusiveCreateError

FORMAT_NAME = "RevisionWorx Workspace"
FORMAT_VERSION = 1
SHARED_FORMAT_VERSION = 2                            # a workspace set up for a team (ADR-024): older versions must refuse it
META_DIRNAME = ".revisionworx"
_SEQ_RE = re.compile(r"^(\d{20})\.json$")
_ID_RE = re.compile(r"^[a-z]{2,8}_[0-9a-f]{32}$|^[a-z0-9][a-z0-9_.-]{0,63}$")
KINDS = {"root", "project", "template", "settings", "audit"}
REV_FIELD = "_rev"


def register_kind(kind: str) -> None:
    """Allow a later subsystem (e.g. Phase 2 documents) to store its own record kind."""
    if not re.match(r"^[a-z]{2,16}$", kind):
        raise ValueError(f"invalid record kind {kind!r}")
    KINDS.add(kind)


class WorkspaceError(Exception):
    pass


class WorkspaceFormatError(WorkspaceError):
    pass


class WorkspaceCorruptError(WorkspaceError):
    """Authoritative data is inconsistent; writes are refused until an administrator intervenes."""


class WorkspaceLockedError(WorkspaceError):
    def __init__(self, holder: dict):
        self.holder = holder
        super().__init__(
            f"workspace is open for writing by {holder.get('user')}@{holder.get('host')} "
            f"(pid {holder.get('pid')}, since {holder.get('acquired')})")


class ReadOnlyError(WorkspaceError):
    pass


class ConflictError(WorkspaceError):
    """A record changed since the caller read it (or exists when it must not). Nothing was written."""

    def __init__(self, kind: str, rid: str, expected: int, actual: int):
        self.kind, self.id, self.expected, self.actual = kind, rid, expected, actual
        what = "already exists" if expected == 0 else (
            "no longer exists" if actual == 0 else "was changed by someone else")
        super().__init__(f"{kind} {rid} {what} (expected revision {expected}, found {actual}); "
                         "reload and try again")


class IdentityUnverifiable(WorkspaceError):
    """A txid was committed by an entry written before ADR-021, so it cannot be proven that a retry
    is the identical request. Nothing was written; ask ``transaction_outcome(txid)`` instead."""

    def __init__(self, txid: str, seq: int):
        self.txid, self.seq = txid, seq
        super().__init__(f"txid {txid} was committed as entry {seq} in a journal format that cannot prove "
                         "this retry is the identical request; nothing was written. Use "
                         "transaction_outcome() to learn its result.")


class TxidReuseError(WorkspaceError):
    """A transaction id was reused for different content; nothing was written."""


class OrphanClearConflict(WorkspaceError):
    """An administrative orphan clear for this orphan incarnation already exists under a different
    reason or administrator (ADR-019 rev. 3). Nothing new was authorised; the existing clear stands
    and is completed by the next acquisition or by its own administrator re-running it."""

    def __init__(self, existing: dict, outcome: str):
        self.existing, self.outcome = existing, outcome
        by = existing.get("by") or {}
        super().__init__(
            f"{existing['file']} is already being cleared as {existing['clear_id']} by "
            f"{by.get('user', '?')}@{by.get('host', '?')} (reason: {existing.get('reason')!r}; "
            f"state: {outcome}); your request was not recorded")


class LockReleaseBusyError(WorkspaceError):
    """A lock-release file (``.releasing-*`` / ``.fencing-*``) exists but could not be read: access was
    denied, as on Windows while another session is still releasing or deleting it, or while a virus
    scanner or backup tool holds it. Its writer cannot be fenced yet, so nobody may write and this
    open was refused (fail closed); unlike :class:`WorkspaceCorruptError` there is no evidence of
    damage, so the remedy is to retry, not an administrative clear. Nothing was written."""

    def __init__(self, name: str, cause: OSError):
        self.name = name
        super().__init__(
            f"lock-release file {name} could not be read ({cause.strerror or cause}); another session may "
            "be releasing the writer lock right now. Nothing was written: try again in a moment. If this "
            "persists, check file permissions and any program (virus scanner, backup) holding that file")


class LockLostError(WorkspaceError):
    """This handle's writer lock was broken or replaced; it must not write any more."""


class CommittedNotApplied(WorkspaceError):
    """The change IS durably committed (journal entry exists) but materialising it failed.

    It will be completed automatically on the next writable open. Callers must not retry it as if
    it had failed."""

    result: dict | None = None  # what the operation would have returned, if it has something worth keeping

    def __init__(self, seq: int, txid: str, cause: BaseException):
        self.seq, self.txid, self.cause = seq, txid, cause
        super().__init__(f"change {seq} (tx {txid}) is saved, but updating derived records failed: "
                         f"{cause}. It will be completed when the workspace is next opened for writing.")


REQUEST_IDENTITY_VERSION = 2
_REQUEST_DOMAIN = b"revisionworx/commit-request/v2\x00"


def journal_gaps(seqs: list[int]) -> list[tuple[int, int]]:
    """Missing numbers in a sorted journal as inclusive ranges. Work and memory follow the number of files, not the
    numbers in their names: one stray file named 00000000010000000000.json is ten billion missing entries."""
    gaps = []
    previous = 0
    for seq in seqs:
        if seq > previous + 1:
            gaps.append((previous + 1, seq - 1))
        previous = seq
    return gaps


def request_identity(op: str, ops: list, blob_refs: list[str] | None) -> str:
    """Versioned digest of the COMPLETE commit request (ADR-021).

    Operations keep their submitted order (reordering is a different request); every RecordPut,
    RecordDelete and RecordCheck is included with an explicit, nullable ``expect_rev``; RecordPut data
    has the derived ``_rev`` removed. JSON keys are sorted recursively, encoding is compact UTF-8,
    non-finite numbers are rejected, and the hash is domain-separated by version."""
    operations = []
    for o in ops:
        if isinstance(o, RecordPut):
            operations.append({"type": "put", "kind": o.kind, "id": o.id, "expect_rev": o.expect_rev,
                               "data": strip_rev(o.data)})
        elif isinstance(o, RecordDelete):
            operations.append({"type": "delete", "kind": o.kind, "id": o.id, "expect_rev": o.expect_rev})
        elif isinstance(o, RecordCheck):
            operations.append({"type": "check", "kind": o.kind, "id": o.id, "expect_rev": o.expect_rev})
        else:
            raise ValueError(f"unknown operation {o!r}")
    req = {"version": REQUEST_IDENTITY_VERSION, "op": op, "operations": operations,
           "blob_refs": list(blob_refs or [])}
    canon = json.dumps(req, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(_REQUEST_DOMAIN + canon.encode("utf-8")).hexdigest()


def entry_digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "digest"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


_KIND_RE = re.compile(r"^[a-z]{2,16}$")              # register_kind's rule; KINDS itself is not consulted, so
_BLOB_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")  # a reader that never imported a subsystem still validates


def _optional(entry: dict, key: str, types: tuple) -> bool:
    return key not in entry or entry[key] is None or (isinstance(entry[key], types)
                                                       and not isinstance(entry[key], bool))


_ORPHAN_OPS = ("workspace.orphan.clear", "workspace.orphan.clear.result")
_ORPHAN_FILE_RE = re.compile(r"^\.(releasing|fencing)-[0-9a-f]{1,64}$")
_ORPHAN_EVIDENCE_RE = re.compile(r"^writer\.lock\.orphaned-[0-9a-f]{1,64}$")
_QUARANTINE_RE = re.compile(r"^\.releasing-[0-9a-f]{1,64}$")
_HEX_ID_RE = re.compile(r"^[0-9a-f]{1,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _orphan_audit_problem(data: dict) -> str | None:
    """An orphan clear renames and writes files using names read from its journal entry. A digest is not a signature, so
    those names must be proved to stay inside locks/ before anything acts on them (rev. 2 audits have no clear_id)."""
    def bad(key: str, pattern: re.Pattern) -> bool:
        return not isinstance(data.get(key), str) or not pattern.match(data[key])
    event = data.get("event")
    if event not in ("orphaned-release-cleared", "orphaned-release-clear-result"):
        return None
    if bad("file", _ORPHAN_FILE_RE) or bad("evidence", _ORPHAN_EVIDENCE_RE):
        return "orphan clear names a file outside the lock folder's rules"
    if event == "orphaned-release-cleared":
        if "clear_id" in data and bad("clear_id", _HEX_ID_RE):
            return "orphan clear has an invalid clear_id"
        if bad("file_sha256", _SHA256_RE):
            return "orphan clear has an invalid file_sha256"
        return None
    if bad("clear_id", _HEX_ID_RE) or data.get("outcome") not in ("completed", "refused"):
        return "orphan clear result has an invalid clear_id or outcome"
    if data["outcome"] == "refused" and bad("quarantine", _QUARANTINE_RE):
        return "orphan clear result has an invalid quarantine name"
    return None


def _entry_shape_problem(entry: dict) -> str | None:
    """Describe why a parsed journal entry cannot be trusted structurally, or None if it can.

    Every field a reader, replay, fence or verify dereferences is checked here, so damage surfaces as
    WorkspaceCorruptError and never as AttributeError/TypeError deeper in. Optional fields stay optional
    (digestless and pre-ADR-021 legacy entries are still accepted); only their types are enforced.
    Purely structural: nothing is written."""
    for key in ("txid", "writer_token", "op", "time", "revokes", "prev", "signer", "sig", "history"):
        if not _optional(entry, key, (str,)):
            return f"{key} must be a string"
    for key in ("actor", "request"):
        if not _optional(entry, key, (dict,)):
            return f"{key} must be an object"
    records = entry.get("records")
    if not isinstance(records, list):
        return "records must be a list"
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            return f"record {i} is not an object"
        kind, rid = r.get("kind"), r.get("id")
        if not isinstance(kind, str) or not _KIND_RE.match(kind):
            return f"record {i} has an invalid kind"
        if not isinstance(rid, str) or not _ID_RE.match(rid):
            return f"record {i} has an invalid id"
        if "delete" in r and not isinstance(r["delete"], bool):
            return f"record {i}: delete must be true or false"
        if "expect_rev" in r and r["expect_rev"] is not None and (
                type(r["expect_rev"]) is not int or r["expect_rev"] < 0):
            return f"record {i}: expect_rev must be a non-negative integer"
        if not r.get("delete") and not isinstance(r.get("data"), dict):
            return f"record {i} is a put without an object of data"
        if entry.get("op") == "workspace.lock.break" and isinstance(r.get("data"), dict):
            holder = r["data"].get("previous_holder")
            if holder is not None and (not isinstance(holder, dict) or not _optional(holder, "token", (str,))):
                return f"record {i}: previous_holder must be an object with a string token"
        if entry.get("op") in _ORPHAN_OPS and isinstance(r.get("data"), dict):
            problem = _orphan_audit_problem(r["data"])
            if problem:
                return f"record {i}: {problem}"
    blobs = entry.get("blobs", [])
    if not isinstance(blobs, list):
        return "blobs must be a list"
    for ref in blobs:
        if not isinstance(ref, str) or ref.count("/") != 1:
            return "blob references must be 'kind/id' strings"
        bkind, bid = ref.split("/")
        if not _BLOB_KIND_RE.match(bkind) or not _ID_RE.match(bid) or bid in (".", ".."):
            return f"blob reference {ref!r} is invalid"
    checks = entry.get("checks", [])
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        return "checks must be a list of objects"
    return None


MAX_RECORD_NESTING = 64


def _nested_deeper_than(value: Any, limit: int) -> bool:
    """Whether a JSON value nests more than ``limit`` levels. Real records are shallow. Parsers differ in how deep they
    can go, so without a bound one version could publish an entry that another can never read, which stops every writer."""
    level, depth = [value], 0
    while level:
        depth += 1
        if depth > limit:
            return True
        level = [child for v in level if isinstance(v, (dict, list))
                 for child in (v.values() if isinstance(v, dict) else v)]
    return False


def strip_rev(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != REV_FIELD}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_shared_info(info: dict) -> bool:
    """Whether a workspace.json describes a workspace set up for a team (ADR-024)."""
    return info.get("collaboration") == "shared" and info.get("format_version") == SHARED_FORMAT_VERSION


def current_actor() -> dict:
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        user = "unknown"
    return {"user": user, "host": socket.gethostname(), "pid": os.getpid()}


@dataclass
class RecordPut:
    kind: str
    id: str
    data: dict
    expect_rev: int | None = None  # None: no check; 0: must not exist; n: must be at revision n

    def as_json(self) -> dict:
        d = {"kind": self.kind, "id": self.id, "data": strip_rev(self.data)}
        if self.expect_rev is not None:
            d["expect_rev"] = self.expect_rev
        return d


@dataclass
class RecordCheck:
    """Assert a record is still at ``expect_rev`` without writing it (not journaled)."""
    kind: str
    id: str
    expect_rev: int


@dataclass
class RecordDelete:
    kind: str
    id: str
    expect_rev: int | None = None

    def as_json(self) -> dict:
        d = {"kind": self.kind, "id": self.id, "delete": True}
        if self.expect_rev is not None:
            d["expect_rev"] = self.expect_rev
        return d


class Workspace:
    """Open handle on a workspace. Use :meth:`create` or :meth:`open`; close when done."""

    materialises = True                              # keeps record files and state.json; a team workspace doesn't (ADR-024)

    def __init__(self, meta_dir: Path, info: dict, writable: bool, lock_token: str | None):
        self.meta_dir = meta_dir
        self.info = info
        self.writable = writable
        self._lock_token = lock_token
        self._closed = False
        self._view_lock = threading.Lock()
        self._view: dict | None = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def id(self) -> str:
        return self.info["workspace_id"]

    @property
    def name(self) -> str:
        return self.info.get("name", "")

    def actor(self) -> dict:
        """Who is making a change through this handle."""
        return current_actor()

    def owns_checkout(self, checkout: dict) -> bool:
        """Whether a checkout is this user's without needing a token held on this computer. Only accounts can say so."""
        return False

    def quick_check(self) -> dict:
        """The check a window repeats while it's open, in the shape of :meth:`verify`."""
        return self.verify()

    def fenced_reason(self, entry: dict) -> str:
        return "was published after its writer lost the lock, so it never took effect. This is fencing having worked"

    @staticmethod
    def meta_dir_for(location: Path) -> Path:
        location = Path(location)
        return location if location.name == META_DIRNAME else location / META_DIRNAME

    @classmethod
    def create(cls, location: Path, name: str) -> "Workspace":
        meta = cls.meta_dir_for(location)
        exists = WorkspaceError(f"{meta} already exists; open it instead of creating a new workspace")
        meta.parent.mkdir(parents=True, exist_ok=True)
        try:
            meta.mkdir()
        except FileExistsError as exc:
            if not cls._is_interrupted_create(meta):
                raise exists from exc
        for sub in cls._SKELETON:
            (meta / sub).mkdir(exist_ok=True)
        atomicio._fsync_dir(meta.parent)
        atomicio._fsync_dir(meta)
        info = {"format": FORMAT_NAME, "format_version": FORMAT_VERSION,
                "workspace_id": new_id("ws"), "name": name, "created": now_iso(),
                "created_by": current_actor()}
        try:
            atomicio.publish_exclusive(meta / "workspace.json", atomicio.dumps_json(info))  # the commit point
        except ExclusiveCreateError as exc:
            raise exists from exc
        atomicio.atomic_write_json(meta / "state.json", {"applied_seq": 0})
        ws = cls.open(meta, writable=True)
        try:
            ws.commit("workspace.create", [RecordPut("settings", "workspace", default_settings())])
        except BaseException:
            ws._close_keeping_the_real_error()
            raise
        return ws

    _SKELETON = ("journal", "records", "blobs", "locks")

    @classmethod
    def _is_interrupted_create(cls, meta: Path) -> bool:
        """A create that died before publishing workspace.json leaves nothing but empty folders and temp
        files: nothing to lose, and not openable either. Anything else is not ours to take over."""
        if (meta / "workspace.json").exists():
            return False
        for child in meta.iterdir():
            if child.name.endswith(".tmp"):
                continue
            if child.name not in cls._SKELETON or not child.is_dir() or any(child.iterdir()):
                return False
        return True

    @classmethod
    def open(cls, location: Path, writable: bool = False, **team_options) -> "Workspace":
        """``team_options`` (``session``, ``state_dir``, ``wait``) only mean something in a workspace set up for a team."""
        meta = cls.meta_dir_for(location)
        info_path = meta / "workspace.json"
        if not info_path.is_file():
            raise WorkspaceError(f"no RevisionWorx workspace at {meta}")
        try:
            info = atomicio.read_json(info_path)
            if not isinstance(info, dict):
                raise ValueError("not an object")
        except ValueError as exc:
            raise WorkspaceFormatError(f"{info_path} cannot be read: {exc}") from exc
        if info.get("format") != FORMAT_NAME:
            raise WorkspaceFormatError(f"{info_path} is not a RevisionWorx workspace")
        if is_shared_info(info):
            from .shared import SharedWorkspace
            return SharedWorkspace.attach(meta, info, writable, **team_options)
        if int(info.get("format_version", 0)) > FORMAT_VERSION:
            raise WorkspaceFormatError(
                f"workspace format {info.get('format_version')} is newer than this RevisionWorx "
                f"supports ({FORMAT_VERSION}); update RevisionWorx. Nothing was changed.")
        token = None
        if writable:
            token = cls._acquire_writer_lock(meta)
        ws = cls(meta, info, writable, token)
        try:
            ws._check_journal()
            if writable:
                ws.recover()
        except BaseException:
            ws._close_keeping_the_real_error()
            raise
        return ws

    def close(self) -> None:
        """Release the writer lock. If the filesystem refuses, this raises and the lock stays held, as it must. Calling
        close() again retries, and the next open of this workspace in this process retries too, so a disk that filled for a
        moment does not need an administrator. Only a process that ends first leaves that to one."""
        self._closed = True
        if self._lock_token:
            lock = self.meta_dir / "locks" / "writer.lock"
            try:
                ours = self._read_holder(lock)["token"] == self._lock_token
            except (FileNotFoundError, ValueError):
                ours = False
            except OSError:
                ours = True   # could not look (issue #10). Release is move-then-verify, so trying is safe.
            if ours:
                try:
                    self._unlink_own_lock(lock)
                except (OSError, WorkspaceError):
                    Workspace._unreleased.add((str(self.meta_dir), self._lock_token))
                    raise
            Workspace._unreleased.discard((str(self.meta_dir), self._lock_token))
            self._lock_token = None

    def _close_keeping_the_real_error(self) -> None:
        try:
            self.close()
        except (OSError, WorkspaceError):
            pass                                     # remembered in _unreleased; the caller is already handling a failure

    _unreleased: set[tuple[str, str]] = set()        # (meta folder, token) this process could not release yet

    @staticmethod
    def _retry_unreleased(meta: Path) -> None:
        """Locks this process still owes a release, from a moment when the disk refused. Each is this process's own token
        and nobody can be writing with it: its handle is closed or was never returned."""
        for folder, token in list(Workspace._unreleased):
            if folder == str(meta):
                try:
                    Workspace(meta, {"workspace_id": "release"}, False, token).close()
                except (OSError, WorkspaceError):
                    pass

    LOCK_RELEASE_ATTEMPTS = atomicio.SHARING_RETRIES
    LOCK_RELEASE_DELAY = 0.02

    def _unlink_own_lock(self, lock: Path) -> None:
        """Release our writer lock without ever removing a successor's (ADR-019).

        There is no portable "delete if content matches" primitive, so a check followed by a pathname
        unlink can delete a lock that replaced ours in between. Instead:
          1. if the journal shows our token revoked, never touch writer.lock (someone else owns it);
          2. atomically ``rename`` whatever is at writer.lock to a private name, then inspect THAT
             file: it is exactly what we removed;
          3. ours -> delete it; someone else's -> put it back with an exclusive create;
          4. if a third writer took the name in between, fail closed: the displaced file stays in the
             scanned namespace until its token's revocation (ADR-015) is durable, and only then
             becomes evidence (ADR-019 rev. 4), so a crash at any point leaves the obligation behind.
        Windows sharing violations (ADR-016) are retried with a bound."""
        import time
        last: OSError | None = None
        for _ in range(self.LOCK_RELEASE_ATTEMPTS):
            try:
                if self._lock_token in {t for _, t in self._revocations()}:
                    return
            except WorkspaceError:
                pass  # unreadable journal: still release our own lock; step 2 alone is safe
            private = lock.with_name(f".releasing-{uuid.uuid4().hex}")
            try:
                os.replace(lock, private)
            except FileNotFoundError:
                return
            except OSError as exc:  # WinError 32: another process has it open; or the disk refused for a moment
                last = exc
                time.sleep(self.LOCK_RELEASE_DELAY)
                continue
            try:
                holder = self._read_holder(private)
            except (OSError, ValueError):
                holder = {}
            if holder.get("token") == self._lock_token:
                try:
                    atomicio.retry_sharing(lambda: private.unlink(missing_ok=True))
                except OSError:
                    pass  # released all the same: writer.lock is free, and the next scan clears this file
                return
            self._restore_foreign_lock(private, lock, holder)
            return
        raise WorkspaceError(f"could not release the writer lock after {self.LOCK_RELEASE_ATTEMPTS} attempts "
                             f"({last}); it is still held by this session. An administrator can break it.")

    def _restore_foreign_lock(self, private: Path, lock: Path, holder: dict) -> None:
        tok = holder.get("token")
        try:
            already_revoked = bool(tok) and tok in Workspace._revoked_tokens(self.meta_dir)
        except WorkspaceError:
            already_revoked = False              # unreadable journal: restoring is still the safe move
        if not already_revoked:
            try:
                atomicio.move_exclusive(private, lock)
                return
            except FileNotFoundError:
                return                           # a concurrent acquirer fenced it and took custody
            except FileExistsError:
                pass
        if not tok:
            return                               # unreadable: it stays a blocking .releasing-* orphan
        evidence = f"writer.lock.displaced-{uuid.uuid4().hex[:8]}"
        Workspace._fence_token_orphan(
            self.meta_dir, private, planned_evidence={tok: evidence},
            reason="lock displaced during a concurrent release; revoked to fail closed (ADR-019)")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ locking
    LOCK_ACQUIRE_ATTEMPTS = 20

    @staticmethod
    def _acquire_writer_lock(meta: Path) -> str:
        lock = meta / "locks" / "writer.lock"
        existing: dict = {"user": "?", "host": "?", "pid": "?", "acquired": "?", "token": "?"}
        Workspace._retry_unreleased(meta)
        for _ in range(Workspace.LOCK_ACQUIRE_ATTEMPTS):
            token = uuid.uuid4().hex
            holder = {**current_actor(), "token": token, "acquired": now_iso()}
            try:
                atomicio.publish_exclusive(lock, atomicio.dumps_json(holder))
            except ExclusiveCreateError:
                try:
                    existing = Workspace._read_holder(lock)
                except FileNotFoundError:
                    continue                     # the holder just left: the name is free, try again
                except (OSError, ValueError):
                    existing = {"user": "?", "host": "?", "pid": "?", "acquired": "?", "token": "?"}
                stale = existing.get("token")
                if stale not in Workspace._revoked_tokens(meta):
                    raise WorkspaceLockedError(existing) from None
                # Only permission to look: what gets moved may no longer be this file (ADR-019 rev. 4).
                try:
                    Workspace._displace_writer_lock(
                        meta, {stale: f"writer.lock.revoked-{uuid.uuid4().hex[:8]}"},
                        "lock displaced while clearing a revoked lock; revoked to fail closed (ADR-019)")
                except PermissionError:
                    raise WorkspaceLockedError(existing) from None
                continue
            except OSError:
                # It can fail after the name was taken (the folder sync). Nobody will ever use this token.
                Workspace._give_back(meta, token)
                raise
            Workspace._fence_orphaned_releases(meta, token)
            return token
        raise WorkspaceLockedError(existing)

    @staticmethod
    def _displace_writer_lock(meta: Path, planned_evidence: dict[str, str], reason: str) -> None:
        """Vacate writer.lock on behalf of someone other than its owner (ADR-019 rev. 4).

        The caller decided from an earlier read, and an ordinary acquirer can replace the file before
        the rename, so that read proves nothing about the object moved. The file therefore moves to
        a scanned ``.releasing-*`` name, is inspected there, and has its actual token revoked before
        it stops blocking. ``planned_evidence`` names the archive for tokens the caller expected."""
        import time
        locks = meta / "locks"
        custody = locks / f".releasing-{uuid.uuid4().hex}"
        for attempt in range(Workspace.LOCK_RELEASE_ATTEMPTS):
            try:
                os.replace(locks / "writer.lock", custody)
                break
            except FileNotFoundError:
                return
            except PermissionError:              # WinError 32: another process has it open (ADR-016)
                if attempt == Workspace.LOCK_RELEASE_ATTEMPTS - 1:
                    raise
                time.sleep(Workspace.LOCK_RELEASE_DELAY)
        Workspace._fence_token_orphan(meta, custody, planned_evidence=planned_evidence, reason=reason)

    @staticmethod
    def _fence_orphaned_releases(meta: Path, own_token: str) -> None:
        """A releaser that crashed after moving writer.lock aside (ADR-019 step 2) may have taken a
        successor's lock with it. Before the new holder writes anything, revoke every token found in
        such orphaned files (fail closed) and keep them as evidence.

        Every file is first moved to a private custody name and only the moved file is inspected
        (move-then-verify, ADR-019 rev. 3): a file swapped in at the pathname after inspection can
        never be archived in place of the one whose token was revoked."""
        locks = meta / "locks"
        def fence_all() -> None:
            for orphan in sorted(locks.glob(".fencing-*")) + sorted(locks.glob(".releasing-*")):
                try:
                    holder = Workspace._read_holder(orphan)
                except FileNotFoundError:
                    continue
                except PermissionError as exc:
                    # Access denied after the bounded sharing retries (ADR-016). A live releaser's
                    # file is delete-pending on Windows until its unlink completes. Gone now: its
                    # releaser (or a competing fencer, whose custody file is scanned again below)
                    # took it. Still there: not damaged, but not fenceable yet either (ADR-019 rev. 5).
                    if not Workspace._listed(orphan):
                        continue
                    raise LockReleaseBusyError(orphan.name, exc) from exc
                except (OSError, ValueError):
                    holder = {}
                if not holder.get("token"):
                    raise WorkspaceCorruptError(
                        f"orphaned lock-release file {orphan.name} is unreadable, so its writer cannot "
                        "be revoked; nobody may write until an administrator confirms that writer is "
                        "stopped and runs 'revisionworx lock clear-orphan'")
                Workspace._fence_token_orphan(meta, orphan)
        try:
            Workspace._settle_orphan_clears(meta)
            # An orphan only ever moves .releasing -> .clearing -> (evidence | .releasing quarantine).
            # Scanning releasing, clearing, releasing again therefore sees every such file at least
            # once even while a concurrent administrator is moving it.
            fence_all()
            busy = sorted(locks.glob(".clearing-*"))
            if busy:
                raise WorkspaceCorruptError(
                    f"orphaned lock-release file {busy[0].name} is being cleared by an administrator; "
                    "retry when 'revisionworx lock clear-orphan' has finished")
            fence_all()
        except BaseException:
            # We cannot establish that every displaced lock is fenced: refuse to become a writer
            # (fail closed) and give our just-acquired lock back.
            Workspace._give_back(meta, own_token)
            raise

    @staticmethod
    def _give_back(meta: Path, token: str) -> None:
        """Release a lock this process took but will not use, without hiding the failure that led here."""
        Workspace(meta, {"workspace_id": "release"}, False, token)._close_keeping_the_real_error()

    @staticmethod
    def _fence_token_orphan(meta: Path, orphan: Path, planned_evidence: dict[str, str] | None = None,
                            reason: str = "lock orphaned by an interrupted release; revoked to fail "
                                          "closed (ADR-019)") -> None:
        """Revoke the token in ``orphan``, then archive it. This is the only way a token-bearing lock
        file leaves the scanned namespace: revocation first, so a crash anywhere leaves a blocking
        file for the next acquirer to finish (ADR-019 rev. 4)."""
        locks = meta / "locks"
        if orphan.name.startswith(".fencing-"):
            custody = orphan                         # resumed after a crash in this very step
        else:
            custody = locks / f".fencing-{uuid.uuid4().hex}"
            try:
                atomicio.retry_sharing(lambda: os.replace(orphan, custody))
            except FileNotFoundError:
                return                               # the releaser finished, or another fencer has it
        busy: PermissionError | None = None
        try:
            holder = Workspace._read_holder(custody)
        except FileNotFoundError:
            return                                   # a concurrent fencer finished it
        except PermissionError as exc:
            holder, busy = {}, exc                   # not readable now: no evidence of damage
        except (OSError, ValueError):
            holder = {}
        tok = holder.get("token")
        if busy is not None:
            # Put it back where every acquirer scans it, bytes unchanged; it keeps blocking.
            quarantine = locks / f".releasing-{uuid.uuid4().hex}"
            atomicio.retry_sharing(lambda: os.replace(custody, quarantine))
            raise LockReleaseBusyError(quarantine.name, busy) from busy
        if not tok:
            # What we took custody of is not what we inspected: keep it as a blocking orphan.
            quarantine = locks / f".releasing-{uuid.uuid4().hex}"
            os.replace(custody, quarantine)
            raise WorkspaceCorruptError(
                f"orphaned lock-release file {quarantine.name} is unreadable, so its writer cannot be revoked; nobody "
                "may write until an administrator confirms that writer is stopped and runs "
                f"'revisionworx lock clear-orphan {quarantine.name}'")
        evidence = (planned_evidence or {}).get(tok) or f"writer.lock.orphaned-{uuid.uuid4().hex[:8]}"
        if tok not in Workspace._revoked_tokens(meta):
            Workspace._publish_revocation(meta, holder, reason=reason, evidence=evidence)
        try:
            atomicio.retry_sharing(lambda: os.replace(custody, locks / evidence))
        except (FileNotFoundError, PermissionError):
            pass                                     # finished by a competitor, or a later scan will

    @staticmethod
    def _listed(path: Path) -> bool:
        """Whether ``path``'s name is still in its folder. Uses the directory listing, not a stat or
        open of the file itself, which a delete-pending file on Windows also refuses."""
        try:
            return any(p.name == path.name for p in path.parent.iterdir())
        except FileNotFoundError:
            return False

    @staticmethod
    def _revoked_tokens(meta: Path) -> set[str]:
        probe = Workspace(meta, {"workspace_id": "probe"}, False, None)
        return {tok for _, tok in probe._revocations()}

    @staticmethod
    def _read_holder(path: Path) -> dict:
        """What a lock file says, or ValueError if it cannot name a writer: not JSON, not an object, or no token that could
        be revoked. Every reader of a lock file comes through here, so all of them agree on what damage is."""
        holder = atomicio.read_json_object(path)
        if not isinstance(holder.get("token"), str) or not holder["token"]:
            raise ValueError(f"{path.name} has no usable token")
        return holder

    @staticmethod
    def _is_shared_folder(meta: Path) -> bool:
        try:
            return is_shared_info(atomicio.read_json_object(meta / "workspace.json"))
        except (OSError, ValueError):
            return False

    @classmethod
    def read_writer_lock(cls, location: Path) -> dict | None:
        lock = cls.meta_dir_for(location) / "locks" / "writer.lock"
        try:
            return cls._read_holder(lock)
        except FileNotFoundError:
            return None
        except ValueError:
            return {"user": "?", "host": "?", "pid": "?", "acquired": "?", "token": "?", "unreadable": True}

    # ADR-019 rev. 3: one administrative clear per orphan instance, bound to the bytes it archives.
    ORPHAN_AUTH_OP = "workspace.orphan.clear"
    ORPHAN_RESULT_OP = "workspace.orphan.clear.result"

    @classmethod
    def clear_orphaned_release(cls, location: Path, name: str, reason: str) -> dict:
        """Administrative resolution of an unreadable orphaned ``.releasing-*`` file (ADR-019, rev. 3).

        Use only after confirming the writer it may have belonged to is stopped: its token is unknown,
        so it cannot be fenced.

          1. **Authorise** (serialized): publish ``workspace.orphan.clear`` with the reason, file name,
             SHA-256 of the bytes the administrator inspected and the planned evidence name, as a
             compare-and-swap on the journal head. If an authorisation for this incarnation already
             exists, an identical request (same reason, same user@host) resumes it idempotently and
             any other request raises :class:`OrphanClearConflict`: there is never more than one
             authorisation per orphan incarnation.
          2. **Take custody**: atomically rename the orphan to a private ``.clearing-<clear_id>-*``
             name. Whatever is now there is exactly what was moved; it keeps blocking writers.
          3. **Verify and archive**: read the custody file once; if its SHA-256 equals the audited
             digest, publish the evidence file from *those verified bytes* (exclusive create), so the
             archived evidence equals the audit by construction.
          4. **Terminal result** (exactly once per clear): ``completed``, or ``refused`` with the
             observed digest and a fresh quarantine name, to which mismatched bytes are moved. A
             quarantined file is a new blocking orphan that needs its own audited decision.
          5. Dispose of the custody file only if it still holds the audited bytes; otherwise it is
             quarantined (blocking) rather than lost.

        Every step is idempotent and is resumed by the next acquisition or by re-running this
        command. Returns ``{clear_id, outcome, adopted, evidence, quarantine}``."""
        if not reason.strip():
            raise WorkspaceError("a reason is required")
        meta = cls.meta_dir_for(location)
        if cls._is_shared_folder(meta):
            raise WorkspaceError("a workspace set up for a team has no orphaned lock files to clear: a stuck lock clears "
                                 "itself once its computer has shown no sign of life for a while")
        if not re.match(r"^\.(releasing|fencing)-[0-9a-f]{1,64}$", name):
            raise WorkspaceError(f"{name!r} is not an orphaned release file")
        auth, adopted = cls._authorise_orphan_clear(meta, name, reason)
        me, by = current_actor(), auth.get("by") or {}
        if adopted and (auth.get("reason") != reason or (by.get("user"), by.get("host"))
                        != (me["user"], me["host"])):
            # A different request for the same incarnation: never a second authorisation and never a
            # silent merge. The existing one is still driven by acquisition / its own administrator.
            result = cls._orphan_clear_state(meta)[1].get(auth["clear_id"])
            raise OrphanClearConflict(auth, result["outcome"] if result else "pending")
        result = cls._drive_orphan_clear(meta, auth)
        return {"clear_id": auth["clear_id"], "adopted": adopted, "outcome": result["outcome"],
                "evidence": auth["evidence"], "quarantine": result.get("quarantine")}

    @staticmethod
    def _clear_id(auth: dict) -> str:
        # rev. 2 audits have no clear_id; their evidence name carries a unique hex suffix
        return auth.get("clear_id") or auth["evidence"].rsplit("-", 1)[1]

    @classmethod
    def _orphan_clear_state(cls, meta: Path, probe: "Workspace | None" = None,
                            upto: int | None = None) -> tuple[list[dict], dict[str, dict]]:
        """Authorisations (journal order) and first terminal result per clear_id, read straight from
        the journal (authoritative), up to ``upto``."""
        probe = probe or cls(meta, {"workspace_id": "probe"}, False, None)
        auths: list[dict] = []
        results: dict[str, dict] = {}
        for s in probe.journal_seqs():
            if upto is not None and s > upto:
                break
            e = probe.read_entry(s)
            if e.get("op") not in (cls.ORPHAN_AUTH_OP, cls.ORPHAN_RESULT_OP):
                continue
            for item in e["records"]:
                d = item.get("data") or {}
                if d.get("event") == "orphaned-release-cleared":
                    auths.append({**d, "clear_id": cls._clear_id(d), "seq": s})
                elif d.get("event") == "orphaned-release-clear-result":
                    results.setdefault(d["clear_id"], {**d, "seq": s})
        return auths, results

    @classmethod
    def _authorise_orphan_clear(cls, meta: Path, name: str, reason: str) -> tuple[dict, bool]:
        """Return the single open authorisation for ``name``, publishing one only if none exists.

        Serialization: the decision is taken against journal head H and published exactly at H+1 by
        exclusive create. If anyone published anything after H, the publish fails and the decision
        is retaken, so two administrators can never both authorise the same orphan instance."""
        probe = cls(meta, atomicio.read_json(meta / "workspace.json"), False, None)
        src = meta / "locks" / name
        for _attempt in range(1000):
            probe._check_journal()
            head = probe.head_seq()
            auths, results = cls._orphan_clear_state(meta, probe, upto=head)
            mine = [a for a in auths if a["file"] == name]
            open_ = [a for a in mine if a["clear_id"] not in results]
            if open_:
                return open_[0], True
            try:
                data = src.read_bytes()
            except FileNotFoundError:
                if probe.head_seq() != head:
                    continue                        # a competitor authorised and moved it: re-decide
                if mine:
                    last = mine[-1]
                    res = results[last["clear_id"]]
                    if res["outcome"] == "refused":
                        raise WorkspaceError(
                            f"{name} was not cleared: its bytes did not match the audited digest and it "
                            f"was quarantined as {res['quarantine']}; inspect and clear that file") from None
                    return last, True               # idempotent retry after completion
                raise WorkspaceError(f"{name} does not exist") from None
            clear_id = uuid.uuid4().hex
            auth = {"event": "orphaned-release-cleared", "clear_id": clear_id, "file": name,
                    "file_sha256": hashlib.sha256(data).hexdigest(),
                    "evidence": f"writer.lock.orphaned-{clear_id}", "reason": reason,
                    "time": now_iso(), "by": current_actor()}
            try:
                seq = cls._publish_admin_entry(meta, cls.ORPHAN_AUTH_OP, auth, expect_head=head)
            except ExclusiveCreateError:
                continue                            # someone published after our decision: retake it
            return {**auth, "seq": seq}, False
        raise WorkspaceError("could not authorise the orphan clear (journal contention)")

    @classmethod
    def _publish_result_once(cls, meta: Path, auth: dict, result: dict) -> dict:
        """Publish the terminal result for ``auth`` unless one exists (CAS on the journal head).
        Returns the result that is in force, which may be a competitor's."""
        probe = cls(meta, atomicio.read_json(meta / "workspace.json"), False, None)
        for _attempt in range(1000):
            probe._check_journal()
            head = probe.head_seq()
            existing = cls._orphan_clear_state(meta, probe, upto=head)[1].get(auth["clear_id"])
            if existing is not None:
                return existing
            data = {"event": "orphaned-release-clear-result", "clear_id": auth["clear_id"],
                    "file": auth["file"], "evidence": auth["evidence"], **result,
                    "time": now_iso(), "by": current_actor()}
            try:
                cls._publish_admin_entry(meta, cls.ORPHAN_RESULT_OP, data, expect_head=head)
                return data
            except ExclusiveCreateError:
                continue
        raise WorkspaceError("could not record the orphan clear result (journal contention)")

    @classmethod
    def _settle_orphan_clears(cls, meta: Path) -> None:
        """Drive every open authorisation and every custody file to its terminal disposition.
        Called by acquisition before the barrier check; idempotent and safe to run concurrently."""
        locks = meta / "locks"
        custody = sorted(locks.glob(".clearing-*"))
        if not custody and not any(locks.glob(".releasing-*")):
            return                                   # common case: nothing to settle, no journal scan
        auths, results = cls._orphan_clear_state(meta)
        by_id = {a["clear_id"]: a for a in auths}
        for c in custody:
            cid = c.name[len(".clearing-"):].rsplit("-", 1)[0]
            if cid not in by_id:   # custody without an authorisation: never ours to archive
                try:
                    os.replace(c, locks / f".releasing-{uuid.uuid4().hex}")
                except FileNotFoundError:
                    pass
        for a in auths:
            in_custody = any(p.name.startswith(f".clearing-{a['clear_id']}-") for p in custody)
            if in_custody or (a["clear_id"] not in results and (locks / a["file"]).exists()):
                cls._drive_orphan_clear(meta, a)

    @classmethod
    def _drive_orphan_clear(cls, meta: Path, auth: dict) -> dict:
        """Steps 2-5 of an authorised clear (see clear_orphaned_release). Idempotent."""
        locks = meta / "locks"
        cid, want = auth["clear_id"], auth["file_sha256"]
        evidence = locks / auth["evidence"]
        result = cls._orphan_clear_state(meta)[1].get(cid)
        if result is None:                           # step 2: custody (never clobbers: fresh name)
            try:
                os.replace(locks / auth["file"], locks / f".clearing-{cid}-{uuid.uuid4().hex[:12]}")
            except FileNotFoundError:
                pass                                 # already in custody, or taken by a competitor
        for c in sorted(locks.glob(f".clearing-{cid}-*")):
            try:
                data = c.read_bytes()                # step 3: the bytes we judge are the bytes we keep
            except FileNotFoundError:
                continue
            got = hashlib.sha256(data).hexdigest()
            if got == want and (result is None or result["outcome"] == "completed"):
                try:
                    atomicio.publish_exclusive(evidence, data)
                except ExclusiveCreateError:
                    if hashlib.sha256(evidence.read_bytes()).hexdigest() != want:
                        raise WorkspaceCorruptError(
                            f"evidence {evidence.name} exists with bytes that differ from its audit; "
                            "writes refused") from None
                if result is None:
                    result = cls._publish_result_once(meta, auth, {"outcome": "completed"})
                if result["outcome"] == "completed":
                    cls._dispose_custody(c, want)
                    continue
            if result is None:
                result = cls._publish_result_once(meta, auth, {
                    "outcome": "refused", "observed_sha256": got,
                    "quarantine": f".releasing-{uuid.uuid4().hex}"})
            target = result.get("quarantine") if (result["outcome"] == "refused"
                                                  and result.get("observed_sha256") == got) else None
            cls._quarantine(c, locks / (target or f".releasing-{uuid.uuid4().hex}"))
        if result is None and not (locks / auth["file"]).exists() and evidence.exists():
            if hashlib.sha256(evidence.read_bytes()).hexdigest() == want:
                result = cls._publish_result_once(meta, auth, {"outcome": "completed"})
        return result or {"outcome": "pending"}

    @staticmethod
    def _dispose_custody(custody: Path, want: str) -> None:
        """The custody file duplicates verified evidence. Remove it only while it still holds those
        bytes; anything else found there is quarantined as a blocking orphan, never discarded."""
        try:
            same = hashlib.sha256(custody.read_bytes()).hexdigest() == want
        except FileNotFoundError:
            return
        if same:
            custody.unlink(missing_ok=True)
        else:
            Workspace._quarantine(custody, custody.with_name(f".releasing-{uuid.uuid4().hex}"))

    @staticmethod
    def _quarantine(src: Path, dst: Path) -> None:
        try:
            atomicio.move_exclusive(src, dst)
        except FileNotFoundError:
            return                                   # a concurrent completer moved it
        except FileExistsError:
            os.replace(src, dst.with_name(f".releasing-{uuid.uuid4().hex}"))

    @classmethod
    def _publish_admin_entry(cls, meta: Path, op: str, audit_data: dict, revokes: str | None = None,
                             expect_head: int | None = None) -> int:
        """Publish an administrative journal entry. With ``expect_head`` it is a compare-and-swap:
        published exactly at expect_head + 1 or not at all (ExclusiveCreateError)."""
        probe = cls(meta, atomicio.read_json(meta / "workspace.json"), False, None)
        probe._check_journal()
        audit = RecordPut("audit", new_id("aud"), audit_data, expect_rev=0)
        for _attempt in range(1000):
            seq = probe.head_seq() + 1 if expect_head is None else expect_head + 1
            entry = {"seq": seq, "txid": uuid.uuid4().hex, "time": now_iso(), "actor": current_actor(),
                     "op": op, "records": [audit.as_json()], "blobs": [], "writer_token": None}
            if revokes:
                entry["revokes"] = revokes
            entry["digest"] = entry_digest(entry)
            try:
                atomicio.publish_exclusive(meta / "journal" / f"{seq:020d}.json", atomicio.dumps_json(entry))
                return seq
            except ExclusiveCreateError:
                if expect_head is not None:
                    raise
                continue
        raise WorkspaceError("could not publish an administrative journal entry (journal contention)")

    @classmethod
    def _publish_revocation(cls, meta: Path, holder: dict, reason: str, evidence: str) -> int:
        """Publish a revocation of ``holder['token']`` as the next journal entry (ADR-015 rev. 2).

        Revocation takes effect at that sequence: exclusive publication orders it against any
        concurrent publication by the revoked writer, whose later entries are fenced everywhere."""
        probe = cls(meta, atomicio.read_json(meta / "workspace.json"), False, None)
        probe._check_journal()
        audit = RecordPut("audit", new_id("aud"), {
            "event": "writer-lock-broken", "previous_holder": holder, "reason": reason,
            "evidence": evidence, "time": now_iso(), "by": current_actor()}, expect_rev=0)
        for _attempt in range(1000):
            seq = probe.head_seq() + 1
            entry = {"seq": seq, "txid": uuid.uuid4().hex, "time": now_iso(), "actor": current_actor(),
                     "op": "workspace.lock.break", "records": [audit.as_json()], "blobs": [],
                     "writer_token": None, "revokes": holder.get("token")}
            entry["digest"] = entry_digest(entry)
            try:
                atomicio.publish_exclusive(meta / "journal" / f"{seq:020d}.json", atomicio.dumps_json(entry))
                return seq
            except ExclusiveCreateError:
                continue
        raise WorkspaceError("could not publish the lock revocation (journal contention)")

    @classmethod
    def break_writer_lock(cls, location: Path, expected_token: str, reason: str, **team_options) -> dict:
        """Administrative recovery of an abandoned writer lock.

        The caller must pass the exact token currently in the lock (as displayed to the
        administrator), so a lock that was released and re-acquired by someone else in the meantime
        is never broken by mistake. The break is recorded in the journal.
        """
        if not reason.strip():
            raise WorkspaceError("a reason is required to break a writer lock")
        meta = cls.meta_dir_for(location)
        if cls._is_shared_folder(meta):
            from .shared import SharedWorkspace
            return SharedWorkspace.break_lock(meta, expected_token, reason, **team_options)
        holder = cls.read_writer_lock(meta)
        if holder is None:
            raise WorkspaceError("there is no writer lock to break")
        if holder.get("token") != expected_token:
            raise WorkspaceError("the writer lock changed since it was inspected; inspect it again")
        if holder.get("unreadable"):
            # Its writer cannot be revoked by name, so this cannot unblock anyone by itself. It turns the file into an
            # orphan, which needs the audited clear. The scan refuses all writers until then.
            cls._displace_writer_lock(meta, {}, reason)
            return holder
        broken = f"writer.lock.broken-{uuid.uuid4().hex[:8]}"
        cls._publish_revocation(meta, holder, reason=reason, evidence=broken)
        # Archiving is optional (acquisition also clears a revoked lock). This check only avoids
        # needless displacement; a successor moved after it is fenced, never silently archived.
        current = cls.read_writer_lock(meta)
        if current and current.get("token") == expected_token:
            try:
                cls._displace_writer_lock(
                    meta, {expected_token: broken},
                    "lock displaced by an administrative break of an earlier writer; revoked to fail "
                    "closed (ADR-019)")
            except OSError:
                pass                                 # the revocation above is what counts
        return holder

    # ------------------------------------------------------------------ journal
    def _journal_dir(self) -> Path:
        return self.meta_dir / "journal"

    def journal_seqs(self) -> list[int]:
        seqs = []
        for entry in os.scandir(self._journal_dir()):
            m = _SEQ_RE.match(entry.name)
            if m:
                seqs.append(int(m.group(1)))
        seqs.sort()
        return seqs

    def _check_journal(self) -> None:
        seqs = self.journal_seqs()
        if seqs and seqs[0] < 1:
            raise WorkspaceCorruptError("the journal has an entry numbered 0, and numbering starts at 1; writes refused")
        gaps = journal_gaps(seqs)
        if gaps:
            first, last = gaps[0]
            more = f" and {len(gaps) - 1} more gap(s)" if len(gaps) > 1 else ""
            raise WorkspaceCorruptError(f"journal has gaps (missing entries {first} to {last}{more}); writes refused")

    def head_seq(self) -> int:
        seqs = self.journal_seqs()
        return seqs[-1] if seqs else 0

    def read_entry(self, seq: int) -> dict:
        path = self._journal_dir() / f"{seq:020d}.json"
        try:
            entry = atomicio.read_json(path)
        except ValueError as exc:
            raise WorkspaceCorruptError(f"journal entry {seq} is unreadable: {exc}") from exc
        if not isinstance(entry, dict):
            raise WorkspaceCorruptError(f"journal entry {seq} is malformed: not a JSON object")
        claimed = entry.get("seq")
        if type(claimed) is not int or claimed != seq:   # bool is an int subclass: True must not pass as 1
            raise WorkspaceCorruptError(f"journal entry {path.name} claims seq {claimed!r}")
        if "digest" in entry:
            if not isinstance(entry["digest"], str):
                raise WorkspaceCorruptError(f"journal entry {seq} is malformed: digest is not a string")
            try:
                digest = entry_digest(entry)
            except RecursionError:                   # parsed, but too deep to encode from this point in the stack
                raise WorkspaceCorruptError(f"journal entry {seq} is malformed: nested too deeply") from None
            if entry["digest"] != digest:
                raise WorkspaceCorruptError(f"journal entry {seq} fails its SHA-256 digest check (altered?)")
        problem = _entry_shape_problem(entry)
        if problem:
            raise WorkspaceCorruptError(f"journal entry {seq} is malformed: {problem}")
        return entry

    def _entry_stat(self, seq: int) -> tuple | None:
        """What the file system says about entry ``seq``'s file, or None if there is no such file. Size, modification
        time and file id: publishing an entry, or a restore putting another one in its place, changes at least one of them.
        Other errors propagate."""
        try:
            st = os.stat(self._journal_dir() / f"{seq:020d}.json")
        except FileNotFoundError:
            return None
        return (st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev)

    def _nothing_new(self, view: dict) -> bool:
        """True when the journal is exactly as the view last found it: nothing published after it, and its last entry is the
        very file that was checked. Two lookups by name, however long the journal or large that entry. Entries are
        published whole, one after another (``publish_exclusive``), so anything new starts at ``seen + 1``. Like the
        team replica's sync, this doesn't list the folder; a gap or damage further back is found where the journal is
        listed: opening, every commit, ``health``, ``verify`` and ``doctor``."""
        if view.get("anchor_stat") is None:
            return False
        try:
            return (self._entry_stat(view["seen"] + 1) is None
                    and self._entry_stat(view["seen"]) == view["anchor_stat"])
        except OSError:
            return False

    def _anchor_holds(self, view: dict, strict: bool = False) -> bool:
        """The last entry the view read is still there, unchanged. Read and checked again only when its file isn't the one
        checked before, or always when ``strict``; the file is looked at first, so one replaced after that is read again
        next time."""
        stat = self._entry_stat(view["seen"])
        if stat is None:
            return False
        if stat == view.get("anchor_stat") and not strict:
            return True
        if self.read_entry(view["seen"]).get("digest") != view["anchor"]:
            return False
        view["anchor_stat"] = stat
        return True

    def _journal_view(self, look: bool = False, strict: bool = False) -> dict:
        """What the journal says, extended to the current head (ADR-022). Per handle, in memory, built
        only from digest-verified entries, and only ever read forward: entries are immutable, so each
        one is read once. The view is thrown away if the journal is shorter than what was seen or the
        last entry seen has changed (a restored journal).

        When nothing has changed (:meth:`_nothing_new`), the view is returned without listing the journal or reading
        its last entry again, so a read costs the same with ten entries or a hundred thousand, and after a large one
        as after a small one. ``look`` lists it anyway: a reader that has seen a record newer than the view asks
        that way, so a listing is still what decides. ``strict`` also reads the last entry again whatever its file
        says: :meth:`commit` starts that way, so a write is decided on the journal exactly as it is.

        ``latest`` maps (kind, id) to (seq, deleted) of the last *effective* write. Going forward in
        journal order, an entry is fenced exactly when its token is already in ``revoked``. ``attempts``
        maps a txid to its first effective entry and its last fenced one: a hung attempt can land after its
        own successful retry, and the one that took effect is the answer."""
        with self._view_lock:
            view = self._view
            if not (look or strict) and view is not None and view["seen"] and self._nothing_new(view):
                return view
            seqs = self.journal_seqs()
            if seqs and seqs[-1] != len(seqs):   # sorted, unique, starting at 1 or more: contiguous exactly then
                self._check_journal()
            if view is not None and view["seen"]:
                try:
                    anchored = bool(seqs) and seqs[-1] >= view["seen"] and self._anchor_holds(view, strict)
                except (FileNotFoundError, WorkspaceCorruptError):
                    anchored = False
                if not anchored:
                    view = None
            if view is None:
                view = {"seen": 0, "anchor": None, "anchor_stat": None, "revocations": [], "revoked": set(),
                        "latest": {}, "attempts": {}}
            for s in seqs:
                if s <= view["seen"]:
                    continue
                try:   # looked at before it is read, as in _anchor_holds
                    stat = self._entry_stat(s) if s == seqs[-1] else None
                except OSError:
                    stat = None
                entry = self.read_entry(s)
                if entry.get("op") == "shared.enable":
                    # Without this, changing workspace.json by hand would switch off every signature check (ADR-024).
                    raise WorkspaceCorruptError(f"journal entry {s} set this workspace up for a team, but workspace.json "
                                                "no longer says so. It has been changed; restore it from a backup")
                tok = entry.get("writer_token")
                fenced = bool(tok) and tok in view["revoked"]
                if not fenced:
                    for item in entry["records"]:
                        view["latest"][(item["kind"], item["id"])] = (s, bool(item.get("delete")))
                if entry.get("txid"):
                    attempt = view["attempts"].setdefault(entry["txid"], {"effective": None, "fenced": None})
                    if fenced:
                        attempt["fenced"] = s
                    elif attempt["effective"] is None:
                        attempt["effective"] = s
                if entry.get("op") == "workspace.lock.break":
                    for item in entry["records"]:
                        revoked = ((item.get("data") or {}).get("previous_holder") or {}).get("token")
                        if revoked:
                            view["revocations"].append((s, revoked))
                            view["revoked"].add(revoked)
                view["seen"], view["anchor"], view["anchor_stat"] = s, entry.get("digest"), stat
            self._view = view
            return view

    def _revocations(self) -> list[tuple[int, str]]:
        """(seq, token) of every audited writer-lock break, in journal order (issue #8 fence)."""
        return list(self._journal_view()["revocations"])

    def is_fenced(self, entry: dict) -> bool:
        """An entry signed with a token that an EARLIER entry revoked never takes effect.

        Journal order decides: a writer paused between its lock check and its publish lands after
        the break entry, so every reader, replay and the writer itself ignore it (ADR-015)."""
        tok = entry.get("writer_token")
        return bool(tok) and any(s < entry["seq"] and t == tok for s, t in self._revocations())

    def entries_after(self, seq: int) -> Iterable[dict]:
        for s in self.journal_seqs():
            if s > seq:
                entry = self.read_entry(s)
                if not self.is_fenced(entry):
                    yield entry

    def applied_seq(self) -> int:
        try:
            applied = atomicio.read_json_object(self.meta_dir / "state.json").get("applied_seq")
        except (FileNotFoundError, ValueError):
            return 0                                 # a cache: the cost of losing it is one replay
        return applied if type(applied) is int and applied >= 0 else 0

    def recover(self) -> int:
        """Finish any commit interrupted by a crash. Returns the number of entries replayed."""
        self._require_writable()
        for sub in ("journal", "locks", "blobs", "content/sha256"):
            atomicio.cleanup_temp_files(self.meta_dir / sub)
        for parent in ("records", "blobs"):
            folder = self.meta_dir / parent
            for kind_dir in folder.iterdir() if folder.is_dir() else []:
                atomicio.cleanup_temp_files(kind_dir)
        applied = self.applied_seq()
        head = self.head_seq()
        if applied > head:
            raise WorkspaceCorruptError(f"state.json says {applied} applied but journal ends at {head}")
        count = 0
        for entry in self.entries_after(applied):
            self._apply(entry)
            count += 1
        if self.applied_seq() != head:  # trailing fenced entries: nothing to apply, advance past them
            atomicio.atomic_write_json(self.meta_dir / "state.json", {"applied_seq": head})
        return count

    def _apply(self, entry: dict) -> None:
        for op in entry["records"]:
            kind_dir = self.meta_dir / "records" / op["kind"]
            kind_dir.mkdir(exist_ok=True)
            target = kind_dir / f"{op['id']}.json"
            if op.get("delete"):
                atomicio.retry_sharing(lambda: target.unlink(missing_ok=True))
            else:
                atomicio.atomic_write_json(target, {**op["data"], REV_FIELD: entry["seq"]})
        atomicio.atomic_write_json(self.meta_dir / "state.json", {"applied_seq": entry["seq"]})

    def _require_writable(self) -> None:
        if self._closed:
            raise WorkspaceError("workspace is closed")
        if not self.writable:
            raise ReadOnlyError("workspace is open read-only")

    def _check_lock_still_held(self) -> None:
        try:
            holder = self._read_holder(self.meta_dir / "locks" / "writer.lock")
        except (OSError, ValueError):
            holder = None
        if not holder or holder["token"] != self._lock_token:
            self.writable = False
            raise LockLostError("this session no longer holds the workspace writer lock (it was broken "
                                "by an administrator or replaced); reopen the workspace")

    def require_authority(self) -> None:
        """Refuse unless this handle may change things right now. For work that a journal entry cannot fence, such as
        overwriting a working file or deleting a content object: do this first, because a refused commit afterwards
        cannot undo it. It narrows the window and cannot close it; a break in the next instant is not seen here."""
        self._require_writable()
        self._check_lock_still_held()
        if self._lock_token in {t for _, t in self._revocations()}:
            self.writable = False
            raise LockLostError("this session's writer lock was revoked by an administrator; reopen the workspace")

    def _current_rev(self, kind: str, rid: str) -> int:
        data = self.get(kind, rid)
        return int(data.get(REV_FIELD, 0)) if data is not None else 0

    @staticmethod
    def _check_request(ops: list, txid: str | None) -> None:
        for item in ops:
            if item.kind not in KINDS:
                raise ValueError(f"unknown record kind {item.kind!r}")
            if not _ID_RE.match(item.id):
                raise ValueError(f"invalid record id {item.id!r}")
            if isinstance(item, RecordPut) and _nested_deeper_than(item.data, MAX_RECORD_NESTING):
                raise ValueError(f"{item.kind} {item.id} is nested more than {MAX_RECORD_NESTING} levels deep")
        if txid is not None and not re.match(r"^[0-9a-f]{32}$", txid):
            raise ValueError("txid must be 32 lowercase hex characters")

    def commit(self, op: str, ops: list, blob_refs: list[str] | None = None, txid: str | None = None) -> int:
        """Durably apply record puts/deletes as one journal entry. Returns its sequence number.

        Raises ConflictError (nothing written) if an ``expect_rev`` does not hold, LockLostError if
        the writer lock was lost, and CommittedNotApplied if the entry was committed but could not be
        materialised."""
        self._require_writable()
        self._check_request(ops, txid)
        self._check_lock_still_held()  # before recover: a writer that lost its lock writes nothing
        if self.applied_seq() != self.head_seq():
            self.recover()
        self._journal_view(strict=True)  # listed, gaps refused and the last entry read again: what follows decides on it
        checks = [{"kind": c.kind, "id": c.id, "expect_rev": c.expect_rev}
                  for c in ops if isinstance(c, RecordCheck)]
        identity = request_identity(op, ops, blob_refs)  # also rejects non-finite numbers up front
        if txid is not None:  # idempotent retry (ADR-017/021): same txid + same request -> same result
            attempt = self._journal_view()["attempts"].get(txid)
            if attempt:
                s_ = attempt["effective"] or attempt["fenced"]
                prior = self.read_entry(s_)
                recorded = prior.get("request") or {}
                if recorded.get("version") != REQUEST_IDENTITY_VERSION:
                    raise IdentityUnverifiable(txid, s_)
                if recorded.get("digest") != identity:
                    raise TxidReuseError(f"txid {txid} was already used for a different change (entry {s_})")
                if attempt["effective"] is None:
                    raise LockLostError(f"fenced: txid {txid} (entry {s_}) never took effect")
                return s_
        if self._lock_token in {t for _, t in self._revocations()}:
            self.writable = False
            raise LockLostError("this session's writer lock was revoked by an administrator; reopen the workspace")
        for item in ops:
            if item.expect_rev is not None:
                actual = self._current_rev(item.kind, item.id)
                if actual != item.expect_rev:
                    raise ConflictError(item.kind, item.id, item.expect_rev, actual)
        writes = [p for p in ops if not isinstance(p, RecordCheck)]
        entry = {"seq": 0, "txid": txid or uuid.uuid4().hex, "time": now_iso(), "actor": current_actor(),
                 "op": op, "records": [p.as_json() for p in writes], "blobs": blob_refs or [], "checks": checks,
                 "request": {"version": REQUEST_IDENTITY_VERSION, "digest": identity},
                 "writer_token": self._lock_token}
        unsynced: OSError | None = None
        for _attempt in range(1000):
            seq = entry["seq"] = self.head_seq() + 1
            entry["digest"] = entry_digest({k: v for k, v in entry.items() if k != "digest"})
            try:
                atomicio.publish_exclusive(self._journal_dir() / f"{seq:020d}.json", atomicio.dumps_json(entry))
                break
            except ExclusiveCreateError as exc:
                if self._lock_token in {t for _, t in self._revocations()}:
                    self.writable = False  # an administrator's revocation took this sequence first
                    raise LockLostError("this session's writer lock was revoked by an administrator before "
                                        "its change was published; nothing was written") from exc
                occupant = self.read_entry(seq)
                if occupant.get("writer_token") and not self.is_fenced(occupant):
                    # An effective entry from another writer: the single-writer invariant was violated.
                    raise WorkspaceCorruptError(
                        f"journal entry {seq} was created by another writer; refusing to continue") from exc
                # An administrative entry or a revoked writer's late publish. Neither changes any record this
                # commit checked (administrative entries only add new audit records), so take the next one.
            except OSError as exc:
                if not self._holds_entry(seq, entry["txid"]):
                    raise                            # nothing was published: an ordinary failure, nothing happened
                unsynced = exc                       # visible to every reader: this change happened, so never say it failed
                break
        else:
            raise WorkspaceError("could not publish to the journal (journal contention)")
        if self.is_fenced(entry):
            self.writable = False
            raise LockLostError(f"fenced: entry {seq} was published after this session's writer lock was "
                                "broken, so it is ignored by every reader and replay; nothing took effect")
        try:
            if self.applied_seq() == seq - 1:
                self._apply(entry)
            else:  # something landed in between (an administrative entry): it must not be skipped
                self.recover()
        except BaseException as exc:  # commit point already passed: report it as committed
            raise CommittedNotApplied(seq, entry["txid"], exc) from exc
        if unsynced is not None:
            raise CommittedNotApplied(seq, entry["txid"], unsynced) from unsynced
        return seq

    def _holds_entry(self, seq: int, txid: str) -> bool:
        try:
            return atomicio.read_json(self._journal_dir() / f"{seq:020d}.json").get("txid") == txid
        except (OSError, ValueError, AttributeError):
            return False

    def health(self) -> dict:
        """Read-only summary for a UI (Claude B's A1). Never raises for a damaged journal: it reports it. ``ok`` is False
        when writes would be refused or the journal could not be fully checked (fail closed; ``head_seq`` is None if the
        journal directory itself could not be listed); ``pending`` entries are saved but not materialised, and need no action."""
        report = {"ok": False, "reason": None, "head_seq": None, "applied_seq": self.applied_seq(), "pending": 0}
        try:   # fail closed: ok becomes True only after every check below has completed
            report["head_seq"] = self.head_seq()
            report["pending"] = max(0, report["head_seq"] - report["applied_seq"])
            self._check_journal()
            self._journal_view()
            report["ok"] = True
        except WorkspaceCorruptError as exc:
            report["reason"] = str(exc)
        except OSError as exc:
            report["reason"] = f"the journal could not be read: {exc}"
        return report

    def transaction_outcome(self, txid: str) -> dict:
        """Durable outcome of a transaction the caller tagged with ``txid``. If attempts with this txid both
        took effect and were fenced, the one that took effect is the outcome."""
        attempt = self._journal_view()["attempts"].get(txid)
        if not attempt:
            return {"state": "not-found"}
        if attempt["effective"] is not None:
            return {"state": "committed", "seq": attempt["effective"]}
        return {"state": "fenced", "seq": attempt["fenced"]}

    def verify(self, repair: bool = False) -> dict:
        """Replay the whole journal and compare with the materialised records.

        Reports entries without digests, records that differ from the journal, records missing, and
        record files the journal never wrote. With ``repair`` (writable only), rewrites records from
        the journal, moves unexplained files to ``quarantine/`` (never deletes them) and journals an
        audit record of what was repaired."""
        if repair:
            self.require_authority()                 # repair moves files, and a commit cannot fence that
            self.recover()
        self._check_journal()
        expected: dict[tuple[str, str], dict | None] = {}
        pending: set[tuple[str, str]] = set()   # written by entries not materialised yet: lag, not damage
        applied = self.applied_seq()
        problems: list[dict] = []
        auths, results = Workspace._orphan_clear_state(self.meta_dir, self)
        for a in auths:  # ADR-019 rev. 3: archived evidence must still be the audited bytes
            if (results.get(a["clear_id"]) or {}).get("outcome") != "completed":
                continue
            ev = self.meta_dir / "locks" / a["evidence"]
            try:
                ok = hashlib.sha256(ev.read_bytes()).hexdigest() == a["file_sha256"]
            except FileNotFoundError:
                problems.append({"problem": "orphan-evidence-missing", "clear_id": a["clear_id"],
                                 "evidence": a["evidence"]})
                continue
            if not ok:
                problems.append({"problem": "orphan-evidence-altered", "clear_id": a["clear_id"],
                                 "evidence": a["evidence"]})
        for s in self.journal_seqs():
            entry = self.read_entry(s)
            if "digest" not in entry:
                problems.append({"problem": "entry-without-digest", "seq": s})
            if self.is_fenced(entry):
                # Information, not damage: this is fencing having worked. Nothing can or needs to clear it.
                problems.append({"problem": "fenced-entry", "severity": "info", "seq": s, "op": entry.get("op")})
                continue
            for item in entry["records"]:
                key = (item["kind"], item["id"])
                expected[key] = None if item.get("delete") else {**item["data"], REV_FIELD: s}
                if s > applied:
                    pending.add(key)
            for ref in entry.get("blobs", []):
                kind, blob_id = ref.split("/", 1)
                if not (self.meta_dir / "blobs" / kind / f"{blob_id}.json").is_file():
                    problems.append({"problem": "missing-blob", "seq": s, "blob": ref})
        on_disk: dict[tuple[str, str], Path] = {}
        records = self.meta_dir / "records"
        for kind_dir in sorted(records.iterdir()) if records.is_dir() else []:
            for f in sorted(kind_dir.iterdir()):
                if f.name.endswith(".json") and not f.name.startswith("."):
                    on_disk[(kind_dir.name, f.name[:-5])] = f
        for key, data in expected.items():
            path = on_disk.pop(key, None)
            if key in pending:
                continue
            if data is None:
                if path is not None:
                    problems.append({"problem": "deleted-but-present", "kind": key[0], "id": key[1]})
                    on_disk[key] = path
                continue
            if path is None:
                problems.append({"problem": "missing", "kind": key[0], "id": key[1]})
                continue
            try:
                actual = atomicio.read_json(path)
            except ValueError:
                actual = None
            if actual != data:
                problems.append({"problem": "mismatch" if actual is not None else "unreadable",
                                 "kind": key[0], "id": key[1]})
        for key in on_disk:
            if expected.get(key, "absent") == "absent":
                problems.append({"problem": "not-in-journal", "kind": key[0], "id": key[1]})
        report = {"head_seq": self.head_seq(), "applied_seq": applied, "problems": problems, "repaired": False}
        fixable = [p for p in problems if p["problem"] in
                   ("mismatch", "unreadable", "missing", "not-in-journal", "deleted-but-present")]
        if repair and fixable:
            quarantine = self.meta_dir / "quarantine" / uuid.uuid4().hex[:12]
            for p in fixable:
                key = (p["kind"], p["id"])
                path = self.meta_dir / "records" / p["kind"] / f"{p['id']}.json"
                if p["problem"] in ("not-in-journal", "deleted-but-present", "mismatch", "unreadable") \
                        and path.exists():
                    quarantine.mkdir(parents=True, exist_ok=True)
                    os.replace(path, quarantine / f"{p['kind']}__{p['id']}.json")
                if expected.get(key) is not None:
                    path.parent.mkdir(exist_ok=True)
                    atomicio.atomic_write_json(path, expected[key])
            self.commit("workspace.verify.repair", [RecordPut("audit", new_id("aud"), {
                "event": "records-repaired", "problems": fixable, "time": now_iso(), "by": current_actor(),
                "quarantine": str(quarantine.relative_to(self.meta_dir)) if quarantine.exists() else None})])
            report["repaired"] = True
        return report

    # ------------------------------------------------------------------ blobs
    def write_blob(self, kind: str, data: Any) -> str:
        self._require_writable()
        blob_id = new_id("blb")
        d = self.meta_dir / "blobs" / kind
        atomicio.make_dirs(d)
        atomicio.publish_exclusive(d / f"{blob_id}.json", atomicio.dumps_json(data))
        return f"{kind}/{blob_id}"

    def read_blob(self, ref: str) -> Any:
        kind, blob_id = ref.split("/", 1)
        if not _ID_RE.match(blob_id) or not re.match(r"^[a-z]+$", kind):
            raise ValueError(f"invalid blob reference {ref!r}")
        return atomicio.read_json(self.meta_dir / "blobs" / kind / f"{blob_id}.json")

    # ------------------------------------------------------------------ reads
    def _record_at(self, kind: str, rid: str, seq: int) -> dict:
        try:
            records = self.read_entry(seq)["records"]
        except FileNotFoundError:
            raise WorkspaceCorruptError(f"journal entry {seq} is missing") from None
        for item in reversed(records):
            if item["kind"] == kind and item["id"] == rid and not item.get("delete"):
                return {**item["data"], REV_FIELD: seq}
        raise WorkspaceCorruptError(f"journal entry {seq} does not hold the {kind} {rid} it should")

    def _effective_writes(self) -> dict[tuple[str, str], tuple[int, bool]] | None:
        """The view for reads (ADR-022). A damaged journal must not take away read access to a fully applied
        workspace (CORE_API: recovery-blocked means read-only), so None then says "serve the files as they
        are". Decisions stay safe: commit() checks revocations first, which raises."""
        try:
            return self._journal_view()["latest"]
        except WorkspaceCorruptError:
            if self.applied_seq() != self.head_seq():
                raise
            return None

    def get(self, kind: str, rid: str) -> dict | None:
        """The journal's answer. The record file is a cache: it is used only if it holds exactly the entry
        the journal says last wrote this record. A stale writer can overwrite it with outdated data at any
        time (nothing can stop that write), and a file the journal never wrote is not a record."""
        writes = self._effective_writes()
        key = (kind, rid)
        if (writes is None or key not in writes) and not _ID_RE.match(rid):
            return None
        path = self.meta_dir / "records" / kind / f"{rid}.json"
        try:
            on_disk = self._read_record_file(path)
        except FileNotFoundError:
            on_disk = None
        except ValueError as exc:
            if writes is None:
                raise WorkspaceCorruptError(f"{kind} {rid}: the journal cannot answer and {exc}") from exc
            on_disk = None                           # the journal can answer; verify() reports the file
        if writes is None:
            return on_disk
        latest = writes.get(key)
        rev = None if on_disk is None else on_disk.get(REV_FIELD, 0)
        if rev is not None and (latest is None or rev > latest[0]):
            latest = self._journal_view(look=True)["latest"].get(key)  # committed since the view was read, or bogus
        if latest is None:
            return None
        seq, deleted = latest
        if deleted:
            return None
        return on_disk if rev == seq else self._record_at(kind, rid, seq)

    @staticmethod
    def _read_record_file(path: Path) -> dict:
        """A cached record, or ValueError if it is damaged in any way, including a revision that is not a whole number."""
        data = atomicio.read_json_object(path)
        rev = data.get(REV_FIELD, 0)
        if type(rev) is not int or rev < 0:
            raise ValueError(f"{path.name} has an invalid revision")
        return data

    def _record_files(self, kind: str, tolerate_damage: bool) -> dict[str, dict]:
        out: dict[str, dict] = {}
        kind_dir = self.meta_dir / "records" / kind
        if kind_dir.is_dir():
            for entry in os.scandir(kind_dir):
                if entry.name.endswith(".json") and not entry.name.startswith("."):
                    try:
                        out[entry.name[:-5]] = self._read_record_file(Path(entry.path))
                    except FileNotFoundError:
                        continue
                    except ValueError as exc:
                        if not tolerate_damage:
                            raise WorkspaceCorruptError(f"{kind}: the journal cannot answer and {exc}") from exc
        return out

    def list(self, kind: str) -> dict[str, dict]:
        """Every record of a kind, by the same rule as :meth:`get`."""
        writes = self._effective_writes()
        on_disk = self._record_files(kind, tolerate_damage=writes is not None)
        if writes is None:
            return on_disk

        def of_kind() -> dict[str, tuple[int, bool]]:
            with self._view_lock:                    # another thread may be extending the view
                return {rid: v for (k, rid), v in self._view["latest"].items() if k == kind}
        latest = of_kind()
        if any(rid not in latest or data.get(REV_FIELD, 0) > latest[rid][0] for rid, data in on_disk.items()):
            self._journal_view(look=True)            # committed since the view was read, or bogus
            latest = of_kind()
        out: dict[str, dict] = {}
        for rid, (seq, deleted) in latest.items():
            if not deleted:
                data = on_disk.get(rid)
                out[rid] = data if data is not None and data.get(REV_FIELD, 0) == seq \
                    else self._record_at(kind, rid, seq)
        return out

    def settings(self) -> dict:
        s = default_settings()
        s.update(strip_rev(self.get("settings", "workspace") or {}))
        return s


def default_settings() -> dict:
    return {
        "collaboration_mode": "single-writer",
        "adoption_exclusions": [".git", ".svn", "__pycache__", "*.bak", "~$*", "*.tmp", "OldVersions",
                                ".revisionworx", "Thumbs.db", "desktop.ini"],
        "adoption_max_files": 200000,
        "hash_on_adopt": True,
        "project_path_pattern": "{customer}/{number} {name}",
        "new_project_mode": "managed",
    }
