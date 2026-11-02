# RevisionWorx — Architecture

**Status:** Phase 1 foundation **implemented and tested** on Linux (local filesystem) in branch
`phase1-foundation`, hardened after the 2026-11-01 reliability review (ADR-008); GUI migrated to
PySide6 (ADR-009, Qt widgets not yet executed). Phase 2 lives on `phase2-documents`. Sections are marked **IMPLEMENTED** (code + automated tests exist),
**PROPOSED** (designed, not built) or **OPEN** (decision pending). Keep this file in step with the code.

## System map

```text
Desktop GUI (PySide6 widgets, gui/qt/) ─> gui/viewmodels.py (toolkit-free view state, validation)
                                              │
CLI (argparse) ──────────────────────────────┼─> gui/controller.py (toolkit-free, background jobs, short write locks)
                              │
                              └─> application services: projects.py (roots, templates, create, adopt)
                                    ├─> scan.py       bounded read-only scan of a user-selected folder
                                    ├─> filetypes.py  recognition + verified OpenSchematix content sniff
                                    ├─> index.py      local SQLite cache (disposable, never authoritative)
                                    └─> workspace.py  authoritative .revisionworx store
                                          └─> atomicio.py  atomic replace / exclusive publish
                                                │
                                                v
   Configured authoritative .revisionworx (local disk or share)
     workspace.json / journal/ / records/ / blobs/ / state.json / locks/
```

Unmanaged historical projects remain outside routine indexing. Registered projects retain their
native directory structures. Planned later layers (revisions, checkouts, parts, dependency graph,
CAD/OpenSchematix adapters) are **PROPOSED** and sit behind the same services/workspace boundary.

## Architecture decision records

### ADR-001 Language, runtime and GUI toolkit — IMPLEMENTED (GUI toolkit superseded by ADR-009)
**Decision:** Python ≥ 3.10, standard library only for the core; ~~Tkinter/ttk for the desktop GUI~~
PySide6 for the desktop GUI (ADR-009).

Rationale (compared with PySide6/Qt, Electron/Tauri, C#/Avalonia):
- **OpenSchematix is pure-stdlib Python + Tkinter** (verified in its `pyproject.toml`, v1.1.0). Same
  language and packaging pipeline (PyInstaller + Windows installer + AppImage) lets RevisionWorx reuse
  proven release tooling and, later, read OpenSchematix data with the same parsing conventions
  without depending on its source.
- Zero runtime dependencies: no licence-compatibility risk (GPL-3.0), offline installs, nothing to
  phone home, trivially auditable.
- `sqlite3` with FTS5 ships with CPython (verified: SQLite 3.53.1 with FTS5 in the dev environment).
- Core is GUI-independent (`gui/controller.py` has no toolkit import). If Tk's table performance
  proves inadequate, a PySide6 (LGPL-3.0, GPL-compatible) view can replace `gui/app.py` without
  touching the core. Large tables are paginated from SQLite rather than loaded into the widget.

Costs/risks: Tk looks dated next to Qt; Treeview has no true virtual model (mitigated by paging);
Python start-up is slower than native code.

### ADR-002 Identity and paths — IMPLEMENTED
- Stable IDs: `<prefix>_<uuid4 hex>` (`ws_`, `prj_`, `root_`, `blb_`, `aud_`), independent of paths
  and of company project numbers.
- A project location is `(root_id, relative POSIX path)`. A root stores one absolute path per OS
  family (`posix`, `windows`), so mixed Windows/Linux clients agree on identity.
- Relative paths are validated against **both** platforms' rules (reserved device names, forbidden
  characters, trailing dot/space, `..`, absolute/drive paths), normalised to Unicode NFC, and compared
  case-insensitively (`casefold`) for overlap/collision detection. Symlink/junction escapes out of a
  root are refused (`os.path.realpath` containment).

### ADR-003 Opt-in adoption, no traversal — IMPLEMENTED
- Nothing enumerates a root. Creating a project performs an exclusive `mkdir` of the target and
  never lists siblings. Opening, syncing and searching read only `.revisionworx` and the local cache.
  Acceptance test: 50,000 unmanaged folders under a root, `os.scandir/listdir/walk` instrumented,
  zero calls into the root (with a positive control proving the instrumentation works).
