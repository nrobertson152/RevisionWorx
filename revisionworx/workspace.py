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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import atomicio
from .atomicio import ExclusiveCreateError

FORMAT_NAME = "RevisionWorx Workspace"
FORMAT_VERSION = 1
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


class LockLostError(WorkspaceError):
    """This handle's writer lock was broken or replaced; it must not write any more."""


class CommittedNotApplied(WorkspaceError):
    """The change IS durably committed (journal entry exists) but materialising it failed.

    It will be completed automatically on the next writable open. Callers must not retry it as if
    it had failed."""

    def __init__(self, seq: int, txid: str, cause: BaseException):
        self.seq, self.txid, self.cause = seq, txid, cause
        super().__init__(f"change {seq} (tx {txid}) is saved, but updating derived records failed: "
                         f"{cause}. It will be completed when the workspace is next opened for writing.")


def entry_digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "digest"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def strip_rev(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != REV_FIELD}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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

    def __init__(self, meta_dir: Path, info: dict, writable: bool, lock_token: str | None):
        self.meta_dir = meta_dir
        self.info = info
        self.writable = writable
        self._lock_token = lock_token
        self._closed = False

    # ------------------------------------------------------------------ lifecycle
    @property
    def id(self) -> str:
        return self.info["workspace_id"]

    @property
    def name(self) -> str:
        return self.info.get("name", "")

    @staticmethod
    def meta_dir_for(location: Path) -> Path:
        location = Path(location)
        return location if location.name == META_DIRNAME else location / META_DIRNAME

    @classmethod
    def create(cls, location: Path, name: str) -> "Workspace":
        meta = cls.meta_dir_for(location)
        meta.parent.mkdir(parents=True, exist_ok=True)
        try:
            meta.mkdir()
        except FileExistsError as exc:
            raise WorkspaceError(f"{meta} already exists; open it instead of creating a new workspace") from exc
        for sub in ("journal", "records", "blobs", "locks"):
            (meta / sub).mkdir()
        info = {"format": FORMAT_NAME, "format_version": FORMAT_VERSION,
                "workspace_id": new_id("ws"), "name": name, "created": now_iso(),
                "created_by": current_actor()}
        atomicio.publish_exclusive(meta / "workspace.json", atomicio.dumps_json(info))
        atomicio.atomic_write_json(meta / "state.json", {"applied_seq": 0})
        ws = cls.open(meta, writable=True)
        ws.commit("workspace.create", [RecordPut("settings", "workspace", default_settings())])
        return ws

    @classmethod
    def open(cls, location: Path, writable: bool = False) -> "Workspace":
        meta = cls.meta_dir_for(location)
        info_path = meta / "workspace.json"
        if not info_path.is_file():
            raise WorkspaceError(f"no RevisionWorx workspace at {meta}")
        info = atomicio.read_json(info_path)
        if info.get("format") != FORMAT_NAME:
            raise WorkspaceFormatError(f"{info_path} is not a RevisionWorx workspace")
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
            ws.close()
            raise
        return ws

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._lock_token:
            lock = self.meta_dir / "locks" / "writer.lock"
            try:
                holder = atomicio.read_json(lock)
            except (OSError, ValueError):
                holder = None
            if holder and holder.get("token") == self._lock_token:
                self._unlink_own_lock(lock)
            self._lock_token = None

    LOCK_RELEASE_ATTEMPTS = 50
    LOCK_RELEASE_DELAY = 0.02

    def _unlink_own_lock(self, lock: Path) -> None:
        """Delete our own lock, retrying transient Windows sharing violations (issue #10).

        On Windows another process briefly opening ``writer.lock`` to read it makes ``unlink`` fail
        with WinError 32. Ownership is re-verified before every attempt, so another writer's lock is
        never deleted. If the lock still cannot be removed, it stays held and the error is reported:
        an administrator can break it (audited)."""
        import time
        last: OSError | None = None
        for _ in range(self.LOCK_RELEASE_ATTEMPTS):
            try:
                holder = atomicio.read_json(lock)
            except FileNotFoundError:
                return
            except (PermissionError, ValueError) as exc:  # being read/written right now: retry
                last = exc if isinstance(exc, OSError) else None
                time.sleep(self.LOCK_RELEASE_DELAY)
                continue
            if holder.get("token") != self._lock_token:
                return  # no longer ours: never delete another writer's lock
            try:
                lock.unlink()
                return
            except FileNotFoundError:
                return
            except PermissionError as exc:
                last = exc
                time.sleep(self.LOCK_RELEASE_DELAY)
        raise WorkspaceError(f"could not release the writer lock after {self.LOCK_RELEASE_ATTEMPTS} attempts "
                             f"({last}); it is still held by this session. An administrator can break it.")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ locking
    @staticmethod
    def _acquire_writer_lock(meta: Path) -> str:
        token = uuid.uuid4().hex
        holder = {**current_actor(), "token": token, "acquired": now_iso()}
        lock = meta / "locks" / "writer.lock"
        try:
            atomicio.publish_exclusive(lock, atomicio.dumps_json(holder))
        except ExclusiveCreateError:
            try:
                existing = atomicio.read_json(lock)
            except (OSError, ValueError):
                existing = {"user": "?", "host": "?", "pid": "?", "acquired": "?", "token": "?"}
            if existing.get("token") in Workspace._revoked_tokens(meta):
                # Revocation is already authoritative in the journal; this file is only evidence.
                os.replace(lock, lock.with_name(f"writer.lock.revoked-{uuid.uuid4().hex[:8]}"))
                return Workspace._acquire_writer_lock(meta)
            raise WorkspaceLockedError(existing) from None
        return token

    @staticmethod
    def _revoked_tokens(meta: Path) -> set[str]:
        probe = Workspace(meta, {"workspace_id": "probe"}, False, None)
        return {tok for _, tok in probe._revocations()}

    @classmethod
    def read_writer_lock(cls, location: Path) -> dict | None:
        lock = cls.meta_dir_for(location) / "locks" / "writer.lock"
        try:
            return atomicio.read_json(lock)
        except FileNotFoundError:
            return None

    @classmethod
    def break_writer_lock(cls, location: Path, expected_token: str, reason: str) -> dict:
        """Administrative recovery of an abandoned writer lock.

        The caller must pass the exact token currently in the lock (as displayed to the
        administrator), so a lock that was released and re-acquired by someone else in the meantime
        is never broken by mistake. The break is recorded in the journal.
        """
        if not reason.strip():
            raise WorkspaceError("a reason is required to break a writer lock")
        meta = cls.meta_dir_for(location)
        lock = meta / "locks" / "writer.lock"
        holder = cls.read_writer_lock(meta)
        if holder is None:
            raise WorkspaceError("there is no writer lock to break")
        if holder.get("token") != expected_token:
            raise WorkspaceError("the writer lock changed since it was inspected; inspect it again")
        broken = lock.with_name(f"writer.lock.broken-{uuid.uuid4().hex[:8]}")
        # Linearization point (issue #8, ADR-015 rev.2): the revocation takes effect when its journal
        # entry is published. Exclusive sequence publication orders it against any concurrent
        # publication by the revoked writer: entries before it were legitimately committed while the
        # lock was valid; entries after it carry a revoked token and are fenced everywhere.
        probe = cls(meta, atomicio.read_json(meta / "workspace.json"), False, None)
        probe._check_journal()
        audit = RecordPut("audit", new_id("aud"), {
            "event": "writer-lock-broken", "previous_holder": holder, "reason": reason,
            "evidence": broken.name, "time": now_iso(), "by": current_actor()}, expect_rev=0)
        for _attempt in range(1000):
            seq = probe.head_seq() + 1
            entry = {"seq": seq, "txid": uuid.uuid4().hex, "time": now_iso(), "actor": current_actor(),
                     "op": "workspace.lock.break", "records": [audit.as_json()], "blobs": [],
                     "writer_token": None, "revokes": expected_token}
            entry["digest"] = entry_digest(entry)
            try:
                atomicio.publish_exclusive(meta / "journal" / f"{seq:020d}.json", atomicio.dumps_json(entry))
                break
            except ExclusiveCreateError:
                continue  # the revoked writer published first: that entry precedes revocation
        else:
            raise WorkspaceError("could not publish the lock revocation (journal contention)")
        # Evidence only; safe if it races: acquisition also treats a revoked-token lock as stale.
        current = cls.read_writer_lock(meta)
        if current and current.get("token") == expected_token:
            try:
                os.replace(lock, broken)
            except OSError:
                pass
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
        if seqs and seqs != list(range(1, len(seqs) + 1)):
            missing = sorted(set(range(1, seqs[-1] + 1)) - set(seqs))[:10]
            raise WorkspaceCorruptError(f"journal has gaps (missing entries {missing}); writes refused")

    def head_seq(self) -> int:
        seqs = self.journal_seqs()
        return seqs[-1] if seqs else 0

    def read_entry(self, seq: int) -> dict:
        path = self._journal_dir() / f"{seq:020d}.json"
        try:
            entry = atomicio.read_json(path)
        except ValueError as exc:
            raise WorkspaceCorruptError(f"journal entry {seq} is unreadable: {exc}") from exc
        if entry.get("seq") != seq:
            raise WorkspaceCorruptError(f"journal entry {path.name} claims seq {entry.get('seq')}")
        if "digest" in entry and entry["digest"] != entry_digest(entry):
            raise WorkspaceCorruptError(f"journal entry {seq} fails its SHA-256 digest check (altered?)")
        return entry

    def _revocations(self) -> list[tuple[int, str]]:
        """(seq, token) of every audited writer-lock break, in journal order (issue #8 fence)."""
        head = self.head_seq()
        cached = getattr(self, "_revocation_cache", None)
        if cached and cached[0] == head:
            return cached[1]
        out = []
        for s in self.journal_seqs():
            e = self.read_entry(s)
            if e.get("op") == "workspace.lock.break":
                for item in e["records"]:
                    tok = (item.get("data") or {}).get("previous_holder", {}).get("token")
                    if tok:
                        out.append((s, tok))
        self._revocation_cache = (head, out)
        return out

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
            return int(atomicio.read_json(self.meta_dir / "state.json")["applied_seq"])
        except FileNotFoundError:
            return 0

    def recover(self) -> int:
        """Finish any commit interrupted by a crash. Returns the number of entries replayed."""
        self._require_writable()
        for sub in ("journal", "locks", "blobs"):
            atomicio.cleanup_temp_files(self.meta_dir / sub)
        records = self.meta_dir / "records"
        for kind_dir in records.iterdir() if records.is_dir() else []:
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
                target.unlink(missing_ok=True)
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
            holder = atomicio.read_json(self.meta_dir / "locks" / "writer.lock")
        except (OSError, ValueError):
            holder = None
        if not holder or holder.get("token") != self._lock_token:
            self.writable = False
            raise LockLostError("this session no longer holds the workspace writer lock (it was broken "
                                "by an administrator or replaced); reopen the workspace")

    def _current_rev(self, kind: str, rid: str) -> int:
        data = self.get(kind, rid)
        return int(data.get(REV_FIELD, 0)) if data is not None else 0

    def commit(self, op: str, ops: list, blob_refs: list[str] | None = None, txid: str | None = None) -> int:
        """Durably apply record puts/deletes as one journal entry. Returns its sequence number.

        Raises ConflictError (nothing written) if an ``expect_rev`` does not hold, LockLostError if
        the writer lock was lost, and CommittedNotApplied if the entry was committed but could not be
        materialised."""
        self._require_writable()
        for item in ops:
            if item.kind not in KINDS:
                raise ValueError(f"unknown record kind {item.kind!r}")
            if not _ID_RE.match(item.id):
                raise ValueError(f"invalid record id {item.id!r}")
        if txid is not None and not re.match(r"^[0-9a-f]{32}$", txid):
            raise ValueError("txid must be 32 lowercase hex characters")
        if self.applied_seq() != self.head_seq():
            self.recover()
        self._check_lock_still_held()
        if self._lock_token in {t for _, t in self._revocations()}:
            self.writable = False
            raise LockLostError("this session's writer lock was revoked by an administrator; reopen the workspace")
        for item in ops:
            if item.expect_rev is not None:
                actual = self._current_rev(item.kind, item.id)
                if actual != item.expect_rev:
                    raise ConflictError(item.kind, item.id, item.expect_rev, actual)
        seq = self.head_seq() + 1
        entry = {"seq": seq, "txid": txid or uuid.uuid4().hex, "time": now_iso(), "actor": current_actor(),
                 "op": op, "records": [p.as_json() for p in ops], "blobs": blob_refs or [],
                 "writer_token": self._lock_token}
        entry["digest"] = entry_digest(entry)
        try:
            atomicio.publish_exclusive(self._journal_dir() / f"{seq:020d}.json", atomicio.dumps_json(entry))
        except ExclusiveCreateError as exc:
            if self._lock_token in {t for _, t in self._revocations()}:
                self.writable = False  # an administrator's revocation took this sequence first
                raise LockLostError("this session's writer lock was revoked by an administrator before "
                                    "its change was published; nothing was written") from exc
            # Another process wrote this sequence: the single-writer invariant was violated.
            raise WorkspaceCorruptError(
                f"journal entry {seq} was created by another writer; refusing to continue") from exc
        if self.is_fenced(entry):
            self.writable = False
            raise LockLostError(f"fenced: entry {seq} was published after this session's writer lock was "
                                "broken, so it is ignored by every reader and replay; nothing took effect")
        try:
            self._apply(entry)
        except BaseException as exc:  # commit point already passed: report it as committed
            raise CommittedNotApplied(seq, entry["txid"], exc) from exc
        return seq

    def transaction_outcome(self, txid: str) -> dict:
        """Durable outcome of a transaction the caller tagged with ``txid``."""
        for s in reversed(self.journal_seqs()):
            entry = self.read_entry(s)
            if entry.get("txid") == txid:
                return {"state": "fenced" if self.is_fenced(entry) else "committed", "seq": s}
        return {"state": "not-found"}

    def verify(self, repair: bool = False) -> dict:
        """Replay the whole journal and compare with the materialised records.

        Reports entries without digests, records that differ from the journal, records missing, and
        record files the journal never wrote. With ``repair`` (writable only), rewrites records from
        the journal, moves unexplained files to ``quarantine/`` (never deletes them) and journals an
        audit record of what was repaired."""
        if repair:
            self._require_writable()
            self.recover()
        expected: dict[tuple[str, str], dict | None] = {}
        problems: list[dict] = []
        for s in self.journal_seqs():
            entry = self.read_entry(s)
            if "digest" not in entry:
                problems.append({"problem": "entry-without-digest", "seq": s})
            if self.is_fenced(entry):
                problems.append({"problem": "fenced-entry", "seq": s, "op": entry.get("op")})
                continue
            for item in entry["records"]:
                key = (item["kind"], item["id"])
                expected[key] = None if item.get("delete") else {**item["data"], REV_FIELD: s}
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
        report = {"head_seq": self.head_seq(), "problems": problems, "repaired": False}
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
        d.mkdir(parents=True, exist_ok=True)
        atomicio.publish_exclusive(d / f"{blob_id}.json", atomicio.dumps_json(data))
        return f"{kind}/{blob_id}"

    def read_blob(self, ref: str) -> Any:
        kind, blob_id = ref.split("/", 1)
        if not _ID_RE.match(blob_id) or not re.match(r"^[a-z]+$", kind):
            raise ValueError(f"invalid blob reference {ref!r}")
        return atomicio.read_json(self.meta_dir / "blobs" / kind / f"{blob_id}.json")

    # ------------------------------------------------------------------ reads
    def _pending_overlay(self) -> dict[tuple[str, str], dict | None]:
        overlay: dict[tuple[str, str], dict | None] = {}
        for entry in self.entries_after(self.applied_seq()):
            for item in entry["records"]:
                overlay[(item["kind"], item["id"])] = (
                    None if item.get("delete") else {**item["data"], REV_FIELD: entry["seq"]})
        return overlay

    def get(self, kind: str, rid: str) -> dict | None:
        overlay = self._pending_overlay()
        if (kind, rid) in overlay:
            return overlay[(kind, rid)]
        if not _ID_RE.match(rid):
            return None
        path = self.meta_dir / "records" / kind / f"{rid}.json"
        try:
            return atomicio.read_json(path)
        except FileNotFoundError:
            return None

    def list(self, kind: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        kind_dir = self.meta_dir / "records" / kind
        if kind_dir.is_dir():
            for entry in os.scandir(kind_dir):
                if entry.name.endswith(".json") and not entry.name.startswith("."):
                    out[entry.name[:-5]] = atomicio.read_json(Path(entry.path))
        for (k, rid), data in self._pending_overlay().items():
            if k == kind:
                if data is None:
                    out.pop(rid, None)
                else:
                    out[rid] = data
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
