# SPDX-License-Identifier: GPL-3.0-or-later
"""A team working from one shared folder, with no server (ADR-024).

What keeps the data right, in order of importance:

1. **The journal's numbered names decide everything.** A change is published under the next number by exclusive create,
   and the file server lets exactly one client have each name. To publish number N a client must already have read
   and checked 1 to N-1, so a change can never be built on a view that someone else's change made stale. No lock
   and no clock is involved in this, and neither a fresh directory listing nor a fresh "not found" is assumed.
2. **Every entry is signed** by the account that made it, and names the entry before it. Every computer applies the same
   rules (:func:`judge`) to every entry, in order, so an entry that breaks them has no effect anywhere, whoever wrote it.
3. **The writer lock only makes people take turns.** Without it, two people's changes would keep aborting each other.
   The data doesn't depend on it, so a stuck one can be cleared on the evidence of silence. The audit entry that does so also revokes
   its token, so anything its holder publishes afterwards has no effect anywhere.
4. **Reads come from this computer's own copy** of the journal (:class:`Replica`): checked once, then never fetched again. It's
   disposable, and it's also a backup: :meth:`SharedWorkspace.repair` can put back entries lost from the folder.

What it can't do is stop someone with write access to the folder from deleting or damaging files. That's noticed, and writes
stop, but it isn't prevented. The document also says what hasn't been verified on real shares (docs/KNOWN_LIMITATIONS.md).
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import atomicio, clientstate, ed25519
from .atomicio import ExclusiveCreateError
from .documents import IMMUTABLE_REVISION_FIELDS
from .workspace import (REQUEST_IDENTITY_VERSION, REV_FIELD, CommittedNotApplied, ConflictError, IdentityUnverifiable,
                        LockLostError, RecordCheck, RecordPut, TxidReuseError, Workspace, WorkspaceCorruptError, WorkspaceError,
                        WorkspaceLockedError, current_actor, entry_digest, new_id, now_iso, register_kind, request_identity,
                        strip_rev)

register_kind("account")

RECOVERY = "@recovery"                       # who signs when an administrator's account is recovered with the recovery code
ENABLE_OP = "shared.enable"
PASSWORD_OP = "account.password"
RECOVER_OP = "account.recover"
LOCK_CLEARED_OP = "workspace.lock.cleared"
ROLES = ("admin", "user")
ADMIN_KINDS = ("settings", "lifecycle", "doctype", "root", "template")
ADMIN_OPS = ("content.gc",)

LOCK_WAIT_SECONDS = 10.0                     # how long opening for writing waits for someone else's turn to end
BEAT_SECONDS = 5.0                           # how often a lock's holder shows it's alive
LEASE_SECONDS = 60.0                         # silence, by this computer's own clock, after which a lock is cleared
COLLISION_PATIENCE = 20.0                    # longer than any client-side cache of "not found" lasts
HINT_PATIENCE = 10.0                         # how long to wait for an entry that's known to exist to become readable
TRANSIENT_PATIENCE = 10.0                    # how long a network filesystem may keep saying "try again"
GAP_PATIENCE = 15.0                          # before believing an entry is missing while the folder lists ones after it
MAX_ENTRY_BYTES = 64 * 1024 * 1024           # far past any real entry: a bigger file is damage or malice, not worth reading
INGEST_BATCH = 200
_SIGN_DOMAIN = b"revisionworx/journal-entry/v1\x00"
_HEX_TOKEN = re.compile(r"^[0-9a-f]{1,64}$")
_HEX_KEY = re.compile(r"^[0-9a-f]{64}$")


# What a network filesystem can say for a moment about a file that's fine: it's busy (the Linux SMB client's word for a sharing
# violation), its handle went stale, or the request didn't make it. Windows says PermissionError for a sharing violation.
_TRANSIENT = {errno.EBUSY, errno.EAGAIN, errno.ESTALE, errno.EIO, errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNABORTED, errno.EINTR}


def is_transient(exc: OSError) -> bool:
    if isinstance(exc, (FileNotFoundError, FileExistsError)):
        return False                                 # answers, not failures
    return exc.errno in _TRANSIENT or (atomicio.RETRY_SHARING and isinstance(exc, PermissionError))


def patiently(op):
    """Run a file operation, giving a network filesystem time to stop saying "try again". Anything else, or the last failure, is raised."""
    give_up = time.monotonic() + TRANSIENT_PATIENCE
    while True:
        try:
            return op()
        except OSError as exc:
            if not is_transient(exc) or time.monotonic() >= give_up:
                raise
            time.sleep(0.05 + random.random() * 0.1)


class NotLoggedIn(WorkspaceError):
    """Changing a workspace set up for a team takes a login. Nothing was written."""


class SessionExpired(NotLoggedIn):
    """This login is no longer good: the password was changed or the account was disabled. Nothing was written."""


class NotPermitted(WorkspaceError):
    """This account may not do that. Every computer would ignore the change, so it wasn't published."""


class SetupIncomplete(WorkspaceError):
    """Setting this workspace up for a team was interrupted. Nobody can write until it's finished."""


class HistoryDiverged(WorkspaceCorruptError):
    """The shared folder's journal isn't the one this computer has already read. Writes are refused."""


class StaleViewError(ConflictError):
    """Someone else's change got in first, so what this change was based on is out of date. Nothing was written."""

    def __init__(self, seq: int, who: str = ""):
        WorkspaceError.__init__(self, f"{who or 'someone else'} changed the workspace at the same moment (entry {seq}), so "
                                      "nothing was saved. Reload and try again")
        self.kind, self.id, self.expected, self.actual = "journal", str(seq), seq, seq


# ---------------------------------------------------------------------------- who is logged in
@dataclass(frozen=True)
class Session:
    """A login: the key that signs this user's changes. It's only ever on the user's own computer."""
    workspace_id: str
    account: str
    name: str
    public: str
    seed: bytes = field(repr=False)

    def sign(self, message: bytes) -> str:
        return ed25519.sign(self.seed, message).hex()


def team_dir(workspace_id: str, state_dir: Path | str | None = None) -> Path:
    base = Path(state_dir) if state_dir else clientstate.default_state_dir()
    return base / "shared" / clientstate.safe_name(workspace_id)


