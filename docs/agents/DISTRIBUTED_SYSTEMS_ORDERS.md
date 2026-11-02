# Standing Orders — Distributed Systems Engineer

**LATEST ORDERS:** [RevisionWorx Parallel Assignments](PARALLEL_ASSIGNMENTS.md) — review ADR-015 rev. 2 and ADR-016 at Claude's new `325b7a8` for protocol/engineering-data integrity. Existing research remains isolated; no other repository changes.

**Assignment:** Shared-filesystem coordination, transaction safety, locking and recovery.
**Branch:** `research/shared-filesystem-protocol`
**Progress log:** `docs/agents/DISTRIBUTED_SYSTEMS_PROGRESS.md`
**Authority:** Independent protocol verification and isolated prototypes; Claude remains Lead Architect.

## Priority tasks
1. Inspect actual `phase1-foundation` durable writes, authoritative journal, lock token/break behavior, recovery and local SQLite synchronization before proposing replacements.
2. Evaluate SMB, Samba, NFS, Windows/Linux and NAS semantics: exclusive creation, atomic rename, durability, directory caching, advisory locks, disconnection and reconnect. Separate documented guarantees from assumptions.
3. Specify linearizable exclusive checkout ownership, stale-client prevention, audit-backed manual recovery and safe lock acquisition under simultaneous attempts.
4. Design crash-recoverable multi-file revision publication with immutable blobs, journal/commit markers, content hashes, ordered change records and recovery state machines.
5. Specify incremental local-cache synchronization, missed-sequence detection, stale indicators and rebuild without scanning unmanaged archives.
6. Build an isolated executable multiprocessing/fault-injection prototype and record executed results. Local tests do **not** certify cross-host SMB/NFS correctness.
7. Develop and validate the Director-approved **optional local-network coordinator** architecture (Issue #5), initially Windows-hosted with SMB shared content, using isolated prototypes and a Claude-reviewed integration interface. Preserve fully offline single-user operation.
8. Track high-severity findings through GitHub issues; block unsupported production-ready claims.

## Deliverables
Maintain `docs/SHARED_FILESYSTEM_ARCHITECTURE.md`, `docs/CONCURRENCY_PROTOCOL.md`, `docs/FAILURE_RECOVERY.md`, `docs/FILESYSTEM_COMPATIBILITY.md`, isolated prototypes/tests, and `docs/agents/DISTRIBUTED_SYSTEMS_PROGRESS.md`. Update your existing distributed systems PR.

## Mandatory operating procedure
1. Read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, and relevant current development documentation. Inspect your existing branch, open PR, issues, and latest comments before acting.
2. Identify yourself by assignment name only, not Agent B/C/D. Claude is Lead Architect and owns core implementation and architecture.
3. Work exclusively on your existing role branch; preserve all previous commits and work. Do not force-push, rewrite history, merge to main, tag, or release without director approval.
4. Maintain the assignment-specific progress file under `docs/agents/`. If an old AGENT_B/C/D_PROGRESS.md exists on your branch, rename it while preserving its content/history; do not create a competing duplicate.
5. Commit and push actual deliverables, tests and documentation; keep the role PR updated. Do not claim tests were run unless actually executed. Distinguish local filesystem tests from verified SMB/NFS behavior.
6. Coordinate interface decisions and findings affecting core architecture with Claude via GitHub issue or PR comments. Avoid changing Claude's core code or another specialist's scope without agreement.
7. Before ending a session update the progress file with commits, files changed, verified test results, unresolved risks, and next steps. In the final report link to your branch/PR and report what was pushed and what remains unverified.
8. If the assignment is blocked, produce independently useful fixtures, research, or prototype work and record the blocker. Do not stop at a plan.

## Immediate response required
After reading these orders, report your role, branch, PR, current completed work, verified tests, outstanding risks, and your next concrete engineering task. Then continue implementation autonomously.

## Active Director priorities — 2026-11-01, latest verified checkpoint

**Architecture decision:** Issue #5 approves an **optional Windows-hosted LAN coordinator** and SMB shared content as the initial supported multi-user design. Standalone single-user operation remains offline and coordinator-free. Claude owns production `CommitAuthority`, `Workspace` and Phase 2 integration. This branch must provide isolated research, specifications and proofs, **not** production write-path changes.

**Evidence:** [run 41382608414](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382608414) passed **42/42 research tests per OS/Python job** across Ubuntu/Windows Python 3.11/3.13, plus 2/2 real Phase 1 defect probes and 2/2 Phase 2 probes (one positive control, one passing reproduction of unsafe behavior). [Run 41383055792](https://github.com/nrobertson152/RevisionWorx/actions/runs/41383055792) also passed all six research/actual-code jobs at `cc08a23f`. Inspected branch head `216d52c948e0` contains documentation-only work after the validated research implementation. **Passing unsafe reproductions are not passing safety acceptance tests.** None of these workflows exercises two physical SMB hosts.

**Specific known limits:** the witnessed SQLite model fails closed for a stale DB restore only if a separately preserved witness is intact; a witness on ordinary restorable files is *not nonrollbackable*. Signed test principals are not Windows authentication; verifying a full witness ledger and all DB rows per operation is not a scalable production design. Issue #8's pre-audit effective stale mutation remains open; issue #9 concerns index snapshot/watermark consistency; issue #10 now records a separate Windows `writer.lock` release/sharing-violation race.

### Execute next, in order

1. **Refine atomic transaction contract for Claude:** provide a minimal unambiguous operation/response specification for acquire, fenced publish with `expect_rev`, idempotent `txid`, multi-record checks, override with mandatory authenticated/audited reason, failure responses, durable commit acknowledgment, and one *atomic linearization point* between revocation and publication. Include failure-state transitions and explicit standalone-to-coordinator migration invariants. Retain #8's post-audit logical-fence positive test and pre-audit unsafe reproduction as separate cases.
2. **Validate restore/witness limitations in isolated research:** build a test in which *both* authority DB and ordinary witness are rolled back together and label it unsafe; test witness missing/tampered/reverted, crash at witness-before-DB-commit, restart/reconciliation, replay and epoch rotation, and fail-closed recovery. Specify the external trust root needed for genuine nonrollbackable high-water and who administers it, without pretending that a second local file provides it. Use bounded validation and document scalability costs; no unsupported claims of real Windows identity/authentication.
3. **Exercise multi-record publish and availability:** prototype atomic `RecordPut/Delete/Check` batch conflicts and idempotent retries at one authoritative sequence, immutable content staging vs authoritative manifest commit, lost ACK, coordinator disconnect/reconnect, and read-only quarantine when authority/witness is unavailable. Record what SQLite verifies locally versus what SMB must establish physically. Do not implement a live network service without architectural review.
4. **Add Windows #10 compatibility analysis:** inspect the actual `WinError 32` trace and clarify how local standalone lock-file release and coordinator-based leases differ. Recommend bounded safe recovery/testing, but do **not** edit production lock release or conflate a Windows NTFS process race with cross-host SMB certification.
5. **Handoff and qualification gates:** keep `docs/COORDINATOR_INTEGRATION_CONTRACT.md`, `docs/COORDINATOR_WITNESS_RESEARCH.md`, `docs/COORDINATOR_RESTORE_FENCING_PROTOCOL.md`, `docs/WINDOWS_SMB_CERTIFICATION_PLAN.md` and `docs/DISTRIBUTED_SYSTEMS_HANDOFF.md` consistent; deliver Claude a short list of interfaces and open design decisions. Define a practical two-host Windows/SMB test matrix (locks/leases, crash/power loss, atomicity, durable content, audit, recovery, auth) that identifies **not executed** items. Commit/push an independently tested, coherent prototype batch and update progress/PR #4.

**Authority:** isolated research code, tests and documentation only. No production coordinator, `Workspace`, `LocalIndex`, `documents.py`, other agents' branches, merges or releases.
