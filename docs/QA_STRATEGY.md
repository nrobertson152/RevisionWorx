# RevisionWorx QA strategy

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



**Status note:** This is a living QA strategy. The **ADR-019 checkpoint immediately below** is authoritative for the exact Phase 2/QA revisions named there; the older run records and historical unresolved-defect narratives later in this document describe superseded implementations. The remaining strict xfail concerns physical presence of a *logically fenced* late journal artifact, not a proven effective stale mutation. Installed desktop, real SMB and restore/backup release gates remain unverified.

## Latest independently verified ADR-019 acceptance (2026-11-01 ET)

**Current test target:** Claude Phase 2 `d656884b39401134149273dd0121ef620b76eb8e` + independent QA `1def701cd06e5e373ab37ca1d1d250cf13c55821`. [Full Linux/Windows Python 3.11/3.13 CI](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199207) succeeded with one pinned source SHA across all four jobs; [focused acceptance + Windows multi-process stress](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199197) also succeeded.

| Runner | QA-only | Complete Phase 2 |
| --- | --- | --- |
| Linux Python 3.11 and 3.13, each | **41 passed, 3 skipped, 1 strict xfailed** | **223 passed, 3 skipped, 1 strict xfailed** |
| Windows Python 3.11 and 3.13, each | **44 passed, 1 strict xfailed** | **225 passed, 1 skipped, 1 strict xfailed** |

Focused Linux Python3.13 `tests/qa/test_integrity.py`: **20 passed, 3 skipped, 1 xfailed**; focused Windows: **23 passed, 1 xfailed**. Windows Server 2025 Python3.13.17 ran **5/5 six-process checkout trials, 30 worker attempts**; each produced one winner and five conflicts, all exits zero, no orphaned lock and no journal verification problems.

Two independently added ADR-019 acceptance tests **passed** on both OSes: a revoked releaser A cannot remove successor B's lock or prevent B's valid commit; and after A crashes having moved B's lock into a `.releasing-*` file, new C must journal-revoke B before publishing, preserve the evidence, refuse B's stale commit and clear the abandoned private file. The previously added Windows transient, persistent and reassigned-token tests now inject at ADR-019's **actual atomic move**, not a superseded `Path.unlink`. Commits: `06e7d8ea9f90918aca5893bb646aab8d9dd24eae` (failpoint compatibility), `1def701cd06e5e373ab37ca1d1d250cf13c55821` (two independent new regressions).

**Remaining acceptance gap:** one strict xfail tests *physical absence of a late journal file*; ADR-015 can instead retain a **logically fenced, ineffective** late artifact. This xfail is not physical-nonpublication success, and passing logical fences are not evidence of physical SMB correctness. Physical two-host Windows coordinator/SMB, installed native Windows/Linux desktop builds, backup/restore epochs and authenticated coordinator remain **NOT VERIFIED**. This is QA review evidence, not merge/release authorization.



**Current extended independently verified checkpoint:** Phase 2 `d202b28305f765b4edead3dede211dd9e7a891a6` + QA `f5b8a13064d8a482fa9492da73da333cf6615a15`. [Full matrix](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185333) passed on Linux and Windows Python 3.11/3.13: each Linux full suite **215 passed, 3 skipped, 1 xfailed**, each Windows full suite **217 passed, 1 skipped, 1 xfailed**. [Focused tests and real Windows process stress](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185328): Linux **18 passed/3 skipped/1 xfailed**, Windows **21 passed/1 xfailed**, and **5/5 six-process checkout trials** (30 attempts). New independent tests verify admin crash after journal revocation, bounded index retry fail-closed, persistent Windows lock-release failure, and ownership change during retry. The strict xfail concerns physical journal file existence only; effective mutation remains fenced. The latest matrix and merge-readiness handoff supersede prior f3c checkpoint status. Native installed GUI and physical Windows coordinator/SMB remain unqualified.


