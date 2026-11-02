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


## Latest Director protocol review — ADR-019 crash custody and ADR-017 conformance (2026-11-01 ET)

**This section supersedes the older #10 status above.** Phase 2 is now pinned to **d656884b39401134149273dd0121ef620b76eb8e** for this review. The revised ADR-019 handles abandoned private release files. No merge, release, network coordinator or physical SMB certification has occurred.

### Independently verified test evidence

[GitHub Actions run 41389551713](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389551713) **SUCCESS**. Research overlay **d4415c0b34932febbd9a0a0d7bc097eef1043fcd** and actual Phase 2 **d656884b39401134149273dd0121ef620b76eb8e**. Inspected all eight job logs:

| OS / environment | Exact result | Interpretation |
| --- | --- | --- |
| Ubuntu Python 3.11 | 63/63 passed | Local SQLite authority/manifest/witness/batch research |
| Ubuntu Python 3.13 | 63/63 passed | Local research |
| Windows Python 3.11 | 63/63 passed | Local Windows research, not SMB |
| Windows Python 3.13 | 63/63 passed | Local Windows research, not SMB |
| Actual Phase 2 Ubuntu Python 3.13 | 11 tests, 1 expected failure, 0 unexpected failures | ADR-019 safety + ADR-017 unresolved gap |
| Actual Phase 2 Windows Python 3.13 | 11 tests, 1 expected failure, 0 unexpected failures | Local Windows file namespace, not SMB |
| Exact upstream ADR-017 CommitAuthorityContract, Ubuntu Python 3.13 | 11/11 passed | Unmodified upstream contract inherited by research adapter, including three supplemental tests |
| Actual older Phase 1 defect probes, Ubuntu Python 3.13 | 2/2 historical reproductions passed | Defect reproductions are not safety acceptance |

Commands executed by the workflow:
- python -m unittest -v prototype.test_authority prototype.test_coordinator_research prototype.test_immutable_manifest_research prototype.test_witnessed_coordinator prototype.test_record_batch_research
- REVISIONWORX_PHASE2_CHECKOUT=/path/to/d656884 python -m unittest -v prototype.test_phase2_lock_break_order prototype.test_phase2_lock_release_race prototype.test_phase2_orphan_recovery prototype.test_phase2_txid_check_reuse
- REVISIONWORX_PHASE2_CHECKOUT=/path/to/d656884 python -m pytest -v -q prototype/test_phase2_contract_adapter.py

**ADR-019 local acceptance:** Independent positive safety tests prove that A does not delete B's lock when an administrator revokes A during release; a third writer C in the restore gap fences B and preserves evidence; late stale commits never become effective; a crashed releaser's orphaned private file is retained as evidence and its token revoked exactly once before a successor commits; malformed orphan evidence is retained; crash during orphan-fencing leaves the authority unavailable until audited administrator recovery. These are local OS tests, not physical machine power-off or SMB semantics. A fail-closed interruption can strand a lock, sacrificing availability rather than permitting two effective writers.

### Risk-ranked protocol review

| Priority | Finding | Verified evidence / required decision |
| --- | --- | --- |
| P0 | No independently **nonrollbackable** epoch/high-water witness; rolling back both DB and ordinary witness resurrects revoked credentials | The isolated double-rollback test demonstrates unsafe behavior. Select independent trusted storage and supervised history reconciliation before enabling writable recovery |
| P0 | SQLite authoritative journal and SMB immutable content have different durability boundaries; real two-host SMB not tested | Physical power-cut, reconnect, ACL and hash validation remain **NOT EXECUTED** |
| P0 | No real authenticated Windows administrator service or integrated production CommitAuthority | TestIdentityProvider is an in-memory signed fixture, not Windows authentication or approved LAN transport |
| P1 | **ADR-017 request identity excludes RecordCheck preconditions** | The actual Phase 2 idempotent txid lookup compares only write-records and blob refs. Changing RecordCheck but keeping writes and txid returns the original sequence. Actual Ubuntu and Windows tests each record an explicit **expected failure**. Recommend hash of full input operation list, or an explicit narrower contract |
| P1 | Upstream CommitAuthorityContract does not directly test altered RecordCheck identity, nonempty blob references, authenticated txid outcome scope or multi-lease commit | Exact upstream suite + three extra research adapter tests **11/11 passed**, but do not mistake this for full production conformance. Approve additional authoritative tests and wire mapping |
| P1 | Research adapter adds resource-level CAS obtained by a pre-read before the final transaction, potentially conservatively rejecting a concurrent *unconditional* write | Explicit prototype limitation. Decide whether an additional resource version check is acceptable or require pure atomic per-record semantics |
| P1 | No transport/status mapping for unavailable versus fenced versus unknown outcome; any authenticated fixture identity can query a txid | Define stable status and per-principal outcome authorization, reject offline authoritative operations without inventing not-found |
| P2 | Malformed abandoned release cannot identify a token; crash during recovery can retain an orphaned active lock | Local evidence and interrupted-recovery tests. Preserve raw forensic evidence and require audited admin break; no silent auto-ownership |
| P2 | Witness validation rehashes full authoritative state and all history per operation and is bounded at 4,096 entries/10,000 rows | Model proves bounded fail-closed behavior only. Production needs incremental commitments and trusted durable checkpoints |

