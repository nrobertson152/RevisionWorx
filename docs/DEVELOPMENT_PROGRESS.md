# RevisionWorx — Development Progress

**Updated:** 2026-11-01 (second session: Phase 1 review, PySide6 migration)
**Branch:** `phase1-foundation` (not merged, not tagged, no release published — awaiting director approval).
Phase 2 work is on `phase2-documents`, stacked on this branch.
**Status:** Phase 1 foundation implemented, reviewed and hardened, with automated tests on Linux.
The GUI was migrated to PySide6, but the Qt widgets have not been executed. No release exists.

## How to resume
1. `git checkout phase1-foundation` and read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md` (ADRs).
2. `python -m pytest -q`: on this branch expect **118 passed, 1 skipped**. The skip is
   `tests/test_qt_gui.py`, which needs PySide6. With PySide6 installed, run
   `QT_QPA_PLATFORM=offscreen python -m pytest` and record the Qt results; they have never been run.
3. Continue with "Next concrete tasks" below. Record every session in the log at the bottom.

## Phase tracking
| Phase | Status |
| --- | --- |
| 1. Foundation, workspace configuration, registration, index | **Implemented, reviewed, tested on Linux. Qt GUI not executed; Windows untested** |
| 2. Drawing/revisions/checkouts | **In progress on `phase2-documents`** (see that branch) |
| 3. Verified multi-user collaboration | Not started. Design gate in ARCHITECTURE.md; coordinated in issue #1 (Agent D) |
| 4. Parts, dependencies, Where Used, Before You Revise | Not started |
| 5. OpenSchematix and BOM integration | Not started (file format inspected; content recognition only) |
| 6. Native CAD integrations | Not started (extension recognition only) |
| 7. Backup, change management and reporting | Not started |
| 8. Benchmarks, regression and packaging | Not started |

## Phase 1 — what exists (verified by tests)
| Area | Module | Notes |
| --- | --- | --- |
| Stack decision | — | Python ≥3.10 stdlib-only core, Tkinter GUI (ADR-001) |
| Atomic/exclusive file primitives | `atomicio.py` | 8-process publish race: exactly one winner, content intact |
| Cross-platform path rules | `paths.py` | Windows reserved names/chars, `..`, absolute, NFC, case-folding, symlink escape |
| Authoritative store | `workspace.py` | Journal-first commits, crash replay, reader overlay, corruption → fail closed, format version gate |
| Writer lock | `workspace.py` | 6-process race: one writer; token-checked release; audited, evidence-keeping `lock break` |
| Roots | `projects.py` | Per-OS paths; duplicate path/name refused; missing OS path explained |
| Templates | `projects.py` | Validated folders + text starter files; JSON import/export; built-in "standard" |
| New project | `projects.py` | Exclusive mkdir (never merges into an existing folder); number duplicate detection; overlap refusal |
| Adopt existing project | `projects.py`, `scan.py` | Read-only preview; baseline with SHA-256; exclusions, limit, cancel, no link following, case-collision and portability warnings; never fabricates history |
| Archive/restore/metadata edit | `projects.py` | Journaled |
| Local index | `index.py` | SQLite/FTS5 in local cache dir; incremental sync, gap/ahead/corrupt/foreign-cache → rebuild; injection-safe FTS; paging; coverage statement |
| OpenSchematix recognition | `filetypes.py` | Content-verified with real upstream samples; misnamed files rejected |
| CLI | `cli.py` | init, status, root, template, project new/preview/adopt/list/set, files search, index rebuild, lock show/break; `--json` |
| Conflict detection | `workspace.py` | `_rev` + `expect_rev` (ADR-008 R1); `RecordDelete` journaled |
| Lock re-validation | `workspace.py` | Token re-read before every publish; `writer_token` in entries (R2) |
| Durable outcome | `workspace.py` | `CommittedNotApplied`, `transaction_outcome(txid)` (R3) |
| Entry integrity | `workspace.py` | SHA-256 digest per journal entry (R4) |
| Verify/repair | `workspace.py`, `cli.py` | Journal replay vs records; quarantine, audited repair; `revisionworx verify` (R5) |
| GUI controller | `gui/controller.py` | Toolkit-free; background jobs with progress/cancel; errors delivered to UI; writer lock held per action only |
| GUI view-models | `gui/viewmodels.py` | Toolkit-free list/details/new-project form/adopt state machine — **tested headless** |
| GUI view (PySide6) | `gui/qt/` | Main window, table models, dialogs. **Never executed**: PyPI is blocked in the dev sandbox. Stub-module smoke only (see log). |

## Session log

### 2026-11-01 (session 2) — Phase 1 review, store hardening, PySide6 migration
- **Directive:** the project director's Phase 1 review / Phase 2 directive. GUI must use PySide6.
- **Commits on `phase1-foundation`:**
  - `08ba872` store hardening (ADR-008)
  - `9ba86b1` PySide6 migration (ADR-009)
  - `8755867` index delete fix
  - this documentation commit
- **Environment:** same as session 1. **PyPI, files.pythonhosted.org, download.qt.io and apt
  mirrors are refused by the session egress policy**, so neither PySide6 nor Tk could be installed.
- **Tests executed:** `python -m pytest -q` gives **118 passed, 1 skipped** (`tests/test_qt_gui.py`:
  no PySide6). The suite was re-run from a clean state at the start of the session (95 passed,
  1 skipped on `23d6ecf`) rather than trusting the earlier report.
- **Reliability review against the directive's seven properties:**
  1. Files untouched during registration/indexing: already covered (snapshot tests). Holds.
  2. No unauthorised recursive scans: already covered (50k-folder instrumented test). Holds.
  3. Corrupt caches rebuilt: already covered. Holds.
  4. Journal corruption cannot silently produce inconsistent state: **gap R4** (altered entry
     accepted) and **gap R5** (damaged records undetectable). Fixed.
  5. Interrupted operations leave no falsely committed records: held. **Gap R3**: a failure after
     the commit point looked like a failure. Fixed (`CommittedNotApplied`, `transaction_outcome`).
  6. Concurrent writers cannot bypass the lock: **gap R2** (a broken-lock writer kept committing).
     Narrowed (token re-check); **not closed** (TOCTOU window, Phase 3 fencing).
  7. Stale clients cannot overwrite without detection: **gap R1** (lost update). Fixed (`expect_rev`).
- **Defects reproduced on the old code before fixing:** R1, R2, R4 by script (stale update
  silently applied; broken-lock writer committed entry 7; altered entry accepted). R3 and R5 by the
  new failing tests. Plus `KeyError: 'data'` in index sync on journaled deletes (found while
  reviewing my own R-series change). All have regression tests.
- **Qt verification performed:** a scratch stub-module smoke (fake PySide6 classes) drove
  `MainWindow` and the dialogs through their Python code paths. It found that slots were connected
  to signals carrying arguments they did not accept; all connections are now wrapped explicitly.
  This is **not** Qt verification and is not committed as a test.
- **Coordination:**
  - Issue #1 (QA, Agent B): requirements 1 (commit boundary + outcome query) and 2 (fencing, partial)
    addressed in ADR-008. Requirement 3 (checkout semantics) is answered in Phase 2 ADR-013.
    Requirements 4–6 are not yet started.
  - PR #2 (OpenSchematix adapter, Agent C): its 6 tests were executed (all pass) and it was run
    against the real upstream samples. Sheet and component counts match upstream; BOM output
    diverges from `bom.generate` (5 vs 16, 0 vs 5, 0 vs 7). Results posted on the PR. Not merged.
  - No findings from Agent D yet. The `qa/testing-infrastructure` branch had no commits beyond
    `23d6ecf`.

### 2026-11-01 — Phase 1 foundation
- **Commit:** `34e254c` on `phase1-foundation` (base `0c9cc6b`).
- **Environment:** Linux, Python 3.13.18, SQLite 3.53.1 (FTS5), pytest 9.1.1. No network access to
  PyPI/apt, no Tkinter. CLI smoke-tested also under Python 3.11 (pytest not available there).
- **Inspected:** OpenSchematix repo at `74e4c79` (v1.1.0): pure-stdlib Python + Tkinter; `.schematix`
  ZIP and legacy `.osch` JSON formats per its `docs/FILE_FORMAT.md`. Not modified.
- **Tests executed:** `python -m pytest -q` → 95 passed, 1 skipped (`test_gui_starts_and_lists_projects`:
  no tkinter). Lock and publish race tests re-run 3× each, all passing.
- **Acceptance tests from REQUIREMENTS §13 covered in this phase:**
  - Non-destructive adoption across three unrelated directory conventions (indexed and managed modes)
    — full path/size/mtime/SHA-256 snapshot identical before and after.
  - No startup traversal of 50,000 unmanaged directories — instrumented `scandir/listdir/walk`, zero
    calls into the root, with a positive control.
  - Crash recovery at the two commit crash points, disk-full before commit, incomplete/corrupt
    journal, journal gaps.
- **Defects found and fixed during the session (each has a regression test):**
  1. A corrupt local index file crashed `open_index` instead of being rebuilt
     (`test_deleted_or_corrupt_cache_is_rebuilt`).
  2. Recovery deleted *all* temp files, including in-flight temp files of other processes racing for
     the writer lock, making their acquisition fail with `FileNotFoundError`. Now only temp files
     older than one hour are removed (`test_writer_lock_race_between_processes`,
     `test_cleanup_temp_files`).

### Known limitations / data-integrity risks (Phase 1)
- **Single writer only.** Locking and journal semantics are verified on a local Linux filesystem only.
  Do not run concurrent writers against an SMB/NFS share; behaviour there is unverified (Phase 3 gate).
- Fencing is incomplete. A lock broken between a writer's token re-check and its journal publish
  still lets that one entry through (ADR-008 residual risk).
- **Windows not tested at all** (Windows-specific `rename`-based exclusive publish is untested code).
- The PySide6 GUI has never been launched. Expect fixes when it is first run with Qt.
- Journal entries written before ADR-008 (none exist outside test data) have no digest. `verify`
  reports them.
- Journal is listed in full on each sync/read — fine for thousands of entries, not millions; compaction
  is proposed, not implemented.
- Hashing during adoption may update file access times (content and mtimes are untouched).
- Baselines record what existed; they are not revisions and are not used for change detection yet.
- No benchmarks have been run. `test_bulk_registration_and_lookup_latency_smoke` (2,000 projects,
  generous 1 s bound) is a sanity check, not evidence for the <50 ms / 10,000-project targets.
- No packaging/installers exist.

## Next concrete tasks
1. Install PySide6 (allow pypi.org for the session, or run locally) and run `tests/test_qt_gui.py`.
   Launch the window on Linux and Windows; fix whatever the stub smoke could not catch.
2. Run the full suite on Windows (NTFS) to verify the `rename`-based exclusive publish and path rules.
3. Add CI (GitHub Actions: Linux + Windows, Python 3.10 and 3.13) — needs director approval for workflow files.
4. Phase 2 design (write into ARCHITECTURE.md before code): Document entity, content-addressed
   immutable revision blobs with SHA-256, revision label schemes, drawing register fields, lifecycle
   states and audited transitions.
5. Phase 2: checkout/check-in protocol on top of the journal (single-writer first), unexpected-edit
   detection against baselines/revisions, administrative checkout recovery.
6. Begin the Phase 3 investigation plan: an SMB/NFS test rig (Samba + NFSv4 containers or two VMs) to
   exercise exclusive publish, fsync and lease behaviour across clients.

## Persistent warnings
- Never conflate a local cache with authoritative lock ownership.
- Do not claim old unmanaged projects have been indexed or included in where-used coverage.
- Do not claim Inventor/SolidWorks integration or OpenSchematix compatibility without real parser/API tests.
- Never invent success or mark a phase completed based solely on documentation.
