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


## Session — 2026-11-01 ET, 23:08 onward: Director follow-on ADR-019 / ADR-017 conformance

**Current orders re-read:** latest main docs/agents/README.md, docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md, docs/agents/PARALLEL_ASSIGNMENTS.md, docs/agents/CLAUDE_RESUME_HANDOFF.md, CLAUDE.md. Inspected live PRs #2/#3/#4/#6/#7 and issues #5/#8/#9/#10. Most recent direct PR #4 instructions are [comment 6569057109](https://github.com/nrobertson152/RevisionWorx/pull/4#issuecomment-6569057109) (independent ADR-019 safety and actual ADR-017 contract) and [comment 6569097906](https://github.com/nrobertson152/RevisionWorx/pull/4#issuecomment-6569097906) (updated Phase 2 source to d656884, orphaned release custody). **Only** the existing research/shared-filesystem-protocol branch modified; Claude remains owner of production revisions.

### Concrete commits and actual files

- 039e1a73: replaced the obsolete issue #10 demonstration with **positive safety acceptance** on real ADR-019 (no successor lock deletion; third-writer B fencing and C ownership; revoked writer never mutates successor).
- 4f4a67c5: extended isolated BatchAuthorityResearch with named conflict types, optional unconditional put/delete expected revisions (matching ADR-017 semantics), and complete command opcode in txid request identity.
- bebde84e: added prototype/test_phase2_contract_adapter.py, inheriting upstream tests/contract_commit_authority.py **unchanged** through a test-only adapter over the witnessed local SQLite batch research. No live service or production integration.
- db3995e2: added a focused Ubuntu/Python 3.13 exact upstream pytest conformance job and pinned Phase 2 checkout. The code-to-code test installs pytest into the CI runner only.
- 7363403b: corrected our test oracle to inspect the **durable revocation journal entry** rather than assuming that audit projection had already materialized.
- e147a2d2: added actual Phase 2 test_phase2_txid_check_reuse.py with positive identical replay and **expected-failure** test for reusing same txid/writes while changing RecordCheck. Changed preconditions are omitted by the current file journal's idempotency payload.
- 46683b6b: research adapter positive test confirms changed RecordCheck **is** rejected with TxidReuseError, and renamed fixture provider to avoid pytest collecting it as a test class.
- c511efb7: expanded actual-source ADR-019 safety and ADR-017 identity job to **Ubuntu and Windows** Python 3.13.
- 7fef1078: new prototype/test_phase2_orphan_recovery.py with **three** independent positive safety tests: recover abandoned release by revoking its token exactly once; preserve malformed private evidence; interrupt revocation, fail closed, require admin break and restore.
- 6e646ccc: pinned both actual Phase 2 and direct-contract CI jobs to **d656884b39401134149273dd0121ef620b76eb8e**, incorporating Claude's new crash-after-rename orphan recovery.
- d4415c0b: revised independent third-writer oracle to allow either the releaser or orphan-scanning acquirer to journal the one effective revocation and preserve evidence. Earlier source at 541abe took one route; d656884 can take the other. It is a test-expectation update, **not** an application fix.
- 0c01af28, 3a3dfcb2: updated risk-ranked docs/DISTRIBUTED_SYSTEMS_HANDOFF.md and ADR-017 docs/COORDINATOR_INTEGRATION_CONTRACT.md with exact evidence and decision requirements.

### Independent verified test evidence

**[GitHub Actions run 41389551713](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389551713) SUCCESS.** Research overlay **d4415c0b34932febbd9a0a0d7bc097eef1043fcd**, actual Phase 2 pinned **d656884b39401134149273dd0121ef620b76eb8e**:
- Ubuntu Python 3.11 **63/63** research prototype tests passed.
- Ubuntu Python 3.13 **63/63** passed.
- Windows Python 3.11 **63/63** passed.
- Windows Python 3.13 **63/63** passed.
- Ubuntu Python 3.13 real Phase 2 **11 tests, 1 expected failure, no unexpected failures**, including three new orphan custody/recovery cases.
- Windows Python 3.13 real Phase 2 **11 tests, 1 expected failure, no unexpected failures**.
- Ubuntu Python 3.13 inherited **unchanged upstream** CommitAuthorityContract against our test-only adapter: **11/11 passed**, including three research-only extensions.
- Ubuntu Python 3.13 real old Phase 1 probes **2/2 passed as historical defect reproductions**, NOT safety acceptance.

