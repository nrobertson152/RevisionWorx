# Shared Filesystem Architecture: Research Decision

**Decision: no production deployment approval.** The inspected repository baseline contains specifications but no verified shared coordination code. A shared folder is not a transactional database.

## System map

- Desktop clients on Windows and Linux; local SQLite is a disposable search index only.
- Shared authoritative .revisionworx stores immutable content and manifests, checkout ownership, transactions, committed journal records and epoch.
- Every write needs a proven cross-machine exclusive authority gate, fencing tokens and crash-recoverable commit protocol.
- Stop authoritative writes when authority or durability is uncertain.

## Safety invariants

1. At most one effective checkout authority per document; all mutations serialized under a proven cross-host gate.
2. A checkout must bind owner, random token, monotonic per-document fencing generation, workspace epoch and expected base revision.
3. The current authority must verify every one of these fields immediately before check-in. Never infer permission from a local SQLite cache.
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

The isolated Python sample demonstrates local POSIX mkdir serialization, random checkout tokens, SHA-256 blobs, PREPARED/COMMIT markers, projection replay and sequential events. It does not support multi-document manifests, monotonic fencing generations, portable Windows fsync, authenticated administration, operational mutex recovery, NAS failure or network partitions. Its process-crash dead mutex deliberately requires manual intervention. The prototype is **not production-ready**.

## Interface contract to Claude

`acquire(doc, actor, expected_head) -> {token, generation, epoch, base}`; `verify(doc, token, generation, epoch)`; `revoke(doc, operator, reason)`; `prepare(txid, base_heads, immutable_manifest_digest)`; `commit(txid) -> {sequence, revisions}`; `changes(epoch, cursor, limit)`; `snapshot(epoch)`.

Do not modify the core PDM entity model here. Add protocol integration only after the authority implementation and cross-host tests pass. Facts: original docs explicitly say coordination is proposed; local prototype tests passed; remote semantics remain unverified.
