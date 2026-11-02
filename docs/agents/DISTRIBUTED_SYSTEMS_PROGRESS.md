# Distributed Systems Engineer — Shared Filesystem and Distributed Systems Progress

**Session:** 2026-11-01  
**Owner:** Independent distributed systems / filesystem reliability research  
**Branch:** `research/shared-filesystem-protocol`  
**Status:** Protocol research and specification in progress; not approved for production.

## Repository baseline inspected
Read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_PROGRESS.md` and main-branch file listing. The inspected baseline contains specifications but no implemented shared storage, synchronization, transaction or checkout subsystem.

## Completed and committed
- `docs/SHARED_FILESYSTEM_ARCHITECTURE.md`: authority/storage/cache separation, invariants, coordinator recommendation, interface.
- `docs/CONCURRENCY_PROTOCOL.md`: checkout and revision transaction state machines, fencing conditions and idempotent recovery.
- `docs/FAILURE_RECOVERY.md`: scenario matrix, detection and recovery.
- `docs/FILESYSTEM_COMPATIBILITY.md`: SMB/NFS/Windows/Linux/NAS assumptions and explicit certification gate.
- `prototype/README.md`: isolated prototype scope and limitations.

Commits on research branch (via GitHub contents API): `25115ea5a62de7740a545d613bb2b7f4f33e2412`, `93f5e43eafcc3cb85a3c34b0c73747b059ea2898`, `a4e0c3924bcae0bac9662c1d7ee492edcf9078fa`, `b8380a590ce6b6bd802675a0ffb2abd8ac9a5507`, `b0b5584850d01762cb56caffd4939d3336c749f8`.

## Verified tests
**No executable prototype committed or tests executed in this GitHub research branch yet.** Any previous informal reference to five tests passing is **not a verifiable result** and must not be treated as evidence. Real SMB, Samba, NFS, NAS, mixed OS and power-loss tests have not been run.

## Architecture decision
**Recommend B, optional on-premise LAN coordinator**, with a locally durable coordinator database and immutable shared blobs, for production safety and simple three-engineer operation. Architecture A is contingent on environment-specific cross-host, restart and durability certification; unproven mounts must fail closed. Never use local SQLite indexes as checkout or check-in authority. A shared network folder alone does not supply a multi-record ACID transaction.

## Unresolved problems
- Implement isolated demonstrator and independent-process fault-injection tests.
- Prove cross-machine mutex and durability behavior for each supported network-share configuration.
- Specify monotonic transaction sequence, fencing generations and epoch changes in production interfaces.
- Verify fsync, atomic marker and server power-loss recovery semantics on supported hosts.
- Define audited manual recovery without unsafe lock stealing, backup-restore quiescence and snapshot watermark.
- Implement authoritative transaction journal replay rather than trusting cached heads.
- Coordinate architectural selection with Claude before integration.

## Exact next steps
1. Commit isolated `prototype/` Python implementation and reproducible `unittest` suite on this branch.
2. Execute tests on actual implementation and record commands, environment, pass/fail counts and any limitations.
3. Extend tests for concurrent publishers, crash points, gap detection and stale checkout prevention.
4. Open or update draft PR targeting `main`, clearly identifying implementation status and unverified conditions.
5. Share the Architecture B recommendation through a GitHub issue for Claude.
6. Update this log at the end of the next development session with newly verified results and commit hashes.

No main-branch files modified; no merge performed.

## Role identification migration (2026-11-01)
- Progress log renamed to `docs/agents/DISTRIBUTED_SYSTEMS_PROGRESS.md` on existing research branch.
- Historical commits and findings preserved; previous log path removed.
- Role name for all future GitHub coordination: **Distributed Systems Engineer**.
- Draft PR #4 updated to role-based identification; Lead Architect retains application architecture ownership.

## Standing orders / Phase 1 inspection — 2026-11-01
- Read `docs/agents/README.md` and `docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md` from `main`.
- Inspected `phase1-foundation` versions of `docs/ARCHITECTURE.md`, `revisionworx/atomicio.py`, `revisionworx/workspace.py`, and `revisionworx/index.py`.
- Actual Phase 1 implements POSIX link-based exclusive journal publication, Windows rename alternative, temp-file fsync, directory fsync on POSIX, writer.lock token, manual lock break, journal recovery and local SQLite incremental cache.
- **High-severity confirmed code-path risk:** `Workspace.break_writer_lock` checks token then `os.replace` moves the lock without a coordination barrier; a previously opened `Workspace` checks only `self.writable` in `_require_writable`, not current token/epoch. Its `commit` may still publish after revocation. The Phase 1 architecture explicitly acknowledges this.
- **Additional review concern (not yet reproduced):** `LocalIndex.rebuild()` captures journal head before iterating materialized records and pending overlay. Concurrent commits may cause index contents newer than the saved cursor, so rebuild consistency should be tested under concurrent publication. This is a hypothesis, not a verified defect.
- No executable tests run this session. No cross-host behavior validated. No claims of production readiness.
- Next: build an isolated deterministic regression/fault-injection harness for stale-writer revocation and cache rebuild concurrency; record actual results; coordinate protocol interface with Lead Architect.

## Session 2026-11-01 — Director fencing directive and isolated prototype
- Re-read standing orders on main, inspected PR #4 and issue #5 comments, and read `docs/ADMINISTRATIVE_LOCK_FENCING_DIRECTIVE.md` on main. **Director has not approved a coordinator architecture.** Administrative override is mandatory.
- Added `prototype/__init__.py`, `prototype/authority.py`, `prototype/test_authority.py` on this research branch. Commits: `30acf952e3c9ef19f052e4d2c1c582d140b0c435`, `f858604d3b47f4b5066d9f2ce076525079c6a267`, `3d6a78515c169beb5f016d4910f32de8c42ec342`.
- Demonstrator uses a **local-only** SQLite database with BEGIN IMMEDIATE to serialize acquire, administrative override and commit. The generation check and commit-record insertion are in the same database transaction; override increments generation and appends audit row. A separate txid idempotency path rejects payload mismatch. Tests cover stale writer after override, idempotency and six independent contender processes.
- **Verified tests: none executed this session.** Tests were committed, not run. This is a minimal coordinator authority model, not the required Phase 1 bug reproduction or a full multi-file revision publisher. It does not authenticate admin identity; supplied actor string is unverified. No SMB/NFS, Windows, server-crash, disk-full or actual share durability tests.
- Immediate next steps: run `python -m unittest -v prototype.test_authority` in a suitable checkout; create deterministic reproduction against actual Phase 1 `Workspace`; test override-versus-publication race and LocalIndex rebuild cursor race; add durable blob/manifest protocol; assess how to preserve serverless operation if certification succeeds; report test results with commands and environment. No merge authorized.

## Session — 2026-11-01 ET / 2026-11-02 UTC: Phase 2 review, independent verification, and CI

**Standing orders:** re-read docs/agents/README.md and docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md from latest main. Reviewed main administrative lock-fencing directive; Claude draft PR #6 (Phase 1/PySide6) and stacked draft PR #7 (Phase 2 revisions/checkout); issue #5 (architecture decision), PR #4 comments, phase1-foundation Workspace/LocalIndex and phase2-documents documents.py / workspace.py. Latest main still requires director approval to introduce a coordinator. Phase 1 and Phase 2 do not claim production multi-user share correctness.

**New/updated deliverables and commits, all on research/shared-filesystem-protocol:**
- b7cf0c66 / cdaec3c7: prototype/authority.py now explicitly closes SQLite connections, uses BEGIN IMMEDIATE, persists monotonic generations and auditable operator claims, atomically checks generation/owner and **mandatory** expected_revision with ordered commit-marker insertion, handles duplicate txid and outcome lookup. Research model only; actor claim is *not authenticated*.
- c55cbd14 / 0f6c60ed / 087b8aa0: prototype/test_authority.py extended for six-process contention, independent-process paused-writer revocation, concurrent override vs publication, rollback on SQL constraint error, persistent audit/revocation on restart, mandatory revision precondition, crash before COMMIT and crash after COMMIT before response, and idempotent replay.
- fbd88d05: prototype/test_phase1_lock_race.py; deterministic real-code Phase 1 preflight-to-publish administrative lock-break reproducer.
- a6ce452c: prototype/test_phase1_cache_watermark.py; actual Phase 1 rebuild snapshot-versus-newer-record hypothesis probe (unverified on real code as of log).
- b5373756 / c5fb432c: .github/workflows/distributed-authority-research.yml; Windows/Ubuntu Python 3.11/3.13 matrix, with real Phase 1 checkout jobs.
- ec283db1 / 2f84e1dc: docs/PHASE2_AUTHORITY_AND_SCALABILITY_AUDIT.md; current code review, detailed integration risk and revision-list O(total-revisions) scalability warning.
- Updated docs/SHARED_FILESYSTEM_ARCHITECTURE.md, docs/CONCURRENCY_PROTOCOL.md, docs/FAILURE_RECOVERY.md and prototype/README.md to eliminate historical *unsubstantiated* five-test/mkdir prototype claims, incorporate mandatory atomic fencing and actual verification status. Relevant recent commits: 86d5b0b8, 8cb8c1cd, 70ef8522, 4f8587c6.

**Tests independently executed locally (actual local code checkout, Linux CPython 3.13.5):**
- Command: python -m unittest -v prototype.test_authority prototype.test_phase1_lock_race prototype.test_phase1_cache_watermark
- **16 discovered; 14 passed, 0 failed, 2 skipped** (~5.88 seconds). Skips are deliberate, because this local execution environment cannot clone/fetch a separate phase1-foundation checkout; they are not treated as passes or as verified real-code results.
- Local tests include 6 spawned contender processes, revoked writer in a separately spawned process, SQLite WAL transaction/crash recovery and restart. No Windows or real network shares in local execution.

**Independently verified CI results (GitHub Actions run 41371405600, completed SUCCESS):**
- Earlier committed 12-test authority edition: **12/12 PASS on EACH** Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11 and Windows Python 3.13, according to completed job statuses and inspected test logs. These are tests of isolated SQLite authority, NOT the RevisionWorx desktop application or a real network share.
- Actual phase1-foundation checkout on Ubuntu, CPython 3.13.17: **1/1 PASS** for prototype.test_phase1_lock_race. The test confirms both the administrative override audit and a **new successful journal publication by the revoked writer**. This is an actual reproducible critical multiwriter integrity violation. Tracked as issue #8.
- Latest 14-test edition, crash tests, and actual cache watermark probe: CI jobs were still queued at this log update; no claim of CI pass or confirmed cache defect.

**Architectural status and blockers:**
- The director requires administrative override and effective atomic fencing. Coordinator option B is not approved; issue #5 remains a decision gate. Claude owns production CommitAuthority seam and core change integration.
- No real transport, authenticated operator, shared SMB/NFS Windows+Linux multi-host testing, NAS/drive power-loss, durable manifest and cross-system commit, independent epoch/high-water store for restored backup, fault-safe administration or distributed cleanup implemented.
- Snapshot-index watermark concern is **still a hypothesis**, awaiting actual Phase 1 test logs; revision-list global scan is confirmed directly by source inspection, not benchmarked for performance.
- Production data integrity requires fail-closed behavior; all above research remains draft, unmerged.

**Coordination:**
- Updated draft PR #4 with deliverables, CI verification and scope.
- Added architecture evidence to issue #5; opened issue #8 with actual reproducible lock-break bug and acceptance conditions.
- No changes to Claude's phase1-foundation/phase2-documents code, no other specialist files altered; no merge, tag or release.

**Next engineering tasks:**
1. Review newest GitHub Actions jobs and triage results of real Phase 1 cache watermark test and 14-test Linux/Windows crash suite.
2. Implement isolated multi-file immutable blob/manifest crash-recovery experiments with ordered journal and txid reconciliation; evaluate blob-store and authority DB acknowledgment boundary.
3. Prototype backup-restore epoch/high-water prevention and administrative authorization semantics, without integrating into production.
4. Define exact cross-host SMB/Samba/NFS qualification matrix after Project Director makes issue #5 architecture decision.

### Final session coordination and verification checkpoint (2026-11-01 ET)

- Reviewed completed GitHub Actions job logs from **run 41371405600**: four isolated authority matrix jobs succeeded (12 tests per environment: Windows/Python 3.11, Windows/Python 3.13, Ubuntu/Python 3.11, Ubuntu/Python 3.13). The real Phase 1 regression job succeeded (**1 test**, Ubuntu/Python 3.13.17), demonstrating the revoked writer's actual journal append. This is a confirmed source-and-runtime defect, not merely a theoretical race.
- GitHub Actions runs for the latest **14-test** edition and the separate real Phase 1 LocalIndex rebuild watermark probe were still **queued**, so no newer cross-platform or cache-probe result is claimed. Pending runs include 41371619563 and 41371528485. Local Python 3.13.5 execution remains 14 passed, two opt-in Phase 1 tests skipped.
- Opened GitHub issue #8 for the confirmed Phase 1 stale-writer post-revocation journal publication. Commented on architecture issue #5, and both Claude PRs #6 and #7, with verified evidence, ownership boundaries, and unresolved risks. Updated own draft PR #4.
- Corrected docs/FILESYSTEM_COMPATIBILITY.md in commit `1067135066feb145fcd2ba966351f4dcfddcc945` to make explicit that the tested SQLite authority prototype does not exercise SMB/NFS file operations.
- No production coordinator was introduced, no modifications to the Lead Architect's core branches, no merge, no release, no credentials or authenticated administration created.
- Current blocker to additional **verified** cross-host results: an approved architecture and physical share test matrix; latest CI jobs remain pending. Next session must read the latest main orders and pending CI logs before further changes.

## Session — 2026-11-01 ET, 20:40 onward: approved Windows/SMB coordinator research

**Orders / review:** Read latest `main` `docs/agents/README.md`, `docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md`, existing progress log, draft PR #4, issues #5/#8 and new Director PR #4 instructions. **New architecture decision:** Director approved optional offline LAN coordinator for supported multi-user mode in issue #5, initially Windows-hosted with SMB shared content. Standalone single-user operation remains fully offline and coordinator-free. Claude recorded ADR-014 on `phase2-documents` and retains the production `CommitAuthority` seam.

**Verified GitHub CI:** [run 41371802579](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371802579) completed successfully. Inspected individual job logs: **14/14 authority tests passed** on each of Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11 and Windows Python 3.13. Actual `phase1-foundation` checkout: **2/2 regression probes passed**, reproducing BOTH (a) revoked writer publishing after administrative lock break (issue #8) and (b) LocalIndex rebuild containing a record newer than its saved journal cursor. These are confirmed real-code defects, not hypotheses. This CI does not test physical SMB, production coordinator, or PySide6 GUI.

**New commits / deliverables:**
- `75b53ed4` `prototype/coordinator_research.py`: isolated local-disk-only coordinator facade, persistent epoch guard, acquire/verify/release, administrative override, txid publication, ordered incremental change feed and gap detection.
- `71c27a95` / `3dec1c5f` `prototype/test_coordinator_research.py`: eight additional research tests for acquire/release, stale writer, restart, missing/mismatched epoch guard, incremental cursor, sequence-gap quarantine and **documented same-epoch snapshot rollback vulnerability**. The rollback test intentionally shows that the simple guard is insufficient; it is NOT a production success criterion.
- `086578e8` updated GitHub Actions matrix to run the new coordinator facade tests on Windows and Ubuntu. At this progress-log update, workflow run 41373765030 (head 3dec1c5f) was still **queued**. **Do not claim the eight new tests passed until logs confirm.**
- `a3d57aa8` / `8117cd4b` `docs/COORDINATOR_INTEGRATION_CONTRACT.md`: proposed `CommitAuthority` interface, transaction/manifest durability, identity/authz and failure-closed requirements, recovery epoch boundary, ownership.
- `79ec7220` `docs/WINDOWS_SMB_CERTIFICATION_PLAN.md`: physical two-host Windows/SMB rig, fault injection matrix, evidence collection and prerequisites.

**Newly verified risk:** LocalIndex rebuild cursor watermark race reproduced in actual Phase 1 CI. Coordinator static epoch guard does **not** detect a same-epoch DB restore; do not use this model in production without nonrollbackable high-water fencing and an audited restore/quiescence process. Real SMB share durability and multi-file immutable content publication remain unimplemented/unverified. Authentication is not implemented; `actor_claim` is not an authenticated identity.

**Coordination:** Posted updated interface proposal and discovered rollback limitation on architecture issue #5 for Claude. PR #4 remains draft. No core application files, production journal, merge, tag or release changed.

**Next:** Review pending GitHub Actions run 41373765030 and fix any failures; prototype SHA-256 blob/manifest staging and crash recovery; design trusted nonrollbackable epoch/high-water restore mechanism; independently qualify Windows/SMB with two real hosts after test rig available. Keep all unsupported multi-user production claims blocked.

## Session — 2026-11-01 ET, 21:55 onward: Windows CI triage, manifest prototype

**Latest orders and architecture:** Continued exclusively on `research/shared-filesystem-protocol` after Director approved optional offline LAN coordinator for Windows/SMB multiuser research; Claude owns the production `CommitAuthority` seam. No core application files modified.

**CI failure independently inspected:** [run 41373765030](https://github.com/nrobertson152/RevisionWorx/actions/runs/41373765030) finished **failure**: Ubuntu Python 3.11 and 3.13 jobs **passed** all 22 then-current tests; actual Phase 1 real-code probes **2/2 passed**; Windows Python 3.11 and 3.13 jobs **failed** with `PermissionError: [WinError 32]` while removing SQLite database files in `TemporaryDirectory.cleanup`. The Python sqlite3 connection context manager does not close the connection. [run 41380280216](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380280216) also failed on Windows after the partial test-only fix, confirming the separate `changes_after` read connection needed closing. Do not describe either run as green.

**Committed fixes and new deliverables:**
- `1dbbfe4f` explicitly closes test SQLite connections; `2b0068d7` explicitly closes `CoordinatorResearch.changes_after` SQLite read connection.
- `bd5ad6eb` added `prototype/immutable_manifest_research.py`, isolated content-addressed SHA-256 staging and multi-file manifest verification before local authoritative commit. Uses hardlinks to avoid overwriting existing objects; not SMB certified.
- `a82c5d12` added `prototype/test_immutable_manifest_research.py` (6 new tests): multi-file commit, idempotency, orphan staging, missing/corrupt content, stale writer and restage corruption.
- `ce515c61` extended CI matrix to include immutable manifest research tests.
- `6513253f` + `672bb43e` reject authoritative change-feed cursors greater than the stored commit high watermark, plus one regression test. `prototype/test_coordinator_research.py` now has 9 tests.
- `d30d766f` updated prototype README with exact failed runs, known limitations, test commands and pending evidence.

**Verification:** Last known successful cross-platform run [41371802579](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371802579) passed 14/14 authority tests per Windows/Linux Python version and 2/2 actual Phase 1 regressions. New 29-test suite (14 authority + 9 coordinator + 6 manifest) has **not yet passed CI**; latest head workflow was queued when checked. The two Windows failure runs are recorded above. No physical two-host SMB test, no disk power-loss test, no authenticated admin and no rollback-safe epoch proof.

**Outstanding critical design limitations:** (1) same-epoch backup restore can resurrect old checkout credentials; (2) staging on SMB and committing in local SQLite is not a single atomic transaction and needs verified SMB durability and quarantine/recovery; (3) production transport/authentication and actual Windows SMB host rig remain unimplemented. Fail closed for multi-user deployment.

**Next:** inspect latest GitHub Actions results for 29-test head and fix failures; implement stronger restore fencing and explicit crash injection at blob staging/commit boundary in isolated prototype; coordinate tested interface with Claude via issue #5; keep PR #4 draft and no merge/release.

## Session — 2026-11-01 ET, after 22:00: green 29-test matrix and process-crash extension

**Verified completed CI:** [GitHub Actions run 41380360669](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380360669) **SUCCESS**. Inspected each job log: **29/29 passed** on Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11, Windows Python 3.13 (116 passing environment-specific test executions). The real Phase 1 `phase1-foundation` regression job **2/2 passed**, continuing to reproduce both stale-writer post-revocation publication and the LocalIndex rebuild cursor mismatch. Windows `WinError 32` SQLite cleanup defect from previous runs is resolved in this matrix. These tests do NOT establish physical SMB or coordinator production correctness.

**New work pushed after green run:**
- `02d909f5`: added two **independent spawned-process crash-injection** cases to `prototype/test_immutable_manifest_research.py`: (1) worker `os._exit(41)` after SHA-256 object staging but before any authority commit must leave no revision, and (2) worker `os._exit(42)` after multi-file authority commit but before acknowledgment must recover exactly one verified revision and its txid. The suite is now **31 tests**, not yet independently verified at this log update; GitHub Actions run **41380924556** was in progress.
- `8f76bf09`: `docs/COORDINATOR_RESTORE_FENCING_PROTOCOL.md` describes controlled quiescence, independent nonrollbackable high-water authority, epoch rotation, manifest/journal reconciliation, read-only quarantine, audit and recovery fault matrix. This is a design proposal; no production restore mechanism implemented.

**Critical remaining blockers:** (1) same-epoch database restore still resurrects credentials in prototype, (2) local SQLite authority and SMB content are separate durability domains, (3) no authenticated admin or transport, (4) no physical two-host Windows/SMB and power-loss testing, (5) no production integration. Continue research only on assigned branch, coordinate through issue #5, keep PR #4 draft and do not merge/release.

### Verification closure — 2026-11-01 ET

[GitHub Actions run 41380924556](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380924556) **COMPLETED SUCCESS**. All four independent matrix jobs (Ubuntu Python 3.11/3.13, Windows Python 3.11/3.13) ran **31 tests each, 31 passed, 0 failed**, confirmed by inspected job logs. Actual `phase1-foundation` defect probes **2/2 passed**. This closes the previous pending-test status for the two new spawned-process manifest crash cases. It does **not** close production rollback/SMB durability/authentication issues.

## Continuation — 2026-11-01 ET: Witnessed recovery, authenticated override model and Phase 2 independent regression

**Orders re-read:** latest `main` `docs/agents/README.md`, `docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md`, `CLAUDE.md`, Phase 2 `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_PROGRESS.md`, live draft PRs #4/#6/#7 and coordination issues #5/#8/#9. Director-approved optional Windows-hosted LAN coordinator is the initial multiuser target. Claude owns application `CommitAuthority` and production `Workspace` / `documents.py` changes. Research PR #4 remains draft.

**Confirmed newly discovered Phase 2 integrity race (issue #8):**
- Inspected actual `phase2-documents` ADR-015 code and `tests/test_issue8_fencing.py`. ADR-015 filters entries with a revoked writer token **after** the audited `workspace.lock.break` entry.
- `8b0805cb`, `2deefde5`: `prototype/test_phase2_lock_break_order.py` deterministically pauses the administrator **after moving writer.lock but before committing the break audit**, then resumes a writer that passed its previous lock preflight. The old writer's entry is committed/materialized at sequence `N`, and the audit at `N+1`; ADR-015 does not fence this earlier entry. Positive control confirms an entry published **after** the audit is fenced.
- `82ca3f4c`: new workflow job checks out the **actual** `phase2-documents` branch; [CI run 41381819173](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381819173) independently ran **2/2 probes successfully**, including a *passing reproduction of unsafe behavior*, not a passing safety guarantee. [Run 41382608414](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382608414) reconfirmed both. QA separately reported a **strict xfail** safety acceptance on issue #8. Sent exact reproduction and acceptance boundary to Claude via issue #8; updated issue body, left open. **Production multiuser safety remains blocked.**

**Implemented new isolated research model:**
- `a650e9f4`: `prototype/witnessed_coordinator.py`: local SQLite transactional authority bound to separately stored append-only hash-chained witness ledger. Under `BEGIN IMMEDIATE`, validate witness, generation/owner/`expected_revision`, mutate, persist witness for prospective state **before** SQLite COMMIT, then ACK. An in-between crash or older-DB restore with retained witness causes mismatch and fail-closed. Research-only signed in-memory credential provider verifies actor and administrator role; override audit contains claimed identity verified by **test authenticator**, reason, old/new generation, previous owner, outcome and UTC timestamp. Stale operator generations are audited without revoking a newer owner.
- `79120b11`: `prototype/test_witnessed_coordinator.py` adds 11 deterministic tests for forged/wrong-role credentials, admin audit/rejected override, owner-bound lease release and publication, idempotency, two-process checkout contention, backup restore, tampered/missing witness, and spawned-process death before/after commit.
- `28f948ce`: expanded Windows/Linux Python 3.11/3.13 CI test matrix. Initial run [41382383710](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382383710) **failed** with 2 errors: new audit reader queried nonexistent `audits.id` instead of actual `audits.sequence`. Corrected in `731401bd`; no other code/test changes after this fix.
- **Independent verified CI:** [run 41382608414](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382608414) **COMPLETED SUCCESS**, inspected all job logs: **42/42 research tests passed on each** Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11 and Windows Python 3.13; actual Phase 1 defect probes 2/2 and Phase 2 probes 2/2. This is **local model CI**; zero physical two-host SMB tests. The two Phase 2 probes deliberately include one **unsafe defect reproduction**.
- `df422667`: `docs/COORDINATOR_WITNESS_RESEARCH.md` specifies ordering, trust assumptions, rollback limitations and recovery matrix. `c4260ada`: `docs/COORDINATOR_INTEGRATION_CONTRACT.md` expanded with concrete batch commit request, error union, fail-closed state machine, migration and no-split-brain rules. `ddd8e6fb`: `docs/DISTRIBUTED_SYSTEMS_HANDOFF.md` covers merge-readiness and owners. `cc08a23f`: `prototype/README.md` updated with 42-test evidence and Phase 2 limitations.
- Posted architecture contract and exact results in issue #5 for Claude; updated issue #8 body with new finding and Director's current approval status.

**Unresolved high-severity safety and deployment blockers (no false claims):**
1. **Actual Phase 2 pre-audit revocation window** remains effective; Claude must close and QA retest. Post-audit logical fencing alone is insufficient.
2. The **witness medium has not been proven nonrollbackable**. Both SQLite and ordinary witness files restored together can resurrect old credentials. The local HMAC provider is **not real Windows identity/authentication**.
3. The witnessed research model hashes all database rows and checks the entire witness chain per operation; **not scalable**, no production transport or concurrency qualification.
4. SQLite commit marker and SMB immutable content require cross-system durability qualification, object GC safety, and full multi-file record CAS integration. No physical Windows/SMB cross-host or power-cut tests.
5. Disposable `LocalIndex.rebuild` mixed snapshot/old cursor still tracked in issue #9; no production index code changed by this role.

**Next engineering priorities:** Claude must review the proposed `CommitAuthority` seam and override linearization; keep issue #8 open. Validate actual two-host Windows/SMB behavior when infrastructure becomes available. Design a genuinely protected high-water witness and audited restore protocol, not merely a second file. Verify multi-record CAS in an isolated adapter before integration. No merge, release or production code modified.

## Session continuation — 2026-11-01 ET ~23:08 onward: explicit director queue, atomic batches, recovery and #10 race

**Orders:** Re-read latest `main` `docs/agents/README.md`, `docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md`, and `docs/agents/CLAUDE_RESUME_HANDOFF.md`; inspected main project rules and Phase 2 requirements/architecture, open research PR #4, issues #8/#9/#10 and Claude Phase 2 PR #7. Director explicitly directed isolated atomic multi-record proof, simultaneous DB+witness rollback counterexample, epoch rotation and bounded validation, Windows #10 source review, concise API/migration handoff and cross-host qualification plan. No merge/release authorization.

**New actual research implementation and commits, all on existing branch:**
- `e782ce6a` extended `prototype/test_witnessed_coordinator.py` with **intentionally passing UNSAFE counterexample:** rolling back **both** ordinary witness files and SQLite snapshot resurrects a revoked owner, while witness-only rollback is rejected. A second local file is not nonrollbackable.
- `98aab9f0`, `56744349`: new `prototype/record_batch_research.py` demonstrates transactionally validated `put/delete/check` with mandatory per-record `expect_rev` and resource revision, tombstones, one journal sequence for all changed records, conflict rollback, identical `txid` replay, local SHA-256 manifest checks, simulated authority disconnection and reconnect quarantine, bounded batch size. Extends the witnessed research authority; **not** a production `CommitAuthority`.
- `4d28977a`: new `prototype/test_record_batch_research.py` with **13** test cases: multi-record document/revision/checkout check-in, atomic conflicting rollback, resource CAS, lost acknowledgement, stale generation, staged/missing/corrupt blobs, reconnect/watermark, spawned process contention and process death before/after publication, input validation.
- `ee4c73b9`: added batch tests to Windows/Ubuntu Python 3.11/3.13 Actions matrix.
- `0cb19e70`: replaced obsolete old-Phase-2 defect-expected assertions with **two positive safety acceptance tests** in `prototype/test_phase2_lock_break_order.py`, validating journal-first revocation before `writer.lock` evidence move and no effective old-token publication. The old pre-audit defect evidence remains in historical CI.
- `f3652a76`, `90199b26`: new `prototype/test_phase2_lock_release_race.py` plus Phase 2 actual-code job; one normal release control and one intentionally passing **unsafe** administrative-override/release interleaving.
- `29120bcc`: added research-only `WitnessedCoordinatorResearch.rotate_epoch` for a healthy authority, test-verified administrator/reason, revoke all leases/generations, journaled audit, durable guard replacement and witness before SQLite commit, fail-closed on partial crash. **Not a supervised backup restore mechanism.**
- `349561ae`: four epoch-rotation tests, including old token rejected across restart, prior commit recoverability, denied wrong-role operation, empty authority and independent-process death between guard replacement and DB commit.
- `2b092d02`: bounded full-witness and authoritative-state validation to **4,096 ledger entries and 10,000 rows**; fail closed on saturation rather than accept unchecked state. Explicit research-only limits; throughput/scalability remains poor.
- `6e2e8be2`: two tests proving capacity and row-limit violations do not acknowledge or materialize transactions.
- Docs updated: `55e2f5d0` `docs/COORDINATOR_INTEGRATION_CONTRACT.md` (batch CAS/fail closed, epoch and migration); `359415e6` `docs/COORDINATOR_WITNESS_RESEARCH.md` (double rollback, epoch rotation, quadratic costs); `b0d283ed` `docs/WINDOWS_SMB_CERTIFICATION_PLAN.md` (actual two-host issue #10 race and transient sharing cases); `104ecbdf` `docs/COORDINATOR_RESTORE_FENCING_PROTOCOL.md` (healthy rotation vs restored history); `faa420a5` `docs/DISTRIBUTED_SYSTEMS_HANDOFF.md` (current safety gates); `6ece0ac9` `prototype/README.md` (commands/evidence).

**Exact independently executed CI (actual job logs checked):**
- [run 41387393542](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387393542) at research commit `0cb19e70`: **57/57 tests each** on Ubuntu/Windows Python 3.11/3.13; Phase 2 positive journal-first revocation tests **2/2**, actual `phase2-documents` checked out in job.
- [run 41387552435](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387552435), research commit `90199b26`, actual Phase 2 `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6` confirmed in job logs: **4/4 source tests**, with three positive controls and **one deliberately passing unsafe pathname-release reproduction**. Exact mechanism: old owner token rechecked, pauses before `Path.unlink`; administrator journals revocation and moves old file; successor acquires new lock; old owner resumes unlink and deletes successor lock. QA's passing Windows 5×6 process stress check addresses transient sharing violations, **not** this release/override interleaving. Reported material evidence to [issue #10](https://github.com/nrobertson152/RevisionWorx/issues/10#issuecomment-6568943617).
- [run 41387667107](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387667107) at research `349561ae`: **61/61 per Windows/Ubuntu Python 3.11/3.13** (epoch rotation and double-rollback tests included), 2/2 Phase 1 and 4/4 Phase 2 probes.
- **Latest fully verified [run 41387844444](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387844444)** at research `6e2e8be2`: **63/63 research tests PASSED on EACH** Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11 and Windows Python 3.13; 2/2 actual Phase 1 historical defect reproductions and 4/4 actual Phase 2 probes. Source Phase 2 pinned by checkout logs to `f3c54386`. The branch commits after `6e2e8be2` at this progress update are **documentation only**, not new executed production code. Workflow results must not be described as physical SMB certification.

**Important changed defect status:** Claude's revised ADR-015 journal-first revocation passes two independent local safety tests for issue #8 against `f3c54386`. Former test assertions expecting an effective stale pre-audit mutation became obsolete and caused workflow runs `41387273229` and preceding to fail; I replaced them with positive safety assertions. Historical unsafe behavior is preserved in older run `41381819173`. Issue #8 still needs QA acceptance and physical multiuser coordinator qualification; cannot claim production safe. Issue #9 production rebuild candidate is Claude-owned and needs QA. A new **issue #10 successor lock deletion** remains source-confirmed after transient Windows retry fix.

**Unverified / blocking:**
1. Restoring **both** ordinary DB and witness can resurrect old authority: demonstrated vulnerability. Truly independent nonrollbackable trust root, supervised reconciliation of acknowledged commits and audited restore remain **not implemented**.
2. Signed fixture identities are **not** Windows SSPI/AD authentication, trusted LAN transport or secure admin operations. The witnessed prototype is O(N²)-like across operations and bounded only by small research limits.
3. Multi-file proof uses local SQLite + local immutable blobs; it does **not** establish SMB server fsync/durable acknowledgment or cross-system atomicity. **Zero** physical two-host Windows/SMB or power-cut tests.
4. Releasing a standalone lock still has an actual token-check-to-pathname-`unlink` race that can delete a successor's lock; Claude owns production correction. No application core files modified by this role.
5. Outstanding Claude design decisions: production `CommitAuthority` encoding, per-document/assembly lease partitioning, admin authentication mapping, external witness high-water and safe standalone-to-coordinator migration. Documentation handoff updated; do not implement speculative production service before review.

**Coordination and boundary:** Draft PR #4 retains all commits and existing branch, no force push, merge, tag or release. Posted new issue #10 evidence, kept #8/#9/#10 owner boundaries. The research branch serves technical review, not a deployment artifact. Next: Claude reviews interface and ownership-safe lock release; QA independently retests fixes; Windows/SMB rig qualification only with actual hosts. No redundant documentation-only full matrix required.

### Session closeout — verification, coordination and scope audit

- Independently inspected [run 41387844444](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387844444), all six job logs: 63 passing research tests on **each** Ubuntu/Python 3.11, Ubuntu/Python 3.13, Windows/Python 3.11, Windows/Python 3.13; 2/2 old Phase 1 defect probes and 4/4 current-Phase-2 tests (3 safety controls, 1 unsafe issue #10 reproduction). All four research matrix jobs were successful. No physical two-host SMB tests performed.
- The Phase 2 checkout SHA in the actual CI job was `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6`; a later Phase 2 branch head must not be presumed covered. The research implementation commit covered is `6e2e8be2f5cc9408ad801c4d2ec446e10c978e36`; all changes since that code checkpoint are documentation-only.
- Source-level issue #10 is reproducible but only under the tested local POSIX pathname interleaving. It is **not** equivalent to QA's transient Windows sharing-violation test; [QA's NTFS 5×6-process success](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311010) and the research release/override failure can both be true.
- Updated `docs/FILESYSTEM_COMPATIBILITY.md` in `841c1a67` to differentiate actual NTFS CI evidence, local Linux successor deletion and **zero** physical SMB coverage.
- Latest PR #4 changed-filename audit verified **28 files**, all within `.github/workflows/distributed-authority-research.yml`, `docs/` and `prototype/`. **No** `revisionworx/workspace.py`, `revisionworx/index.py`, `revisionworx/documents.py`, other agent branches, merges, tags or releases were modified.
- Sent one consolidated technical decision request to Claude through [architecture issue #5](https://github.com/nrobertson152/RevisionWorx/issues/5#issuecomment-6569024688): batch interface/lease boundaries, administrative identity, external nonrollbackable trust root and #10 release ownership. Separate exact issue #10 reproduction [comment](https://github.com/nrobertson152/RevisionWorx/issues/10#issuecomment-6568943617) includes source SHA and interleaving. Updated draft PR #4's body and handoff.
- Per Director's stop condition, refrain from speculative production integrations while awaiting Claude's interface and recovery decisions. Physical two-host Windows/SMB certification, secure auth and lossless restore reconciliation remain blocked by missing approved implementation and rig; any safety/production readiness claim is unsupported.
