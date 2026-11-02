# RevisionWorx — QA acceptance and PR merge-readiness handoff

**QA Engineer • 2026-11-01 ET • Evidence-based advisory, NOT merge or release approval**

**Current extended implementation under test:** `d202b28305f765b4edead3dede211dd9e7a891a6`; independent QA overlay `f5b8a13064d8a482fa9492da73da333cf6615a15`. Historical `f3c54386` evidence below remains valid for that prior revision.

## Latest extended independent acceptance — 2026-11-01 ET

**Pinned source SHAs:** Claude Phase 2 `d202b28305f765b4edead3dede211dd9e7a891a6` and QA `f5b8a13064d8a482fa9492da73da333cf6615a15`. The newer candidate incorporates the previously accepted #8/#9/#10 corrections and was tested with four additional negative-path assertions.

| Evidence | Linux | Windows |
| --- | --- | --- |
| [Full four-platform Phase 2 suite](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185333), Python 3.11 and 3.13 | **215 passed, 3 skipped, 1 strict xfailed** per Python version | **217 passed, 1 skipped, 1 strict xfailed** per Python version |
| [Focused acceptance](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185328), Python 3.13.17 | **18 passed, 3 skipped, 1 strict xfailed** | **21 passed, 1 strict xfailed** |
| [Five-trial Windows checkout stress](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388185328) | Not run on Linux in this job | **5/5 trials passed**; 30 worker attempts; each exactly one `won` and five `conflict`; all exit codes 0, no orphan lock or journal errors |

