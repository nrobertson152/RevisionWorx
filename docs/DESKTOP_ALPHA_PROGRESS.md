# Desktop 0.1.0 Alpha — progress log (Issue #13)

Owner: Claude B (desktop/GUI). Branch: `feature/desktop-alpha` (from Phase 2 `366d435`).
Scope: PySide6 GUI, UI adapters and UI tests only. No core files are modified on this branch.

> **Alpha status:** single user, local disk only. Not suitable for irreplaceable production data.
> The app shows this permanently in a banner.

## Checkpoint 1 — `df024e6` (2026-11-02)

### What exists and works (verified by tests below)
| Alpha workflow | GUI | Tests |
|---|---|---|
| 1. Start offline, create/open a local project | Start page: one-step **Create project** (workspace + `Projects` root + managed project), **Open workspace…**, recent list; startup reopens last workspace or falls back to the start page with the reason | `test_quick_start_*`, `test_window_starts_on_start_page…`, `test_missing_workspace_returns…` |
| 2. Import a document, browse metadata/status | **Import file…** (Ctrl+I): exclusive copy into a chosen project folder + revision 1; summary fields, coloured status | `test_import_*`, `test_import_flow_viewmodel`, `test_full_alpha_journey…` |
| 3. Check out / check in with a meaningful description | **Check out** (Ctrl+K), **Check in…** dialog (Ctrl+Shift+K): description required (≥3 chars), optional label, explicit "unchanged" override | `test_checkout_checkin_history_and_restart`, journey test |
| 4. Revision history, reopen after restart | History table + full revision detail pane (author, size, SHA-256, state changes); checkouts survive restart (per-user token store) | `test_checkout_survives_restart…`, journey test |
| 5. Actionable errors | Missing file, permission denied, conflicting edit (stale checkout / foreign checkout / token mismatch), **recovery-blocked** (journal integrity failure) and pending-recovery banner | `test_missing_working_file…`, `test_permission_denied…`, `test_conflicting_edit…`, `test_recovery_blocked…`, `test_pending_recovery…` |
| 6. Alpha marking | Permanent banner + "(Alpha)" in title | `test_alpha_notice_is_explicit`, `test_window_starts…` |

Defect fixed: Workspace menu actions late-bound their slot, so **every** menu item ran "Refresh"
(`test_menu_actions_call_their_own_slots`).

### Test evidence
| Where | Command | Result |
|---|---|---|
| CI run 41564516271, ubuntu-latest, Python 3.11.17, PySide6/Qt 6.12.0 | `pytest -q -ra -m qt tests/test_qt_gui.py tests/test_qt_desktop_alpha.py` | 11 passed |
| same | `pytest -q -ra` | 222 passed |
| CI run 41564516271, windows-latest, Python 3.11.9, PySide6/Qt 6.12.0 | Qt tests | 11 passed |
| same | `pytest -q -ra` | 220 passed, 2 skipped (POSIX permission test; case-insensitive FS test) |
| Claude B sandbox, Linux, Python 3.13.18, **no PySide6** (no package index reachable) | `pytest -q -ra` | 210 passed, 3 skipped (2 Qt modules, permission test as root) |
| same, as non-root user | `pytest -q tests/test_desktop_alpha.py` | 20 passed |

Qt tests run offscreen. **The installed GUI has not been launched interactively by anyone yet**; see
manual acceptance below.

### Design notes / residual risks
- **Import** is a GUI adapter over public core calls: the copy is created with `open(..., "xb")`
  (never overwrites) while the writer lock is held, after a fail-closed journal integrity check. The
  copy is removed only when nothing can have been committed (copy failed, or core validation refused
  registration). A crash between copy and commit leaves an unregistered copy in the project folder
  (no data loss; register it with "Register file…"). A core API would close this gap (see A2 below).
- **Health check** reads every journal entry (seq + SHA-256). O(journal size); fine for alpha.
- The GUI holds the writer lock only per operation (unchanged Phase 1 model).

### API dependencies requested from core (Claude A, via PR #7) — GUI works around all of them today
- **A1 Read-only health API**: `Workspace.health()` → integrity status, pending-recovery count,
  reason. GUI currently loops `journal_seqs()` + `read_entry()`.
- **A2 Core import**: `documents.import_document(ws, project_id, source, rel_path, …)` that copies
  via temp + exclusive publish and records intent, so a crash cannot leave an unregistered copy.
- **A3 "Nothing was written" classification**: a marker (e.g. `exc.nothing_written`) or
  `CommitNotAttempted` base, so adapters can safely clean up after a refused operation.
- **A4 `MissingFileError(DocumentError)`**: GUI matches the text "does not exist" today.
- **A5 Public accessors** for what the GUI reaches privately: `documents._require_doc`,
  `documents.lc_mod.allowed_targets`, and `LocalIndex.db` (project file listing).

