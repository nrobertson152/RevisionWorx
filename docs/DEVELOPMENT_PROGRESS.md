# RevisionWorx — Development Progress

**Updated:** 2026-11-01
**Branch:** `phase1-foundation` (not merged, not tagged, no release published — awaiting director approval)
**Status:** Phase 1 foundation implemented with automated tests on Linux. No release exists.

## How to resume
1. `git checkout phase1-foundation` and read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md` (ADRs).
2. `python -m pytest -q` — expect **95 passed, 1 skipped** (the skip is the live Tk window test).
3. Continue with "Next concrete tasks" below. Record every session in the log at the bottom.

## Phase tracking
| Phase | Status |
| --- | --- |
| 1. Foundation, workspace configuration, registration, index | **Implemented, tested on Linux; GUI not run-verified; Windows untested** |
| 2. Drawing/revisions/checkouts | Not started |
| 3. Verified multi-user collaboration | Not started (design gate documented in ARCHITECTURE.md) |
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
| GUI controller | `gui/controller.py` | Toolkit-free; background jobs with progress/cancel; errors delivered to UI; writer lock held per action only |
| GUI view | `gui/app.py` | Project table with search/paging, details + baseline files, new project and adopt dialogs, roots, rebuild index. **Never executed** — Tkinter unavailable in the dev sandbox. Only syntax/entry point are checked. |

## Session log

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
- No lock fencing: a writer paused past an administrative `lock break` could still publish one entry.
- **Windows not tested at all** (Windows-specific `rename`-based exclusive publish is untested code).
- GUI has never been launched; expect fixes when first run with Tk.
- Journal is listed in full on each sync/read — fine for thousands of entries, not millions; compaction
  is proposed, not implemented.
- Hashing during adoption may update file access times (content and mtimes are untouched).
- Baselines record what existed; they are not revisions and are not used for change detection yet.
- No benchmarks have been run. `test_bulk_registration_and_lookup_latency_smoke` (2,000 projects,
  generous 1 s bound) is a sanity check, not evidence for the <50 ms / 10,000-project targets.
- No packaging/installers exist.

## Next concrete tasks
1. Run the GUI on Linux and Windows with Tk; fix layout/behaviour; run the `gui`-marked test under Xvfb.
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
