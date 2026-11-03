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
- Keyboard-only walkthrough and tab order; accessible names on remaining controls.
- Document list status column (missing/modified-externally) without opening each document.
- Restore-earlier-revision-to-working (core `restore_to_working` exists) and compare revisions.
- Long operations: progress for large imports/check-ins; cancel where safe.
- Packaging coordination with PR #2 (Windows/Linux offline bundles).
