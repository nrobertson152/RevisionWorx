# RevisionWorx — Architecture

**Status:** Phase 1 foundation **implemented and tested** on Linux (local filesystem) in branch
`phase1-foundation`. Sections are marked **IMPLEMENTED** (code + automated tests exist),
**PROPOSED** (designed, not built) or **OPEN** (decision pending). Keep this file in step with the code.

## System map

```text
Desktop GUI (Tkinter view)  ──┐
CLI (argparse)              ──┼─> gui/controller.py (toolkit-free, background jobs, short write locks)
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

### ADR-001 Language, runtime and GUI toolkit — IMPLEMENTED
**Decision:** Python ≥ 3.10, standard library only for the core; Tkinter/ttk for the desktop GUI.

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

Known Phase 1 gap: a writer whose lock was broken by an administrator while it was paused can still
publish one more journal entry; exclusive seq publication prevents corruption but not that write.
Fencing is part of Phase 3.

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