## Checkpoint 2 — `7125d12` + busy-state commit (2026-11-02)
- **Working-file column** in the document list (OK / Edited / Changed, not checked out / Missing /
  Unavailable) from core `document_status` (size+mtime, hash only when needed); red/amber rows and a
  "need attention" notice. Problems are visible without opening each document.
- **Restore to working file** (while checked out, older revisions only, confirmation first) via core
  `restore_to_working`. History is never rewritten; the next check-in records `restored_from`.
- **Compare with previous** via core `compare_revisions` (metadata only; CAD content is not diffed).
- **Busy state**: while an import/check-in/scan runs, the status bar says so and check-out, check-in,
  cancel and import are disabled; a second job is refused, not queued.
- Evidence for `7125d12`: CI run 41565168800 — ubuntu 3.11.17: Qt 12 passed, full 226 passed;
  windows 3.11.9: Qt 12 passed, full 224 passed, 2 skipped (same two as before).

## Release-candidate work — `e5725df`, `beb1564`, `b4c6d72` (Claude B)
CORE_API compliance, no false success, read-only when recovery-blocked, launch contract
(`docs/GUI_LAUNCH_CONTRACT.md`), F5 keeps the selection.
Evidence for `b4c6d72`: CI run 41567499552 — ubuntu 3.11.17: Qt 20 passed, full 242 passed;
windows 3.11.9: Qt 20 passed, full 240 passed, 2 skipped. Integration accepted this exact commit in its
frozen GUI/CLI matrix (run 41568957858); it has not accepted anything newer.

## Checkpoint 3 — `062baa3`..`17651af` (2026-11-02, takeover from Claude B)
First time the GUI was rendered and looked at (offscreen). Screenshots, Linux offscreen, not an
interactive session: [default size](desktop-alpha/workspace-1200x760.png),
[minimum size](desktop-alpha/workspace-minimum.png).

| Defect on `b4c6d72` | Now |
|---|---|
| Default 1200x760: revision history showed no rows. Minimum 800x520: action buttons and history off screen | Project details moved to a tab; tables keep at least 3 rows; the layout sets its own minimum |
| Selecting a document that failed to load left the previous document's actions enabled while another row was highlighted, so Check out acted on the wrong document | Panel clears, reason shown inline, F5 retries |
| Import or adopt saved with a warning raised `TypeError` out of the GUI poll | Shown as saved with a warning |
| Register saved with a warning showed nothing; project creation saved with a warning looked like a failure and invited a duplicate | Warning shown; the attempt ends |
| Committed check-in, cancel or checkout reported as failed if the local token file could not be updated | Warning; the change is reported as saved |
| Check-in outcome unreadable from the journal looked like an ordinary failure | Says it could not confirm, and to check the history before retrying |
| Filtered-out document or project kept its actions; project list refresh dropped the selection | Cleared when hidden; selection kept otherwise |
| Change state and three menu items enabled while recovery-blocked; project actions enabled during jobs | Disabled |
| Quit, close or switch workspace during a job | Refused with a message |
| Open file did nothing when no application could open the file | Reports it |
| Errors mentioned shares and networks | Local wording (Director tier 1) |
| A long Windows path in a label set the window's minimum width (introduced by `062baa3`, fixed in `06a9d4b`) | Clipped, with the full text in a tooltip |

Regressions: `tests/test_qt_desktop_alpha_rc.py`. Against the `b4c6d72` GUI source, 25 of its first 26
tests failed; the other is a size guard that already passed. The later long-path test failed on `062baa3`
(1728 px).

Windows CI was red on `062baa3`, `06a9d4b` and `0a07b91` (minimum width 1340 px). My first explanation,
long paths, was wrong for that number. The width report showed the cause: the offscreen fallback font on
Windows CI is about 1.9x wider than on Linux at the same size and DPI. The offscreen limit now scales with the
text, and a Windows-only CI step measures on the native platform.

| Where (commit `17651af`) | Command | Result |
|---|---|---|
| CI run 41570211687, ubuntu-latest, Python 3.11.17, PySide6/Qt 6.12.0 | `pytest -q -ra -m qt tests/test_qt_gui.py tests/test_qt_desktop_alpha.py` / `pytest -q -ra` | 20 passed / 269 passed |
| same run, windows-latest, Python 3.11.9, PySide6/Qt 6.12.0 | same | 20 passed / 267 passed, 2 skipped (POSIX-permission test; case-insensitive FS test) |
| same Windows job, `QT_QPA_PLATFORM=windows` | `pytest -q -ra -s -m qt tests/test_qt_desktop_alpha_rc.py -k "minimum_size_fits or long_paths"` | 2 passed; Segoe UI 9 pt, 96 DPI, minimum window 842x666 |
| Local, Linux, Python 3.13, PySide6 6.12.0, as root | `QT_QPA_PLATFORM=offscreen pytest -q -ra` | 268 passed, 1 skipped (POSIX-permission test) |

The first three rows count `17651af`. The local row ran on the same code before the commit.

Known limits: the native step measures the window's size hints; it does not exercise dialogs or a person
using the app. A long unbreakable path is clipped (tooltip and text selection still give the full text).

