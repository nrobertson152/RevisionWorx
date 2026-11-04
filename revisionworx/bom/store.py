# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence contract for the parts catalog and engineering BOM (PROPOSED, pending Claude A's review).

The service never touches files. Each user action becomes **one** commit of puts, deletes and checks,
applied all-or-nothing, and refused (:class:`StoreConflict`, nothing written) if any ``expect_rev`` no
longer holds. Optimistic checks make the invariants hold even when two editors race:

* ``partnum`` claim records (``expect_rev=0`` on create) make part numbers unique;
* every BOM line change bumps the ``bomstruct`` record, so two edits that are each acyclic but form a
  cycle together cannot both commit;
* ``partseq`` counters issue each automatic number once.

Implementations:

* :class:`MemoryCatalogStore` - same semantics in memory (tests, GUI view-model tests, what-if sessions).
* :class:`WorkspaceCatalogStore` - the core workspace. It uses only ``register_kind`` and
  ``Workspace.get/list/commit`` with ``RecordPut``/``RecordDelete``/``RecordCheck``, ``expect_rev`` and
  ``txid`` - the path the document subsystem and change control (PR #20) use. Every write is therefore one
  journal entry under the core writer lock, fenced and replayed by the core. This module never reads or
  writes journal, lock or record files itself. Core exceptions other than ``ConflictError`` (for example
  ``LockLostError``, ``CommittedNotApplied``, ``ReadOnlyError``, ``WorkspaceCorruptError``) propagate
  unchanged so the GUI handles them exactly as CORE_API.md section 4 describes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol, Union

from .catalog import number_key
from .errors import BomError

KINDS = ("part", "bomline", "partnum", "bomstruct", "partseq", "partrule")
STRUCT_ID = "default"
_TXID_RE = re.compile(r"[0-9a-f]{32}")


class StoreConflict(BomError):
    """A record changed since it was read (or already exists); nothing was written."""

    def __init__(self, kind: str, rid: str, expected: int | None, actual: int):
        self.kind, self.id, self.expected, self.actual = kind, rid, expected, actual
        super().__init__(f"{kind} {rid} changed since it was read (expected revision {expected}, found {actual}); "
                         "reload and try again")


class StoreError(BomError):
    """Programming or contract error (unknown kind, txid reused for a different request)."""


@dataclass(frozen=True)
class Put:
    kind: str
    id: str
    data: dict
    expect_rev: int | None = None      # None: unchecked; 0: must not exist; n: must be at revision n


@dataclass(frozen=True)
class Delete:
    kind: str
    id: str
    expect_rev: int | None = None


@dataclass(frozen=True)
class Check:
    kind: str
    id: str
    expect_rev: int


Op = Union[Put, Delete, Check]


@dataclass
class Commit:
    op: str
    ops: list[Op] = field(default_factory=list)


class CatalogStore(Protocol):
    def get(self, kind: str, rid: str) -> dict | None:
        """The record with its ``_rev``, or None. ``_rev`` is an opaque positive version token: pass back the
        value you read as ``expect_rev``. (The memory store counts 1, 2, ...; the workspace store uses the
        journal sequence of the record's last write.)"""

    def list(self, kind: str) -> dict[str, dict]:
        """All records of a kind, by id, each with ``_rev``."""

    def commit(self, commit: Commit, txid: str | None = None) -> int:
        """Apply all of it or none of it. Returns a sequence number. Same txid + same request -> same
        result without a second write (crash-safe retry)."""


def partnum_id(number: str) -> str:
    return "pnum_" + hashlib.sha256(number_key(number).encode("utf-8")).hexdigest()[:32]


def _identity(commit: Commit) -> str:
    def enc(o: Op) -> dict:
        d = {"t": type(o).__name__, "kind": o.kind, "id": o.id, "expect_rev": o.expect_rev}
        if isinstance(o, Put):
            d["data"] = {k: v for k, v in o.data.items() if k != "_rev"}
        return d
    raw = json.dumps({"op": commit.op, "ops": [enc(o) for o in commit.ops]}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_ops(commit: Commit, txid: str | None) -> None:
    for o in commit.ops:
        if o.kind not in KINDS:
            raise StoreError(f"unknown record kind {o.kind!r}")
    if txid is not None and not _TXID_RE.fullmatch(txid):
        raise StoreError("txid must be 32 lowercase hex characters")


class MemoryCatalogStore:
    """In-memory store with the same all-or-nothing and expect_rev semantics as the workspace store."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self._seq = 0
        self._txids: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()
        self.fail_next: Exception | None = None   # fault injection for tests: raised before anything applies

    def get(self, kind: str, rid: str) -> dict | None:
        rec = self._data[kind].get(rid)
        return copy.deepcopy(rec) if rec is not None else None

    def list(self, kind: str) -> dict[str, dict]:
        return copy.deepcopy(self._data[kind])

    def commit(self, commit: Commit, txid: str | None = None) -> int:
        _check_ops(commit, txid)
        identity = _identity(commit)
        with self._lock:
            if txid is not None and txid in self._txids:
                prior_identity, seq = self._txids[txid]
                if prior_identity != identity:
                    raise StoreError(f"txid {txid} was already used for a different change")
                return seq
            for o in commit.ops:
                actual = self._data[o.kind].get(o.id, {}).get("_rev", 0)
                if o.expect_rev is not None and actual != o.expect_rev:
                    raise StoreConflict(o.kind, o.id, o.expect_rev, actual)
            if self.fail_next is not None:
                exc, self.fail_next = self.fail_next, None
                raise exc
            for o in commit.ops:
                if isinstance(o, Put):
                    rev = self._data[o.kind].get(o.id, {}).get("_rev", 0) + 1
                    rec = json.loads(json.dumps({k: v for k, v in o.data.items() if k != "_rev"}))  # JSON-safe copy
                    rec["_rev"] = rev
                    self._data[o.kind][o.id] = rec
                elif isinstance(o, Delete):
                    self._data[o.kind].pop(o.id, None)
            self._seq += 1
            if txid is not None:
                self._txids[txid] = (identity, self._seq)
            return self._seq


class WorkspaceCatalogStore:
    """Catalog records in a core workspace (one journal entry per action). Reading needs only a read-only
    handle; :meth:`commit` needs a writable one and raises the core's ``ReadOnlyError`` otherwise."""

    def __init__(self, ws: Any):
        from revisionworx import workspace as core
        for k in KINDS:
            core.register_kind(k)
        self.ws = ws
        self._core = core

    def get(self, kind: str, rid: str) -> dict | None:
        return self.ws.get(kind, rid)

    def list(self, kind: str) -> dict[str, dict]:
        return self.ws.list(kind)

    def commit(self, commit: Commit, txid: str | None = None) -> int:
        _check_ops(commit, txid)
        core = self._core
        ops: list = []
        for o in commit.ops:
            if isinstance(o, Put):
                ops.append(core.RecordPut(o.kind, o.id, {k: v for k, v in o.data.items() if k != "_rev"}, o.expect_rev))
            elif isinstance(o, Delete):
                ops.append(core.RecordDelete(o.kind, o.id, o.expect_rev))
            else:
                ops.append(core.RecordCheck(o.kind, o.id, o.expect_rev))
        try:
            return self.ws.commit(commit.op, ops, txid=txid)
        except core.ConflictError as exc:
            raise StoreConflict(exc.kind, exc.id, exc.expected, exc.actual) from exc
        except core.TxidReuseError as exc:
            raise StoreError(str(exc)) from exc
