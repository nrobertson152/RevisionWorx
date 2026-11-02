# RevisionWorx — Independent QA merge-readiness assessment

**QA Engineer | 2026-11-01 | Advisory, not merge approval**

## Architecture and scope

- PR #3: QA infrastructure, independent tests, Linux/Windows CI. Base: `phase1-foundation`.
- PR #6: Phase 1 reliability and PySide6. Base: `main`. Head `412e69ab90bb1d8b74c1a3cf5519f581e4420e63`.
- PR #7: Phase 2 document/revision/checkouts. Base: `phase1-foundation`. Head `47d81658764cd6bde60e391ac9aac24e722317e8`.
- The user/Project Director approves merges. This report is a quality assessment, not permission to merge or deploy.

| PR | Test infrastructure / review readiness | Product release readiness | Required action |
| --- | --- | --- | --- |
| #3 — QA | **Ready for director's review**, subject to no newer failing CI: independent QA suites and Windows/Linux matrix execute; the strict fencing xfail is transparent. PR remains draft. | **Not ready.** A green QA PR cannot certify the PDM product. | Director/Claude decide base/merge sequencing; preserve regression test for issue #8. |
| #6 — Phase 1 + PySide6 | **Reviewable, with independent CI evidence.** QA-on-PR-base run [41380328108](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380328108) passed Ubuntu/Windows Python 3.11/3.13; GUI tests run with offscreen Qt in CI. | **Not ready for shared multi-user production use.** Writer-break TOCTOU, unqualified SMB/NFS, and no installed desktop validation. | Resolve issue #8 before claiming concurrent safety; qualify native GUI and share behavior. |
| #7 — Phase 2 | **Reviewable, with cross-platform integrated CI evidence.** Disposable QA overlay [run 41382170092](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382170092), Phase 2 `47d81658764cd6bde60e391ac9aac24e722317e8`, QA `20cd42368244a1b4daa6eed62230994d106503db`: Linux Python 3.13 **33 QA passed/3 xfailed**, full **190 passed/3 xfailed**; Windows **33 QA passed/3 xfailed**, full **189 passed/1 skipped/3 xfailed**. | **Not ready for production multi-user deployment.** Pre-audit revocation can still allow a stale mutation; index rebuild cursor mismatch is unresolved. | Claude owns fixes for #8/#9, approved ADR-014 coordinator, and native Windows/SMB qualification. Director controls merge and release. |

## What the test results mean

The **strict xfail** `test_paused_writer_break_after_lock_check_must_not_publish` is a deterministic reproduction of the non-atomic **physical publication** window. ADR-015 has an independently passing positive test for **post-audit logical fencing**, but it still permits the late physical journal file. An additional strict acceptance xfail tests the distinct **lock-renamed-before-audit** window, where even logical replay can accept a stale mutation. Issue #8 remains open. The runner reports overall success because the defect is explicitly recorded as expected, **not because the requirement passed**. If a relevant defect is corrected, its strict test should become XPASS and fail CI, prompting QA to revise the oracle only after verifying that the corrected authority contract meets the **intended invariant**. A late file that is provably ignored may satisfy logical safety but not an oracle that prohibits physical publication; these are different claims.

Windows Phase 2's one skipped test is `tests/test_projects.py`'s case-sensitive-filesystem assumption; it is not a failed Phase 2 feature. GitHub-hosted offscreen Qt tests demonstrate widget execution but not installed native GUI behavior, interactive dialogs, rendering quality, or accessibility.

The integrated Phase 2 workflow copies only QA tests into a disposable Phase 2 checkout. It **does not** merge PR #3 into PR #7 or test an approved final merge commit. Final integration and post-merge tests remain mandatory.

## Blocking qualification evidence