- **Adopt Existing Project** = read-only preview (no hashing, no writes anywhere) → explicit adopt.
  Adoption scans only the chosen folder: does not follow symlinks/junctions, honours exclusion
  patterns and a file limit (refuses to register a truncated scope), supports cancellation and
  progress, and reports unreadable items, non-portable names and case collisions. It records an
  initial **baseline** (path, size, mtime, SHA-256, verified format) as an immutable blob and
  explicitly asserts no history. Verified non-destructive across three unrelated directory
  conventions (snapshot of every path, size, mtime and content hash before/after).
- Registration that overlaps an existing project (same, parent or child folder) is refused.
- Known limitation: reading files for hashing can update access times on filesystems mounted
  without `noatime`/`relatime`; modification times and content are untouched.

### ADR-004 Authoritative store and commit protocol — IMPLEMENTED (single writer)
Layout of `.revisionworx/` (format version 1):

| Path | Purpose |
| --- | --- |
| `workspace.json` | identity + `format_version`; written once with exclusive create |
| `journal/<seq:020d>.json` | ordered immutable change entries — the source of truth |
| `records/<kind>/<id>.json` | materialised current records (derivable from the journal) |
| `blobs/<kind>/<id>.json` | large immutable payloads (adoption baselines) |
| `state.json` | `applied_seq` materialisation checkpoint |
| `locks/writer.lock` | single-writer lease (holder user/host/pid/time/token) |

Commit: (1) hold writer lock; (2) write blob under a fresh unique name; (3) **publish journal entry
`seq = head+1` with exclusive create — the commit point**; (4) atomically rewrite affected records
(idempotent puts); (5) advance `state.json`.

Failure modes, each covered by a test:
- Crash before (3): no entry, nothing visible; orphan blob/temp files are harmless.
- Crash between (3) and (5): readers overlay unapplied entries in memory; next writable open replays.
- Journal gap, unreadable entry, entry/filename seq mismatch, `applied_seq > head`:
  `WorkspaceCorruptError`, writes refused (fail closed).
- Another writer published the same seq: fatal error, never silently merged.
- Newer `format_version`: refused before anything is written.

Primitives (`atomicio.py`): `atomic_write_bytes` = temp file + fsync + `os.replace` + directory
fsync (POSIX). `publish_exclusive` = fully written, fsynced temp then `link()` (POSIX, fails if the
name exists) or `rename()` (Windows, refuses an existing destination). Exclusivity verified locally
with 8 competing processes.

Orphan temp cleanup only removes temp files older than one hour. *Regression:* the first
implementation deleted all temp files on recovery and broke concurrent lock acquisition by other
processes (caught by `test_writer_lock_race_between_processes`).

### ADR-005 Writer lock and administrative recovery — IMPLEMENTED (single writer)
- One writer at a time per workspace; readers never block. The GUI/CLI hold the lock only for the
  duration of one operation.
- Close releases the lock only if it still holds the same token.
- A stale lock is **never** stolen automatically. `lock break` requires the exact token currently
  displayed and a reason; the old lock file is kept as evidence and the break is journaled as an
  audit record.
- Verified: 6 processes racing to open writable → exactly one acquires.

### ADR-006 Local index — IMPLEMENTED
- Per-workspace SQLite file in the user's local cache dir (`$XDG_CACHE_HOME/revisionworx` or
  `%LOCALAPPDATA%\RevisionWorx\cache`); never on the share; WAL mode.
- Stores the journal sequence it reflects. `sync()` applies entries after the cursor in order;
  rebuilds when a gap is detected or the cache is ahead of the workspace (restored backup). A cache
  that is corrupt, from another schema version or another workspace is deleted and rebuilt.
- B-tree indexes for exact lookups (number, customer, name, file category, SHA-256); FTS5 for
  project metadata and file paths (path separators tokenised). User text is converted to quoted
  prefix terms so FTS operators cannot be injected. Paging via LIMIT/OFFSET.
- `status()` reports `authoritative: false` and staleness; `coverage()` states that only registered
  projects are covered.
- Nothing may use the index to authorise a write (it has no write paths into the workspace).

### ADR-008 Store hardening from the Phase 1 reliability review — IMPLEMENTED
Five defects were reproduced on `23d6ecf` before fixing; regressions in `tests/test_reliability_review.py`.

