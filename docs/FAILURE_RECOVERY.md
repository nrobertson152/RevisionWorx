# Failure Recovery and Validation Register

No entry below claims SMB, NFS or NAS testing. H=possible authority/data loss, M=unavailable or needs reconciliation, L=local nonauthoritative cleanup.

| Failure | Detection and expected response | Recovery | Risk | Automated verification |
|---|---|---|---|---|
| Crash during checkout mutex creation | acquisition timeout, fail closed | Quiesce all writers, audit and quarantine mutex | H if blindly removed | Outstanding |
| Crash holding checkout | durable record persists | Audited admin revoke; fence previous generation | M | Local SQLite model tested; real share unverified |
| Simultaneous checkouts | authoritative lock conflict | One winner, other retries later | L if tested share | Six independent processes passed locally with SQLite authority |
| Partial content write | no committed manifest | Discard orphan after reachability/retention analysis | L | Outstanding |
| Crash during PREPARED | no COMMIT, no new revision | Ignore uncommitted transaction | L | Not yet implemented/tested with immutable multi-file manifest |
| Crash after COMMIT before projection | committed marker but stale HEAD | Recover from committed manifest before reads | M | Not yet implemented/tested with actual projection |
| Network failure before/after COMMIT | unknown acknowledgment | Query txid and reconcile idempotently | H until resolved | Real network outstanding |
| Server reboot / NAS failover | lock cache/durability uncertainty | Freeze writers, validate markers, objects, epoch | H | Real hardware outstanding |
| Disk full | short write/fsync failures | No COMMIT; reclaim capacity and reconcile | M/H | Outstanding |
| ACL or permission change | access denied | Stop mutation, repair policy, verify | M | Outstanding |
| Corrupted journal | parse/digest/gap mismatch | Read-only quarantine, restore or rebuild verified projection | H | No test executed on this prototype |
| Concurrent submissions | serialize, verify base | Reject stale base, recheckout | L | Checkout only tested |
| Stale client | epoch/fencing/base mismatch | Reconcile without silent overwrite | M | Stale generation rejected in local SQLite prototype; no cross-host test |
| Missing metadata | manifest or journal integrity failure | Rebuild projections or restore backup | H | Not tested |
| External deletion/modification | targeted hash and existence verification | Restore authoritative blob, warn about native CAD | H | Outstanding |
| Backup restored while live clients exist | epoch/sequence may diverge | Quiesce, atomic restore, new epoch, invalidate caches | H | Outstanding |

## Local prototype evidence

Executed `python -m unittest -v prototype.test_authority` from an isolated local checkout on Linux, Python 3.13.5: **14 tests passed, 0 failed**, approximately five seconds. Coverage includes six-process checkout contention, a paused writer revoked across processes, atomic generation and revision precondition checks, txid replay, audit persistence across restart, and SQL constraint rollback. The two opt-in real Phase 1 harnesses (`prototype.test_phase1_lock_race` and `prototype.test_phase1_cache_watermark`) ran **two skipped** because a Phase 1 checkout could not be fetched into the local execution environment. The lock-break race was subsequently **reproduced against actual Phase 1 source in GitHub Actions run 41371405600** (one passed test); the cache-watermark probe awaits CI verification.

Earlier entries claiming five tested POSIX mkdir/COMMIT-marker prototype cases were **incorrect**: no such prototype was committed or executed. They are withdrawn. No physical Windows/SMB/Samba/NFS/NAS, power-cut, server crash, network partition, authenticated override or real multi-file manifest test was executed. A SQLite model is not a certified distributed product.

## Operational requirement

Never automatically steal a stale mutex or lease. Freeze all writes on uncertain authority or missing committed content. Restore a consistently snapshotted commit watermark and its referenced immutable blobs. Backup restoration must advance an authority epoch and force every client to re-register and rebuild cache before writes.