Newly **PASSING** independent QA tests in `tests/qa/test_integrity.py`: `test_journal_first_revocation_survives_admin_crash_before_lock_move` (#8); `test_index_rebuild_fails_closed_if_snapshot_never_stabilizes` (#9); `test_windows_persistent_lock_unlink_denial_is_reported_and_retained` and `test_windows_lock_release_does_not_unlink_reassigned_token` (#10). The sole strict expected failure tests a **physical late journal artifact**: it is not a proven effective stale mutation because logical fencing and recovery pass. Skip counts: Linux three Windows-only lock tests; Windows one case-sensitive-filesystem-only full-suite test.

**Director handoff:** QA-only PR #3 is ready for code review independently of product readiness. Claude controls defect disposition and core implementation. Standalone release still requires installed desktop GUI/packaging and real backup-recovery qualification; approved multi-user Windows coordinator + SMB remains unimplemented/unqualified. No merge/release authorization or physical SMB/NFS certification follows from this evidence.

## 1. Current engineering decision

**PR #3 (independent QA infrastructure) is reviewable by Claude and the Director.** The new #8/#9/#10 corrective code on Claude's Phase 2 branch has passed the independent local-filesystem acceptance suite and the real Windows checkout stress specified below. This is **not** an approval to merge or release. A physically installed GUI and supported multi-user SMB deployment remain unqualified.

| PR | Branch and current inspected SHA | Independent review disposition | Product release status |
| --- | --- | --- | --- |
| [#3 — QA](https://github.com/nrobertson152/RevisionWorx/pull/3) | `qa/testing-infrastructure`; QA test SHA `f5b8a13064d8a482fa9492da73da333cf6615a15` in current independently verified run; documentation-only commit follows in progress log | **Ready for Director/Claude review.** Tests, failpoints, CI and evidence, no changes to production code. Remains draft. | QA green status does not certify product. |
| [#6 — Phase 1](https://github.com/nrobertson152/RevisionWorx/pull/6) | `phase1-foundation` `412e69ab90bb1d8b74c1a3cf5519f581e4420e63` | Phase 1 + QA PR integration runs on Linux/Windows; still needs approved integration order. | No installed-desktop or physical-share qualification. |
| [#7 — Phase 2](https://github.com/nrobertson152/RevisionWorx/pull/7) | `phase2-documents` `d202b28305f765b4edead3dede211dd9e7a891a6` | **#8 effective revocation, #9 index consistency, #10 local Windows lock cleanup accepted under exact CI configurations.** Full suite and focused stress green except explicitly retained physical-artifact xfail. | Standalone *testable/reviewable*, but no product-release approval; multi-user **NOT qualified**. |

## 2. Current exact-revision execution evidence

Full integration [GitHub Actions run 41387311002](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311002), disposable Phase 2 `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6` plus QA `454391dfd6bd66059b97631701f6e5722b5e862e`, executed with `python -m pytest -q -ra tests/qa` and `python -m pytest -q -ra`:

| Host | Python | Independent QA | Complete Phase 2 suite |
| --- | --- | --- | --- |
| Linux | 3.11 | 37 passed, 1 skipped, 1 strict xfailed | **211 passed, 1 skipped, 1 strict xfailed** |
| Linux | 3.13 | 37 passed, 1 skipped, 1 strict xfailed | **211 passed, 1 skipped, 1 strict xfailed** |
| Windows | 3.11 | 38 passed, 1 strict xfailed | **211 passed, 1 skipped, 1 strict xfailed** |
| Windows | 3.13 | 38 passed, 1 strict xfailed | **211 passed, 1 skipped, 1 strict xfailed** |

Skipped: Linux QA skips the Windows NTFS-specific fault injection; Windows full suite skips a case-sensitive-filesystem-only test. The remaining strict xfail is the physical late-journal-file oracle under #8 (see below).

Focused [run 41387311010](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311010), same two SHAs, Linux and Windows Python **3.13.17**, commands:

```bash
python -m pytest -q -ra tests/qa/test_integrity.py
python tests/qa/windows_checkout_stress.py --trials 5 --workers 6 --timeout 100
```

Linux: **16 passed, 1 skipped, 1 strict xfailed**. Windows: **17 passed, 1 strict xfailed**.

**Windows Server 2025 runner 10.0.26100, local temporary filesystem:** 5 independent trials × 6 spawned processes = **30 attempts**. Each trial: `won=1`, `conflict=5`, all six exit codes zero, no orphaned writer lock, one durable checkout, original immutable revision unchanged, journal head 5, zero `Workspace.verify()` problems. **5/5 trials passed; 0 crashes, 0 timeouts.** This is local NTFS-like CI evidence, *not* a physical two-host SMB certification or a long-duration soak.

## 3. Five independent safety and reliability acceptance oracles

All test paths are relative to the QA branch. Claude owns production fixes; QA owns verification and historical regressions.

| Issue / invariant | Independent regression path | Exact-current candidate outcome | Claude handoff / follow-up |
| --- | --- | --- | --- |
| [#8](https://github.com/nrobertson152/RevisionWorx/issues/8): an old writer must not create a physical journal file after revoked | `tests/qa/test_integrity.py::test_paused_writer_break_after_lock_check_must_not_publish` | **STRICT XFAIL remains**, deliberately. A late journal file can physically exist. | ADR-015 rev.2 explicitly allows a provably **fenced, logically ineffective** artifact. Physical nonpublication is a stronger and *unmet* oracle; do not conflate it with an effective stale mutation. |
| [#8](https://github.com/nrobertson152/RevisionWorx/issues/8): revocation before evidence lock move must block an effective stale write | `tests/qa/test_integrity.py::test_writer_revoked_before_admin_audit_cannot_take_effect`; positive `test_revoked_writer_publication_is_logically_fenced_on_replay` | **PASS** on Linux/Windows; revocation journal entry linearizes before lock-file evidence. | Maintain token-checked reason-required audited override. Revalidate every later authority protocol change, especially coordinator transitions. |
| [#9](https://github.com/nrobertson152/RevisionWorx/issues/9): index cannot include an N+1 insert under cursor N | `tests/qa/test_integrity.py::test_index_rebuild_does_not_include_records_newer_than_its_watermark` | **PASS** on Linux/Windows. | Bounded stable-snapshot retry accepted for tested deterministic insertion; independently verify again if replay architecture changes. |
| [#9](https://github.com/nrobertson152/RevisionWorx/issues/9): index cannot omit an N record because of a deletion at N+1 | `tests/qa/test_integrity.py::test_index_rebuild_does_not_omit_pre_watermark_records_due_to_late_delete` | **PASS** on Linux/Windows. | Verify bounded-retry failure-closed behavior under ongoing publication in future load tests. |
| [#10](https://github.com/nrobertson152/RevisionWorx/issues/10): transient Windows sharing violation must preserve lock ownership, retry boundedly and allow successor | `tests/qa/test_integrity.py::test_windows_writer_lock_close_survives_transient_sharing_violation` plus `tests/qa/windows_checkout_stress.py` | **PASS** Windows injected violation and real **5/5 six-process trials**. | Preserve ownership checks each retry; later test persistent denial, reassignment and longer contention on release candidates. |

The version-aware strict `xfail` markers remain active **only on older implementations** for the four *corrected* invariants. They become normal passing tests on the current implementation; an unexpected error still fails CI. The physical-file oracle remains an explicit strict expected failure because the artifact can exist but has no authoritative effect. Its presence must never be counted as a passing physical-nonpublication requirement.

## 4. Qualification tiers and outstanding gates

**QA infrastructure review (#3):** Independent tests, reproducible fixtures, actual PySide6 offscreen widget tests, CLI subprocess tests, deterministic fault injection, Windows processes and pinned current Phase 2 code provide a reviewable QA PR. The approved integration order and any merge decision remain Director-controlled.

**Standalone single-user desktop release:** Local application behavior and revised storage checks are independently tested, but a clean, **installed native Windows and Linux build** has not been manually qualified for actual interactive dialogs, installation/upgrades, long-file paths, CAD application roundtrip, backups or unexpected permission failures. Do not infer a release from passing offscreen widgets.

**Supported multi-user deployment:** ADR-014 authorizes an optional offline Windows-hosted LAN coordinator plus SMB. **Not production-certified.** Before qualification: atomically fenced generation/transaction authority, authenticated audited override, durable txid replay and restore witnessing, real separate-host SMB 3.x contention/failure tests (disconnect, power loss, server reboot, permissions, backup rollback), and committed-object integrity. Local Windows 30-process-attempt evidence proves none of those network properties. NFS/Linux hosting are later targets.

## 5. Retest recipe and ownership

1. Resolve one immutable candidate Phase 2 commit SHA **once for the whole matrix** (the QA workflow now pins a single head; previously independently starting jobs could test different Claude commits).
2. Overlay `tests/qa` from an exact QA SHA into a *disposable* Phase 2 checkout; run `python -m pytest -q -ra tests/qa` and `python -m pytest -q -ra` on Linux/Windows Python 3.11/3.13. Record **both** SHAs, every pass/skip/xfail/XPASS/failure and job URLs.
3. Run the **focused** `test_integrity.py` and real Windows `windows_checkout_stress.py --trials 5 --workers 6` against the same Phase 2 SHA. A failed/timeout trial must print worker outcomes and exits, not be retried away silently.
4. For any changed fix, verify its test stops being a strict expected failure and becomes a real pass **for the intended invariant**. A logical-fencing pass is not a physical-no-file pass. On failures, file reproducible details in issues #8/#9/#10; never alter authoritative storage from the QA branch.
5. Before Director-approved merge/release, retest the **actual intended integration commit** and qualify native UI and physical supported SMB separately.

**QA recommendation:** PR #3 may be reviewed independently of production release readiness. Claude may evaluate closing the locally corrected #8/#9/#10 implementation defects after reviewing this evidence, while keeping physical-artifact semantics explicit and all shared-network qualification gates open. **No PR merge, release or deployment approval is given by QA.**