| # | Defect | Fix |
|---|---|---|
| R1 | A stale client silently overwrote a newer change (lost update). | Every materialised record carries `_rev` (the seq that last wrote it). `RecordPut`/`RecordDelete` take `expect_rev` (`0` = must not exist). Expectations are checked under the writer lock before publication; any failure rejects the whole entry with `ConflictError` and nothing is written. Services and the GUI pass the revision the user was shown. |
| R2 | A writer whose lock was administratively broken kept committing. | Before publishing, the writer re-reads `locks/writer.lock`. If its token is gone it raises `LockLostError` and becomes read-only. Every entry records `writer_token`. |
| R3 | A failure after the commit point looked identical to "not committed". | Failures after publication raise `CommittedNotApplied(seq, txid)`. Callers may pass their own `txid` and query `transaction_outcome(txid)`. This is the prepare/publish/apply boundary requested in issue #1. |
| R4 | A journal entry altered into different valid JSON was accepted. | Each entry stores a SHA-256 `digest` of its canonical JSON, checked on every read. Older digest-less entries are reported by `verify`. |
| R5 | Damaged materialised records could not be detected or repaired. | `Workspace.verify(repair=False)` replays the whole journal and reports `mismatch`, `missing`, `unreadable`, `not-in-journal`, `deleted-but-present` records, `missing-blob` references and entries without digests. `repair=True` rewrites records from the journal, moves unexplained files to `quarantine/` (never deletes them) and journals an audit record. Exposed as `revisionworx verify [--repair]`. |

Journaled record deletes were added (`RecordDelete`), replayed by recovery and overlaid for readers.

**Residual risk (not solved):** the lock re-check and the journal publish are separate filesystem
operations. A lock broken in that window still allows one entry, and the exclusive sequence
publication only prevents it from colliding. True fencing needs storage that validates a token
atomically with the write, which a plain file share does not provide. Phase 3 must close this
(e.g. epoch-fenced journal directories, or the optional LAN coordinator) and test it across machines.

### ADR-009 Desktop GUI toolkit: PySide6 / Qt 6 — IMPLEMENTED, NOT EXECUTED
**Decision (project director, 2026-11-01):** replace the Tkinter view with PySide6. The Tk view was
removed rather than kept in parallel, to avoid two GUIs.

Structure (Model-View with a controller):
- `gui/controller.py`: toolkit-free application controller (unchanged role). Runs background jobs
  and keeps the writer lock only per action.
- `gui/viewmodels.py`: toolkit-free view state. Covers project list paging and search, project
  details, new-project form validation, the adopt workflow state machine
  (idle → previewing → previewed → adopting → adopted/error), and report text. All GUI decisions
  live here; tested headless in `tests/test_viewmodels.py`.
- `gui/qt/`: `ViewModelTable` (`QAbstractTableModel` adapter), dialogs and `MainWindow`. They bind
  widgets and forward events only. A `QTimer` drains background results on the GUI thread, so the
  UI never blocks on network I/O.
- `gui/app.py`: entry point. Without PySide6 it prints install instructions and exits with code 3.
  The CLI never needs Qt.

Dependency, licensing and packaging:
- Optional extra `revisionworx[gui]` → `PySide6-Essentials>=6.5`. Only QtCore/QtGui/QtWidgets are
  used; the core and CLI stay dependency-free.
- PySide6 is available under LGPL-3.0 (also GPL-2/3 and commercial). RevisionWorx is
  GPL-3.0-or-later, which is compatible. Binary distributions must ship the Qt/PySide6 LGPL notices
  and source offer, and keep Qt as replaceable shared libraries. PyInstaller's default one-folder
  build does this; do not statically link Qt.
- Size: a PyInstaller build with PySide6-Essentials is expected to be tens of MB larger than the Tk
  build. This has **not** been measured yet.
- Linux runtime needs the Qt xcb platform plugin's system libraries (e.g. `libxcb-cursor0` for
  Qt ≥ 6.5) or Wayland. The AppImage must bundle or declare them. Windows needs no extra system
  packages.

Verification status:
- The PySide6 widgets have **never been executed**. PyPI is blocked by this session's network policy.
- `tests/test_qt_gui.py` runs offscreen automatically wherever PySide6 is installed; here it is
  reported as SKIPPED.