The *one expected failure per Phase 2 OS* is **open ADR-017 txid identity ambiguity**: a retry that changes RecordCheck while keeping txid and write-records identical returns an earlier committed sequence instead of TxidReuseError. This is currently P1 interface ambiguity, **not demonstrated data loss**. Exact reproduction and request for Claude decision posted to [PR #7 comment 6569196260](https://github.com/nrobertson152/RevisionWorx/pull/7#issuecomment-6569196260). The research adapter correctly rejects this changed precondition.

Earlier runs 41388921867 and 41389001909 failed because our newly authored third-writer test assumed the changed journal audit was already materialized; corrected by reading the authoritative journal. Run 41389488005 failed only because the new upstream d656884 uses the **orphaned** audit/evidence route rather than the earlier **displaced** route; corrected in d4415c0b and then verified in run 41389551713. Prior failed runs are not misrepresented as green or hidden.

### Architecture and release position

**Risk-ranked protocol review** and exact local test evidence in docs/DISTRIBUTED_SYSTEMS_HANDOFF.md, signed batch source/contract mismatch in docs/COORDINATOR_INTEGRATION_CONTRACT.md. ADR-019 source-level successor protection and orphan custody satisfy the *modeled local Windows/Ubuntu* interleavings. A crash during orphan scanning can strand the provisional successor's lock; this is a fail-closed **availability** cost requiring audited admin recovery, not proof of arbitrary crash-safe reboot on actual SMB.

**Unresolved hard gates:** independently nonrollbackable epoch/high-water root; lossless backup restore/journal/blob reconciliation; real Windows authenticated admin and protected transport; physical **two-host Windows/SMB** correctness, cache and power-loss tests (NONE EXECUTED); bundled Windows/Linux desktop artifact smoke; production LAN CommitAuthority integration and safe mode migration. Research SQLite currently uses local disk only and does not make SMB content durable or atomic with a journal.

**Repository boundaries / next steps:** Maintain draft PR #4; no modifications to production Workspace, LocalIndex, documents.py, Claude branches or other repositories; no merges, retargets, force pushes or releases. Await Claude's ADR-017 full-command txid identity decision and independent QA's final ADR-019 acceptance. No speculative production coordinator service will be built without architecture approval.


## Director follow-on completion, 2026-11-01 ET / 2026-11-02 early UTC

Read latest main agent orders, README, PARALLEL_ASSIGNMENTS and Claude resume handoff, then current PR #4 [instruction 6569261694](https://github.com/nrobertson152/RevisionWorx/pull/4#issuecomment-6569261694), PR #7, issues #5/#8/#9/#10 and live CI. Scope remains solely distributed research/shared-filesystem-protocol. Claude owns actual Workspace/CommitAuthority; QA owns independent final acceptance.

### Commits and tests added

- 90a5aa7f: strict positive expected-failure assertion: unreadable orphan may not admit a new writer when a displaced owner was preflighted.
- 9df169bd: explicit passing UNSAFE demonstration on frozen d656884, Ubuntu and Windows: B and C both publish effective records after B's orphan bytes become unreadable; B's token was never revoked. Added concurrent acquirer and orphan crash/restart coverage, changed/reordered/check-only txid tests, four adapter contract tests.
- 3b9b478e: prototype/request_identity_research.py and 16 invariant tests for proposed full ordered request identity; docs/ADR017_FULL_REQUEST_IDENTITY_PROPOSAL.md with versioned digest and legacy-txid migration warning; matrix extended.
- b2791702: atomic SQLite batch commit against concurrent administrator override, verified fail-closed generation revocation; mislabeled malformed orphan positive test explicitly renamed as an UNSAFE control; persistent counterexample doc.
- 756c218d: two exact-source Ubuntu/Windows candidate acceptance jobs pinned to Claude's 2a2804820d4aca48b50026bdb7edfe8c241b1866, with positive new ADR-017/019 fixes and strict negative legacy/order/audit recovery oracles. Upstream new PreconditionIdentityContract inherited unchanged in adapter.
- This progress update and handoff docs are documentation-only and do not trigger research matrix CI.

### Independently verified

**[GitHub Actions run 41391444478](https://github.com/nrobertson152/RevisionWorx/actions/runs/41391444478) SUCCESS in all 10 jobs.** Research overlay 756c218ded575b665b2119e480b075330713268b; exact current source 2a2804820d4aca48b50026bdb7edfe8c241b1866. Local research 80/80 PASS on EACH Ubuntu 3.11, Ubuntu 3.13, Windows 3.11, Windows 3.13. Actual corrected Phase 2 Ubuntu/Windows Python 3.13: 7 tests each, **3 expected failures** (audited recovery crash, overall operations interleaving, legacy-txid proof), zero unexpected failures. Original/new unchanged upstream contract via test-only adapter: 16/16 PASS on Ubuntu Python 3.13. Frozen d656884 Ubuntu/Windows Python 3.13: 18 tests each, 4 expected failures and one explicitly passing UNSAFE malformed orphan reproduction; those are HISTORIC, not current acceptance. Old Phase 1 unsafe probes 2/2 reproduced.

Research command: python -m unittest -v prototype.test_authority prototype.test_coordinator_research prototype.test_immutable_manifest_research prototype.test_witnessed_coordinator prototype.test_record_batch_research prototype.test_request_identity_research.
Candidate command: REVISIONWORX_PHASE2_CHECKOUT=/path/to/2a28048 python -m unittest -v prototype.test_phase2_new_fixes_acceptance.
Contract command: REVISIONWORX_PHASE2_CHECKOUT=/path/to/2a28048 python -m pytest -v -q prototype/test_phase2_contract_adapter.py.

### Findings / coordination

P0 original d656884 malformed-orphan dual effective writer proof [CI 41390605906](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390605906) reported [issue #10 6569351511](https://github.com/nrobertson152/RevisionWorx/issues/10#issuecomment-6569351511) and PR #7; Claude corrected automatic acquisition in 2a28048. Our corrected-candidate local positive oracle PASS on both OS, but operator must externally confirm old writer stopped.

**P1 new audited-recovery crash gap:** clear_orphaned_release moves .releasing evidence before journal audit. Fault-injected death leaves no audit and permits later writable acquisition. Both OS positive oracles deliberately expected failure. Reported to Claude [issue #10 6569464980](https://github.com/nrobertson152/RevisionWorx/issues/10#issuecomment-6569464980) and [PR #7 6569467656](https://github.com/nrobertson152/RevisionWorx/pull/7#issuecomment-6569467656). Requested audit-first idempotent recovery.

P1/P2 ADR-017 remaining identity policy: current separate checks array does not encode check/write interleaving; historical journal without checks cannot verify complete old request. Request to Claude [PR #7 6569414410](https://github.com/nrobertson152/RevisionWorx/pull/7#issuecomment-6569414410), executable v2 canonical proposal and migration note. Current Phase 2 passes positive changed/reordered/check-only expectation tests and research adapter new conformance.

Remaining severe blockers: both SQLite+ordinary witness may be rolled back together; no independently nonrollbackable trust root, authentic Windows LAN administration, authenticated transport, approved server, physical two-host SMB and power-cut durability (NONE EXECUTED), standalone/coordinator migration, native installed GUI or Director-authorized merges/releases. PR #4 stays draft, no production code/other agent branch modified. Await Claude/QA on new audit crash gap and versioned full txid identity policy.

## Current follow-up — Director PR #4 orders 6569573586, executed 2026-11-02 ~00:10–00:27 ET

**Assignment:** Distributed Systems Engineer, existing `research/shared-filesystem-protocol` / draft PR #4. Read latest main README/orders/Parallel Assignments/Claude handoff plus live coordination issues and PRs. Actual Claude candidate evolved from `2a280482` to `366d4355abcde7543122551fadaab01ac9832e01` (ADR-019 audit-first and ADR-021 versioned request identity). Frozen Claude reliability backport remains `36d1957fe8d7800659b488106ef6cec1fa0c6267`.

### Real engineering work pushed on this branch

- `924917fe`: new `prototype/test_phase2_orphan_clear_protocol.py` with deterministic state/crash matrix: before audit, audit committed before evidence move, after evidence move, spawned independent-process `os._exit(74)`, retry/restart, failed filesystem move, audit/source validation, two overlapping administrator requests and pathname/content interference.
- `742f218e`, `2692551a`: adjusted positive safety oracles to the newly implemented audit-first resume semantics; the next acquisition may legitimately complete *already durably audited* evidence, rather than requiring indefinite quarantine. Left unsatisfied invariants marked expectedFailure.
- `731f5af8`, `a35b391c`: isolated `BatchAuthorityResearch` permits check-only requests with one journal marker and no data mutation; rejects nonfinite JSON prepublication; tests idempotent replay and no side effects.
- `a073c7cb`: aligned domain separator of the reference `request_digest` to Claude's actual ADR-021 `revisionworx/commit-request/v2\0` prefix.
- `5db88938`, `df7df652`: inherited upstream RequestIdentityV2Contract unchanged in adapter; added `prototype/test_phase2_identity_interop.py` comparing *real deployed digests* against our serializer and an actual persisted journal and legacy `IdentityUnverifiable` outcome.
- `d081b5db`: focused Windows/Linux CI jobs pinned to actual `366d4355`, preserving older 2a280 and d656 historical jobs distinctly.
- `3a57437c`: added explicit **passing unsafe reproduction** of two admins publishing separate `workspace.orphan.clear` audits for one original orphan under a deterministic overlap; separate expected-failure exactly-one safety oracle.
- `0025c060`, `cfce3c82`: new `prototype/test_phase1_backport_release_race.py` and Windows/Linux exact source CI pinned to frozen `36d1957`, with strict positive ownership xfail and passing successor-lock-deletion defect reproduction.
- `eff229c9`: updated ADR-017 identity specification with exact adopted prefix, source/reference byte equivalence, legacy outcome and migration limits; `77c4844b` risk-ranked handoff; `825eda40` integration contract.

### Verified complete CI and exact source SHAs

**[GitHub Actions run 41393842114](https://github.com/nrobertson152/RevisionWorx/actions/runs/41393842114), research code SHA `cfce3c82b0f45e57ea1645b0f3ddf6e6665554cb`, 14/14 jobs SUCCESS** (all job logs independently inspected):

- Ubuntu Python 3.11: **81/81 local research PASS**.
- Ubuntu Python 3.13: **81/81 PASS**.
- Windows Python 3.11: **81/81 PASS**.
- Windows Python 3.13: **81/81 PASS**.
- Ubuntu Python 3.13 actual current Phase 2 `366d4355`: **16 local tests, 2 expected failures** (admin double-audit and byte-swap between hash check/move), no unexpected failures; explicit passing unsafe reproductions demonstrate each defect.
- Windows Python 3.13 actual current Phase 2 `366d4355`: **16 local tests, 2 expected failures**, zero unexpected failures, both passing unsafe demonstrations.
- Unmodified upstream `CommitAuthorityContract`, `PreconditionIdentityContract` and `RequestIdentityV2Contract` inherited by the local witnessed adapter: **20/20 pass**, Ubuntu Python 3.13.
- Actual frozen `phase1-backport-8-9-10` @ `36d1957`, Ubuntu Python 3.13: **2 cases, 1 expected failure**, 1 passing UNSAFE old-owner/successor lock deletion.
- Same actual frozen backport on Windows Python 3.13: **2 cases, 1 expected failure**, 1 passing UNSAFE bug reproduction.
- Historic Phase 2 `2a28048`: Ubuntu and Windows each **7 cases, 3 expected failures**, a frozen previous-head baseline, not current candidate.
- Historic Phase 2 `d656884`: Ubuntu/Windows each **18 cases, 4 expected failures**, plus passing unsafe demonstrations of original unreadable orphan/txid bugs.
- Historic Phase 1 source probes: 2/2 passing OLD defect reproductions, not safety acceptance.

**Commands:**
`python -m unittest -v prototype.test_authority prototype.test_coordinator_research prototype.test_immutable_manifest_research prototype.test_witnessed_coordinator prototype.test_record_batch_research prototype.test_request_identity_research`;
`REVISIONWORX_PHASE2_CHECKOUT=<path-to-366d435> python -m unittest -v prototype.test_phase2_orphan_clear_protocol prototype.test_phase2_identity_interop`;
`REVISIONWORX_PHASE2_CHECKOUT=<path-to-366d435> python -m pytest -v -q prototype/test_phase2_contract_adapter.py`;
`REVISIONWORX_BACKPORT_CHECKOUT=<path-to-36d1957> python -m unittest -v prototype.test_phase1_backport_release_race`.

### Risk classification and communication

- **Verified local acceptance (not production certification):** ADR-019 audit-first commit before evidence disposition passes before/after journal interruption and independent-process death/restart. ADR-021 exact production request hashes match research for full operation order, Unicode, blob refs, null expectations and `_rev`; historical txids lacking a request digest raise `IdentityUnverifiable` without write and outcome lookup remains valid.
- **Open P1 under concurrent/untrusted filesystem mutation:** audit binds an orphan file's SHA-256 when read but an independent pathname `os.replace` later moves potentially different bytes; deterministic injected mutation caused mismatched archived evidence and permitted writer acquisition. Paired strict safety xfail and passing unsafe proof on both OS. This is not measured ordinary Windows NTFS or SMB failure. Needs a reviewed trust model and a crash-safe pending intent through post-move verification.
- **Open P2 audit uniqueness:** deterministic overlapping admin clears can each append a different durable audit for the same orphan name/hash although only one evidence file exists. Separate explicit passing unsafe proof and expected-failure positive exactly-one oracle on both OS. This is audit/custody inconsistency, not demonstrated duplicate engineering changes.
- **Backport P0:** exact older `phase1-backport-8-9-10` `36d1957` retains token-check followed by path unlink, physically deletes successor lock in deterministic Windows/Linux test. Do not fast-forward or merge this obsolete candidate. Reported on [PR #6](https://github.com/nrobertson152/RevisionWorx/pull/6#issuecomment-6569794832).
- **Coordination:** both new admin risks and positive ADR-021 interoperability reported on [Claude PR #7](https://github.com/nrobertson152/RevisionWorx/pull/7#issuecomment-6569784602). All production core modifications and QA final approval remain Claude/QA responsibility.

### Remaining hard gates and stop

No independently nonrollbackable epoch/high-water trust root; test-authorized principals are NOT Windows SSPI/AD; no physical two-host SMB power-cut/ACL/reconnect tests (**NOT EXECUTED**); no deployed LAN coordinator or service, no integrated production batch bridge, no safe physical backup-restore reconciliation; no standalone migration or installed interactive GUI acceptance. **No production source, other agent branch, other repository, merges, releases, retargets, force pushes or tags changed.** PR #4 remains draft. Next step only when Claude reviews source-identity and audit-CAS design; do not fabricate approval or add a speculative live service.

## Resumed independent verification — 2026-11-02 ET

Research parent: `a8f0daa28ad116076450841d6000775a9021c8fd`.
Read historical main agent README/orders and Claude handoff from `e31efe1`
(the latest main removed these files), branch progress/handoff and production
ADR-017/019/021. Read-only remote discovery found production unchanged at
`366d4355abcde7543122551fadaab01ac9832e01` and backport unchanged at
`36d1957fe8d7800659b488106ef6cec1fa0c6267`.

Preserved both original race reproducers. Added deterministic pre-verification
replacement positive control and distinct-object pathname replacement unsafe
proof, including a POSIX open descriptor showing original bytes while pathname
custody archives different bytes. See `docs/ORPHAN_CUSTODY_REVIEW.md` for proposed
exactly-once semantics, custody states, trust boundary and backport applicability.
No production or OpenSchematix files changed.

Executed on Debian GNU/Linux 13, Python 3.12.14, local overlay containing this
entry. Production source exported using git archive at the full pins above into
`/workspace/scratch/revisionworx-review/{phase2,backport}`; no worktree created.
Exact commands from the research root:

- `python3 -m unittest -v prototype.test_authority prototype.test_coordinator_research prototype.test_immutable_manifest_research prototype.test_witnessed_coordinator prototype.test_record_batch_research prototype.test_request_identity_research`: 81 passed, 0 failed, 0 skipped, 0 expected failures.
- `REVISIONWORX_PHASE2_CHECKOUT=/workspace/scratch/revisionworx-review/phase2 python3 -m unittest -v prototype.test_phase2_orphan_clear_protocol prototype.test_phase2_identity_interop`: 18 total, 16 passed, 2 expected failures, 0 unexpected failures, 0 skipped. Passing unsafe demonstrations are defect evidence, not acceptance.
- `REVISIONWORX_PHASE2_CHECKOUT=/workspace/scratch/revisionworx-review/phase2 /workspace/.venvs/openschematix/bin/python -m pytest -q prototype/test_phase2_contract_adapter.py`: 20 passed, 0 failed/skipped/xfail; reused existing pytest installation without modifying the other repository.
- `REVISIONWORX_BACKPORT_CHECKOUT=/workspace/scratch/revisionworx-review/backport python3 -m unittest -v prototype.test_phase1_backport_release_race`: 2 total, 1 passing unsafe proof, 1 expected failure, 0 unexpected failures, 0 skipped.

Current comments on PRs #4/#7/#3, issue #10 and live CI could NOT be read:
`gh pr view` receives Forbidden from api.github.com; CONNECT diagnosis confirms
HTTP 403. Existing Git proxy read authentication works. Saved additive
api.github.com domain requirement in environment draft; user must apply it in
settings before live coordination/CI inspection can resume. Historical CI totals
above are prior logged evidence, NOT independently reverified this session.
Windows and physical SMB/power-loss checks NOT RUN. No corrected Phase 2 or
backport commit is present; do not certify either. Existing pinned CI jobs will
cover the added methods if the push workflow executes; results remain unverified
until API access is restored. Next: inspect live comments/CI, report the
pathname-object refinement to PR #7, update PR #4 with the pushed SHA, and test
Claude's eventual correction. No merges/releases/force-pushes/retargeting.

## GitHub connector verification checkpoint — research 23d8b941 (2026-11-02 ET)

Independently inspected live branch refs and completed Actions run [41562781242](https://github.com/nrobertson152/RevisionWorx/actions/runs/41562781242), research head `23d8b94113be11bbdce83b9db06349b63dc29d68`. All **14/14 jobs SUCCESS**; exact job logs sampled and inspected for Phase 2, Phase 1 backport, authority model and ADR-017 contract. Pinned production Phase 2 remains `366d4355abcde7543122551fadaab01ac9832e01`; backport remains `36d1957fe8d7800659b488106ef6cec1fa0c6267`. No later corrective SHA existed at this check.

- Isolated research model: **81/81 passed** on each of Ubuntu 3.11, Ubuntu 3.13, Windows 3.11, Windows 3.13; command defined in `.github/workflows/distributed-authority-research.yml`.
- Deployed Phase 2 ADR-019/021 source tests: **18 tests each** on Ubuntu and Windows Python 3.13; **16 passed, 2 strict expected failures, 0 unexpected failures**. The two xfails are (a) archive digest must match authorization even under replacement between verification and move, and (b) overlapping admin clears must produce one terminal authorization. Separate passing tests deliberately demonstrate the *unsafe* outcomes. Passing unsafe reproduction != acceptance.
- Research adapter against unchanged upstream ADR-017/021 contract: **20/20 passed** on Ubuntu Python 3.13.
- Frozen Phase 1 backport: **2 tests each** on Ubuntu/Windows Python 3.13, **one strict expected safety failure plus one passing unsafe successor-lock deletion reproduction**.
- Earlier Phase 2/Phase 1 legacy-probe jobs in the same run also succeeded; their success does not expand safety acceptance beyond pinned sources.

The newly committed path-object replacement probe distinguishes POSIX open descriptor identity from pathname identity and does not claim equivalent Windows handle behavior. No physical two-host SMB, power-loss durability, adversarial remote-server, or Windows installed GUI certification has been performed. The current branch and CI already contain the additional test and protocol review files; this entry records independently verified *live* evidence and does not claim a newer correction.

**Explicit recommendation:** REJECT Phase 2 administrative orphan-clear production acceptance until the audit/custody TOCTOU and duplicate-clear authorization issues are corrected and retested against an exact new SHA. REJECT the frozen Phase 1 backport. ACCEPT only the stated standalone/local positive ADR-021 interoperability and specific audit-first crash-order properties at the pinned source. Keep PR #4 draft, and do not merge, release, retarget, force-push or modify production code.


## Independent ADR-019 rev.3 safety acceptance — 2026-11-02 ET / 2026-11-03 UTC

**Role:** Principal Distributed Systems Engineer. **Exact reviewed production source:** `e5b38a69edd71eb580f5bae67a570d4def94eb8f`, phase2-documents (not merged). **Research test SHA:** `2191dd96e19b38b924c2a92d36d7b389ca78c43d`; pinned 4-platform workflow SHA `8f8c8d33ca2c5c725830ab908f63e51059422f2e`. **GitHub Actions run:** [41564498507](https://github.com/nrobertson152/RevisionWorx/actions/runs/41564498507): **all 18 workflow jobs SUCCESS** (some historical jobs retain deliberate strict safety xfails against older vulnerable source; a green historical job is NOT acceptance of those prior commits).

**New source:** `prototype/test_phase2_rev3_independent_acceptance.py`; execute from research checkout with `REVISIONWORX_PHASE2_CHECKOUT=<exact e5b38a6 checkout> python -m unittest -v prototype.test_phase2_rev3_independent_acceptance`. The workflow checks out the exact production SHA, rather than mutable branch HEAD. Five independent *positive safety oracles* per local native runner: (1) atomic **file-object replacement** at orphan -> private custody move yields a reasoned refused audit, SHA mismatch and blocking quarantine, never incorrectly verified evidence; (2) custody mutation after verification is excluded from archived verified bytes and quarantined; (3) reentrant overlapping administrative authorization results in exactly one durable authorization, one result, one archive and adoption of the winner's reason; (4) injected process death after custody but before evidence preserves blocking custody, then deterministic audited resumption without duplicate records; (5) nested competing terminal publication is serialized by CAS and records one terminal result.

**Independently inspected job logs:** Ubuntu Python 3.11: **5/5 PASS**; Ubuntu Python 3.13: **5/5 PASS**; Windows Python 3.11: **5/5 PASS**; Windows Python 3.13: **5/5 PASS**, zero test skip/xfail/error in the *new* 20 executions. Old vulnerability reproductions remain preserved and pinned to `366d4355` in `prototype/test_phase2_orphan_clear_protocol.py`; the four new jobs are separate and do not simply relabel expected failures as passing safety. QA additionally independently accepted exact `e5b38a6` (full four-platform integrated run 41563864278 and before/after 41563864461).

**Disposition:** **ACCEPT the corrected P1 audit digest-to-archived-evidence binding and P2 serialized one-authorisation/one-result administrative orphan-clear semantics for the exact reviewed Phase 2 commit on native local Linux and Windows filesystems**, within tested fault-injection and mutation boundaries. This is **NOT blanket Phase 2 release acceptance**, not Phase 1 backport acceptance, and not a merge/retarget authorization. Known boundaries: administrator must independently establish the unknown writer stopped before a manual clear; a pre-opened in-place mutation handle, malicious same-directory actor, fsync/power-cut hardware, physical dual-host SMB/NFS, installed GUI and backup/restore have not been certified. No Phase 1 fast-forward: old backport `36d1957` retains its independently reproduced P0 successor-lock deletion race and must be replaced and reviewed at a new exact SHA. Continue focused concurrency and storage-fault research without changing production core.


## ADR-019 protocol-only versus external namespace-actor boundary — 2026-11-02 ET / 2026-11-03 UTC

**New exact Phase 2 review source:** `d2b7a1cfead5b83acecce2252e70d0a155f146b7`. Claude A reproduced the two Codex/QA dual-effective-writer cases from `e5b38a6` but did not patch those out-of-protocol lock pathname substitution cases; instead ADR-019 documents a trusted lock-directory deployment boundary. QA verified the unsafe outside-rename cases on real local Ubuntu/Windows Python 3.11/3.13, [run 41564896651](https://github.com/nrobertson152/RevisionWorx/actions/runs/41564896651): **2 passing intentionally UNSAFE reproducers + 2 strict XFAIL positive safety requirements per OS/Python**. Their green CI is evidence of vulnerability under external pathname writer capability, not acceptance.

**Independent research implementations and commits:**
- `d50594ac0d00e0e2a68f32f3d0248f9e35950f99`: new `prototype/test_phase2_rev3_protocol_boundary.py`, actual-production tests pinning protocol-only crashes and authorization conflicts.
- `22aab2efcecfb16b1280a16b71810a5c2ad8d841`: four-platform CI with exact `d2b7a1c` source rather than moving head.
- `f74923b8af6cbb1ac44bdead74f82e428449ca2e`: extended positive suite to cover same-byte new orphan incarnation and tampered/missing forensic evidence.
- `e92f75f798fabc73c5a3aa8d9aec7ee987f35e10` and `660453fb4de07d6c94c7dd7d279b8b5b953b4219`: independent [ADR-019 threat-boundary and Director-decision review](https://github.com/nrobertson152/RevisionWorx/blob/research/shared-filesystem-protocol/docs/ADR019_THREAT_BOUNDARY_REVIEW.md).

**Reproducible command:** `REVISIONWORX_PHASE2_CHECKOUT=<exact d2b7a1c checkout> python -m unittest -v prototype.test_phase2_rev3_protocol_boundary` from research branch; pinned [Actions run 41566269058](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566269058) is **COMPLETED SUCCESS, all 22 jobs**. All 4 new matrix jobs: native **Ubuntu Python 3.11: 6/6 PASS, Ubuntu Python 3.13: 6/6 PASS, Windows Python 3.11: 6/6 PASS, Windows Python 3.13: 6/6 PASS**, total **24/24 positive safety passes, 0 new XFAIL/skips/unexpected failures**. Historical-source strict xfails in other jobs do not certify their older sources.

**New independently exercised properties:**
1. Actual B writer paused inside commit beyond lock preflight; normal release-displacement state exposes two readable orphans; injected crash immediately after A's .releasing -> .fencing move. Candidate acquirer gives back its lock. On resumption both displaced tokens are each revoked exactly once before C publishes; B's attempted publication is rejected; only C's record is effective.
2. Same B/C setup, injected crash after first token journal revocation but before evidence archive. Resumption archives A without duplicate revocation, fences B exactly once and rejects B's late commit; C only effective.
3. Crash after durable administrative authorization, before custody. Different reason and distinct user@host each yield `OrphanClearConflict` with pending outcome and no extra auth. Identical original request replays through completion with one auth, one result and unchanged evidence. Conflicting late request is rejected with completed outcome.
4. Recreated identical orphan bytes at same pathname after terminal clearance get a **new clear_id**, not mistaken idempotent replay.
5. `Workspace.verify()` detects later unauthorized archived evidence modification or removal by `orphan-evidence-altered`/`orphan-evidence-missing`.

**Disposition:** **ACCEPT only the tested in-scope protocol-process crash/fencing and administrative identity properties for exact `d2b7a1c` on local Windows/Linux. REJECT any blanket writer-exclusivity claim against outside direct-file actors** until a Director-approved deployment/trust boundary exists. The earlier QA/Codex P1 proofs remain valid; this research did *not* make them impossible. A file-only lock cannot protect against an arbitrary same-privilege process moving `writer.lock`: QA reproduced and Claude A produced a minimal baseline without the orphan algorithm. OS ACLs applied to the same user do not isolate an app from another script under that account. Director must select trusted-folder operational contract versus separate service identity or a coordinator, with appropriate architecture review. No broad ADR-019 release approval, Phase 1 backport, SMB/multi-host, direct malicious journal writer or physical power-cut certification. Old Phase 1 backport `36d1957` remains REJECT/HOLD. Production untouched; no merge, retarget, release or force push.


## Critical corrective review — two NEW in-scope P1 ADR-019 authority races, 2026-11-02 ET / 2026-11-03 UTC

**Director deployment choice:** trusted local single-user filesystem for RevisionWorx 0.1.0 Alpha approved by PR #7 comment 6593823207. This excludes direct lock-namespace substitution by outside actors from the alpha guarantee but does **not** exclude ordinary RevisionWorx acquire/restore/crash races.

**New verified defects:** Codex found two in-scope protocol-only writer races on production `d2b7a1cfead5b83acecce2252e70d0a155f146b7` after the earlier 24/24 safety run. Independent research commit `7ac5f619be8192886031654fc37c03c895b87dbf` added `prototype/test_phase2_protocol_only_new_p1.py`, with 2 intentionally passing UNSAFE actual-code two-effective-writer reproductions and 2 strict expected-failure positive safety oracles. Matrix workflow commit `fd13fe0526e3b460526d3667122f1c3847c776e6` pins exact production source and Linux/Windows Python 3.13; [completed run 41566903120](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566903120), **all 24 workflow jobs SUCCESS**. Independently inspected both dedicated raw logs: Ubuntu: `Ran 4 tests ... OK (expected failures=2)`; Windows: `Ran 4 tests ... OK (expected failures=2)`, each **2 unsafe PASS + 2 safety XFAIL**, zero unexpected failures. A green xfail result does NOT accept the safety requirement.

**Defect 1:** In `_acquire_writer_lock`, D reads revoked-token A from `writer.lock`, pauses, B normally archives A and acquires new live lock. D then moves B's lock into `writer.lock.revoked-*` using the stale A inspection, no B revocation. C commits effectively. B's real post-preflight commit also becomes effective while C holds writer.lock; B remains unrevoked.

**Defect 2:** In `_restore_foreign_lock`, A's delayed normal release moves B to `.releasing-*`, C publishes its new writer.lock but pauses before orphan fence scan. A cannot restore B and archives B as unscanned `writer.lock.displaced-*` **before** B's journal revocation. Crash injected immediately after that actual protocol rename leaves no blocking orphan. C scans clear and commits; B resumes post-preflight and also commits effectively with unrevoked token.

**Repro command:** from research checkout with `REVISIONWORX_PHASE2_CHECKOUT=<exact d2b7a1c checkout>`, run `python -m unittest -v prototype.test_phase2_protocol_only_new_p1`. This suite preserves failing positive safety requirements and passing unsafe controls; any future corrected-source adaptation MUST make both strict positive oracles pass and unsafe demonstrations cease reproducing. The two separate previous outside-actor P1 proofs remain distinct.

**Disposition: REJECT / HOLD full ADR-019 writer-authority safety at exact `d2b7a1c` even for approved local alpha**, notwithstanding earlier narrow 24/24 PASS from [run 41566269058](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566269058). Require Claude A to fix both actual protocol-only races, then independent exact-SHA Windows/Linux acceptance. Earlier original P1 custody binding and P2 audited conflict tests remain valid only for their own exercised interleavings. Phase 1 backport `36d1957` still REJECT/HOLD. Physical SMB, genuine power-loss, installed GUI and hostile journal writer not certified. No production files changed, no merge/retarget/release. Full research review: [ADR019_THREAT_BOUNDARY_REVIEW](https://github.com/nrobertson152/RevisionWorx/blob/research/shared-filesystem-protocol/docs/ADR019_THREAT_BOUNDARY_REVIEW.md).


## 2026-11-02 ET / 2026-11-03 UTC — new in-scope protocol-only P1 findings

**Reviewed exact Phase 2 head:** `d2b7a1cfead5b83acecce2252e70d0a155f146b7`. Director chose single-computer/local, *trusted internal authority namespace* for alpha; this does **not** excuse defects made by normal RevisionWorx protocol actors. New Codex and independent QA findings describe two P1 unsafe operations and QA's `tests/qa/test_rev4_protocol_only_p1.py` contains effective dual-writer reproductions. I examined `Workspace._acquire_writer_lock` and `Workspace._restore_foreign_lock` directly: (1) stale read of revoked A followed by pathname `os.replace` may move successor B without revocation; (2) foreign release moves B outside the scanner namespace before journal revocation, so a crash creates an untracked surviving writer. Prior P1/P2 acceptance at `e5b38a6` was narrower and **does not certify either newly identified protocol-only interleaving**.

**New independent work on research branch:** `prototype/test_phase2_revoked_cleanup_protocol_only.py` @ commit `577d0de7e809abb905d74842884d464b0e9c2b30`: native actual-Workspace deterministic stale cleanup overlap with an ordinary competing `Workspace.open`, one explicitly passing UNSAFE demonstration and one strict expected-failure POSITIVE safety oracle against known vulnerable SHA. Four-platform matrix pinned to `d2b7a1cfead5b83acecce2252e70d0a155f146b7` in commit `cfa680dbfb028797cbe78f015910c92fe41ce152`. [Run 41567096076](https://github.com/nrobertson152/RevisionWorx/actions/runs/41567096076) was **queued at this checkpoint; no passing test result claimed until actual log inspection**. The new oracle tests pathname holder/revocation rather than two effective commits; QA independently has deeper journal-effective B/C assertions. Do not claim the narrow oracle alone establishes dual-effective-writer.

**Engineering correction request:** [PR #7 comment 6593994028](https://github.com/nrobertson152/RevisionWorx/pull/7#issuecomment-6593994028): revoked-lock cleanup must custody and inspect actual displaced object, preserve a scanned guard until any successor token is durably fenced; restore/archival must similarly retain the scanned .releasing obligation until durable B revocation; new positive interleavings and crash/restart checks should assert journal-effective outcomes. **Disposition: REJECT/HOLD full ADR-019 protocol-only local-alpha safety on `d2b7a1c`; old Phase 1 backport `36d1957` remains rejected.** No production source edited, merge or release.


### Exact-source independent CI verification outcome (2026-11-03 UTC)

[GitHub Actions run 41567096076](https://github.com/nrobertson152/RevisionWorx/actions/runs/41567096076), research workflow head `cfa680dbfb028797cbe78f015910c92fe41ce152`, actual candidate **`d2b7a1cfead5b83acecce2252e70d0a155f146b7`**: **28/28 jobs SUCCESS**, including the four new protocol-only stale cleanup probes. Independently inspected raw logs for the new jobs on **Ubuntu Python 3.11, Ubuntu Python 3.13, Windows Python 3.11 and Windows Python 3.13**. On each OS/Python runner, `prototype.test_phase2_revoked_cleanup_protocol_only` reported **2 tests: one passing deliberately UNSAFE reproduction and one strict expected failure of the positive safety requirement; no unexpected errors**. This proves stale revoked A inspection can cause D's protocol cleanup to archive successor B's live `writer.lock`, leaving B unrevoked and no longer holding the pathname. This *narrow* new test does not independently attempt two effective record commits; QA's separate full interleaving `tests/qa/test_rev4_protocol_only_p1.py` verifies the more serious effective B/C outcome and the displaced-archive crash. The passing workflow is **negative safety evidence**, not acceptance. **Final disposition remains REJECT/HOLD for d2b7a1c ADR-019 cooperative-local lock authority.**


## Independent ADR-019 rev.4 administrative break acceptance (2026-11-02 ET / 2026-11-03 UTC)

**Exact candidate:** `fe8a179f0203ef08151dc8459c84609dc639c4fe` (Claude A). **Vulnerable negative control:** `d2b7a1cfead5b83acecce2252e70d0a155f146b7`. **Independent source:** `prototype/test_phase2_admin_break_successor.py` in research commit `6e09b6ea0df406fae6652bd3cb7100e4d5d8ebfb`; CI workflow pinned in `c4ebe913ceacaf7a255ef3d716ecb9e503c4e301`.

**Raw GitHub Actions evidence:** [run 41570591613](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570591613) **COMPLETED SUCCESS, all jobs.** The focused **four-platform** corrected-source matrix executed two positive safety oracles per runner: **Ubuntu Python 3.11 2 PASS, Ubuntu Python 3.13 2 PASS, Windows Python 3.11 2 PASS, Windows Python 3.13 2 PASS (8/8)**, no expected failures in these positive tests. Verified Windows 3.11 and 3.13 logs. The frozen old-source Ubuntu Python 3.13 negative-control job deliberately expects a non-zero test command: both of the same safety tests **FAIL** on `d2b7a1c` with `{'revoked_b': False, 'live_b': False, 'barrier': False, 'crashed': False}`. This is a genuine before/after positive safety contrast, not merely obsolete xfail status.

**Exact test boundary:** Real administrative `Workspace.break_writer_lock` journal-revokes A, pauses at its filesystem move; ordinary B `Workspace.open(writable=True)` legitimately displaces revoked A and obtains a new B lock; administrator resumes the stale move. The oracle requires B's *actual* token durably revoked, B still owning the lock, or a scanned blocking custody barrier. The second oracle injects `SystemExit` before durable B revocation on the revised custody path, then attempts ordinary C acquisition: revoked B or retaining a live lock/barrier is required. Both protected on corrected source; old code silently moves B into unscanned `writer.lock.broken-*`.

**Disposition:** **ACCEPT the focused administrative-break successor-displacement and pre-revocation crash protections at exact `fe8a179` for tested local Linux/Windows filesystem behavior.** This is **not full ADR-019 local-alpha acceptance**. The other two protocol-only P1s (revoked cleanup and release-displacement crash), multi-party schedule stability, journal-effective B/C outcome safety under all interleavings, and multi-host SMB/NFS require separate QA/DS exact-SHA acceptance. The test intentionally accepts a revoked B, even if a healthy B session loses ownership, as Claude A's deliberate fail-closed policy. No production core changes, merge, release or Phase 1 fast-forward.
