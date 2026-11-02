# Distributed Systems Engineer — isolated transaction-authority research

This folder contains **research code only**, independent of RevisionWorx core. It is not a production coordinator, and no protocol selection has been approved. Claude owns the application's prospective `CommitAuthority` integration interface.

## Model and actual behavior

`authority.py` stores local SQLite authority generations, per-resource owner, revision preconditions, ordered commit rows, transaction IDs and an administrative revocation audit. `BEGIN IMMEDIATE` serializes acquisition, override and publication; the current generation and revision are validated **inside the same database transaction** that inserts the commit row. All SQL connections are explicitly closed. The database must reside on the coordinator's **local disk**; never put a writable SQLite authority database on SMB/NFS.

`test_authority.py` uses Python standard-library unittest and independent processes to verify local authority properties. Run from the repository root:

```sh
python -m unittest -v prototype.test_authority
```

An opt-in regression `test_phase1_lock_race.py` imports the **real** `revisionworx.workspace` from a separate `phase1-foundation` checkout. The test forces a writer to pause *after* its lock preflight, performs an audited administrative lock break, and checks whether the stale writer then manages to append a journal entry. This deliberately demonstrates the existing defect, not safe production behavior.

```sh
REVISIONWORX_PHASE1_CHECKOUT=/path/to/phase1-foundation \\
  python -m unittest -v prototype.test_phase1_lock_race
```

Both opt-in Phase 1 test modules skip when the separate checkout is not supplied. The second probe, `test_phase1_cache_watermark.py`, tests whether `LocalIndex.rebuild()` materializes newer records while retaining an older captured journal cursor. Neither has been executed against real Phase 1 in the present *local* environment. However, the lock-break test **was executed successfully against the real Phase 1 checkout in GitHub Actions run 41371405600** and confirmed the stale-writer commit. The cache-watermark test remains unverified against real Phase 1.

## Verification record (2026-11-01)

Linux, CPython 3.13.5: `python -m unittest -v prototype.test_authority` — **14 passed, 0 failed**, approximately 5 seconds. The separate opt-in Phase 1 reproducers — **2 skipped** without a Phase 1 checkout. The first three prototype tests were also separately executed by the Lead Architect on another Linux Python 3.13 environment, reported in PR #4; this is not a substitute for the tests above.

The earlier README described an **unimplemented mkdir/COMMIT-marker experiment** and claimed related local tests. That description was inaccurate and has been replaced with the actual implementation.

## Outstanding safety gaps

* No identity authentication, authorization or secure remote transport. Audit stores a caller-provided **unverified actor claim**, not an authenticated administrator identity.
* No real RevisionWorx journal adapter, multi-file manifest, SHA-256 content verification, blob preparation or durability acknowledgment.
* No external epoch/high-water protection against restoring an older coordinator database and resurrecting revoked credentials.
* No network partitions, server crash during commit, power loss, disk-full, SMB/NFS or Windows testing.
* No protocol decision: optional offline local-LAN coordinator remains a proposal under GitHub issue #5, not an approved implementation.

Fail closed for authoritative multi-user writes until these gaps and real deployment qualification are resolved.

## Further research — Windows CI failures and immutable manifests (2026-11-01 ET)

`prototype/coordinator_research.py` now models acquire/verify/release, an epoch guard and ordered incremental change feed; `prototype/test_coordinator_research.py` has **9 new tests**. The guard detects missing/mismatched epoch files but **does not detect a same-epoch database snapshot rollback**. A test intentionally demonstrates this unsafe state. A nonrollbackable authority or mandatory epoch rotation on restore remains required.

`prototype/immutable_manifest_research.py` stages SHA-256-named objects using local filesystem hardlinks and verifies their bytes before a transaction marker; `prototype/test_immutable_manifest_research.py` contains **6 tests** for multi-file publication, orphan staging, corrupt/missing objects and stale writers. These tests do not establish SMB server power-loss durability. This is a local research-only experiment.

