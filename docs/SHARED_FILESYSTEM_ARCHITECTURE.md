# Shared Filesystem Architecture: Research Decision

**Status: research only, no multi-user deployment approval.** Phase 1 (`phase1-foundation`, draft PR #6) has an implemented local-filesystem journal and writer lock; Phase 2 (`phase2-documents`, stacked draft PR #7) adds checkouts and immutable revisions. Neither branch certifies SMB/NFS nor closes the administrative lock-break TOCTOU publication race. A shared folder is not a transactional database.

## System map

- Desktop clients on Windows and Linux; local SQLite is a disposable search index only.
- Shared authoritative .revisionworx stores immutable content and manifests, checkout ownership, transactions, committed journal records and epoch.
- Every write needs a proven cross-machine exclusive authority gate, fencing tokens and crash-recoverable commit protocol.
- Stop authoritative writes when authority or durability is uncertain.

## Safety invariants

1. At most one effective checkout authority per document; all mutations serialized under a proven cross-host gate.
2. A checkout must bind owner, random token, monotonic per-document fencing generation, workspace epoch and expected base revision.
3. The **authoritative generation, owner, epoch and expected record revisions must be checked atomically with commit-marker publication**, not as an earlier preflight; otherwise an administrator may revoke between checking and publishing. Never infer permission from a local SQLite cache.
4. Publish all immutable CAD content, metadata, BOM/dependencies and audit intent before a single durable commit marker containing a complete manifest hash.
5. The commit marker is the sole logical visibility point. Heads and search change records are rebuildable projections; readers must not treat unrecovered projections as current.
6. Recovery must refuse writes when any committed blob is missing, any hash disagrees, sequence continuity is broken, or workspace epoch unexpectedly changes.
7. Offline clients may retain isolated working copies but cannot acquire checkout or publish until reconciled.

## Authority architecture comparison

| Property | A: shared folder | B: on-premise coordinator |
|---|---|---|
| Installation | No service | A small LAN service required |
| Locking | Depends on rigorously tested SMB/NFS behavior and mount config | One coordinator serializes and fences all operations |
| Multi-OS support | Server/client dialect and caching differences | Stable documented client protocol |
| Recovery | Ambiguous network failure and stale mutex must be handled | Durable local DB transactions, explicit idempotent request IDs |
| Security | Share ACLs, direct out-of-band edits remain possible | Coordinator authorization plus underlying share ACLs |
| Maintenance | Vendor/share certification and power-failure testing | Service health and local database backup |
| Three-engineer suitability | Only on a certified environment | **Recommended** |
| Offline | Read + local private work only | Read + local private work only |

A small offline-capable on-premise coordinator using a local SQLite database in WAL mode can issue monotonically increasing fencing generations and sequence numbers in an ACID transaction. Shared blobs remain immutable. It must confirm content availability before making a manifest authoritative and backup metadata and blobs at a consistent watermark. Never place its writable DB on the network share.

## Prototype limits

The *actual* isolated prototype is `prototype/authority.py`, a serialized local SQLite authority model (not a shared-filesystem mutex): it records monotonically increasing fencing generations, audited administrative override, idempotent commit IDs, revision preconditions and ordered transaction markers in one ACID transaction. It does **not** write a real RevisionWorx journal, publish immutable CAD blobs, provide a service transport, authenticate operators, or protect against database rollback/restore without an external epoch. `prototype/test_phase1_lock_race.py` separately provides an opt-in regression harness against a real Phase 1 checkout. Neither prototype is production-ready.

## Interface contract to Claude

`acquire(doc, actor, expected_head) -> {token, generation, epoch, base}`; `verify(doc, token, generation, epoch)`; `revoke(doc, operator, reason)`; `prepare(txid, base_heads, immutable_manifest_digest)`; `commit(txid) -> {sequence, revisions}`; `changes(epoch, cursor, limit)`; `snapshot(epoch)`.

Do not modify the core PDM entity model here. The Lead Architect owns the `CommitAuthority` seam. **10 local Python unittest cases passed** (Python 3.13.5, Linux; 2026-11-01) against an equivalent local prototype checkout; an opt-in real Phase 1 regression harness was skipped without a separate Phase 1 checkout. The former five-test result in early research notes was unsubstantiated and is withdrawn. Remote SMB/NFS semantics remain unverified. Option B is a proposal awaiting the Project Director's decision in issue #5.