## Checkpoint 4 — `2a6a7bd`..`d4e3623` (2026-11-02)
Tested against accepted core `fe8a179`, then on a real X display for the first time: the real GUI process on
Xvfb, driven by real keystrokes (`tests/e2e_x11_walkthrough.py`). Outcomes are checked through the CLI and the
files on disk. It is not a person using the app, and says nothing about Windows or native file dialogs.

| Defect | Found by | Now |
|---|---|---|
| A write refused by a recovery block left the GUI fully writable, because the read-only check cannot see every cause | Probe on `fe8a179` | Same read-only state and banner as a block found by the health check; F5 re-checks |
| A lock left by a crash: "try again in a moment", for ever | Probe on `fe8a179` | The error gives the token and the exact command, with the real folder, in selectable text. The walkthrough follows it and recovers |
| A job's result could arrive while it still counted as busy, so the new revision showed with Check out disabled | Integration, run 41572552095 | The slot is freed immediately before `on_done`, so `busy` false means settled |
| Help promised Enter opens the selected row; only a double-click did | Offscreen audit | Enter works in both lists |
| Alt+W, Alt+P and Alt+I each meant two things; several controls had no screen-reader name; paging buttons came last in Tab order | Offscreen audit | Fixed. Check in is now Alt+K, Compare Alt+M, Restore Alt+T |
| Typing a path in Import recorded a document titled with its first letter | Real display | Title follows the file name until you type your own |
| Tab was trapped inside tables, moving between cells | Real display | Tab leaves the table; arrow keys move within it |
| After a restart no document was selected, so Ctrl+K silently did nothing | Real display | Opening a project selects its first document |
| Status bar cut off on a 768 px high screen | Real display | Default size allows for the title bar |
| Successful export showed nothing; a replace confirmed in the save dialog then failed with a disk error; export ran on the GUI thread | Audit | Says where the file went; replace works; runs in the background |
| Export could overwrite a managed working file or write into the workspace metadata | Audit | Refused |

Regressions: `tests/test_qt_desktop_alpha_rc.py`, `tests/test_qt_desktop_alpha_keyboard.py`. The walkthrough fails on
`e5fb7e1` at the wrong document title, so it can detect a real defect.

| Where (commit `d4e3623`, CI run 41575959230) | Command | Result |
|---|---|---|
| ubuntu-latest, Python 3.11.17, PySide6/Qt 6.12.0 | Qt tests (`-m qt`, 3 files) / `pytest -q -ra` | 66 passed / 299 passed |
| windows-latest, Python 3.11.9, PySide6/Qt 6.12.0 | same | 66 passed / 297 passed, 2 skipped (POSIX-permission test; case-insensitive FS test) |
| same Windows job, `QT_QPA_PLATFORM=windows` | window-size checks | 2 passed; Segoe UI, minimum window 842x666 |
| GUI merged with accepted core `fe8a179`, on the runner only: ubuntu-latest | `pytest -q -ra` | 353 passed |
| same: windows-latest | `pytest -q -ra` | 351 passed, 2 skipped (same two) |
| ubuntu-latest, Python 3.11, xcb on Xvfb, openbox | `xvfb-run ... python tests/e2e_x11_walkthrough.py` | all 9 steps passed; screenshots kept as a CI artifact |

Also run locally, not accepted evidence: the same code merged with `phase2-documents` `22a5273` (newer than the accepted
core): 381 passed, 3 skipped, and the walkthrough passed (Linux, Python 3.13, as root).

Known limit: while a recovery block is known only from a refused write, F5 clears the banner until the next write finds it
again and fails closed. A read-only `Workspace.health()` (request A1) would close this.

## Manual acceptance (installed GUI) — not yet performed
1. `python -m pip install -e ".[gui]"` then `revisionworx-gui` (Windows: Start → run from venv).
2. Start page: confirm the amber alpha banner. Create project "Test Bracket", number "T-1".
3. Documents tab → **Import file…** → pick any file → Import. Confirm revision A/1 in history.
4. **Check out** → **Open file** → edit/save → **Check in…** with a description → 2 revisions.
5. Quit, relaunch: the workspace reopens; history still shows both revisions.
6. Delete the working file in Explorer/Files → select the document: red "missing" status;
   Check in shows a "Missing file" message with the recovery hint.
7. Record OS, Python and Qt versions and any deviations on Issue #13.

## Next
- Screen-reader check and a keyboard walkthrough on Windows (needs an interactive session; the Linux one is automated).
- A8: `verify` on a read-only handle reports unapplied journal entries as "missing", so a normal administrator clear
  shows an inconsistency notice until the next write.
- Visual redesign: mockup with the Director; no UI code changes until it is reviewed.
- Byte-level progress for very large files needs a core progress callback on `store_file` (A7).
- A6 (requested in `controller.checkout`): return the checkout token with `CommittedNotApplied`, so a saved
  checkout does not need an administrator to release it.
- Packaging coordination with PR #2 (Windows/Linux offline bundles).
