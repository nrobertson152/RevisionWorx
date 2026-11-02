# Distributed Systems Engineer — Technical Handoff and Merge-Readiness Gate

**Reviewed:** November 1, 2026 (Eastern). **Branch:** `research/shared-filesystem-protocol`. **Draft PR:** #4. **Lead Architect:** Claude. **Director authorization:** Optional Windows-hosted LAN coordinator for supported multi-user operation (issue #5). **Standalone** remains completely offline/coordinator-free.

## Executive disposition

**Research deliverables ready for technical review; not ready for production integration or deployment.** Do not merge PR #4 or declare multiuser reliability established. The isolated code has no authenticated network service, no independently protected witness hardware, no SMB durability certification and no application integration. The existing standalone and Phase 2 journal still have an independently reproduced administrative lock-break race.

## Evidence inventory

| Item | Evidence | Status |
| --- | --- | --- |
| SQLite generation/owner fencing; revision conflict checks | `prototype/authority.py`, `test_authority.py` | Local model tests passed |
| Recoverable local immutable multi-file manifest | `prototype/immutable_manifest_research.py` + tests | Local process-crash tests passed; no SMB guarantee |
| Coordinator facade, releases, ordered change cursor | `prototype/coordinator_research.py` + tests | Local tests passed |
| Backup restore same-epoch vulnerability | Original static `EpochGuard` test | Vulnerability demonstrated; remains unsafe |
| External witnessed recovery and signed test identities | `prototype/witnessed_coordinator.py` + tests | Local model demonstrated only; nonrollbackable witness **not** provided |
| Real Phase 1 stale writer and index cursor defect | `prototype/test_phase1_lock_race.py`, `test_phase1_cache_watermark.py` | **2/2 reproducible defects** on real Phase 1 |
| Real Phase 2 lock-break ordering | `prototype/test_phase2_lock_break_order.py` | **1 positive control and 1 reproduced defect** on real Phase 2 |
| Windows/SMB hardware certification | `docs/WINDOWS_SMB_CERTIFICATION_PLAN.md` | **Plan only. Zero two-host SMB tests run** |
| Production `CommitAuthority` seam and application migration | `docs/COORDINATOR_INTEGRATION_CONTRACT.md` | Contract proposed; implementation owned by Claude |

**Latest verified CI:** [GitHub Actions run 41382608414](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382608414). **42 research tests passed on each of four combinations** (Windows and Ubuntu; CPython 3.11 and 3.13). Additional Ubuntu jobs executed **2 real Phase 1 defect probes** and **2 real Phase 2 probes**. The latter includes a passing *defect reproduction*, not a passing production safety acceptance test. Earlier run 41382383710 **failed** because a test selected nonexistent SQL column `id` rather than audit primary key `sequence`; fixed in `731401bd` and reverified by the green run. Total counts are for isolated model tests and demonstrations, not actual SMB.

## Release blockers by severity and ownership