**Observed CI failure:** [run 41373765030](https://github.com/nrobertson152/RevisionWorx/actions/runs/41373765030) passed Linux jobs and both actual Phase 1 regression probes but failed Windows Python 3.11/3.13: `WinError 32` on SQLite file deletion during test cleanup. The root cause is SQLite connections left open by Python's connection context manager (which commits/rolls back but does not close). Changes `1dbbfe4f` and `2b0068d7` explicitly close all new research/test SQLite read connections. Run `41380280216` at the intermediate test-only fix still failed on Windows because the production research change-feed connection had not yet been fixed. **Do not claim a passing Windows result until a later run containing both fixes succeeds.**

Current intended command:

```sh
python -m unittest -v prototype.test_authority prototype.test_coordinator_research prototype.test_immutable_manifest_research
```

Expected discovery count is **29 tests** (14 authority + 9 coordinator + 6 manifest). This is **not** a verified pass count until CI confirms it. The separate real Phase 1 probes remain **2/2 confirmed** on earlier completed GitHub CI.

## Verified latest crash-injection matrix

[GitHub Actions run 41380924556](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380924556) **SUCCESS**: inspected logs show **31/31 tests passed** on each Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11 and Windows Python 3.13; actual Phase 1 regression job **2/2 passed**. The new manifest tests spawn independent workers that exit without normal cleanup immediately after staging or immediately after durable local SQLite commit. Recovery checks verify that staged-only content is not a committed revision and that the committed transaction remains idempotently discoverable.

This is **process-crash simulation**, not actual power-loss durability or real SMB/network-host testing. `docs/COORDINATOR_RESTORE_FENCING_PROTOCOL.md` proposes a nonrollbackable external epoch/high-water and controlled restore process; no such mechanism is implemented. The prototype's known same-epoch rollback weakness remains.

## Witnessed authority and real Phase 2 verification (2026-11-01 ET)

The new `witnessed_coordinator.py` research model illustrates an authority database bound to a **separately preserved** hash-chained witness. Each SQLite `BEGIN IMMEDIATE` mutation verifies the existing witness and appends an attestation of prospective state **before** database COMMIT. A lost DB commit leaves a mismatching witness and blocks operations; restoring an old DB snapshot while the witness remains intact also blocks. `test_witnessed_coordinator.py` includes **11 tests** for model authentication/administrator role, successful and rejected audited override, stale checkout rejection, idempotent replay, process contention, crash points, and witness tampering/rollback.

`TestIdentityProvider` only models authentication with signed in-memory test credentials. It is **not Windows integrated authentication** or a real user/account service. An ordinary file-based witness is **not nonrollbackable**. Restoring both the SQLite database and witness to a matching older snapshot could revive stale credentials. No production claim is justified.

Verified [GitHub Actions run 41382608414](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382608414): **42/42 tests passed in each** Windows Python 3.11, Windows Python 3.13, Ubuntu Python 3.11 and Ubuntu Python 3.13. The two actual Phase 1 defect probes passed. The **two actual Phase 2 probes** also passed: one demonstrates ADR-015 fencing *after* an audit, while the other **reproduces an unsafe pre-audit lock-break window** (the old writer's change is effective). A test that successfully reproduces a defect must not be treated as a passing safety guarantee. The earlier [run 41382383710](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382383710) failed because the new audit query ordered by an incorrect SQL column; fixed in commit `731401bd`, then independently reverified by the green run.

Current commands:

```sh
python -m unittest -v prototype.test_authority prototype.test_coordinator_research prototype.test_immutable_manifest_research prototype.test_witnessed_coordinator
REVISIONWORX_PHASE2_CHECKOUT=/path/to/phase2-documents python -m unittest -v prototype.test_phase2_lock_break_order
```

See `docs/COORDINATOR_WITNESS_RESEARCH.md`, `docs/COORDINATOR_INTEGRATION_CONTRACT.md`, and `docs/DISTRIBUTED_SYSTEMS_HANDOFF.md`. No actual two-host Windows SMB testing or verified nonrollbackable storage has been performed.
