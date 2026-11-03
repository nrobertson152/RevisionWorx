# Distributed Systems P0 Merge-Readiness Audit — 22a5273

**Audited production SHA:** `22a5273d4740b3a7af8e8cb5cc3565e4336b763b` (`phase2-documents`).  
**Prior scoped accepted core:** `fe8a179f0203ef08151dc8459c84609dc639c4fe`.  
**Phase 1 base:** `412e69ab90bb1d8b74c1a3cf5519f581e4420e63`.  
**Main at review:** `3b2d75ad36790c9308f73d3b5e22c002a9d117fb`.  
**Scope:** native local filesystems and cooperating RevisionWorx processes; no SMB/NFS, hostile namespace mutation, real power-loss durability, release or merge authorization.

## Executive disposition

**HOLD both the standalone Phase 1 merge and the unqualified Phase 2→Phase 1→main sequence.**

- **P0, confirmed old-source integrity risk:** `phase1-foundation` still performs `read_json(writer.lock)` then `lock.unlink()` in `Workspace.close()`, allowing a paused old releaser to delete a new writer's lock. This is the historical independently proven ADR-019 successor-lock race. The isolated backport `36d1957` also retains a check-then-unlink race and was already rejected. Neither old head should land on `main` as a standalone deliverable.
- **P1 availability/liveness risk in new core:** `22a5273` newly combines `atomicio.retry_sharing` (250 attempts × 20 ms on Windows) with a *second* 250-attempt PermissionError loop in `Workspace._read_lock_file`, potentially **62,500 attempted file reads and roughly 1,250 seconds** in one call when Windows continues to deny a read. On Linux, the outer loop still retries PermissionError even though the atomicio helper is one-shot. On Windows, neither layer restricts retry to transient **WinError 32**, so permanent **WinError 5** ACL denials are repeatedly retried too. The code comment saying a bounded ~5 seconds applies to each loop separately, not their composition. No journal-corruption or dual-writer outcome has been established from this defect; classify **availability P1 pending owner triage**, not an unsupported integrity P0.
- **Additional changed core:** four commits after accepted `fe8a179` introduce ADR-022 journal-backed record reads/materialisation, new `atomicio.py` read/share-delete and retry behavior, and Windows contention changes. The prior acceptance cannot be inherited. The owner's Linux/Windows core CI on `22a5273` is [41574652418](https://github.com/nrobertson152/RevisionWorx/actions/runs/41574652418) green, but cannot override new independent failing safety predicates.

## Source-level filesystem audit

| Component / new code | Intended safety property | Independent finding / limitation |
|---|---|---|
| `atomicio._open_sharing_delete` | Windows readers grant FILE_SHARE_DELETE to avoid blocking rename | Appropriate design on local Win32, but does not itself cover all third-party handles |
| `atomicio.retry_sharing` | Bounded retry of WinError 32 rename/open failures | Catches **all** `PermissionError` on Windows; WinError 5 and nonsharing ACL failures are delayed |
| `Workspace._read_lock_file` | Transient reader sharing denial must not permanently mark a lock lost | Duplicates retry budget; nested 250 × 250 upper bound; retries POSIX EACCES |
| `atomicio.atomic_write_bytes` | Fully-fsynced temp and atomic replace; directory fsync POSIX | Windows directory durability intentionally unverified; no claim about power loss or network shares |
| `atomicio.publish_exclusive` | One winner for a published journal name or lock | Uses POSIX hard-link vs Windows non-overwriting rename; this commit does not change its algorithm |
| ADR-022 journal-backed read cache | A late stale writer's record-file materialisation cannot override the authoritative effective journal | Correct architectural direction. Materialisation/read-view behavior changed since accepted SHA and still needs independent exact-source regression qualification |

### Independent deterministic executable evidence

Research test `prototype/test_core_22a5273_retry_composition.py`, introduced at `5310ce3d7a876479797e741663f59f4f261e29cf`, uses the *real exact-source* `Workspace._read_lock_file → atomicio.read_json → retry_sharing` chain. Accelerated injected error responses and reduced budgets avoid CI hangs. With four outer and five inner attempts, it verifies:

