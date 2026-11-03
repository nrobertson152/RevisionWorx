# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence adapter contract for change control.

The service never talks to files. It hands one :class:`Commit` per user action to a :class:`ChangeStore`,
which must apply all of it or none of it, and must refuse it (``ConflictError``, nothing written) if any
``expect_rev`` no longer holds. Two implementations:

* :class:`WorkspaceStore` - the production adapter. It is the ONLY module in this package that touches the
  core store, and it does so only through the interface core subsystems already use (``register_kind``,
  ``Workspace.get/list/commit`` with ``RecordPut``/``RecordCheck``/``expect_rev``/``txid`` and
  ``transaction_outcome``). Every change-control write is therefore one journal entry, published under the
  workspace writer lock with fencing, replayed after a crash and checked by ``verify()``. Nothing here
  reads or writes journal, lock or record files directly.
* :class:`MemoryStore` - same semantics in memory, for unit tests, GUI view-model tests and fixtures.
"""
from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from typing import Protocol

from ..workspace import ConflictError, RecordCheck, RecordPut, Workspace, register_kind

# logical kind -> workspace record kind (register_kind requires [a-z]{2,16})
KIND_MAP = {"ecr": "changereq", "eco": "changeord", "event": "changeevt", "number": "changenum",
            "config": "changecfg"}
for _k in KIND_MAP.values():
    register_kind(_k)

CORE_PREFIX = "core:"   # a Check on a core record, e.g. Check("core:document", doc_id, rev)


@dataclass(frozen=True)
class Put:
    kind: str
    id: str
    data: dict
    expect_rev: int | None = None       # None: unchecked; 0: must not exist; n: must be at n


@dataclass(frozen=True)
class Check:
    kind: str
    id: str
    expect_rev: int


@dataclass
class Commit:
    op: str
    puts: list[Put] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)


class ChangeStore(Protocol):
    workspace_id: str

    def get(self, kind: str, rid: str) -> dict | None: ...
    def list(self, kind: str) -> dict[str, dict]: ...
    def commit(self, c: Commit, txid: str | None = None) -> int: ...
    def outcome(self, txid: str) -> dict: ...


class WorkspaceStore:
    """Adapter onto an open :class:`~revisionworx.workspace.Workspace` handle (read-only or writable).

    Core exceptions propagate unchanged, so callers handle them exactly as CORE_API.md describes:
    ``ConflictError`` (nothing written), ``LockLostError`` (nothing took effect), ``ReadOnlyError``,
    ``WorkspaceCorruptError`` (recovery-blocked) and ``CommittedNotApplied`` (the change IS saved)."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.workspace_id = ws.id

    def get(self, kind: str, rid: str) -> dict | None:
        if kind.startswith(CORE_PREFIX):
            return self.ws.get(kind[len(CORE_PREFIX):], rid)
        return self.ws.get(KIND_MAP[kind], rid)

    def list(self, kind: str) -> dict[str, dict]:
        return self.ws.list(KIND_MAP[kind])

    def commit(self, c: Commit, txid: str | None = None) -> int:
        ops: list = [RecordPut(KIND_MAP[p.kind], p.id, p.data, expect_rev=p.expect_rev) for p in c.puts]
        for chk in c.checks:
            kind = chk.kind[len(CORE_PREFIX):] if chk.kind.startswith(CORE_PREFIX) else KIND_MAP[chk.kind]
            ops.append(RecordCheck(kind, chk.id, chk.expect_rev))
        return self.ws.commit(c.op, ops, txid=txid)

    def outcome(self, txid: str) -> dict:
        return self.ws.transaction_outcome(txid)


class MemoryStore:
    """In-memory store with the same all-or-nothing and ``expect_rev`` semantics.

    ``core`` holds fake core records (``{"document": {id: record}}``) for :class:`Check` on core kinds.
    ``fail_next_commit`` injects a failure *before* the commit point (nothing written), for tests."""

    def __init__(self, workspace_id: str = "ws_memory"):
        self.workspace_id = workspace_id
        self.records: dict[str, dict[str, dict]] = {}
        self.core: dict[str, dict[str, dict]] = {}
        self.seq = 0
        self.txids: dict[str, int] = {}
        self.log: list[tuple[int, str]] = []
        self.fail_next_commit: BaseException | None = None
        self._lock = threading.Lock()

    def _table(self, kind: str) -> dict[str, dict]:
        if kind.startswith(CORE_PREFIX):
            return self.core.setdefault(kind[len(CORE_PREFIX):], {})
        if kind not in KIND_MAP:
            raise ValueError(f"unknown record kind {kind!r}")
        return self.records.setdefault(kind, {})

    def get(self, kind: str, rid: str) -> dict | None:
        rec = self._table(kind).get(rid)
        return copy.deepcopy(rec) if rec is not None else None

    def list(self, kind: str) -> dict[str, dict]:
        return copy.deepcopy(self._table(kind))

    def commit(self, c: Commit, txid: str | None = None) -> int:
        with self._lock:
            if txid is not None and txid in self.txids:
                return self.txids[txid]
            if self.fail_next_commit is not None:
                exc, self.fail_next_commit = self.fail_next_commit, None
                raise exc
            expectations = [(p.kind, p.id, p.expect_rev) for p in c.puts if p.expect_rev is not None]
            expectations += [(k.kind, k.id, k.expect_rev) for k in c.checks]
            for kind, rid, want in expectations:
                rec = self._table(kind).get(rid)
                actual = int(rec.get("_rev", 0)) if rec is not None else 0
                if actual != want:
                    raise ConflictError(KIND_MAP.get(kind, kind), rid, want, actual)
            self.seq += 1
            for p in c.puts:
                self._table(p.kind)[p.id] = {**copy.deepcopy(p.data), "_rev": self.seq}
            if txid is not None:
                self.txids[txid] = self.seq
            self.log.append((self.seq, c.op))
            return self.seq

    def outcome(self, txid: str) -> dict:
        if txid in self.txids:
            return {"state": "committed", "seq": self.txids[txid]}
        return {"state": "not-found"}
