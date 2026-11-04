# Desktop GUI A — native accessibility and release-smoke acceptance protocol

Status: **AUTOMATED OFFSCREEN GATES PASS AT PINNED PR MERGE; NATIVE GATES NOT EXECUTED**. This is a reproducible operator test protocol, not evidence that the application passes native desktop acceptance. Owner: Desktop GUI A; independent signoff: QA and Integration. Scope: single-user, local-disk, offline. No SMB/NFS or multi-host safety claim.

## Evidence and prerequisites

Record the exact GUI branch SHA, GUI base SHA, CI synthetic merge SHA (if any), Python and PySide6/Qt versions, OS build, display resolution, OS scaling, package artifact name and SHA-256, and whether the application is source-launched or packaged. Attach timestamped screenshots or screen recordings of focus and dialogs, and a redacted transcript of failures. Use a disposable project and fixture CAD files; never test against production data. For Windows use a standard non-admin user; for Linux use a normal desktop session (not `QT_QPA_PLATFORM=offscreen`). Test with network disabled. Record PASS, FAIL, BLOCKED or NOT RUN for each numbered gate; do not count a blocked or negative-path-only result as acceptance.

Suggested fixture: create a disposable project, import a small `.ipt` binary with known SHA-256, a Unicode filename (e.g. `轴承-épreuve.ipt`), and a second drawing. Record SHA-256 of the external originals before testing. Keep the workspace on a local filesystem. The CLI's verify/export results, when available, should be recorded separately from GUI outcomes.

## Gate K: keyboard-only engineering journey

1. Start the application with the mouse disconnected or unused. From initial focus, use Tab and Shift+Tab to reach project navigation, create/open controls, document table, search/filter and action buttons. Record the exact focus sequence and any unreachable target. Do **not** use `setFocus()` or a test harness to bypass actual traversal.
2. Create/open a project using only keyboard controls. Navigate the document table with arrows; select a document and inspect its metadata and history. Search for the Unicode filename, clear search and confirm the original selection remains understandable.
3. Import/register a CAD document using keyboard navigation and the native file picker. Record whether the path field, confirmation and cancel actions are reachable. Verify no modification of the original source bytes.
4. Check out a document, edit the disposable working copy, activate Check In using keyboard Space/Enter, and traverse every field in the modal using Tab and Shift+Tab. Ensure a required change comment cannot be bypassed. Press Escape and verify the checkout remains available, no revision is added and focus returns to a usable main-window control.
5. Reopen Check In, enter a valid comment, submit by keyboard and verify the outcome is not falsely presented as success on error. Inspect history, export the revision and verify its bytes against the expected fixture.
6. Close the application, launch a new process and reopen the project. Verify revision history, selection, checkout state and export remain consistent. Compare original CAD hashes and authoritative journal/object hashes before and after the cancellation substep.
7. Trigger a documented read-only or corrupt-journal recovery fixture in a disposable workspace. Confirm all write affordances are disabled, the reason is readable by keyboard, and no success toast appears. Restore only the fixture from backup.

For each step, log the focused widget's accessible name/role, visible focus indication, keyboard key used, modal close behavior, and any trapped or lost focus. Screen-reader testing (Windows Narrator and a Linux accessibility reader such as Orca) is a **separate** gate; passing keyboard navigation does not establish screen-reader support.

## Gate D: real display scaling and text accessibility

Run Gate K at 100%, 150%, and 200% OS display scaling on Windows, and at 100% plus one >100% scaling mode on a real Linux desktop. Record display resolution and whether scaling is fractional or integer. At minimum inspect 1366×768 and 1920×1080 effective desktops where available. Increase system text size independently if supported. Check the project navigator, document table, property/history panels, check-in dialog, warnings, file picker, and recovery banner for clipping, overlapping controls, unreadable labels, unreachable buttons, truncated error detail, unexpected horizontal scrolling and modal positioning. Capture screenshots for each scaling setting. Automated large-font offscreen geometry tests are **not** a substitute.

## Gate P: native package and failure outcomes

On each OS, install or launch the actual produced artifact from a clean, non-developer environment; record artifact SHA-256 and packaging mechanism. If no artifact exists, mark BLOCKED rather than passing a source-tree launch. Confirm offline startup, local project creation, restart and export. Test permission-denied, read-only project, missing/corrupted managed object, changed source and insufficient-disk cases with disposable fixtures and safe simulation. Distinguish (a) no commit, (b) durable commit but post-commit apply warning, and (c) fully applied commit using authoritative journal/CLI verification, not merely a toast. For Windows specifically, repeat the saved-with-warning regression associated with CI run 41711104288 and record whether `.revisionworx/state.json` is readable after the committed outcome. Never modify the core protocol to make a GUI test pass.

