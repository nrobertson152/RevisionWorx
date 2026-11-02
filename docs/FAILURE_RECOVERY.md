# Failure Recovery and Validation Register

No entry below claims SMB, NFS or NAS testing. H=possible authority/data loss, M=unavailable or needs reconciliation, L=local nonauthoritative cleanup.

| Failure | Detection and expected response | Recovery | Risk | Automated verification |
|---|---|---|---|---|
| Crash during checkout mutex creation | acquisition timeout, fail closed | Quiesce all writers, audit and quarantine mutex | H if blindly removed | Outstanding |
| Crash holding checkout | durable record persists | Audited admin revoke; fence previous generation | M | Token check tested locally |
| Simultaneous checkouts | authoritative lock conflict | One winner, other retries later | L if tested share | Eight independent processes passed locally |
| Partial content write | no committed manifest | Discard orphan after reachability/retention analysis | L | Outstanding |
| Crash during PREPARED | no COMMIT, no new revision | Ignore uncommitted transaction | L | Local fault-injection passed |
| Crash after COMMIT before projection | committed marker but stale HEAD | Recover from committed manifest before reads | M | Local fault-injection passed |
| Network failure before/after COMMIT | unknown acknowledgment | Query txid and reconcile idempotently | H until resolved | Real network outstanding |
| Server reboot / NAS failover | lock cache/durability uncertainty | Freeze writers, validate markers, objects, epoch | H | Real hardware outstanding |
| Disk full | short write/fsync failures | No COMMIT; reclaim capacity and reconcile | M/H | Outstanding |
| ACL or permission change | access denied | Stop mutation, repair policy, verify | M | Outstanding |
| Corrupted journal | parse/digest/gap mismatch | Read-only quarantine, restore or rebuild verified projection | H | Missing-event gap tested locally |
| Concurrent submissions | serialize, verify base | Reject stale base, recheckout | L | Checkout only tested |
| Stale client | epoch/fencing/base mismatch | Reconcile without silent overwrite | M | Token invalidation tested locally |
| Missing metadata | manifest or journal integrity failure | Rebuild projections or restore backup | H | Event gap tested |
| External deletion/modification | targeted hash and existence verification | Restore authoritative blob, warn about native CAD | H | Outstanding |
| Backup restored while live clients exist | epoch/sequence may diverge | Quiesce, atomic restore, new epoch, invalidate caches | H | Outstanding |

## Local prototype evidence

Executed `cd prototype && python -m unittest -v`: **5 tests passed** in approximately four seconds on a local POSIX filesystem. Tests cover eight independent process checkout contention, fencing after release, simple publication and ordered event replay, incomplete PREPARED crash, and post-COMMIT recovery with administrator-simulated mutex removal. No tests on two physical hosts, Windows, SMB, Samba, NFS, NAS, power loss or disk-full were run. Simulating mutex removal after confirmed process death is not evidence of a safe generalized administrative workflow.

## Operational requirement

Never automatically steal a stale mutex or lease. Freeze all writes on uncertain authority or missing committed content. Restore a consistently snapshotted commit watermark and its referenced immutable blobs. Backup restoration must advance an authority epoch and force every client to re-register and rebuild cache before writes.
