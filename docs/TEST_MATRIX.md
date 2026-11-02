# RevisionWorx — Risk-based QA acceptance matrix

## Current independently verified Phase 2 acceptance — 2026-11-02 ET

**Exact source:** Claude Phase 2 `366d4355abcde7543122551fadaab01ac9832e01` + independent QA tests `77aba36b72f50890e2df753df86aebeae6f395e5`. [GitHub Actions run 41392876919](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392876919): **all four integrated application/test jobs SUCCESS** on Ubuntu and Windows × Python 3.11/3.13, with an identical pinned Phase 2 SHA in each job.

| Runner, each Python version | QA-only | Full Phase 2 |
| --- | --- | --- |
| Linux Python 3.11 / 3.13 | **56 passed, 3 skipped, 1 strict xfailed** | **251 passed, 3 skipped, 1 strict xfailed** |
| Windows Python 3.11 / 3.13 | **59 passed, 1 strict xfailed** | **253 passed, 1 skipped, 1 strict xfailed** |

**ACCEPT (local filesystem, exact code):** ADR-019 automatic fail-closed malformed orphan and crash-resumable, audit-first supervised orphan clearance (original evidence preserved, one durable audit, exact SHA-256 checked on restart and rejection of changed evidence); ADR-021 v2 complete ordered request digest, correct `TxidReuseError` for altered/removed/reordered/interleaved `RecordCheck`, identical lost-ACK retry after restart, explicit `IdentityUnverifiable` for pre-v2 txids, non-finite payload refusal; transaction outcome after prepublication and postpublication faults, GC audit-before-delete with old referenced/young content protected, revision-chain cycle rejection before new revision, and bounded verified SHA-256 reads. **New independent test paths:** `tests/qa/test_phase2_recovery_boundaries.py` and `tests/qa/test_txid_check_identity.py`.

**Historical regression evidence:** [run 41392345698](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392345698), Phase 2 `2a280482` + QA `47c5c0bb`, showed two separate strict expected failures: administrator clear moved evidence before audit, and reordered check/write interleaving reused txid. Both now **PASS normally** at `366d4355`/`77aba36b`. Research confirmed the old malformed-orphan dual-writer bug at `d656884b` before the P0 correction. These historical results must not be described as defects in the current SHA.

**One open strict XFAIL:** `tests/qa/test_integrity.py::test_paused_writer_break_after_lock_check_must_not_publish` asks for physical absence of a late journal entry. A physically published but **logically fenced** artifact is permitted by the accepted authority model; physical nonpublication is not certified. No effective stale mutation was proven in the current tested local code.

**Remaining product/release gates:** manual operator must independently prove an unknowable orphan owner stopped; approved optional multi-user coordinator, authenticated recovery, real two-host SMB/uncertain-ACK and power-loss durability, backup/restore trust root, interactive installed Windows/Linux GUI, offline deployment and upgrades/rollback **NOT CERTIFIED**. QA PR #3 is independently reviewable; Phase 2 local integrity acceptance is **not** authorization to merge/release or certify multi-user use. The isolated Phase 1 reliability backport `phase1-backport-8-9-10` is still **`36d1957` and obsolete** (pre-ADR-019); **HOLD** until Claude updates it with applicable accepted corrections and asks QA to retest exact new SHA.

All historical 'latest' headings below are archival checkpoints for older sources and **are superseded by this section**.


## Latest P0/P1 independent acceptance — 2026-11-01 ET