1. **UNSAFE reproduction:** 20 low-level reads on one persistent simulated WinError 32.
2. **UNSAFE reproduction:** persistent simulated WinError 5 likewise receives 20 low-level reads.
3. **UNSAFE reproduction:** POSIX PermissionError gets four outer retries.
4. **Positive control:** transient Windows WinError 32 clears after two denials, yielding a correct response on the third attempt.
5. **Strict expected-failure safety requirements:** a single bounded Windows retry budget, immediate WinError 5 rejection, immediate POSIX EACCES rejection. Intentional XFAIL is *not safety acceptance*.

Four-platform pinned `ubuntu-latest`/`windows-latest` Python 3.11/3.13 workflow added at `2329faff4f2f4a9881bdb59268a70836cfcda0db`: [run 41577243072](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577243072). Full result should be recorded after all jobs complete. Added an independent *genuine Win32 CreateFileW exclusive-handle* confirmation at `c2267dbbe288c08abfe17524eff0373225271577`, workflow `99aeb91c56da7337d0c39bf776fa959489428c0c`: [run 41577491200](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577491200). Do not count queued jobs as complete.

## Actual Git topology and safe candidate order

From the exact GitHub compare API at this audit: `phase2-documents` is **25 ahead, 0 behind** `phase1-foundation`, so the Phase 2 tree includes Phase 1 history. `main` and `phase1-foundation` diverge (**6 ahead / 31 behind**, with a common ancestor at `0c9cc6b`). PR #7 currently reports mergeable, while PR #6 currently reports **mergeable=false**. The core owner's `docs/INTEGRATION_PLAN.md` had previously identified a `docs/DEVELOPMENT_PROGRESS.md` conflict; re-evaluate actual conflict against current `main`, which has advanced since that report.

**The dependency requirement is invariant:** do not land `phase1-foundation` SHA `412e69a` by itself. Any Director-approved integration would first incorporate a *new independently accepted* Phase 2 exact commit into the Phase 1 branch, then resolve the current-main conflict and test the resulting merged tree. Only then consider GUI PR #14. Do not rebase approved commits or treat a green component CI as merged-tree acceptance. The rejected Phase 1 backport `36d1957` should not be substituted silently. GitHub branches, PR targets, and production files were not changed by this audit.

## Required owner/QA gates

1. Fix the nested retry composition so a single bounded Windows **WinError 32** retry budget applies to an operation, and other PermissionError/WinError 5/POSIX EACCES errors fail immediately. Preserve correct `FileNotFoundError`/successor-handoff and Win32 FILE_SHARE_DELETE semantics. Add ordinary **PASS** tests for all three unmet safety predicates on the corrected *exact new SHA*.
2. Independently re-run the three accepted ADR-019 rev.4 positive safety interleavings and separate-process custody crashes on the next SHA, alongside ADR-022 authoritative record-file/journal replay and Windows checkout contention.
3. Verify and test the *merged tree* on native Linux and Windows; repeat after any GUI or docs conflict resolution. Obtain Director approval for the actual merge sequence. No automatic backport, merge, retarget, release or network-folder claim.

**No production core, phase1, GUI, or branch target modified. Only research tests, workflow, documentation and PR comments are in scope.**


## Superseding latest-core checkpoint: `a03f1f0` (same sprint)

During the exact-`22a5273` investigation, Claude A advanced `phase2-documents` by two commits: `feffbcfd9a928c30771e2b42f7c920ccf8ed5675` (16 reliability corrections, including index and journal cache) and `a03f1f00ab58b815534c4282b20ffc6503119aea` (separate-process crash matrix and four-platform CI configuration). **Do not carry the old 22a liveness defect forward uncritically**:

- I inspected the new `workspace.py`: `Workspace._read_lock_file` no longer exists, and `_check_lock_still_held`/`close` call `atomicio.read_json` directly. Therefore the original **250 × 250** nested read retries have been **removed by the new code** (pending independent CI). A new `RETRY_SHARING = IS_WINDOWS` flag governs the atomicio retry budget.
- The new `atomicio.retry_sharing` still catches **every** `PermissionError` whenever `RETRY_SHARING` is true, without checking that `exc.winerror == 32`. Therefore a permanent **WinError 5** remains eligible for up to 250 retries and about five seconds of unnecessary delay. This is **P2 availability/diagnostics** unless evidence shows more severe effects, not a proven writer-safety failure.
- New independent source-pinned tests `prototype/test_core_a03f_retry_semantics.py` (commit `5b9418a71829c2381f69a344aae71cca830d7bf5`) and four-platform CI (commit `849011948c25d549d1983d477b5a2d22a848210a`, [run 41577872734](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577872734)) distinguish old fixed retry nesting, transient WinError 32 recovery, POSIX immediate failure, and WinError 5 repeated denial. **Queued at this writing; no new-SHA PASS/FAIL claim yet.**
- The two new commits also change ADR-022 `_rev` validation, `CheckoutTokens`, content object publication, index cache/recovery and other core surfaces. **Independent exact-`a03f1f0` qualification is still required** before changing the merge hold. The user-requested `22a5273` findings remain valid historical negative controls only.

**Unchanged merge blocker:** direct Phase 1 `412e69a` or rejected backport `36d1957` into `main` would reintroduce known unsafe writer-lock behavior. No merge authorization.


## Additional verified results and P0 current-head CI blocker

1. **Native old-source confirmation complete.** Historical exact `22a5273` Win32 `CreateFileW` exclusive-handle tests `prototype/test_core_22a5273_real_windows_retry_stack.py` [run 41577491200](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577491200) **Windows Python 3.11 2 tests (1 explicit UNSAFE PASS + 1 strict positive-safety XFAIL); Windows Python 3.13 identical**. This proves actual kernel WinError 32 causes 4×3 read attempts with accelerated retry limits, not just simulated PermissionError. It does *not* say nested retry remains at `a03f1f0`.
2. **New exact-head native test prepared:** `prototype/test_core_a03f_real_windows_no_nesting.py` source `5a8de5d9902690de43b289321839f05612d9ebe0`, pinned Windows 3.11/3.13 CI workflow `b24f2c984fed0314343d08295858780e33481d31` [run 41578193272](https://github.com/nrobertson152/RevisionWorx/actions/runs/41578193272), queued at this checkpoint. Positive gate holds a real CreateFileW handle that denies reading, requires exactly three attempted reads with a three-attempt budget and no second loop, and checks successful read after handle release.
3. **Latest Phase 2 CI RED at exact `a03f1f0`.** [Core run 41577700770](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577700770) finished **three jobs SUCCESS, one FAILURE**: Windows Python 3.13 failed `tests/test_core_reliability.py::test_index_open_race_between_threads_on_a_new_cache`. The Windows 3.13 job finished **343 passed, 2 skipped, 1 failed**. Six competing first-time `open_index` calls produced a genuine `IndexBusyError` for one thread. The API documents `IndexBusyError` as a recoverable condition, so this is presently classified a **merge-blocking test/initial-index availability contract disagreement**, not proven journal corruption. Owner must choose (a) bounded retry inside `open_index` while schema is first created or (b) bounded retry in the test to match the documented recoverable-busy contract, with no cache deletion and all six eventually converging. Then rerun native Windows 3.13 and all four platforms. [Notified Claude A PR #7](https://github.com/nrobertson152/RevisionWorx/pull/7#issuecomment-6595465372).
4. **Latest source provisional status:** `a03f1f0` has removed nested read retries; `atomicio.retry_sharing` still retries Windows permanent WinError 5, a smaller availability/diagnostic concern. Its independent exact-SHA synthetic tests [run 41577872734](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577872734) remain awaiting CI; do not treat source inspection as completed native acceptance.

**Current disposition: HOLD Phase 1/Phase 2 → main merge**: Phase 1 alone has independently established P0 writer-lock vulnerability, and latest combined core has a red Windows CI matrix plus fresh unqualified reliability changes. The findings do not authorize rebase, retarget, backport, merge, or release.