| Priority | Blocker | Required evidence before approval | Owner |
| --- | --- | --- | --- |
| P0 | Administrative override and publication are not atomic on current Phase 2 (issue #8) | Both pre-audit and post-audit races independently reject stale publications | Claude (core) + QA |
| P0 | Coordinator authority not integrated into production `Workspace.commit` | Serializable generations/epoch/`expect_rev` and multi-record CAS in one durable authority commit | Claude |
| P0 | Restored DB plus stale checkout may resurrect credentials | Independently protected nonrollbackable epoch/high-water and safe recovery tests, including rollback of both domains | Distributed Systems research + Claude review |
| P0 | Two durability domains (SQLite authority vs SMB immutable blobs) | Two-host Windows/SMB failover, power-loss, disconnect, fsync and content-hash verification | Distributed Systems + QA |
| P0 | No authenticated admin transport or authorization | Verified Windows principal identity, privilege checks, audited outcome/reason, server authorization | Claude architecture + Distributed Systems research |
| P1 | LocalIndex inconsistent rebuild cursor (issue #9) | Exact replay/snapshot watermark and regression test for insert, delete and revision records | Claude + QA |
| P1 | Journal scanning and revision-list scalability | Benchmarks at representative dataset sizes and bounded incremental sync | Claude + QA |
| P1 | No real service lifecycle/installer | Windows service, recovery mode, ACLs, restart, backup runbook and secure endpoint contract | Claude + Distributed Systems research |

## Minimal handoff for Claude

Maintain `Workspace.commit(op, ops, blob_refs, txid)` semantics at the application layer, but dispatch **all coordinator-mode mutations** to one server-owned transaction. Expected record versions, checkout owner/token, generation, epoch, all `RecordPut/Delete/Check` operations and commit journal marker must be validated/applied **together**. Do not compute authoritative outcomes from local caches. Preserve txid replay, `CommittedNotApplied`, fail-closed unknown outcomes, and the ability to recover a revoked user's local CAD edits.

Review `docs/COORDINATOR_INTEGRATION_CONTRACT.md` (request/response/error union, migration stages, failure-closed state machine), `docs/COORDINATOR_RESTORE_FENCING_PROTOCOL.md`, and `docs/COORDINATOR_WITNESS_RESEARCH.md`.

**Do not** automatically fall back to standalone shared-folder writes when the coordinator disappears. The same workspace must never have two writable authorities. Standalone deployment must continue to operate entirely without any coordinator or network dependency.

## Next independent verification

1. Review Claude's next Phase 2 revision against the **pre-audit** race harness (current ADR-015 does not pass that safety requirement).
2. Build a disposable physical or virtual two-host Windows SMB rig with separately deployed coordinator host and client, then record actual share metrics, OS versions and fault outcomes. GitHub Windows runners are **not** two-host SMB qualification.
3. Develop fail-closed and recoverable witness mismatch procedures: a trusted external authority must not be rolled back alongside the DB. The local ledger file model cannot prove this.
4. Test multi-record CAS/check-in/checkout semantics and audit integrity against the production adapter once Claude defines and implements the seam.
5. Keep the research PR draft and report every unsupported production claim or integrity defect. No merge, tag or release without Director approval.

## Updated research handoff — 2026-11-01 ET, later continuation

**Claude's candidate corrections evolved after the earlier checkpoint.** Phase 2 `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6` implements journal-first lock revocation (ADR-015 rev.2), stable index rebuild (ADR-016) and bounded Windows sharing-violation retries (ADR-016). Our own exact-Phase-2 [CI run 41387393542](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387393542) passed **two independently authored *safety acceptance* cases** for #8, rather than the obsolete pre-audit unsafe reproduction. This is positive **local** evidence of the revised standalone ordering; it does not certify SMB/NFS or replace QA approval of #8/#9.

**New confirmed issue #10 risk:** despite 5/5 passing Windows six-process stress trials reported by QA for transient `WinError 32` (issue #10), the current name-based lock-release method has a separate administrator/release race. [Real Phase 2 CI run 41387552435](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387552435) checked out `f3c54386` and ran **four tests**: two #8 positive safety controls, one normal-lock-release control, and **one passing unsafe-reproduction**. The old writer pauses after rechecking its token but before `unlink(writer.lock)`; an administrator revokes and moves that file, a successor acquires a new lock, and then the old writer's pathname unlink **deletes the successor's lock**. Reported with exact interleaving on issue #10. The QA Windows transient sharing fix and this successor-ownership failure address different properties.

**New verified isolated model:** [CI run 41387844444](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387844444) completed successfully with **63/63 research tests per job** (Windows/Ubuntu × Python 3.11/3.13), **2/2 historic Phase 1 defect reproductions**, and **4/4 actual Phase 2 probes** (three positive controls, one *unsafe* issue #10 demonstration) at the pinned Phase 2 SHA above. This includes:
- `prototype/test_witnessed_coordinator.py`: double DB+witness rollback that **resurrects** revoked credentials (documented failing safety guarantee), witness-only rollback denial, healthy-authority epoch rotation + old credential fencing, crash between guard replacement and DB commit, bounded witness/state-hash validation.
- `prototype/record_batch_research.py`, `prototype/test_record_batch_research.py`: transactionally validated `RecordPut/Delete/Check`-style batch, one global sequence for all check-in records, per-record expected revision, tombstones, txid replay, immutable SHA-256 manifests, independent-process contender/crash tests and fail-closed offline/reconnect/high-water quarantine. No actual SMB, network service or production authentication.
- `prototype/test_phase2_lock_release_race.py`: source-level successor lock deletion; current acceptance requirement is that **no old owner may remove a replacement lock**, not that the reproduction remains green.

**Production ownership remains unchanged:** Claude owns `Workspace`, `LocalIndex`, `documents.py`, production `CommitAuthority` and #8/#9/#10 fixes. QA owns independent acceptance. Distributed Systems owns isolated protocol proofs, source review and the two-host SMB certification rig. Standalone use remains offline/coordinator-free. **Do not merge this research PR or label supported multiuser mode production-ready.**

**Remaining decisions for Claude and Director:** production per-record CAS/txid wire contract; checkout resource partitioning for assemblies; independently **nonrollbackable** epoch/high-water authority with restore reconciliation; Windows service identity and authenticated admin mapping; SMB content fsync/ACL and crash durability; and an ownership-safe lock-release protocol for #10. All physical two-host tests remain **NOT EXECUTED**.
