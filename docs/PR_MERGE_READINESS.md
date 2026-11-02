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
| #7 — Phase 2 | **Reviewable, with independent CI evidence.** Disposable integration [41380322764](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380322764), Phase 2 `47d81658764cd6bde60e391ac9aac24e722317e8`, QA `af1d18cd1c184afbb224db86c7d185f42ae4a567`: Linux Python 3.13 QA **27 passed, 1 xfailed**, full **184 passed, 1 xfailed**; Windows Python 3.13 QA **27 passed, 1 xfailed**, full **183 passed, 1 skipped, 1 xfailed**. | **Not ready for multi-user production release.** A checkout relies on the writer authority that issue #8 demonstrates can be revoked too late. | Resolve fencing/authority decision; qualify cross-host concurrency and installed Windows desktop. |

## What the test results mean

The **strict xfail** `test_paused_writer_break_after_lock_check_must_not_publish` is a deterministic reproduction of an actual safety defect. The runner reports overall success because the defect is explicitly recorded as expected, **not because the requirement passed**. If the defect is corrected, the test should become XPASS (and fail under `strict=True`), prompting QA to remove the marker only after verifying the new contract.

Windows Phase 2's one skipped test is `tests/test_projects.py`'s case-sensitive-filesystem assumption; it is not a failed Phase 2 feature. GitHub-hosted offscreen Qt tests demonstrate widget execution but not installed native GUI behavior, interactive dialogs, rendering quality, or accessibility.

The integrated Phase 2 workflow copies only QA tests into a disposable Phase 2 checkout. It **does not** merge PR #3 into PR #7 or test an approved final merge commit. Final integration and post-merge tests remain mandatory.

## Blocking qualification evidence

1. **Known release-blocking defect:** [issue #8](https://github.com/nrobertson152/RevisionWorx/issues/8) administrative lock break can permit a stale writer to publish after its last token check. The Director's audited override requirement remains mandatory. No production authority mechanism has been approved.
2. **No real shared-filesystem certification:** no separate-host SMB/NFS fault injection, lock-break race, disconnect/reconnect, lease/rename semantics or Windows share contention verification.
3. **No installed desktop smoke:** test Windows and Linux packaged builds, document registration, checkout/check-in, dialogs, path-preserving native CAD files and recovery under a real GUI session.
4. **Final integration not tested:** stacked PRs and QA overlays were verified at the exact SHAs above, not at a director-approved final merged revision.

## Minimal Windows installed-GUI smoke procedure

Use a clean Windows VM and an actual installed build produced by the approved release pipeline. Keep a disposable test directory with an assembly referencing a part by relative path; save the directory fingerprint before testing. Open the app, create/open a workspace, register a native CAD drawing, filter it in the Documents tab, check it out, modify the file, check in a revision, inspect history and export an earlier revision. Verify the original assembly and part paths and bytes remain unchanged except for the deliberately edited file, and that the previous content snapshot hashes to its recorded SHA-256. Repeat on a Linux desktop with an installed package. Test ordinary users and permission-denied locations. Record OS/build SHA, screenshot or log evidence, and pass/fail for every action. Offscreen CI is not a substitute for this manual qualification.

**QA conclusion:** test infrastructure is suitable for review, but the product is not certified for production multi-user shared-folder writes. No merge or release recommendation is made while issue #8 remains open.