1. **Known release-blocking defect:** [issue #8](https://github.com/nrobertson152/RevisionWorx/issues/8) administrative lock break can permit a stale writer to publish after its last token check. The Director's audited override requirement remains mandatory. ADR-014 approves an optional local LAN coordinator for multi-user deployments, initially Windows-hosted with SMB. **It is not integrated or physically certified**; ADR-015's post-audit logical fencing does not close the pre-audit lock-rename window.
2. **No real shared-filesystem certification:** no separate-host SMB/NFS fault injection, lock-break race, disconnect/reconnect, lease/rename semantics or Windows share contention verification.
3. **No installed desktop smoke:** test Windows and Linux packaged builds, document registration, checkout/check-in, dialogs, path-preserving native CAD files and recovery under a real GUI session.
4. **Final integration not tested:** stacked PRs and QA overlays were verified at the exact SHAs above, not at a director-approved final merged revision.
5. **Separately reproduced cache defect:** [issue #9](https://github.com/nrobertson152/RevisionWorx/issues/9): `LocalIndex.rebuild` mixes newer records with an older journal cursor. New independent strict-expected-failure regression; fix belongs to Claude before claiming reliable cache consistency.

## Minimal Windows installed-GUI smoke procedure

Use a clean Windows VM and an actual installed build produced by the approved release pipeline. Keep a disposable test directory with an assembly referencing a part by relative path; save the directory fingerprint before testing. Open the app, create/open a workspace, register a native CAD drawing, filter it in the Documents tab, check it out, modify the file, check in a revision, inspect history and export an earlier revision. Verify the original assembly and part paths and bytes remain unchanged except for the deliberately edited file, and that the previous content snapshot hashes to its recorded SHA-256. Repeat on a Linux desktop with an installed package. Test ordinary users and permission-denied locations. Record OS/build SHA, screenshot or log evidence, and pass/fail for every action. Offscreen CI is not a substitute for this manual qualification.

**QA conclusion:** **PR #3's test infrastructure can be reviewed independently** of the product release blockers, once all QA jobs at the chosen merge SHA complete successfully. This is not approval to merge. Phase 1/2 remain **not certified for production multi-user shared-folder writes**; issues #8 and #9 and physical Windows/SMB qualification require Lead Architect ownership. The Director authorizes integration and releases.

## Current independent verification snapshot and limitations

- **Latest verified complete Phase 2 + QA before the newest cache-delete/restore-history tests:** [run 41382170092](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382170092), Phase 2 `47d81658764cd6bde60e391ac9aac24e722317e8`, QA `20cd42368244a1b4daa6eed62230994d106503db`. Linux/Python 3.13 **33 QA passed/3 xfailed, full 190 passed/3 xfailed**; Windows/Python 3.13 **33 QA passed/3 xfailed, full 189 passed/1 skipped/3 xfailed**. Strict xfails cover post-audit physical publication (#8), pre-audit-rename stale mutation (#8), and LocalIndex watermark mismatch (#9). The separately **passing** ADR-015 logical post-audit fencing test is included.
- **Real PySide6 tests:** `tests/qa/test_qt_documents.py` executes genuine Qt widgets offscreen (not stubs) on Linux and Windows. The initial two cases for status warning and validation passed in [run 41381681615](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381681615); the authoritative checkout-conflict dialog case also passed on both platforms in [run 41382022898](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382022898).
- **Issue #8 remaining window:** in addition to the original post-audit physical append, the Distributed Systems Engineer identified an administratively broken lock that is *renamed before its audit entry is committed*. Current ADR-015 journal ordering does not fence a stale write published in this gap. The independent strict expected-failure test was **verified** as XFAIL on both operating systems in [run 41382170092](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382170092). This confirms a stale mutation can take effect before the audit despite ADR-015.
- **Historical CI failures and correction:** the first index watermark acceptance test used `expect_rev=0`, which the original Phase 1 `RecordPut` lacked. This was QA test incompatibility, **not** a production defect. Commit `4f2f40f` removed that unnecessary argument, and [run 41381768439](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381768439) completed successfully on Linux and Windows, confirming repaired QA-branch testing.

The latest current-head CI must still be inspected before assigning any final merge-readiness status. New tests **not yet executed** at this snapshot cannot be counted in the numbers above.