def replica_path(meta: Path, workspace_id: str, state_dir=None) -> Path:
    """One copy per folder: a backup or a training copy of a workspace has the same id, and its own history. A computer that reaches one
    folder by two paths keeps two copies, which costs nothing but a second read."""
    where = hashlib.sha256(os.path.normcase(os.path.abspath(meta)).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return team_dir(workspace_id, state_dir) / f"journal-{where}.sqlite"


def save_session(session: Session, state_dir=None, hours: float = 12.0) -> None:
    clientstate.write_private(team_dir(session.workspace_id, state_dir) / "session.json", {
        "account": session.account, "name": session.name, "public": session.public, "seed": session.seed.hex(),
        "expires": time.time() + hours * 3600.0})


def forget_session(workspace_id: str, state_dir=None) -> None:
    atomicio.remove_quietly(team_dir(workspace_id, state_dir) / "session.json")


def saved_session(workspace_id: str, state_dir=None) -> Session | None:
    path = team_dir(workspace_id, state_dir) / "session.json"
    try:
        raw = atomicio.read_json_object(path)
        expires = raw["expires"]
        if not isinstance(expires, (int, float)) or expires <= time.time():
            forget_session(workspace_id, state_dir)
            return None
        return Session(workspace_id, str(raw["account"]), str(raw["name"]), str(raw["public"]), bytes.fromhex(raw["seed"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------- the local copy of the journal
class ReplicaBusy(WorkspaceError):
    """Another RevisionWorx process on this computer is using the local copy of the journal. Try again."""


_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE entries (seq INTEGER PRIMARY KEY, digest TEXT NOT NULL, why TEXT, body TEXT NOT NULL);
CREATE TABLE records (kind TEXT NOT NULL, id TEXT NOT NULL, seq INTEGER NOT NULL, deleted INTEGER NOT NULL, data TEXT,
                      PRIMARY KEY (kind, id)) WITHOUT ROWID;
CREATE TABLE attempts (txid TEXT PRIMARY KEY, effective INTEGER, fenced INTEGER) WITHOUT ROWID;
CREATE TABLE revoked (token TEXT PRIMARY KEY) WITHOUT ROWID;
"""
_SCHEMA_VERSION = "1"


class Replica:
    """This computer's checked copy of the journal, and what it adds up to.

    Local, disposable and never trusted by anyone else: deleting it costs one re-read of the shared journal. It's why a click in
    the window doesn't read a thousand files across the network, and why a signature is only ever checked once per computer."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = self._connect()

    def _connect(self) -> sqlite3.Connection:
        for attempt in (0, 1):
            db = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None, check_same_thread=False)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
                if exists is None:
                    db.execute("BEGIN IMMEDIATE")
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() is None:
                        for statement in _SCHEMA.split(";"):
                            if statement.strip():
                                db.execute(statement)
                        db.execute("INSERT INTO meta VALUES ('schema', ?)", (_SCHEMA_VERSION,))
                    db.execute("COMMIT")
                row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if row is not None and row[0] == _SCHEMA_VERSION:
                    return db
            except sqlite3.DatabaseError as exc:
                db.close()
                if self._is_busy(exc):
                    raise ReplicaBusy("the local copy of the journal is in use by another RevisionWorx process; try again in a "
                                      "moment") from exc
                if not self._is_corrupt(exc):
                    raise WorkspaceError(f"this computer's copy of the journal can't be used right now: {exc}") from exc
            else:
                db.close()                           # made by another version: it's only a cache, so it's replaced
            if attempt == 0:
                self._delete_files()
        raise WorkspaceError(f"the local copy of the journal at {self.path} could not be created")

    @staticmethod
    def _is_busy(exc: sqlite3.Error) -> bool:
        return "locked" in str(exc) or "busy" in str(exc)

    @staticmethod
    def _is_corrupt(exc: sqlite3.Error) -> bool:
        return "not a database" in str(exc) or "malformed" in str(exc)

    def _delete_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            atomicio.remove_quietly(Path(str(self.path) + suffix))

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def _query(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            try:
                return self.db.execute(sql, params).fetchall()
            except sqlite3.DatabaseError as exc:
                self._damaged(exc)

    def _damaged(self, exc: sqlite3.Error):
        """Only a file that really is damaged gets thrown away. It holds what this computer knows of history, which is what lets it
        notice the shared folder changing, so a full disk or a closed handle mustn't cost it that."""
        if isinstance(exc, sqlite3.ProgrammingError):
            raise WorkspaceError("workspace is closed") from exc
        if self._is_busy(exc):
            raise ReplicaBusy("the local copy of the journal is in use by another RevisionWorx process; try again in a "
                              "moment") from exc
        if not self._is_corrupt(exc):
            raise WorkspaceError(f"this computer's copy of the journal can't be used right now: {exc}") from exc
        self.db.close()
        self._delete_files()
        self.db = self._connect()
        raise WorkspaceError("this computer's copy of the journal was damaged, so it was discarded. Try again: it is "
                             "rebuilt from the shared folder") from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    yield
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
                self.db.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                self._damaged(exc)

    # ---- reading
    def meta(self, key: str, default: str = "") -> str:
        rows = self._query("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else default

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def head(self) -> int:
        return int(self.meta("head", "0"))

    def anchor(self) -> str:
        return self.meta("anchor")

    def enabled_at(self) -> int | None:
        value = self.meta("enabled_at")
        return int(value) if value else None

    def entry(self, seq: int) -> dict | None:
        rows = self._query("SELECT body FROM entries WHERE seq=?", (seq,))
        return json.loads(rows[0][0]) if rows else None

    def digest(self, seq: int) -> str | None:
        rows = self._query("SELECT digest FROM entries WHERE seq=?", (seq,))
        return rows[0][0] if rows else None

    def why_ignored(self, seq: int) -> str | None:
        rows = self._query("SELECT why FROM entries WHERE seq=?", (seq,))
        return rows[0][0] if rows else None

    def effective_entries_after(self, seq: int) -> Iterable[dict]:
        for row in self._query("SELECT body FROM entries WHERE seq>? AND why IS NULL ORDER BY seq", (seq,)):
            yield json.loads(row[0])

    def all_entries(self) -> Iterable[tuple[int, str, str | None]]:
        return self._query("SELECT seq, digest, why FROM entries ORDER BY seq")

    def rev(self, kind: str, rid: str) -> int:
        rows = self._query("SELECT seq, deleted FROM records WHERE kind=? AND id=?", (kind, rid))
        return rows[0][0] if rows and not rows[0][1] else 0

    def record(self, kind: str, rid: str) -> dict | None:
        rows = self._query("SELECT seq, data FROM records WHERE kind=? AND id=? AND deleted=0", (kind, rid))
        return {**json.loads(rows[0][1]), REV_FIELD: rows[0][0]} if rows else None

    def records(self, kind: str) -> dict[str, dict]:
        rows = self._query("SELECT id, seq, data FROM records WHERE kind=? AND deleted=0", (kind,))
        return {rid: {**json.loads(data), REV_FIELD: seq} for rid, seq, data in rows}

    def attempt(self, txid: str) -> dict | None:
        rows = self._query("SELECT effective, fenced FROM attempts WHERE txid=?", (txid,))
        return {"effective": rows[0][0], "fenced": rows[0][1]} if rows else None

    def revoked(self, token: str) -> bool:
        return bool(self._query("SELECT 1 FROM revoked WHERE token=?", (token,)))

    # ---- writing: only ever one entry after another, inside a transaction
    def apply(self, entry: dict, why: str | None) -> None:
        seq, txid = entry["seq"], entry.get("txid")
        digest = entry.get("digest") or entry_digest(entry)
        self.db.execute("INSERT INTO entries VALUES (?, ?, ?, ?)", (seq, digest, why, json.dumps(entry, separators=(",", ":"))))
        if why is None:
            for item in entry["records"]:
                data = None if item.get("delete") else json.dumps(strip_rev(item["data"]), separators=(",", ":"))
                self.db.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?)",
                                (item["kind"], item["id"], seq, 1 if item.get("delete") else 0, data))
        if txid:
            known = self.db.execute("SELECT effective, fenced FROM attempts WHERE txid=?", (txid,)).fetchone()
            effective, fenced = known if known else (None, None)
            if why is not None:
                fenced = seq
            elif effective is None:
                effective = seq
            self.db.execute("INSERT OR REPLACE INTO attempts VALUES (?, ?, ?)", (txid, effective, fenced))
        revoking = "workspace.lock.break" if self.enabled_at() is None else (LOCK_CLEARED_OP if why is None else None)
        if entry.get("op") == revoking:
            for item in entry["records"]:
                holder = (item.get("data") or {}).get("previous_holder")
                if isinstance(holder, dict) and isinstance(holder.get("token"), str):
                    self.db.execute("INSERT OR REPLACE INTO revoked VALUES (?)", (holder["token"],))
        if entry.get("op") == ENABLE_OP and why is None and self.enabled_at() is None:
            self._set_meta("enabled_at", str(seq))
        self._set_meta("rolling", next_rolling(self.meta("rolling"), digest))
        self._set_meta("anchor", digest)
        self._set_meta("head", str(seq))


def next_rolling(rolling: str, digest: str) -> str:
    """A digest of every entry so far, in order. The entry that turns team use on quotes it, which seals what came before."""
    return hashlib.sha256((rolling + digest).encode("ascii", "replace")).hexdigest()


# ---------------------------------------------------------------------------- the rules every computer applies
def signing_message(entry: dict) -> bytes:
    body = {k: v for k, v in entry.items() if k not in ("digest", "sig")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _SIGN_DOMAIN + hashlib.sha256(canon.encode("utf-8", "surrogatepass")).digest()


def _signature_holds(public, entry: dict) -> bool:
    try:
        return ed25519.verify(bytes.fromhex(public), signing_message(entry), bytes.fromhex(entry["sig"]))
    except (TypeError, ValueError, KeyError):
        return False


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def account_shape_problem(data: dict) -> str | None:
    key = _dict(data.get("key"))
    if data.get("role") not in ROLES:
        return "its role isn't one this version knows"
    if not isinstance(data.get("active"), bool) or not isinstance(data.get("name"), str):
        return "it's malformed"
    if not (isinstance(key.get("public"), str) and _HEX_KEY.match(key["public"]) and isinstance(key.get("salt"), str)
            and isinstance(key.get("kdf"), dict) and isinstance(key.get("device"), bool)):
        return "its key is malformed"
    return None


def _account_rule(entry: dict, item: dict, signer: str, account: dict, r: Replica) -> str | None:
    if item.get("delete"):
        return "accounts are disabled, not deleted"
    data = item["data"]
    problem = account_shape_problem(data)
    if problem:
        return f"account {item['id']}: {problem}"
    if account.get("role") == "admin":
        return None
    if entry.get("op") == PASSWORD_OP and item["id"] == signer and len(entry["records"]) == 1:
        old = strip_rev(r.record("account", signer) or {})
        changes = {k for k in set(old) | set(data) if old.get(k) != data.get(k)}
        if changes <= {"key", "must_change", "modified"}:
            return None
        return "a password change can't change anything else about an account"
    return "only an administrator can change accounts"


def _keeps_an_administrator(entry: dict, r: Replica) -> bool:
    accounts = {rid: strip_rev(data) for rid, data in r.records("account").items()}
    accounts.update({i["id"]: i["data"] for i in entry["records"] if i["kind"] == "account" and not i.get("delete")})
    return any(a.get("active") is True and a.get("role") == "admin" for a in accounts.values())


def _checkout_rule(item: dict, signer: str, admin: bool, r: Replica) -> str | None:
    existing = r.record("checkout", item["id"])
    held_by_signer = existing is not None and _dict(existing.get("owner")).get("account") == signer
    if item.get("delete"):
        return None if existing is None or held_by_signer or admin else "only its owner or an administrator can release a checkout"
    owner = _dict(item["data"].get("owner")).get("account")
    if owner != signer:
        return "a checkout can only be taken in its taker's own name"
    return None if existing is None or held_by_signer else "this document is already checked out by someone else"


def _revision_rule(entry: dict, item: dict, signer: str, r: Replica) -> str | None:
    if item.get("delete"):
        return "revisions can't be deleted"
    data = item["data"]
    old = r.record("revision", item["id"])
    if old is None:
        if _dict(data.get("author")).get("account") != signer:
            return "a revision can only be made in its author's own name"
        doc = data.get("document_id")
        new_document = any(i["kind"] == "document" and i["id"] == doc for i in entry["records"]) and \
            not isinstance(r.record("document", doc if isinstance(doc, str) else ""), dict)
        ends_checkout = any(i["kind"] == "checkout" and i["id"] == doc and i.get("delete") for i in entry["records"]) and \
            r.record("checkout", doc if isinstance(doc, str) else "") is not None
        return None if new_document or ends_checkout else "a new revision has to come from a check-in"
    if any(old.get(f) != data.get(f) for f in IMMUTABLE_REVISION_FIELDS):
        return "a published revision can't change"
    before, after = old.get("transitions"), data.get("transitions")
    if not isinstance(before, list) or not isinstance(after, list) or after[:len(before)] != before:
        return "a revision's history can only be added to"
    steps = after[len(before):]
    if any(_dict(_dict(step).get("by")).get("account") != signer for step in steps):
        return "a lifecycle step can only be taken in its taker's own name"
    policy = _dict(r.record("lifecycle", "default"))
    if policy.get("require_distinct_approver") and _dict(old.get("author")).get("account") == signer \
            and any(_dict(step).get("to") == policy.get("approved") for step in steps):
        return "policy requires a different person to approve this revision"
    return None


def _document_rule(entry: dict, item: dict, r: Replica) -> str | None:
    """Which revision is a document's latest or released is what a drawing register reads, so it can only move along with a
    revision record in the same entry, whose own rules say whose name that's in."""
    if item.get("delete"):
        return "documents can't be deleted"
    old, data = r.record("document", item["id"]) or {}, item["data"]
    revisions = {i["id"] for i in entry["records"] if i["kind"] == "revision" and not i.get("delete")}
    for field_name in ("latest_rev_id", "released_rev_id"):
        new = data.get(field_name)
        if new == old.get(field_name):
            continue
        if not revisions or (new is not None and (not isinstance(new, str) or new not in revisions)):
            return "a document's latest or released revision can only change along with that revision"
    return None


def _expectations_problem(entry: dict, r: Replica) -> str | None:
    """A publisher checks these before publishing. Checking them again here means nobody has to take that on trust."""
    for item in entry["records"]:
        expected = item.get("expect_rev")
        if expected is not None and r.rev(item["kind"], item["id"]) != expected:
            return f"{item['kind']} {item['id']} wasn't at the revision it expected"
    for check in entry.get("checks", []):
        kind, rid, expected = check.get("kind"), check.get("id"), check.get("expect_rev")
        if not isinstance(kind, str) or not isinstance(rid, str) or r.rev(kind, rid) != expected:
            return f"{kind} {rid} wasn't at the revision it expected"
    return None


def _recovery_rule(entry: dict) -> str | None:
    records = entry["records"]
    if entry.get("op") != RECOVER_OP or len(records) != 1 or records[0]["kind"] != "account" or records[0].get("delete"):
        return "the recovery code can only recover an administrator"
    data = records[0]["data"]
    problem = account_shape_problem(data)
    if problem:
        return f"account {records[0]['id']}: {problem}"
    return None if data.get("role") == "admin" and data.get("active") is True else "the recovery code can only recover an administrator"


def judge(entry: dict, r: Replica) -> str | None:
    """Why this entry has no effect, or None if it does. It decides from the entries before it and nothing else, so every
    computer gets the same answer."""
    signer = entry.get("signer")
    if not isinstance(signer, str) or not isinstance(entry.get("sig"), str):
        return "it isn't signed"
    token = entry.get("writer_token")
    if token and r.revoked(token):
        return "its writer's turn had been cleared"
    if _dict(entry.get("actor")).get("account") != signer:
        return "it names someone other than its signer"
    problem = _expectations_problem(entry, r)
    if problem:
        return problem
    if signer == RECOVERY:
        public = _dict(r.record("settings", "sharing")).get("recovery_public")
        if not _signature_holds(public, entry):
            return "its signature doesn't match the recovery key"
        return _recovery_rule(entry)
    account = r.record("account", signer)
    if account is None:
        return f"it's signed by {signer}, and there's no such account"
    if account.get("active") is not True:
        return f"account {signer} is disabled"
    if not _signature_holds(_dict(account.get("key")).get("public"), entry):
        return f"its signature doesn't match {signer}'s key"
    op = entry.get("op")
    if account.get("must_change") and op not in (PASSWORD_OP, LOCK_CLEARED_OP):
        return f"{signer} has to choose a new password first"
    admin = account.get("role") == "admin"
    if op in ADMIN_OPS and not admin:
        return "only an administrator can do that"
    for item in entry["records"]:
        kind = item["kind"]
        problem = None
        if kind == "account":
            problem = _account_rule(entry, item, signer, account, r)
        elif kind in ADMIN_KINDS:
            problem = None if admin else f"only an administrator can change {kind} records"
        elif kind == "checkout":
            problem = _checkout_rule(item, signer, admin, r)
        elif kind == "revision":
            problem = _revision_rule(entry, item, signer, r)
        elif kind == "document":
            problem = _document_rule(entry, item, r)
        elif kind == "audit":
            problem = "audit records can't be changed" if item.get("delete") or r.record("audit", item["id"]) else None
        if problem:
            return problem
    if any(i["kind"] == "account" for i in entry["records"]) and not _keeps_an_administrator(entry, r):
        return "it would leave no active administrator"
    return None


def _check_enable(entry: dict, r: Replica) -> None:
    """The entry that turns team use on introduces the first administrator's key, signs itself with it and seals the history
    before it. There's nothing to judge it against, so if it doesn't hold together, nothing after it can be trusted."""
    seq = entry["seq"]
    bad = WorkspaceCorruptError(f"journal entry {seq}, which set this workspace up for a team, doesn't hold together: "
                                "writes refused. Restore it from a backup")
    if entry.get("prev") != r.anchor() or entry.get("history") != r.meta("rolling"):
        raise bad
    signer = entry.get("signer")
    records = {(i["kind"], i["id"]): i for i in entry["records"]}
    account = _dict(_dict(records.get(("account", signer))).get("data"))
    sharing = _dict(_dict(records.get(("settings", "sharing"))).get("data"))
    if account_shape_problem(account) or account.get("role") != "admin" or account.get("active") is not True:
        raise bad
    if not isinstance(sharing.get("recovery_public"), str) or not _HEX_KEY.match(sharing["recovery_public"]):
        raise bad
    if not _signature_holds(account["key"]["public"], entry):
        raise bad


def judge_next(entry: dict, r: Replica) -> str | None:
    """Judge the entry that comes next in the journal. Raises if the journal itself can't be trusted from here on."""
    if r.enabled_at() is None:
        if entry.get("op") == ENABLE_OP:
            _check_enable(entry, r)
            return None
        token = entry.get("writer_token")               # before team use: the same fence as a single-computer workspace
        return "its writer's lock had been revoked" if token and r.revoked(token) else None
    if entry.get("prev") != r.anchor():
        raise WorkspaceCorruptError(f"journal entry {entry['seq']} doesn't follow the entry before it, so an entry has been "
                                    "altered or replaced; writes refused. Run 'revisionworx doctor'")
    return judge(entry, r)


# ---------------------------------------------------------------------------- taking turns
class _Heartbeat(threading.Thread):
    """Shows other computers this lock's holder is alive during a long operation. Most operations end before the first beat."""

    def __init__(self, path: Path):
        super().__init__(name="revisionworx-lock-heartbeat", daemon=True)
        self.path = path
        self._stop_event = threading.Event()

    def run(self) -> None:
        count = 0
        while not self._stop_event.wait(BEAT_SECONDS):
            count += 1
            try:
                atomicio.atomic_write_json(self.path, {"beat": count})
            except OSError:
                pass                                 # others may clear the lock, and then this writer is refused: safe

    def stop(self) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)


_watched: dict[tuple[str, str], tuple[object, float]] = {}
_watch_lock = threading.Lock()


def _beat_path(meta: Path, token: object) -> Path | None:
    return meta / "locks" / f"beat-{token}.json" if isinstance(token, str) and _HEX_TOKEN.match(token) else None


def lock_silence(meta: Path, holder: dict) -> float:
    """For how long, by this computer's clock, this holder has shown no sign of life. Clocks on different computers are never
    compared: this only counts how long the same lock has looked the same from here."""
    token = holder.get("token")
    path = _beat_path(meta, token)
    beat: object = None
    if path is not None:
        try:
            beat = atomicio.read_json_object(path).get("beat")
        except (OSError, ValueError):
            beat = None
    key, now = (str(meta), str(token)), time.monotonic()
    with _watch_lock:
        if len(_watched) > 256:
            _watched.clear()
        seen = _watched.get(key)
        if seen is None or seen[0] != beat:
            _watched[key] = (beat, now)
            return 0.0
        return now - seen[1]


# ---------------------------------------------------------------------------- the workspace itself
class SharedWorkspace(Workspace):
    """A workspace handle for a team working from a shared folder. Implements the same :class:`CommitAuthority` as
    :class:`Workspace`, by different means: see the module's docstring."""

    materialises = False

    def __init__(self, meta_dir: Path, info: dict, writable: bool, lock_token: str | None, session: Session | None = None,
                 state_dir: Path | str | None = None):
        super().__init__(meta_dir, info, writable, lock_token)
        self.session = session
        self.state_dir = state_dir
        self._damage: str | None = None
        self._base = 0
        self._heart: _Heartbeat | None = None
        self._replica = Replica(replica_path(meta_dir, info["workspace_id"], state_dir))

    @classmethod
    def attach(cls, meta: Path, info: dict, writable: bool = False, session: Session | None = None,
               state_dir: Path | str | None = None, wait: float | None = None) -> "SharedWorkspace":
        session = session or saved_session(info["workspace_id"], state_dir)
        ws = cls(meta, info, False, None, session=session, state_dir=state_dir)
        try:
            ws.sync(fresh=True)
            if writable:
                ws._begin_writing(LOCK_WAIT_SECONDS if wait is None else wait)
        except BaseException:
            ws._close_keeping_the_real_error()
            raise
        return ws

    def _begin_writing(self, wait: float) -> None:
        self._refuse_if_untrustworthy()
        self._signer()
        self._take_lock(wait)
        self.writable = True
        self.sync(fresh=True, patient=True)
        self._refuse_if_untrustworthy()
        self._check_same_history()
        self._check_for_gaps()
        self._signer()
        self._base = self._replica.head()
        path = _beat_path(self.meta_dir, self._lock_token)
        self._heart = _Heartbeat(path)
        self._heart.start()
        if random.random() < 0.02:                   # now and then: temp files left behind by crashes on any computer
            self._sweep_old_temps()

    def close(self) -> None:
        """Never raises for the lock: if it can't be released now, it expires and someone else clears it."""
        self._closed = True
        if self._heart is not None:
            self._heart.stop()
            self._heart = None
        if self._lock_token:
            try:
                self._release_lock()
            except OSError:
                pass
            self._lock_token = None
        self._replica.close()

    def actor(self) -> dict:
        identity = current_actor()
        if self.session is not None:
            identity.update(user=self.session.account, name=self.session.name, account=self.session.account)
        return identity

    def owns_checkout(self, checkout: dict) -> bool:
        return self.session is not None and _dict(checkout.get("owner")).get("account") == self.session.account

    @property
    def role(self) -> str | None:
        """What the logged-in user is here, or None if nobody is."""
        account = self._replica.record("account", self.session.account) if self.session else None
        return account.get("role") if account else None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin" or (self.session is not None and self.session.account == RECOVERY)

    # ------------------------------------------------------------------ the journal, as this computer has checked it
    def sync(self, fresh: bool = False, patient: bool = False) -> None:
        """Read and check whatever has been published since this computer last looked. It walks forward by name, and doesn't
        believe a directory listing, which can be seconds old on a network share. ``fresh`` also looks at the hint left by
        the last writer, in case "not found" is stale, and ``patient`` waits for what the hint promises."""
        self._damage = None
        target = max(self._hinted_head(), 0) if fresh else 0
        give_up = time.monotonic() + (HINT_PATIENCE if patient else 0.0)
        while True:
            head = self._replica.head()
            batch = self._read_batch(head + 1)
            if batch:
                self._ingest(batch)
                if self._damage is None and self._replica.head() > head:
                    continue
            if self._damage is not None or self._replica.head() >= target or time.monotonic() >= give_up:
                return
            time.sleep(0.2)

    def _hinted_head(self) -> int:
        try:
            seq = atomicio.read_json_object(self.meta_dir / "locks" / "last.json").get("seq")
        except (OSError, ValueError):
            return 0
        return seq if type(seq) is int else 0

    def _read_batch(self, start: int) -> list[dict]:
        batch = []
        for seq in range(start, start + INGEST_BATCH):
            try:
                batch.append(self._read_from_folder(seq))
            except FileNotFoundError:
                break
            except (WorkspaceCorruptError, ValueError) as exc:
                self._damage = str(exc)
                break
        return batch

    def _read_from_folder(self, seq: int) -> dict:
        """What the shared folder says this entry is. Anyone can write there, so size is checked before reading it all."""
        path = self._journal_dir() / f"{seq:020d}.json"

        def read() -> dict:
            if os.stat(path).st_size > MAX_ENTRY_BYTES:
                raise WorkspaceCorruptError(f"journal entry {seq} is far too big to be real; writes refused")
            return Workspace.read_entry(self, seq)
        return patiently(read)

    def _ingest(self, batch: list[dict]) -> None:
        r = self._replica
        with r.transaction():
            for entry in batch:
                head = r.head()
                if entry["seq"] <= head:
                    continue                         # another process on this computer got here first
                if entry["seq"] != head + 1:
                    break
                r.db.execute("SAVEPOINT one_entry")
                try:
                    r.apply(entry, judge_next(entry, r))
                except sqlite3.Error:
                    raise
                except WorkspaceCorruptError as exc:
                    self._damage = str(exc)
                except (RecursionError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    # Anyone can write anything in the folder. Whatever this is, it's damage, and it's never a crash.
                    self._damage = (f"journal entry {entry['seq']} can't be understood ({type(exc).__name__}); writes refused. "
                                    "Run 'revisionworx doctor'")
                if self._damage is not None:
                    r.db.execute("ROLLBACK TO one_entry")
                    r.db.execute("RELEASE one_entry")
                    break
                r.db.execute("RELEASE one_entry")

    def _refuse_if_untrustworthy(self) -> None:
        if self._damage:
            raise WorkspaceCorruptError(self._damage)
        if self._replica.enabled_at() is None:
            raise SetupIncomplete("setting this workspace up for a team didn't finish. Run it again on the computer it was "
                                  "started on: 'revisionworx team enable'. Nobody can make changes until then")

    def _check_same_history(self) -> None:
        """The last entry this computer read must still be there. If it isn't, this folder isn't the history this computer
        knows: restored from a backup, or damaged. Publishing on top of that would fork it, so stop here."""
        head = self._replica.head()
        if not head:
            return
        advice = ("If the folder was restored from a backup on purpose, run 'revisionworx team forget-local' on this computer. "
                  "If entries were lost, 'revisionworx team repair' puts back what this computer has")
        try:
            digest = self._folder_digest(head)
        except FileNotFoundError:
            raise HistoryDiverged(f"journal entry {head} is gone from the shared folder, though this computer has read it. "
                                  f"Writes refused. {advice}") from None
        except (WorkspaceCorruptError, ValueError):
            digest = None
        if digest != self._replica.digest(head):
            raise HistoryDiverged(f"journal entry {head} in the shared folder isn't the one this computer read. Writes refused. "
                                  f"{advice}")

    def _folder_digest(self, seq: int) -> str:
        entry = self._read_from_folder(seq)
        return entry.get("digest") or entry_digest(entry)

    def _check_for_gaps(self) -> None:
        """Walking forward by name stops at the first missing entry. If there's more past it, that entry was removed, and
        publishing into its place would fork history for anyone who read it."""
        give_up = time.monotonic() + GAP_PATIENCE
        while True:
            seqs = Workspace.journal_seqs(self)
            if not seqs or seqs[-1] <= self._replica.head():
                return
            if time.monotonic() >= give_up:
                missing = self._replica.head() + 1
                raise WorkspaceCorruptError(f"journal entry {missing} is missing, but there are entries after it; writes refused. "
                                            "Run 'revisionworx doctor'")
            time.sleep(0.2)
            self.sync()

    def head_seq(self) -> int:
        self.sync()
        return self._replica.head()

    def read_entry(self, seq: int) -> dict:
        entry = self._replica.entry(seq)
        return entry if entry is not None else Workspace.read_entry(self, seq)

    def is_fenced(self, entry: dict) -> bool:
        return self._replica.why_ignored(entry["seq"]) is not None

    def fenced_reason(self, entry: dict) -> str:
        return f"never took effect, because {self._replica.why_ignored(entry['seq'])}"

    def entries_after(self, seq: int) -> Iterable[dict]:
        return self._replica.effective_entries_after(seq)

    def _revocations(self) -> list[tuple[int, str]]:
        return []                                    # commit() and judge() ask the local copy instead, and no caller outside needs this

    def _check_journal(self) -> None:
        self.sync()
        self._refuse_if_untrustworthy()

    def applied_seq(self) -> int:
        return self._replica.head()                  # nothing is materialised in the folder, so nothing is ever waiting

    def recover(self) -> int:
        self._require_writable()
        return 0

    def get(self, kind: str, rid: str) -> dict | None:
        return self._replica.record(kind, rid)

    def list(self, kind: str) -> dict[str, dict]:
        return self._replica.records(kind)

    def transaction_outcome(self, txid: str) -> dict:
        self.sync()
        attempt = self._replica.attempt(txid)
        if not attempt:
            return {"state": "not-found"}
        if attempt["effective"] is not None:
            return {"state": "committed", "seq": attempt["effective"]}
        return {"state": "fenced", "seq": attempt["fenced"]}

    def health(self) -> dict:
        report = {"ok": False, "reason": None, "head_seq": None, "applied_seq": None, "pending": 0}
        try:
            self.sync(fresh=True)
            report["head_seq"] = report["applied_seq"] = self._replica.head()
            self._refuse_if_untrustworthy()
            self._check_same_history()
            report["ok"] = True
        except WorkspaceError as exc:
            report["reason"] = str(exc)
        except OSError as exc:
            report["reason"] = f"the journal could not be read: {exc}"
        return report

    def quick_check(self) -> dict:
        self.sync(fresh=True)
        self._refuse_if_untrustworthy()
        self._check_same_history()
        return {"head_seq": self._replica.head(), "applied_seq": self._replica.head(), "problems": [], "repaired": False}

    def verify(self, repair: bool = False) -> dict:
        """Compare every entry in the folder with what this computer has checked, and report what doesn't match. Entries that
        had no effect are reported as information. With ``repair``, put back what's been lost: the entries are signed, so
        it takes no authority to restore them and nobody has to take them on trust."""
        self.sync(fresh=True)
        if self._damage:
            problems = [{"problem": "journal-damaged", "seq": self._replica.head() + 1, "detail": self._damage}]
        else:
            problems = []
        for seq, digest, why in self._replica.all_entries():
            try:
                found = self._folder_digest(seq)
            except FileNotFoundError:
                problems.append({"problem": "journal-entry-missing", "seq": seq})
                continue
            except (WorkspaceCorruptError, ValueError):
                found = None
            if found != digest:
                problems.append({"problem": "journal-entry-altered", "seq": seq})
            if why is not None:
                problems.append({"problem": "fenced-entry", "severity": "info", "seq": seq, "why": why})
        for seq, _digest, _why in self._replica.all_entries():
            entry = self._replica.entry(seq)
            for ref in entry.get("blobs", []):
                kind, blob_id = ref.split("/", 1)
                if not (self.meta_dir / "blobs" / kind / f"{blob_id}.json").is_file():
                    problems.append({"problem": "missing-blob", "seq": seq, "blob": ref})
        report = {"head_seq": self._replica.head(), "applied_seq": self._replica.head(), "problems": problems, "repaired": False}
        if repair:
            fixed = self._restore_entries([p for p in problems if p["problem"] in ("journal-entry-missing", "journal-entry-altered")])
            report["repaired"] = bool(fixed)
            report["restored"] = fixed
        return report

    def _restore_entries(self, problems: list[dict]) -> list[int]:
        fixed = []
        quarantine = self.meta_dir / "quarantine" / uuid.uuid4().hex[:12]
        for problem in problems:
            seq = problem["seq"]
            path = self._journal_dir() / f"{seq:020d}.json"
            if problem["problem"] == "journal-entry-altered":
                quarantine.mkdir(parents=True, exist_ok=True)
                try:
                    atomicio.retry_sharing(lambda p=path: os.replace(p, quarantine / p.name))
                except FileNotFoundError:
                    pass
            try:
                atomicio.publish_exclusive(path, atomicio.dumps_json(self._replica.entry(seq)))
            except ExclusiveCreateError:
                continue
            fixed.append(seq)
        return fixed

    # ------------------------------------------------------------------ writing
    def _signer(self) -> Session:
        session = self.session
        if session is None:
            raise NotLoggedIn("log in to make changes here. This workspace is set up for a team, so every change is made in "
                              "somebody's name")
        if session.account == RECOVERY:
            current = _dict(self._replica.record("settings", "sharing")).get("recovery_public")
        else:
            account = self._replica.record("account", session.account)
            if account is None or account.get("active") is not True:
                raise SessionExpired(f"account {session.account} isn't active any more, so it can't make changes")
            current = _dict(account.get("key")).get("public")
        if current != session.public:
            raise SessionExpired("this login is out of date, because the password was changed. Log in again")
        return session

    def _seal(self, entry: dict, session: Session) -> None:
        entry["prev"] = self._replica.anchor()
        entry["signer"] = session.account
        entry["sig"] = session.sign(signing_message(entry))
        entry["digest"] = entry_digest(entry)

    def _refuse_foreign_changes(self) -> None:
        """While this handle holds the lock nobody else should have changed anything. If they did (its lock was cleared while it
        was stopped, say), what it read before committing may be out of date, in ways no record's revision would show."""
        r = self._replica
        for seq in range(self._base + 1, r.head() + 1):
            entry = r.entry(seq)
            if entry is None or r.why_ignored(seq) is not None:
                continue
            if entry.get("writer_token") == self._lock_token or entry.get("op") == LOCK_CLEARED_OP:
                continue
            raise StaleViewError(seq, _dict(entry.get("actor")).get("user", ""))
        self._base = r.head()

    def _earlier_attempt(self, txid: str, identity: str) -> int | None:
        attempt = self._replica.attempt(txid)
        if not attempt:
            return None
        seq = attempt["effective"] or attempt["fenced"]
        recorded = _dict(_dict(self._replica.entry(seq)).get("request"))
        if recorded.get("version") != REQUEST_IDENTITY_VERSION:
            raise IdentityUnverifiable(txid, seq)
        if recorded.get("digest") != identity:
            raise TxidReuseError(f"txid {txid} was already used for a different change (entry {seq})")
        if attempt["effective"] is None:
            raise LockLostError(f"fenced: txid {txid} (entry {seq}) never took effect")
        return seq

    def commit(self, op: str, ops: list, blob_refs: list[str] | None = None, txid: str | None = None) -> int:
        """Publish one signed journal entry. See :class:`CommitAuthority` for what a caller can rely on, and the module's
        docstring for why. Raises NotPermitted, without publishing, for a change every computer would ignore."""
        self._require_writable()
        self._check_request(ops, txid)
        session = self._signer()
        self._check_lock_still_held()
        identity = request_identity(op, ops, blob_refs)
        checks = [{"kind": c.kind, "id": c.id, "expect_rev": c.expect_rev} for c in ops if isinstance(c, RecordCheck)]
        writes = [p.as_json() for p in ops if not isinstance(p, RecordCheck)]
        the_txid = txid or uuid.uuid4().hex
        r = self._replica
        give_up = time.monotonic() + COLLISION_PATIENCE
        unsynced: OSError | None = None
        while True:
            self.sync()
            self._refuse_if_untrustworthy()
            session = self._signer()
            if txid is not None:
                earlier = self._earlier_attempt(txid, identity)
                if earlier is not None:
                    return earlier
            if r.revoked(self._lock_token):
                self.writable = False
                raise LockLostError("this session's turn was cleared, because it had gone quiet; nothing was written. Reopen the workspace")
            self._refuse_foreign_changes()
            for item in ops:
                if item.expect_rev is not None and r.rev(item.kind, item.id) != item.expect_rev:
                    raise ConflictError(item.kind, item.id, item.expect_rev, r.rev(item.kind, item.id))
            seq = r.head() + 1
            entry = {"seq": seq, "txid": the_txid, "time": now_iso(), "actor": self.actor(), "op": op, "records": writes,
                     "blobs": blob_refs or [], "checks": checks,
                     "request": {"version": REQUEST_IDENTITY_VERSION, "digest": identity}, "writer_token": self._lock_token}
            self._seal(entry, session)
            refusal = judge(entry, r)
            if refusal:
                raise NotPermitted(f"this change wasn't made: {refusal}")
            try:
                atomicio.publish_exclusive(self._journal_dir() / f"{seq:020d}.json", atomicio.dumps_json(entry))
                break
            except ExclusiveCreateError:
                if self._holds_entry(seq, the_txid):
                    break                            # ours: the reply was lost on the way back, which a network can do
                if time.monotonic() >= give_up:
                    raise StaleViewError(seq) from None
                time.sleep(0.05 + random.random() * 0.2)
            except OSError as exc:
                if self._holds_entry(seq, the_txid):
                    unsynced = exc                   # visible to everyone: this change happened, so never say it failed
                    break
                if not is_transient(exc) or time.monotonic() >= give_up:
                    raise                            # nothing was published: an ordinary failure, nothing happened
                time.sleep(0.05 + random.random() * 0.2)
        try:
            self.sync()
            self._base = max(self._base, seq)
            self._leave_hint(seq, entry["digest"])
        except BaseException as exc:                 # the change is published; only this computer's copy lags
            raise CommittedNotApplied(seq, the_txid, exc) from exc
        if unsynced is not None:
            raise CommittedNotApplied(seq, the_txid, unsynced) from unsynced
        return seq

    def _leave_hint(self, seq: int, digest: str) -> None:
        """Tells the next writer how far the journal has got. It's a file that already exists, so opening it asks the server,
        where the name of a new entry might get a stale "not found" from a client's cache. Only a hint: it's never believed
        past what the entries themselves prove."""
        try:
            atomicio.atomic_write_json(self.meta_dir / "locks" / "last.json", {"seq": seq, "digest": digest})
        except OSError:
            pass

    def _publish_audit(self, op: str, data: dict) -> int:
        """An entry that only records something. It holds no lock, and needs none, as it changes no record anyone reads."""
        session = self._signer()
        audit = RecordPut("audit", new_id("aud"), data, expect_rev=0)
        give_up = time.monotonic() + COLLISION_PATIENCE
        identity = request_identity(op, [audit], None)
        while True:
            self.sync()
            self._refuse_if_untrustworthy()
            seq = self._replica.head() + 1
            entry = {"seq": seq, "txid": uuid.uuid4().hex, "time": now_iso(), "actor": self.actor(), "op": op,
                     "records": [audit.as_json()], "blobs": [], "checks": [],
                     "request": {"version": REQUEST_IDENTITY_VERSION, "digest": identity}, "writer_token": None}
            self._seal(entry, session)
            try:
                atomicio.publish_exclusive(self._journal_dir() / f"{seq:020d}.json", atomicio.dumps_json(entry))
                self.sync()
                return seq
            except ExclusiveCreateError:
                if time.monotonic() >= give_up:
                    raise WorkspaceError("could not record this in the journal (too many changes at once)") from None
            except OSError as exc:
                if not is_transient(exc) or time.monotonic() >= give_up:
                    raise
            time.sleep(0.05 + random.random() * 0.2)

    # ------------------------------------------------------------------ the writer lock
    def _lock_path(self) -> Path:
        return self.meta_dir / "locks" / "writer.lock"

    def _take_lock(self, wait: float) -> None:
        """Wait for a turn. A holder that's gone silent for long enough has its lock cleared, on the record."""
        lock = self._lock_path()
        give_up = time.monotonic() + wait
        delay = 0.03
        holder: dict = {}
        while True:
            try:
                holder = Workspace.read_writer_lock(self.meta_dir) or {}
            except OSError:                          # it's being renamed right now, or the network hiccuped: look again
                holder = {"user": "?", "host": "?", "pid": "?", "acquired": "?", "token": "?", "unknown": True}
            if not holder:
                token = uuid.uuid4().hex
                claim = {**self.actor(), "token": token, "acquired": now_iso()}
                try:
                    atomicio.publish_exclusive(lock, atomicio.dumps_json(claim))
                    self._lock_token = token
                    return
                except ExclusiveCreateError:
                    if self._lock_says(token):
                        self._lock_token = token     # ours: the reply was lost on the way back
                        return
                except OSError as exc:
                    if self._lock_says(token):
                        self._lock_token = token     # the name is taken and only a later step failed: it's still a turn
                        return
                    if not is_transient(exc):
                        raise
            elif not holder.get("unknown") and lock_silence(self.meta_dir, holder) >= LEASE_SECONDS:
                self._clear_lock(holder, f"its computer showed no sign of life for {LEASE_SECONDS:.0f} seconds", automatic=True)
                continue
            if time.monotonic() >= give_up:
                raise WorkspaceLockedError(holder or {"user": "?", "host": "?", "pid": "?", "acquired": "?", "token": "?"})
            time.sleep(delay * (0.5 + random.random()))
            delay = min(delay * 1.6, 0.5)

    def _lock_says(self, token: str) -> bool:
        try:
            return (Workspace.read_writer_lock(self.meta_dir) or {}).get("token") == token
        except OSError:
            return False

    def _check_lock_still_held(self) -> None:
        """Only an answer counts here. A network filesystem saying "try again" doesn't mean the lock has gone."""
        try:
            patiently(lambda: Workspace._read_holder(self._lock_path()))
        except (FileNotFoundError, ValueError):
            pass
        except OSError as exc:
            raise WorkspaceError(f"the writer lock can't be checked just now: {exc.strerror or exc}. Nothing was written; try again") from exc
        super()._check_lock_still_held()

    def _clear_lock(self, holder: dict, reason: str, automatic: bool) -> None:
        """Move the lock aside and look at what was moved: it's that file's token that counts, not the one read earlier. If it
        isn't the same lock, that's someone else's turn, so it goes back."""
        custody = self.meta_dir / "locks" / f".clearing-{uuid.uuid4().hex}"
        try:
            patiently(lambda: os.replace(self._lock_path(), custody))
        except FileNotFoundError:
            return
        try:
            moved = Workspace._read_holder(custody)
        except (OSError, ValueError):
            moved = {"token": "?"}
        if moved.get("token") != holder.get("token"):
            try:
                atomicio.move_exclusive(custody, self._lock_path())
            except (FileExistsError, FileNotFoundError):
                atomicio.remove_quietly(custody)     # taken again already. Whoever this was finds out before committing
            return
        self._publish_audit(LOCK_CLEARED_OP, {"event": "writer-lock-cleared", "previous_holder": moved, "reason": reason,
                                              "automatic": automatic, "time": now_iso(), "by": self.actor()})
        atomicio.remove_quietly(custody)
        path = _beat_path(self.meta_dir, moved.get("token"))
        if path is not None:
            atomicio.remove_quietly(path)

    def _release_lock(self) -> None:
        lock, private = self._lock_path(), self.meta_dir / "locks" / f".releasing-{uuid.uuid4().hex}"
        try:
            patiently(lambda: os.replace(lock, private))
        except FileNotFoundError:
            return                                   # someone cleared it while this handle was stopped
        try:
            ours = Workspace._read_holder(private).get("token") == self._lock_token
        except (OSError, ValueError):
            ours = False
        if ours:
            atomicio.remove_quietly(private)
            path = _beat_path(self.meta_dir, self._lock_token)
            if path is not None:
                atomicio.remove_quietly(path)
            return
        try:
            atomicio.move_exclusive(private, lock)   # someone else's turn: put it back
        except (FileExistsError, FileNotFoundError):
            atomicio.remove_quietly(private)

    def _sweep_old_temps(self) -> None:
        for sub in ("journal", "locks", "content/sha256"):
            atomicio.cleanup_temp_files(self.meta_dir / sub, min_age_seconds=86400.0)
        cutoff = time.time() - 86400.0
        locks = self.meta_dir / "locks"
        for path in locks.iterdir() if locks.is_dir() else []:
            if path.name.startswith((".releasing-", ".clearing-", "beat-")):
                try:
                    if path.stat().st_mtime < cutoff:
                        atomicio.remove_quietly(path)
                except OSError:
                    pass

    @classmethod
    def break_lock(cls, meta: Path, expected_token: str, reason: str, session: Session | None = None,
                   state_dir: Path | str | None = None) -> dict:
        """An administrator clears the writer lock now, rather than waiting for it to expire. On the record, as ever."""
        with Workspace.open(meta, session=session, state_dir=state_dir) as ws:
            ws._signer()
            if not ws.is_admin:
                raise NotPermitted("only an administrator can break the writer lock. It clears itself once its computer has "
                                   f"shown no sign of life for {LEASE_SECONDS:.0f} seconds")
            holder = Workspace.read_writer_lock(meta)
            if holder is None:
                raise WorkspaceError("there is no writer lock to break")
            if holder.get("token") != expected_token:
                raise WorkspaceError("the writer lock changed since it was inspected; inspect it again")
            ws._clear_lock(holder, reason, automatic=False)
            return holder

    # ------------------------------------------------------------------ this computer's copy
    @classmethod
    def forget_local(cls, meta: Path, state_dir: Path | str | None = None) -> None:
        """Throw away this computer's copy of the journal. Everything is read and checked again next time. That's the right thing
        after the shared folder was deliberately restored from a backup, and only then."""
        meta = Workspace.meta_dir_for(meta)
        info = atomicio.read_json_object(meta / "workspace.json")
        replica = Replica(replica_path(meta, info["workspace_id"], state_dir))
        replica.close()
        replica._delete_files()

    @classmethod
    def repair(cls, meta: Path, state_dir: Path | str | None = None, remove_damaged: bool = False) -> dict:
        """Put back journal entries the shared folder has lost, from this computer's copy. With ``remove_damaged``, also take out of the
        way a next entry that nobody can have used, because no computer accepts it: it's kept in quarantine, not destroyed. That's what
        unblocks everyone when something wrote nonsense there, and it's for when this computer's history is otherwise intact."""
        with Workspace.open(meta, state_dir=state_dir) as ws:
            report = ws.verify(repair=True)
            removed = []
            if remove_damaged:
                ws._check_same_history()
                quarantine = ws.meta_dir / "quarantine" / uuid.uuid4().hex[:12]
                while ws._damage is not None and len(removed) < 1000:
                    seq = ws._replica.head() + 1
                    quarantine.mkdir(parents=True, exist_ok=True)
                    path = ws._journal_dir() / f"{seq:020d}.json"
                    atomicio.retry_sharing(lambda p=path: os.replace(p, quarantine / p.name))
                    removed.append(seq)
                    ws.sync(fresh=True)
                if removed:
                    report = ws.verify()
            report["removed"] = removed
            return report
