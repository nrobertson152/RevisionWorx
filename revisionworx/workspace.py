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
"""
from __future__ import annotations

import getpass
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

    def as_json(self) -> dict:
        return {"kind": self.kind, "id": self.id, "data": self.data}


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
                lock.unlink(missing_ok=True)
            self._lock_token = None

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
            raise WorkspaceLockedError(existing) from None
        return token

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
        os.replace(lock, broken)  # keep evidence instead of deleting
        ws = cls.open(meta, writable=True)
        try:
            ws.commit("workspace.lock.break", [RecordPut("audit", new_id("aud"), {
                "event": "writer-lock-broken", "previous_holder": holder, "reason": reason,
                "evidence": broken.name, "time": now_iso(), "by": current_actor()})])
        finally:
            ws.close()
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
        return entry

    def entries_after(self, seq: int) -> Iterable[dict]:
        for s in self.journal_seqs():
            if s > seq:
                yield self.read_entry(s)

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
        return count

    def _apply(self, entry: dict) -> None:
        for put in entry["records"]:
            kind_dir = self.meta_dir / "records" / put["kind"]
            kind_dir.mkdir(exist_ok=True)
            atomicio.atomic_write_json(kind_dir / f"{put['id']}.json", put["data"])
        atomicio.atomic_write_json(self.meta_dir / "state.json", {"applied_seq": entry["seq"]})

    def _require_writable(self) -> None:
        if self._closed:
            raise WorkspaceError("workspace is closed")
        if not self.writable:
            raise ReadOnlyError("workspace is open read-only")

    def commit(self, op: str, puts: list[RecordPut], blob_refs: list[str] | None = None) -> int:
        """Durably apply a set of record puts as one journal entry. Returns its sequence number."""
        self._require_writable()
        for put in puts:
            if put.kind not in KINDS:
                raise ValueError(f"unknown record kind {put.kind!r}")
            if not _ID_RE.match(put.id):
                raise ValueError(f"invalid record id {put.id!r}")
        if self.applied_seq() != self.head_seq():
            self.recover()
        seq = self.head_seq() + 1
        entry = {"seq": seq, "txid": uuid.uuid4().hex, "time": now_iso(), "actor": current_actor(),
                 "op": op, "records": [p.as_json() for p in puts], "blobs": blob_refs or []}
        try:
            atomicio.publish_exclusive(self._journal_dir() / f"{seq:020d}.json", atomicio.dumps_json(entry))
        except ExclusiveCreateError as exc:
            # Another process wrote this sequence: the single-writer invariant was violated.
            raise WorkspaceCorruptError(
                f"journal entry {seq} was created by another writer; refusing to continue") from exc
        self._apply(entry)
        return seq

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
    def _pending_overlay(self) -> dict[tuple[str, str], dict]:
        overlay: dict[tuple[str, str], dict] = {}
        for entry in self.entries_after(self.applied_seq()):
            for put in entry["records"]:
                overlay[(put["kind"], put["id"])] = put["data"]
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
                out[rid] = data
        return out

    def settings(self) -> dict:
        s = default_settings()
        s.update(self.get("settings", "workspace") or {})
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