## Automated gates and current evidence

`QT_QPA_PLATFORM=offscreen python -m pytest -q -ra -m qt tests/test_qt_gui_accessibility_a.py`

`QT_QPA_PLATFORM=offscreen python -m pytest -q -ra -m qt tests/test_qt_gui.py tests/test_qt_desktop_alpha.py tests/test_qt_desktop_alpha_rc.py`

At GUI A SHA `4a6427ccff85124338cd14a39fe551072cc34975`, GitHub Actions run 41711104288: Ubuntu Python 3.11.17, Qt 6.12.0, 9 accessibility PASS and 66 desktop PASS; Windows Python 3.11.9, Qt 6.12.0, 9 accessibility PASS, 65 desktop PASS, **1 desktop FAIL** (`test_checkin_saved_with_warning_is_never_shown_as_failure`), 0 SKIP/XFAIL in these steps. This is a **cross-platform HOLD**, not green acceptance. The Windows state-file permission failure is assigned for production-owner investigation; GUI A does not alter Claude B's branch.

## Pinned automated acceptance evidence (2026-11-03 UTC)

The latest verified run is [GitHub Actions 41736947002](https://github.com/nrobertson152/RevisionWorx/actions/runs/41736947002), PR #35's **synthetic merge commit** `18ff3be` (GUI A head `d4bc0157d5b0938def7bfc465872a415e48004d9` merged into Claude B GUI base `ce00ac450405b1e37b354c51302f3b9c4b1c70f4`). This distinction matters: the result verifies that combined checkout, not an isolated source-branch test or a packaged build.

| Runner / job ID | Runtime | Independent offscreen command | Existing Qt command | Fail / skip / xfail |
| --- | --- | --- | --- | --- |
| Ubuntu / 126268757745 | Python 3.11, PySide6 6.12.0, Qt 6.12.0 | `python -m pytest -q -ra -m qt tests/test_qt_gui_accessibility_a.py`: **9 passed** | `python -m pytest -q -ra -m qt tests/test_qt_gui.py tests/test_qt_desktop_alpha.py tests/test_qt_desktop_alpha_rc.py`: **66 passed** | 0 / 0 / 0 in both steps |
| Windows / 126268757513 | Python 3.11, PySide6 6.12.0, Qt 6.12.0 | Same command: **9 passed** | Same command: **66 passed** | 0 / 0 / 0 in both steps |

The recursive all-file journal snapshot introduced at `d4bc015` is included in the passing nine-test independent suite. The earlier Windows `state.json` PermissionError at `4a6427c` (run 41711104288) **did not recur** in this run; intermittent defect root cause and repeated-Windows evidence remain open. This does not demonstrate that the race is fixed. CI is not a native keyboard, display scaling, screen-reader, or installed-artifact test.

| Acceptance gate | Evidence | Disposition |
| --- | --- | --- |
| Offscreen keyboard/modal/restart/cancellation regressions | Run 41736947002, synthetic merge `18ff3be` | PASS on Ubuntu and Windows, scoped to nine tests |
| Existing offscreen desktop regression suite | Same run | PASS on Ubuntu and Windows, scoped to 66 tests |
| Native keyboard-only journey (K) | No recorded physical-desktop walkthrough for GUI A head | NOT RUN |
| Real DPI/text scaling (D) | No native 100/150/200% evidence | NOT RUN |
| Narrator/Orca accessibility | No screen-reader evidence | NOT RUN |
| Packaged application and clean-machine launch (P) | No artifact SHA or native launch evidence | BLOCKED pending artifacts and Integration |
| Intermittent Windows saved-with-warning state access | One earlier FAIL, latest PASS | OPEN; not independently root-caused |
| Independent QA/Integration signoff | No complete signoff for GUI A head | PENDING |

## Handoff and decision

QA must independently review focus and disk-authoritative assertions, reproduce or disposition failures, and cite exact CI job logs. Integration must validate actual Windows/Linux package artifacts and clean-machine launches. Director decides release; a successful offscreen test or synthetic PR merge is not a substitute for native GUI, DPI, screen-reader or installed-artifact evidence. Update this document with an evidence table only after executing the gates. No automatic release claim.
