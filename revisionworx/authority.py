# SPDX-License-Identifier: GPL-3.0-or-later
"""The commit-authority contract (ADR-014 seam, ADR-017).

Every authoritative mutation in RevisionWorx goes through one call: ``commit(op, ops, blob_refs,
txid)``. Phase 2 code (``documents.py``) depends only on this contract, never on the journal's
files. Two implementations are planned:

* ``Workspace`` (file journal) - standalone single-user mode, implemented.
* An optional on-premise LAN coordinator - approved (issue #5), not implemented.

A conforming implementation must pass ``tests/contract_commit_authority.py``. Semantics:

1. **Atomicity:** all ``RecordPut``/``RecordDelete`` in one call take effect together or not at all.
2. **Optimistic concurrency:** every ``expect_rev`` (``RecordPut``, ``RecordDelete``,
   ``RecordCheck``) is checked against authoritative state *inside* the commit; any mismatch raises
   ``ConflictError`` and nothing is written. ``0`` means "must not exist".
3. **Ordering:** each commit returns a strictly increasing sequence number; readers observe commits
   in that order. Records carry ``_rev`` = the sequence that last wrote them.
4. **Fencing:** after the caller's write authority is revoked, a commit raises ``LockLostError`` and
   has no effect, even if a physical artifact is left behind.
5. **Outcome:** after any exception the caller can ask ``transaction_outcome(txid)`` ->
   ``committed`` / ``fenced`` / ``not-found``. ``CommittedNotApplied`` means committed.
6. **Idempotency:** repeating ``commit`` with the same ``txid`` and identical content returns the
   original sequence and writes nothing; the same ``txid`` with different content raises
   ``TxidReuseError``.
"""
from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class CommitAuthority(Protocol):
    def commit(self, op: str, ops: list, blob_refs: list[str] | None = None, txid: str | None = None) -> int: ...

    def transaction_outcome(self, txid: str) -> dict: ...

    def head_seq(self) -> int: ...

    def get(self, kind: str, rid: str) -> dict | None: ...

    def list(self, kind: str) -> dict[str, dict]: ...

    def entries_after(self, seq: int) -> Iterable[dict]: ...