- A stub-module smoke run executed the Qt layer's Python code paths. It found one real issue: slots
  connected to signals that carry arguments. All connections are now wrapped explicitly. That smoke
  run does not validate Qt API usage.

### ADR-007 OpenSchematix recognition — IMPLEMENTED (recognition only)
Verified against OpenSchematix 1.1.0 `docs/FILE_FORMAT.md` and real sample files (copied into
`tests/fixtures/openschematix/`): `.schematix` = ZIP whose first member `mimetype` contains
`application/x-openschematix-project`; legacy `.osch` = JSON with `"format": "OpenSchematix"`.
Files passing the content check are tagged `verified_format: openschematix`; a wrongly named file is
not. BOM import, component identities and revision tracking are **PROPOSED** (Phase 5). The format
documents `components[].props.part_number/manufacturer` and an `in_bom` flag per component, which
the Phase 5 adapter should use. OpenSchematix source is not modified.

## Shared filesystem coordination — OPEN design gate (Phase 3)

Phase 1 workspaces run in **single-writer** mode (`settings.collaboration_mode = "single-writer"`).
The protocol above relies on exclusive `link()`/`rename()`, `os.replace` atomicity and fsync, which
are verified only on a local Linux filesystem. They are **not** verified on SMB or NFS. Before any
claim of safe concurrent multi-user writes:
1. Define the supported filesystem and share configuration (SMB 3.x on Windows Server/Samba, NFSv4).
2. Verify exclusive publish semantics across machines (link/rename races, oplock/lease caching,
   attribute caching on NFS `ac` options).
3. Verify journal publication ordering and durability (fsync honoured by the server; close-to-open).
4. Lock lease expiry and fencing tokens so a paused client cannot write after its lock was broken.
5. Multi-machine fault-injection tests: cable pull, server reboot, disk full, permission revocation.
6. If guarantees cannot be established, fail closed for writes and offer the optional on-premise LAN
   coordinator (contingency, not a cloud service).

Known gap (narrowed by ADR-008, not closed): a writer whose lock is broken *between* its lock re-check
and its journal publish can still publish one entry. Fencing is part of Phase 3 and is tracked with
the QA/concurrency agents in GitHub issue #1.

## Core data entities

| Entity | State |
| --- | --- |
| Workspace (roots, settings, format version) | IMPLEMENTED |
| Root (name, per-OS paths) | IMPLEMENTED |
| Project (id, number, customer, mode indexed/managed, status, origin, baseline) | IMPLEMENTED |
| Template (folders, small text starter files) | IMPLEMENTED |
| Baseline (immutable inventory blob) | IMPLEMENTED |
| AuditEvent (lock break) | IMPLEMENTED (general audit trail PROPOSED) |
| Document, Revision, Checkout, Part, Assembly, BOM, DependencyEdge, ChangeRequest | PROPOSED |

Distinguish a part *identity* from a document file and from a specific revision. No path-based identity.

## Search and graph design
- Exact B-tree lookups and FTS5: IMPLEMENTED for projects and baseline files.
- Dependency graph (forward/reverse edges, revision-specific references, cycle-safe traversal,
  coverage and verification states): PROPOSED (Phase 4).
- Journal compaction/segmentation for very long journals: PROPOSED. Phase 1 lists the journal
  directory on each sync, which is adequate for thousands of entries, not millions.

## CAD compatibility
Recognition by extension only (`capability: recognized-only`). No Inventor/SolidWorks reference
extraction exists; previews and baselines say so. Adapters must expose provenance (inspected,
API-extracted, imported BOM, declared, inferred, unknown). A filename match is never a verified
dependency. Rename/move/duplicate must preview and refuse unsafe reference edits. All PROPOSED.

## Implementation decisions still pending
- Shared filesystem support and verified multi-writer protocol (Phase 3 gate).
- Revision blob storage, deduplication, retention, quotas and backup consistency (Phase 2/7).
- Metadata migration policy beyond "refuse newer format".
- Supported Inventor/SolidWorks adapters and verified capabilities.
- Access-control enforcement model on shared drives.
- Packaging (PyInstaller/AppImage/Windows installer) and the CI test matrix (Windows not yet tested).