### Handoff: strict authority semantics and ownership

All RecordPut, RecordDelete and RecordCheck validations, generation+epoch+owner checks and one sequenced durable commit marker must share the coordinator's **one** atomic authority transaction. A stable txid must identify a complete request including read preconditions if the ADR-017 phrase "identical content" is to be upheld. The file journal currently excludes non-journaled RecordCheck inputs from the txid replay identity; this is **a request identity ambiguity**, not evidence of duplicate published writes or loss of an acknowledged revision. The research adapter checks all submitted operations in its idempotency payload.

The private-rename release protocol changes ownership of the file actually moved, avoiding the old pathname-unlink race. Its orphan discovery journal-fences an unknown-to-successor token before the fresh holder can write, if the orphan contents can be read. Windows NTFS simulated test success is not a claim about server-enforced SMB handle caches or remote disconnections. No direct shared-file writer should ever be enabled automatically if the coordinator is down.

**Next decision owners:** Claude resolves full-command txid identity and core API semantics; QA independently accepts actual production fixes; the Director approves external trust root, Windows authentication, physical SMB certification and any merge/release. This research branch cannot independently certify them.


## Director-directed ADR-017 / ADR-019 review — 2026-11-01 ET, newest checkpoint

Current verified production candidate: Phase 2 SHA 2a2804820d4aca48b50026bdb7edfe8c241b1866. Previous d656884 is a frozen historical defect baseline, NOT current production safety acceptance. Research overlay SHA 756c218ded575b665b2119e480b075330713268b.