**Verified current source:** Phase 2 `2a2804820d4aca48b50026bdb7edfe8c241b1866` + QA `17ac2374b3234c495608300a6b0e9309b44d7143`, [GitHub Actions run 41391301343](https://github.com/nrobertson152/RevisionWorx/actions/runs/41391301343). All four integrated jobs SUCCESS on Linux/Windows Python 3.11/3.13.

Linux each Python: QA **46 passed / 3 skipped / 1 strict xfailed**, full **230 passed / 3 skipped / 1 strict xfailed**. Windows each Python: QA **49 passed / 1 strict xfailed**, full **232 passed / 1 skipped / 1 strict xfailed**.

**P0 #10 accepted locally:** unreadable or tokenless `.releasing-*` orphan refuses C authority while B may have preflighted; evidence preserved. Test: `test_malformed_release_orphan_must_not_allow_two_effective_writers`. **P1 ADR-017 accepted locally:** unchanged txid/checks replay after restart returns same seq; changed, missing or reordered RecordChecks raise TxidReuseError without a new commit. Test: `tests/qa/test_txid_check_identity.py`.

**Historical:** pre-fix `d656884b` allowed the tokenless orphan double-effective-write counterexample (research [run 41390605906](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390605906)) and omitted checks in txid identity (QA [run 41390455004](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390455004), three strict xfails). Corrected-source test markers now PASS normally; legacy source retains feature-gated expected failures.

**Unresolved:** One strict XFAIL requires no *physical* late journal file, although the file is logically fenced. Legacy journal entries lack historical RecordCheck request identity and need an explicit versioned migration/retry decision; manual clear-orphan requires confirming the unknown former writer stopped. Native installed desktop, backup/restore, coordinator authentication and real two-host SMB remain NOT EXECUTED.


## New independent P1 acceptance finding — ADR-017 RecordCheck replay identity (2026-11-01 ET)

**Actual Phase 2** `d656884b39401134149273dd0121ef620b76eb8e` with **QA overlay** `ee0b6978fe0a5459932e27b45e177fa4e8841f6d`. [GitHub Actions run 41390455004](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390455004), all four disposable Phase 2 jobs **SUCCESS** on Linux/Windows × Python 3.11/3.13. Each Linux QA suite: **42 passed, 3 skipped, 4 strict xfailed**; full **224 passed, 3 skipped, 4 strict xfailed**. Each Windows QA suite: **45 passed, 4 strict xfailed**; full **226 passed, 1 skipped, 4 strict xfailed**. One of the four xfails is the historically retained physical nonpublication condition, three are **new P1 txid identity defects**. All counts are verified from actual job logs, not estimates.

`tests/qa/test_txid_check_identity.py` runs a committed, two-guard `RecordCheck` + `RecordPut` request with an explicit `txid`. An **identical** replay after closing/reopening the workspace passes and creates no journal entry. Three altered replays with the *same txid and writes*, but (1) a different checked revision, (2) a removed check, or (3) reordered checks, all **strict XFAIL**: `Workspace.commit` returns the previous successful sequence rather than raising `TxidReuseError`. The current journal stores writes, not full check-identity metadata. This is an incorrect idempotency acknowledgment, **not** evidence of a second write or data loss. The desired full submitted-operation-list identity must be confirmed by Claude and persisted for restart/recovery and future coordinator compatibility. The corrected QA fixture uses the actual journal revision `_rev`, not an assumed numeric revision; initial QA fixture failure [run 41390357245](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390357245) was a test error and is superseded by passing execution at `ee0b6978`.

This new P1 item requires corrected-production exact-SHA retest before acceptance. The existing #8/#9/#10 local safety results remain valid for the unchanged production SHA. It does **not** affect SMB/installed-GUI certification status.


**QA Engineer • latest independently verified candidate: Phase 2 `d656884b39401134149273dd0121ef620b76eb8e` + QA `1def701cd06e5e373ab37ca1d1d250cf13c55821`**

This is the **current acceptance state**, not a historical run list. **Current exact evidence:** [full Linux/Windows × Python 3.11/3.13 run 41389199207](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199207), and [focused ADR-019 + five-trial Windows stress run 41389199197](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199197), both with Phase 2 `d656884b` + QA `1def701c`. Earlier `41387311002`/`41387311010` runs are historical. Preserve pre-fix failure evidence in `docs/agents/QA_PROGRESS.md` and issues #8/#9/#10.


## Latest independently verified ADR-019 acceptance (2026-11-01 ET)

**Current test target:** Claude Phase 2 `d656884b39401134149273dd0121ef620b76eb8e` + independent QA `1def701cd06e5e373ab37ca1d1d250cf13c55821`. [Full Linux/Windows Python 3.11/3.13 CI](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199207) succeeded with one pinned source SHA across all four jobs; [focused acceptance + Windows multi-process stress](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199197) also succeeded.

| Runner | QA-only | Complete Phase 2 |
| --- | --- | --- |
| Linux Python 3.11 and 3.13, each | **41 passed, 3 skipped, 1 strict xfailed** | **223 passed, 3 skipped, 1 strict xfailed** |
| Windows Python 3.11 and 3.13, each | **44 passed, 1 strict xfailed** | **225 passed, 1 skipped, 1 strict xfailed** |

Focused Linux Python3.13 `tests/qa/test_integrity.py`: **20 passed, 3 skipped, 1 xfailed**; focused Windows: **23 passed, 1 xfailed**. Windows Server 2025 Python3.13.17 ran **5/5 six-process checkout trials, 30 worker attempts**; each produced one winner and five conflicts, all exits zero, no orphaned lock and no journal verification problems.

Two independently added ADR-019 acceptance tests **passed** on both OSes: a revoked releaser A cannot remove successor B's lock or prevent B's valid commit; and after A crashes having moved B's lock into a `.releasing-*` file, new C must journal-revoke B before publishing, preserve the evidence, refuse B's stale commit and clear the abandoned private file. The previously added Windows transient, persistent and reassigned-token tests now inject at ADR-019's **actual atomic move**, not a superseded `Path.unlink`. Commits: `06e7d8ea9f90918aca5893bb646aab8d9dd24eae` (failpoint compatibility), `1def701cd06e5e373ab37ca1d1d250cf13c55821` (two independent new regressions).

**Remaining acceptance gap:** one strict xfail tests *physical absence of a late journal file*; ADR-015 can instead retain a **logically fenced, ineffective** late artifact. This xfail is not physical-nonpublication success, and passing logical fences are not evidence of physical SMB correctness. Physical two-host Windows coordinator/SMB, installed native Windows/Linux desktop builds, backup/restore epochs and authenticated coordinator remain **NOT VERIFIED**. This is QA review evidence, not merge/release authorization.

**Status rules:** PASS LOCAL = executed and accepted under the cited runner; XFAIL PHYSICAL = a stronger physical condition deliberately remains unmet, not a passing requirement; PLANNED = no executable acceptance of that product feature; NOT CERTIFIED = certification hardware/environment not exercised. Never count an xfail or skip as a passing safety requirement.

| ID | Priority | Acceptance invariant | Current evidence and status |
| --- | --- | --- | --- |
| ADOPT-01 | P0 | Customer/year/product archive registration never rewrites, moves or renames unmanaged content | **PASS LOCAL**, fixture hashes/mtime/path and application integration |
| ADOPT-02 | P0 | Adoption preview and initial baseline never claim invented revision history | **PASS LOCAL**, adoption tests |
| START-01 | P0 | Opening a workspace does not enumerate unmanaged archives | **PASS LOCAL**, instrumented scan negative test |
| START-02 | P0 | 50,000 unmanaged folders do not trigger initial traversal | **PASS LOCAL**, developer full suite instrumented test, independently rerun in exact-head suite |
| JOURNAL-01 | P0 | Disk full before publication cannot create a committed revision | **PASS LOCAL**, QA failpoint |
| JOURNAL-02 | P0 | Failure after journal publication remains readable and recoverable; ambiguous txid is resolvable | **PASS LOCAL**, QA and application tests |
| JOURNAL-03 | P0 | Journal gap or checksum alteration fails closed | **PASS LOCAL**, QA and application tests |
| LOCK-01 | P0 | A separate process cannot acquire an active writer lock, then can acquire following clean release | **PASS LOCAL** Linux/Windows interpreter isolation |
| LOCK-02 | P0 | Authorized administrative revocation is audited and stale commits have no effective data mutation | **PASS LOCAL** #8 journal-first acceptance; strong physical no-file oracle remains **XFAIL PHYSICAL** |
| LOCK-03 | P0 | Pre-audit/lock-move race does not authorize a stale publication | **PASS LOCAL** #8 `test_writer_revoked_before_admin_audit_cannot_take_effect` |
| LOCK-04 | P0 | Post-audit fenced journal artifacts remain logically invisible to reader, cache, recovery and txid outcome | **PASS LOCAL** independent `test_revoked_writer_publication_is_logically_fenced_on_replay`; physical artifact may exist |
| LOCK-05 | P0 | Audit revocation survives crash before physical lock move; successor reacquires and old writer is refused | **PASS LOCAL** explicit crash/reacquire injection in current candidate |
| INDEX-01 | P0 | Rebuild from corrupted/damaged cache repairs disposable index, never authoritative records | **PASS LOCAL**, application tests |
| INDEX-02 | P0 | Record inserted after sampled journal cursor cannot appear with an older cursor | **PASS LOCAL** #9 deterministic index snapshot regression |
| INDEX-03 | P0 | Record deleted after sampled journal cursor cannot disappear with an older cursor | **PASS LOCAL** #9 deterministic index snapshot regression |
| INDEX-04 | P0 | Cache rebuild with permanently unstable snapshots must fail closed without altering previous cache cursor/rows | **PASS LOCAL** deterministic retry-exhaustion injection, actual continuous-writer soak not qualified |
| CHECKOUT-01 | P0 | Exact one winner in real local six-process checkout; others are conflicts; no orphan lock | **PASS LOCAL Windows Server 2025: 5/5 trials, 30 process attempts, 1 winner + 5 conflicts per trial, all exits zero** |
| CHECKOUT-02 | P0 | Stale checkout tokens rejected and checkout history unaltered | **PASS LOCAL**, independent Phase 2 suite |
| TXID-01 | P1 | Same txid replay identical across restart, altered/missing/reordered checks and interleaved operations rejected; legacy txid identity not guessed | **PASS LOCAL**, ADR-021 v2 at `366d4355` + QA `77aba36b` [run 41392876919](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392876919); historical strict xfail on `2a28048` corrected |
| WINDOWS-LOCK-01 | P0 | Transient Windows writer.lock sharing violation gets bounded retry, verifies owner, removes own lock | **PASS LOCAL Windows**, #10 injected failure and successor acquisition |
| WINDOWS-LOCK-02 | P0 | Persistent Windows lock release denial is reported and rightful lock remains held | **PASS LOCAL Windows**, bounded retry exhaustion and exclusive successor refusal |
| WINDOWS-LOCK-03 | P0 | Writer must not unlink successor token after sharing violation and ownership reassignment | **PASS LOCAL Windows**, injection at ADR-019's actual atomic move |
| LOCK-ADR019-01 | P0 | Old releaser A cannot delete successor B lock following administrative override | **PASS LOCAL Linux/Windows**, deterministic independent release-interleaving, B remains able to commit |
| ORPHAN-CLEAR-01 | P0 | Administrator clear audit must be durable before evidence move; crash, restart and tampered evidence fail closed; retry exactly once | **PASS LOCAL**, `tests/qa/test_phase2_recovery_boundaries.py`, Phase 2 `366d4355` + QA `77aba36b` [run 41392876919](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392876919); operator stopped-writer assertion not mechanically verifiable |
| GC-01 | P0 | Failed GC audit publication preserves old orphan; completed GC emits durable audit before deletion; young and old referenced objects survive | **PASS LOCAL** independent `test_content_gc_audit_is_published_before_delete_and_precommit_failure_preserves_data`, same exact CI |
| REV-CHAIN-01 | P0 | Corrupt cyclic revision chain rejected before history/check-in; no new revision published | **PASS LOCAL** independent cyclic-chain fault injection, same exact CI |
| READ-VERIFIED-01 | P0 | Immutable revision reads enforce max_bytes, expected size, SHA-256 despite mutable working edits | **PASS LOCAL** independent verified-read boundary/corruption test, same exact CI |
| TX-OUTCOME-01 | P0 | Prepublication OSError -> not-found; postpublication materialization failure -> committed and recoverable, retry idempotent | **PASS LOCAL** independent journal failpoint test, same exact CI |
| LOCK-ORPHAN-01 | P0 | Malformed `.releasing-*` file cannot grant C authority while preflighted B remains effective; retain evidence | **PASS LOCAL** at Phase 2 `2a280482` + QA `17ac2374`, Linux/Windows Python 3.11/3.13 [run 41391301343](https://github.com/nrobertson152/RevisionWorx/actions/runs/41391301343); manual recovery and SMB unqualified |
| LOCK-ADR019-02 | P0 | Crash-orphaned successor token must be fenced before new C can commit | **PASS LOCAL Linux/Windows**, single audit revocation, preserved evidence, no remaining .releasing file |
| REV-01 | P0 | Historical revision SHA-256 and content immutable after check-in | **PASS LOCAL**, dedicated Phase 2 integrity tests |
| REV-02 | P0 | Release supersedes prior release without rewriting old history; corrupt blob refuses release | **PASS LOCAL**, dedicated Phase 2 integrity tests |
| REV-03 | P0 | Restore prior revision creates a NEW revision C, retaining revisions A/B and preserving native CAD references | **PASS LOCAL**, dedicated history-restore test |
| EDIT-01 | P0 | Explicit forced-hash verification detects unauthorized same-size/same-mtime edits | **PASS LOCAL**; metadata-only fast path cannot guarantee adversarial detection |
| CAD-01 | P0 | Registration/check-in never moves or silently rewrites unrelated assembly files | **PASS LOCAL** on synthetic CAD content and directory fingerprint; real Inventor/SolidWorks resolver integration **NOT CERTIFIED** |
| CLI-01 | P0 | Corrupt revision verify exits nonzero; revision export cannot overwrite existing path | **PASS LOCAL**, real subprocess executable |
| GUI-01 | P1 | Real PySide6 Documents tab filtering, unauthorized-edit warning, checkout-conflict error, blank message rejection | **PASS OFFSCREEN** Linux/Windows; actual installed desktop UI **NOT CERTIFIED** |
| OSCH-01 | P1 | Verified content-based OpenSchematix recognition, not extension-only assertions | **PASS LOCAL** recognition tests; BOM and part-reference adapter integration not approved |
| BACKUP-01 | P0 | Metadata and immutable blobs restored from one consistent point, with safe authority epoch | **PLANNED**; restore trust and authoritative high-water design needed |
| BOM-01 | P0 | Engineering BOM quantities, revisions and provenance are accurate | **PLANNED** |
| GRAPH-01 | P0 | Where-used direct, indirect, cyclical, cross-project and version-specific links disclose coverage | **PLANNED** |
| IMPACT-01 | P0 | Change impact does not imply compatibility without verified dependencies | **PLANNED** |
| PART-01 | P0 | Configurable numbering reserves/collisions and stable part identity | **PLANNED** |
| SCALE-01 | P1 | 10k projects, 2m documents, 500k edges: actual application p95/RSS target | **NOT MEASURED**; synthetic dataset generation is not an application benchmark |
| WINDOWS-GUI-01 | P0 | Clean installed Windows release launches and completes register/checkout/checkin/UI recovery | **NOT CERTIFIED**; no packaged installer/manual visual desktop run |
| SMB-01 | P0 | Windows coordinator and two independent SMB clients preserve atomic revocation/commit on disconnect, restart, contention | **NOT CERTIFIED**; ADR-014 coordinator and physical share rig required |
| NFS-01 | P1 | Supported Linux/NFS share semantics, if approved | **NOT CERTIFIED**, later target |

## Latest extended independent acceptance — 2026-11-01 ET

**Pinned source SHAs:** Claude Phase 2 `d202b28305f765b4edead3dede211dd9e7a891a6` and QA `f5b8a13064d8a482fa9492da73da333cf6615a15`. The newer candidate incorporates the previously accepted #8/#9/#10 corrections and was tested with four additional negative-path assertions.

| Evidence | Linux | Windows |
| --- | --- | --- |
| [Full four-platform Phase 2 suite](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185333), Python 3.11 and 3.13 | **215 passed, 3 skipped, 1 strict xfailed** per Python version | **217 passed, 1 skipped, 1 strict xfailed** per Python version |
| [Focused acceptance](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185328), Python 3.13.17 | **18 passed, 3 skipped, 1 strict xfailed** | **21 passed, 1 strict xfailed** |
| [Five-trial Windows checkout stress](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185328) | Not run on Linux in this job | **5/5 trials passed**; 30 worker attempts; each exactly one `won` and five `conflict`; all exit codes 0, no orphan lock or journal errors |

Newly **PASSING** independent QA tests in `tests/qa/test_integrity.py`: `test_journal_first_revocation_survives_admin_crash_before_lock_move` (#8); `test_index_rebuild_fails_closed_if_snapshot_never_stabilizes` (#9); `test_windows_persistent_lock_unlink_denial_is_reported_and_retained` and `test_windows_lock_release_does_not_unlink_reassigned_token` (#10). The sole strict expected failure tests a **physical late journal artifact**: it is not a proven effective stale mutation because logical fencing and recovery pass. Skip counts: Linux three Windows-only lock tests; Windows one case-sensitive-filesystem-only full-suite test.

**Director handoff:** QA-only PR #3 is ready for code review independently of product readiness. Claude controls defect disposition and core implementation. Standalone release still requires installed desktop GUI/packaging and real backup-recovery qualification; approved multi-user Windows coordinator + SMB remains unimplemented/unqualified. No merge/release authorization or physical SMB/NFS certification follows from this evidence.

## Five separately retained regression oracles

These remain present on `tests/qa/test_integrity.py`, not folded into a single green check:

1. `test_paused_writer_break_after_lock_check_must_not_publish`: **strict XFAIL** for physical post-revocation publication, even though ADR-015 rev.2 fences the artifact's logical effect.
2. `test_writer_revoked_before_admin_audit_cannot_take_effect`: **PASS** on the corrected journal-first revocation; old implementation remains version-gated strict xfail.
3. `test_index_rebuild_does_not_include_records_newer_than_its_watermark`: **PASS** on current bounded stable snapshot; older implementation remains version-gated strict xfail.
4. `test_index_rebuild_does_not_omit_pre_watermark_records_due_to_late_delete`: **PASS** on current bounded stable snapshot; older implementation remains version-gated strict xfail.
5. `test_windows_writer_lock_close_survives_transient_sharing_violation`: **PASS** on current Windows release retry; old implementation remains version-gated strict xfail.

**Complementary positive oracle:** `test_revoked_writer_publication_is_logically_fenced_on_replay` checks replay/index/reader and `transaction_outcome=fenced`. The physical xfail must not be presented as an effective unauthorized mutation.

## Exact execution and release gates

The main QA PR is based on Phase 1. Phase 2 tests are executed using a separate disposable checkout with an exact recorded Phase 2 SHA and QA test overlay, **not** a merge into another engineer's branch. `python -m pytest -q -ra tests/qa` and `python -m pytest -q -ra` on the current test revisions reported:

- Linux Python 3.11/3.13: QA **37 passed, 1 skipped, 1 strict xfailed**; full **211 passed, 1 skipped, 1 strict xfailed**.
- Windows Python 3.11/3.13: QA **38 passed, 1 strict xfailed**; full **211 passed, 1 skipped, 1 strict xfailed**.
- Focused Windows stress: `python tests/qa/windows_checkout_stress.py --trials 5 --workers 6 --timeout 100` → **5 successes, 0 failures**, 30 actor attempts, zero orphan lock and clean verification.

The **single-user desktop application is not automatically release-ready** solely from test results: the installed native GUI, packaging/upgrade paths and backup recovery remain unqualified. **Multi-user Windows/SMB is not certified** by any local test; it requires an integrated coordinator, authenticated authority, restore fencing and real separate-host testing. The Director approves any merge or release.

See [the QA merge-readiness handoff](PR_MERGE_READINESS.md) for exact tested SHAs, ownership and retest steps.
