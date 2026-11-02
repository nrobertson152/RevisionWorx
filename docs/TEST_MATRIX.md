# RevisionWorx risk-based QA matrix

**Status:** AUTOMATED = executable test exists, not a claim it has passed every platform; PLANNED = API or test rig absent; BLOCKED = unresolved safety requirement.

| ID | Priority | Oracle | Status |
| --- | --- | --- | --- |
| ADOPT-01 | P0 | Customer/year/product archive, indexed/managed: original paths, hashes and mtimes unchanged | AUTOMATED |
| ADOPT-02 | P0 | Read-only preview; baseline makes no historical revision claim | AUTOMATED |
| START-01 | P0 | Opening workspace does not enumerate unmanaged archives | AUTOMATED |
| START-02 | P0 | 50,000 unmanaged directories remain untraversed | PLANNED (developer suite covers this; independent rerun pending) |
| JOURNAL-01 | P0 | Injected disk-full before publication creates no committed entry | AUTOMATED |
| JOURNAL-02 | P0 | Crash after journal publication remains readable and recoverable | AUTOMATED |
| JOURNAL-03 | P0 | Journal gap denies writable open | AUTOMATED |
| LOCK-01 | P0 | Multiple local writers race: only one winner | AUTOMATED (existing suite) |
| LOCK-02 | P0 | Active writer excludes another writer | AUTOMATED |
| LOCK-03 | P0 | Administrative override must not give revoked writer authority | **Known failure (#8):** strict xfail for physical post-audit publish; positive ADR-015 test verifies logical fencing; distinct pre-audit-rename strict xfail pending execution. No SMB certification. |
| CHECKOUT-01 | P0 | Exclusive checkout, stale token rejected, view-model state | **VERIFIED** in Phase 2 Linux/Windows independent overlay; local multiprocess upstream checkout race in full suite; no cross-host guarantee |
| REV-01 | P0 | Immutable historical snapshot, release supersession, damaged-content release blocked | **VERIFIED** in independent Phase 2 Linux/Windows overlay; packaged CAD editor not tested |
| REV-02 | P0 | Prepublication journal failure retains checkout, prior revision, and journal seq | **VERIFIED** in independent Phase 2 Linux/Windows overlay |
| EDIT-01 | P0 | Forced hash detects same-size, same-mtime unauthorized edit | **VERIFIED** in independent Phase 2 Linux/Windows overlay; default metadata-only path can miss adversarial restored mtime |
| BACKUP-01 | P0 | Restored metadata and blobs from same consistent point | PLANNED |
| DUP-01 | P0 | Failed project duplication safely rolled back | PLANNED |
| PART-01 | P0 | Number collisions and reserved ranges rejected | PLANNED |
| CAD-01 | P0 | Registration and check-in preserve assembly paths/bytes | **VERIFIED** via independent file-fingerprint tests on Linux/Windows; native CAD resolver integration still unavailable |
| OSCH-01 | P1 | OpenSchematix content recognized, false extension rejected | AUTOMATED (existing suite) |
| BOM-01 | P0 | BOM quantity/revision/provenance correct | PLANNED |
| GRAPH-01 | P0 | Direct/indirect/cyclic/cross-project/revision Where-Used | PLANNED |
| IMPACT-01 | P0 | Before You Revise shows coverage and does not imply incompatibility | PLANNED |
| INDEX-01 | P1 | Local cache corruption/staleness rebuilt | AUTOMATED (existing suite) |
| INDEX-02 | P0 | Rebuild must not index records later than advertised journal cursor | **Known defect #9**; deterministic strict xfail in independent QA on modern Phase 2, with verified reproducer in research CI; not fixed |
| SCALE-01 | P1 | 10k projects, 2m docs, 500k edges and actual query p95/RSS | PLANNED |
| WINDOWS-01 | P0 | NTFS exclusive publish, Windows case/path rules | Windows GitHub Actions original QA branch 111 passed/3 skipped on Python 3.11 and 3.13 (run 41371072088); case-sensitive-only test skipped; native installer and production NTFS certification NOT DONE |
| WINDOWS-LOCK-02 | P0 | A legitimate writer must release its lock even if another Windows process briefly opens writer.lock | **Intermittent production defect #10**: actual Windows CI 41382433694 failed in `Workspace.close` with `WinError 32`, six-process checkout had three timeouts. Later CI 41382488751 passed; transient sharing-violation acceptance test added as Windows-only strict xfail, awaiting execution. |
| SMB-NFS-01 | P0 | Two-host shared filesystem concurrency and disconnection | BLOCKED |
| GUI-01 | P1 | Real PySide6 Documents-tab widgets on Linux/Windows offscreen | **VERIFIED** initial tab warning/filter/validation tests on Linux+Windows in Phase 2 run 41381681615; Qt conflict dialog negative test pending CI; installed Windows desktop smoke NOT RUN |

No absent feature is considered a passing test.

## Phase 2 execution context and release gate

The QA PR is currently based on `phase1-foundation`. The dedicated `tests/qa/test_phase2_integrity.py` module is intentionally **skipped** when `revisionworx.documents` is absent. A skipped module does **not** certify Phase 2. Run these tests on an integration checkout containing both the QA commits and Claude's `phase2-documents` branch only after the Lead Architect approves integration/retargeting. Do not report 151 passed / 1 skipped as independently QA-verified: it was executed by Claude, not QA.

The journal crash test has been updated for ADR-008's `CommittedNotApplied` exception, caller `txid`, and `_rev` materialized-record metadata. The writer-break test is an **xfail(strict=True)** known safety deficit; it does not count as a passing production requirement. Unexpected pass should prompt review of the fencing implementation and removal of the marker only after genuine cross-client verification.

The local Python checkout cannot resolve `github.com`. The GitHub connector supports source inspection and commits but does not execute repository tests. Exact-head full-suite, Phase 2, live Qt, Windows NTFS and SMB/NFS execution remain unverified by this QA role in the current session.

## Verified Phase 2 integrated execution — 2026-11-01

GitHub Actions [run 41371698368](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371698368), Linux Python 3.13, disposable overlay of QA tests at `2905632fe9b84e97bf121edf94a45f2bb44f5aa3` on Phase 2 implementation `77419e34a35a27d29dd9eab3bce858a1ad93871b`: **QA 24 passed, 1 xfailed**; **full suite 179 passed, 1 xfailed**. The eight Phase 2 integrity tests all ran and passed. No skips. The strict xfail is the independently reproduced stale-writer publication after audited administrative break; see issue #8. A successful job is not a passing fencing requirement.

GitHub Actions [PR integration run 41371702767](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371702767) succeeded across Linux and Windows, Python 3.11/3.13, with PySide6 available and Linux Qt runtime libraries installed. These PR-merged-base jobs validate Phase 1+QA; Phase 2 tests remain skipped there because Phase 2 is not on that PR's base. Installed desktop GUI and actual SMB/NFS are still unverified.

Two additional toolkit-independent DocumentListModel/DocumentPanel tests have been committed after the above verified run; they must not be counted as passed until a later Phase 2 integration job executes them. They cover live Controller-backed filtering, checkout/cancel action state, preservation of working bytes and rejection of blank check-in descriptions.

## Additional verification gates — 2026-11-01 continuation

- **Separate-process writer exclusion:** `tests/qa/test_integrity.py::test_second_process_cannot_take_active_writer_lock` spawns a second real Python interpreter while the first holds the workspace writer lock, requires `WorkspaceLockedError`, verifies lock metadata is unchanged, then releases and reacquires the lock. This is **local OS process isolation only**, not a cross-host or SMB/NFS test. Committed `cef419c`; execution pending.
- **Cross-platform Phase 2:** `.github/workflows/qa-integrity.yml` now runs the disposable stacked Phase 2 suite on **Ubuntu and Windows** Python 3.13, using portable Python-based QA fixture overlay rather than Unix `cp`. Committed `25ceb44`; cross-platform Phase 2 results pending.
- **Previously verified latest Phase 2:** run [41373823866](https://github.com/nrobertson152/RevisionWorx/actions/runs/41373823866) used Phase 2 `47d81658764cd6bde60e391ac9aac24e722317e8` and QA `8d5a70f3e0ed1f58811a489e2271d1056a85abff`; QA **26 passed, 1 xfailed**; full **183 passed, 1 xfailed** on Linux Python 3.13. Both new view-model tests passed. The xfail remains the known stale-writer publication defect.

## Latest independent cross-platform regression evidence

[GitHub Actions run 41381884699](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381884699), disposable Phase 2 source `47d81658764cd6bde60e391ac9aac24e722317e8` plus QA source `d72b7ba25ccbf5162e4db6df13914fbf5faa373e`: Linux/Python 3.13 **32 passed, 2 strict xfailed** (QA) and **189 passed, 2 strict xfailed** (full); Windows/Python 3.13 **32 passed, 2 strict xfailed** (QA) and **188 passed, 1 skipped, 2 strict xfailed** (full). Strict xfails are physical post-audit writer publish (#8) and LocalIndex snapshot-watermark mismatch (#9). The separate **positive** ADR-015 logical post-audit fence test passed. Newer pre-audit override and Qt checkout-conflict tests are committed but not included in this run; their status must come from subsequent CI logs. No physical SMB/NFS, native installed GUI, or actual concurrent shared-folder certification.

## Verified additional Phase 2 coverage (2026-11-01)

[GitHub Actions run 41382488751](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382488751), Phase 2 `47d81658764cd6bde60e391ac9aac24e722317e8` and QA `6d60f61ba01f7329d6937f63cffb595b527b9ff2`: Linux/Python 3.13 **34 QA passed, 4 xfailed**; full **191 passed, 4 xfailed**. Windows/Python 3.13 **34 QA passed, 4 xfailed**; full **190 passed, 1 skipped, 4 xfailed**. Added revision restoration (new revision C, A/B content untouched) **passed**, and new cache watermark delete-side test **strict xfailed**, validating issue #9 from both insert and delete. The four xfails are: post-audit physical stale publication and pre-audit logical stale mutation (#8), and inconsistent cache on newer insert and later delete (#9). A previous Windows run [41382433694](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382433694) failed an existing **six-process checkout test** due `WinError 32` in `Workspace.close`; this is separately filed as [issue #10](https://github.com/nrobertson152/RevisionWorx/issues/10) and must not be concealed by the later green run. Linux/Windows Python 3.11 Phase 2 matrix and new Windows sharing-violation failpoint are queued at update time; not yet verified.