QA development branch: `qa/testing-infrastructure` (draft PR #3 currently based on `phase1-foundation`).
Review targets: `phase1-foundation` (PR #6) and stacked `phase2-documents` (PR #7).\nThe Lead Architect owns integration and protocol choices; QA independently evaluates both.

## Integrity requirements

Preserve unmanaged files during adoption; refuse conflicting checkout authorities, unpublished revisions, and journal corruption; distinguish authoritative `.revisionworx` journal from disposable local SQLite caches. Never claim unmanaged archives were indexed. Fail closed when write authority is ambiguous.

## Implemented code and independent execution status

Phase 1 (PR #6) now uses Python >=3.10, a PySide6 GUI, journal-first `Workspace`, single-writer lock checks, transaction outcome IDs, journal checksums, verify/repair, adoption, and disposable SQLite. Phase 2 (PR #7) adds document registry, immutable content snapshots and revision records, lifecycle, checkout/check-in, CLI and a Qt Documents tab. Parts, dependency graph, BOM, native CAD parsing, backups and production-safe SMB/NFS multi-writer operation remain unimplemented or unverified.

**Current independently executed application evidence supersedes the early architect-only baselines below.** GitHub Actions [run 41387311002](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311002) exercised *the same exact source revisions* (Phase 2 `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6`, QA `454391dfd6bd66059b97631701f6e5722b5e862e`) on Linux and Windows with Python 3.11/3.13. All four complete Phase 2 suites returned **211 passed, 1 skipped, 1 strict xfailed**. That xfail is specifically the *physical* late-publication oracle; logical journal-order fencing is independently passing. The QA-only commands returned Linux **37 passed / 1 skipped / 1 strict xfailed** and Windows **38 passed / 1 strict xfailed** on each Python version.

Focused [run 41387311010](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311010) exercised the three Claude fixes: #8 effective journal-first administrative revocation, #9 cache cursor snapshot consistency for insert/delete, and #10 bounded Windows writer-lock retry. **All acceptance invariants for the candidate implementation passed on Linux/Windows Python 3.13**, except the deliberately stronger physical-no-file xfail. Windows Server 2025 Python 3.13.17 also executed **five real six-process checkout trials (30 worker attempts)** with exactly 1 successful checkout and 5 conflicts in every trial, no worker errors, orphan locks or journal-verification problems. These are local runner filesystem outcomes, **not multi-host SMB certification**.

QA-only Phase 1/PR integration runs also exercise actual PySide6 widgets offscreen, including the Documents tab on the disposable Phase 2 checkout. No Qt *stub* is substituted. Installed Windows/Linux desktop packaging, actual CAD application interoperability, power-loss recovery and physical SMB/NFS remain unqualified. Synthetic dataset generation is not a query benchmark.

The CI workflow was corrected to avoid duplicate push/PR matrix work and to resolve a **single immutable Phase 2 SHA** before executing its four OS/Python matrix jobs. [Run 41387618838](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387618838) independently verified this pinned matrix on Phase 2 `f3c54386` and QA `b0bafe57`; all four Phase 2 full suites returned **211 passed/1 skipped/1 strict xfailed**. Documentation-only pushes no longer launch a second Phase 2 matrix.

For current bug-level acceptance paths, owners, exact SHAs and release prerequisites see [PR_MERGE_READINESS.md](PR_MERGE_READINESS.md) and [TEST_MATRIX.md](TEST_MATRIX.md). Historical failures and early 'unexecuted' notes in `docs/agents/QA_PROGRESS.md` remain as chronology, **not current status**.

## Testing layers

Unit: path validation, hashing, serialization, later graph and numbering. Integration: adoption, journal replay, local indexes and crashes. Adversarial: ENOSPC, EACCES, missing parents, journal gaps, abandoned temp files, external changes. Multiprocess: barriers and authoritative winner count. Network: real SMB/NFS shares from separate hosts, fault injection, reconnect, server restart, stale cache, fencing. Platform: Linux and Windows, Python 3.11/3.13, PySide6 offscreen GUI launch plus Windows desktop smoke; do not treat skipped Qt tests as coverage. Benchmark: 10,000 projects, 2 million docs, 500,000 dependencies; separate database generation from real application timing.

## P0 release blockers

Unmanaged file mutation; false revision commit; lost immutable release; conflicting checkout; stale writer after administrative lock recovery; corrupted authoritative metadata silently accepted; inconsistent backup restoration; false completeness claims. All block release until empirically resolved. A missing API is NOT RUN, not PASS.

## Immediate executable tests

Use `tests/qa` for independent synthetic workspaces (customer/year/product layouts), byte/mtime/path snapshots, scoped adoption, startup non-traversal, injected journal publication failure and replay. Run `python -m pytest -q tests/qa` then `python -m pytest -q`. Record OS, commit, Python, filesystem, counts and skips.

## Historical pre-fix safety findings (superseded for current Phase 2)

Earlier ADR-008/ADR-015 implementations allowed a stale effective write after a lock break. Those pre-fix reproducers are recorded in issues #8 and #9 and historical CI logs, but do **not** describe the accepted current standalone Phase 2 code (`d656884b`). Current journal-first revocation (ADR-015 rev. 2), stable index snapshots (#9) and ADR-019 atomic move-then-verify lock release (#10) were independently accepted by [full run 41389199207](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199207) and [focused run 41389199197](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199197). A physical stale journal file may still be published but is fenced from all authoritative logical effects; the physical nonpublication test remains strict XFAIL. Standalone correctness under local OS filesystems does not certify multi-user SMB, serverless share authority, or backup-restore fencing. The Director approved optional on-premises coordinator design in ADR-014; it is not implemented or physically qualified.


## Dependency verification

Create directed/cyclic, direct/indirect, revision-specific, unverified and cross-project edges. Where-Used must show exact known downstream relationships and unmanaged coverage warnings. Before You Revise must not classify all affected assemblies as incompatible. Native CAD filenames alone never prove reference extraction. OpenSchematix current fixtures prove format recognition only.

No test, feature, platform, performance result or release may be declared verified without an actual execution record.

## Independent CI execution record (2026-11-01)

Original QA branch at `304500f`: [GitHub Actions run 41371072088](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371072088), SUCCESS on Ubuntu and Windows with Python 3.11/3.13; QA-only 16 passed/2 skipped and full suite 111 passed/3 skipped in each runner. Both skips in QA cover version-gated tests, not passing Phase 2 or fencing.

GitHub PR merge check of QA atop updated Phase 1, [run 41371072451](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371072451): Windows Python 3.13 QA 16 passed/1 skipped/1 xfailed, full suite 136 passed/2 skipped/1 xfailed. Linux 3.11/3.13 QA 16 passed/1 skipped/1 xfailed, but full suite aborted during Qt import due absent `libEGL.so.1`. Qt runner packages were added in `d3a4378`; rerun results not yet verified. Phase 2-including QA integration execution (eight tests) is queued and must not be counted as passing until its job logs are reviewed.

## Verified Phase 2 stacked test execution (2026-11-01)

The disposable CI integration job on [run 41371698368](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371698368) checked out Claude's Phase 2 commit `77419e34a35a27d29dd9eab3bce858a1ad93871b` and QA commit `2905632fe9b84e97bf121edf94a45f2bb44f5aa3` independently, then copied only `tests/qa` into the temporary Phase 2 test checkout. Linux Python 3.13 results: QA **24 passed, 1 strict xfailed**, full **179 passed, 1 strict xfailed**; no skips. All eight Phase 2-specific integrity tests passed. The known stale-writer TOCTOU remains a real safety defect independently corroborated by the Distributed Systems Engineer in issue #8. The newly added two Controller-backed view-model regressions were committed after that run and await their own CI results. The latest QA/Phase 1 PR run `41371702767` succeeded across Ubuntu/Windows and Python 3.11/3.13 with Qt enabled; that is not a Phase 2-on-Windows test or a production multi-host certification.

## Current QA verification and defect tracking

- The latest verified complete Linux/Windows Phase 2 overlay before the most recent negative-path cases is [run 41381884699](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381884699), Phase 2 SHA `47d81658764cd6bde60e391ac9aac24e722317e8`, QA SHA `d72b7ba25ccbf5162e4db6df13914fbf5faa373e`: Linux QA 32 passed/2 xfailed and full 189 passed/2 xfailed; Windows QA 32 passed/2 xfailed and full 188 passed/1 skipped/2 xfailed. The two failures are strict xfails for physical late publication (#8) and inconsistent index watermark (#9). New test commits require fresh CI evidence.
- Actual offscreen Qt widget tests exercise the Documents tab under PySide6 on both Linux and Windows, including registration/filtering, external-edit warning and blank check-in protection. Additional conflict-dialog regression is added and awaiting exact-current-run verification. This is **real Qt**, but not a packaged Windows application or a live human desktop session.
- `tests/qa/test_integrity.py::test_revoked_writer_publication_is_logically_fenced_on_replay` verifies ADR-015 logical fencing of a post-audit stale entry and local SQLite indexing/replay. It does not imply that a previously renamed lock cannot be exploited before the audit (issue #8), or that SMB enforces any local filesystem operation semantics.
- [Issue #9](https://github.com/nrobertson152/RevisionWorx/issues/9) separately tracks the deterministic LocalIndex snapshot/watermark mismatch. Claude owns the corrective production change; QA owns the acceptance regressions.