**Independently executed and inspected:** [CI 41391444478](https://github.com/nrobertson152/RevisionWorx/actions/runs/41391444478), 10/10 jobs completed successfully. Linux and Windows Python 3.11 and 3.13 EACH passed 80/80 local research tests. Actual corrected Phase 2 at 2a28048 ran 7 tests on Linux Python 3.13 and Windows Python 3.13, EACH with 3 deliberate expected failures and no unexpected failures. The unchanged upstream CommitAuthorityContract plus newly added PreconditionIdentityContract ran against the research-only adapter, 16/16 PASS (Ubuntu Python 3.13). Frozen d656884 actual Phase 2 ran 18 tests per OS with 4 expected failures and one passing explicitly unsafe malformed-orphan demonstration. Historic Phase 1 probes 2/2 passing known defect demonstrations (not acceptance).

**Corrected local behaviors (2a28048):** changed/reordered RecordCheck and check-only txid retries are rejected; identical original command succeeds after restart; unreadable orphan refuses new writable authority and keeps raw bytes until administrator clears it with a reason. Our prototype additionally proves one SQLite BEGIN IMMEDIATE serializes validated multi-record check-in against concurrent administrator generation override. These are local tests, NOT physical SMB, nonrollbackable recovery, Windows authentication or a production coordinator.

**P1 NEW finding: audited recovery crash gap.** On 2a28048, Workspace.clear_orphaned_release moves an unreadable .releasing-* file into orphan evidence BEFORE publishing workspace.orphan.clear audit. Fault-injected death after evidence move leaves neither orphan pathname nor audit; next writer can acquire and commit. Strict expected-failure oracle on both local OS. [Issue #10 evidence](https://github.com/nrobertson152/RevisionWorx/issues/10#issuecomment-6569464980). Under the documented requirement that an operator first stop the unknown owner, this is not itself proven concurrent dual-writer publication, but the recovery was not durably audited. Propose audit-first with idempotent evidence custody/restart; keep authority unavailable until the operator-confirmed recovery transition is durable.

**P1/P2 ADR-017 policy decisions:** 2a28048 stores an ordered checks array but loses the positions of checks interleaved with writes. A reordered overall operation list can still replay as identical when each separate array order is unchanged; this is an expected failure against our proposed complete ordered-request identity, not proven duplicate writes. Legacy journal entries lacked a checks field, and prior.get("checks", []) assumes absent means none even though original checks may be lost. Decide a versioned migration policy: never fabricate missing historical check bytes. [Executable full-request identity proposal](https://github.com/nrobertson152/RevisionWorx/blob/research/shared-filesystem-protocol/docs/ADR017_FULL_REQUEST_IDENTITY_PROPOSAL.md) includes 16 golden invariants.

**P0 trust-root gates remain open:** rolling back both ordinary DB and witness files can revive a revoked lease; no protected external monotonic high-water, authenticated Windows service, physical two-host SMB/ACL/power-loss testing (NOT EXECUTED), lossless restore, approved production integration, or safe standalone/coordinator migration. Keep PR #4 draft, no merge/tag/release or unsupported certification.

## Latest Director-directed review — ADR-019 rev.2, ADR-021 and Phase 1 backport (2026-11-02)

**Source and test pins:** Current production candidate `phase2-documents` @ `366d4355abcde7543122551fadaab01ac9832e01`; frozen Phase 1 reliability backport @ `36d1957fe8d7800659b488106ef6cec1fa0c6267`; research overlay `cfce3c82b0f45e57ea1645b0f3ddf6e6665554cb`. [Full research Actions run 41393842114](https://github.com/nrobertson152/RevisionWorx/actions/runs/41393842114) completed **14/14 jobs SUCCESS**. This means *tests executed*, not that intentional failing safety oracles were fixed.

| Pinned job | Independently inspected result |
| --- | --- |
| Isolated research Ubuntu Python 3.11 and 3.13 | **81/81 passed each** |
| Isolated research Windows Python 3.11 and 3.13 | **81/81 passed each** |
| Actual Phase 2 `366d435` Ubuntu and Windows Python 3.13 | **16 tests/OS, 2 expected failures/OS**, plus explicitly passing unsafe reproduction cases |
| Unchanged upstream CommitAuthority + Precondition + RequestIdentityV2 contracts on local SQLite adapter | **20/20 passed**, Ubuntu Python 3.13 |
| Actual frozen Phase 1 backport `36d1957` Ubuntu and Windows Python 3.13 | **2 tests/OS: 1 passing unsafe reproduction, 1 expected-failure safety oracle** |
| Historical Phase 2 `2a28048` Windows/Linux positive-vs-xfail acceptance | **7 tests/OS with 3 expected failures** |
| Historical Phase 2 `d656884` Windows/Linux negative baseline | **18 tests/OS with 4 expected failures** |
| Original Phase 1 defect probes | **2/2 intentionally passing historical defect reproductions** |

**Verified corrected (local OS only):** ADR-019 rev.2 audit precedes evidence movement, including independent-process termination after evidence move, durable journal outcome and restart acquisition that completes a prior audited-but-unmoved orphan; malformed evidence remains blocked before audit. ADR-021 production `request_identity()` and research canonical serializer produce **the same SHA-256 digest** over full submitted ordered operations, check-only commands, Unicode, blob order and stripped revision field. Legacy journal entries without `request` yield `IdentityUnverifiable`, no new publication, and an accurate `transaction_outcome`. See [ADR-017 proposal updated with verified adoption](https://github.com/nrobertson152/RevisionWorx/blob/research/shared-filesystem-protocol/docs/ADR017_FULL_REQUEST_IDENTITY_PROPOSAL.md).

**Open P1, additional fault-injected pathname/content TOCTOU:** `_complete_orphan_clear()` hashes `.releasing-*` and later moves the file by pathname. If a concurrent actor changes the bytes between that read and move, bytes not matching the audited SHA-256 can be stored under its evidence name and a later writer can acquire. The positive safety oracle is a strict expected failure on Ubuntu/Windows; the paired **explicit passing unsafe reproduction** verifies the mismatched evidence digest and that a new writer can commit. This requires deliberate concurrent directory/content interference, **not** ordinary crash/restart and not measured SMB corruption. Recommend a durable pending-clear barrier followed by verification of the actually moved file before completing authorization, with crash-safe quarantine and a defined filesystem trust model.

**Open P2, overlapping administrators:** two requests that pass the initial audit lookup can both append `workspace.orphan.clear` for the **same name and digest**, with different reasons and planned evidence filenames, even though one evidence object survives. Passing unsafe demonstration plus expected-failure exactly-one oracle on both local OS. Recommend a serialized audit CAS keyed by canonical orphan identity and an idempotent terminal custody transition. This is an audit correctness problem, **not** evidence of two effective document commits.

**P0 release stop on Phase 1 backport:** exact `phase1-backport-8-9-10` `36d1957` still uses the token-check-to-`Path.unlink(writer.lock)` race. [CI 41393842114](https://github.com/nrobertson152/RevisionWorx/actions/runs/41393842114) independently reproduced the former owner deleting the successor's lock on **Ubuntu and Windows**. One safety assertion expected failure, one passing explicitly unsafe demonstration in each OS. Reported to Claude via [PR #6](https://github.com/nrobertson152/RevisionWorx/pull/6#issuecomment-6569794832). **Do not fast-forward that frozen candidate**; Claude owns correction and QA acceptance.

**External approval/release gates unchanged:** local Windows process tests do not certify actual two-host SMB/NAS consistency or power-cut durability (**not executed**). No real Windows service/AD administrator authentication, nonrollbackable epoch trust root, backed-up journal reconciliation, or production coordinator/SMB write-path exists. Ordinary SQLite + file-witness simultaneous restore still resurrects old authority. No merge, release, retarget, force-push or other repository modifications authorized.
