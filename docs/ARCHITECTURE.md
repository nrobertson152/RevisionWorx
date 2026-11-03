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

## Phase 2 — documents, revisions, lifecycle, checkout (design, ADR-010 … ADR-013)

Designed before implementation, as `CLAUDE.md` requires for shared-data protocols
(design commit precedes the code commits on `phase2-documents`). Status markers reflect what was
built and tested.

Implementation notes beyond the original design:
- `RecordCheck` (workspace) asserts a record is still at a revision without writing it. Checkout uses
  it on the document, so a checkout does not bump the document's `_rev` and does not make other
  users' views stale.
- Registration refuses symbolic links *before* resolving the path. Otherwise the link's target
  would be registered instead of the path the user chose (found by test).
- Duplicate detection runs before the existence check, so a case-variant path is refused even when
  it exists as a distinct file on a case-sensitive filesystem (found by test).
- Drawing register roles: **checker** = actor who approved the latest revision; **approver** =
  actor who released it. Document owner is a free-text field.
- Local index schema v2 adds `documents` (+FTS), `revisions` and `checkouts`. An older cache is
  discarded and rebuilt; it holds no authoritative data.
- Scaling: a document's history (and therefore check-in and label checks) walks its
  `previous_rev_id` chain in O(its revisions), and cross-checks the chain against `rev_count`.
  Whole-workspace scans remain only in admin operations (`verify_revisions`, `doc gc`,
  `save_lifecycle`) and in `drawing_register`, which lists documents.

### ADR-010 Document registry — IMPLEMENTED (`documents.py`, tests `tests/test_documents.py`)
- New record kinds `doctype`, `document`, `revision`, `checkout`, `lifecycle` (registered via
  `workspace.register_kind`, so Phase 1 code is untouched).
- **Document type** `{id, name, extensions[], revision_scheme, category}`. Built-in defaults:
  `drawing`, `cad-part`, `cad-assembly`, `schematic`, `pdf`, `manufacturing`, `general`. They can
  be overridden or extended per workspace. The type is suggested from the file extension and the
  user can change it.
- **Document** `{id doc_…, project_id, rel_path, type_id, number, title, description, owner,
  revision_scheme, latest_rev_id, released_rev_id, rev_count, status, created, created_by}`.
  - `rel_path` is relative to the project folder, POSIX/NFC, validated for both OSes and unique
    case-insensitively within the project. **The file stays where it is**: no move, rename, copy
    into a vault or rewrite. This preserves native Inventor/SolidWorks reference paths.
  - Documents can only be registered in **managed** projects. Indexed projects stay read-only
    references.
  - The company document number is optional and unique case-insensitively across the workspace,
    unless explicitly overridden.
- Registration may capture the file's current content as the first revision in the **same journal
  entry**. That is an explicit snapshot of present content, not invented history.

### ADR-011 Immutable revisions and content store — IMPLEMENTED (`content.py`, `revlabels.py`; orphan GC implemented: `documents.gc_content`, CLI `doc gc`)
- Content store: `.revisionworx/content/sha256/<aa>/<64-hex>`, holding the raw bytes and named by
  their SHA-256.
  - Written by streaming the source once into a temp file while hashing, fsynced, then
    `publish_exclusive`.
  - If the name already exists (deduplication), the existing object is re-hashed. A mismatch is
    `WorkspaceCorruptError`, never silently accepted.
- The source file's size and mtime are compared before and after the copy. A file that changes
  during the copy aborts the check-in.
- **Revision** `{id rev_…, document_id, number (1…n), label, state, sha256, size, source_mtime_ns,
  source_rel_path, description, author, created, previous_rev_id, transitions[]}`.
  - Every field except `state` and `transitions` is immutable. The service layer rejects any
    attempt to change them, and `verify_revision` re-hashes stored content on demand.
- **Publication order:** content object is durable → journal entry → records. A revision is never
  visible before its content is durable. A crash before the entry leaves an unreferenced content
  object, which is harmless. Garbage collection is PROPOSED.
- Labels come from the document's scheme:
  - `alpha` (A…Z, AA…), skipping I, O, Q, S, X, Z as ASME Y14.35 recommends;
  - `numeric` (1, 2, …);
  - `numeric2` (01, 02, …);
  - `manual` (label required).
  Labels are unique per document, case-insensitively.
- Retrieval: `export_revision` writes to a new path only (never overwrites) and verifies the hash.
- Restoring an old revision **creates a new revision**. While checked out, the old content is
  written to the working file (atomic replace, audited); the next check-in records it, with
  `restored_from` set. History is never rewritten.
- Comparison: `compare_revisions` reports metadata differences (content equality, size delta,
  labels, states, authors, dates, path changes). Content diffing of CAD formats is out of scope.

### ADR-012 Lifecycle — IMPLEMENTED (`lifecycle.py`)
- A configurable lifecycle record, with defaults:
  - **States:** `wip` (Work in Progress), `in_review`, `approved`, `released`, `superseded`,
    `obsolete`.
  - **Transitions:**
    - wip → in_review, obsolete
    - in_review → wip, approved
    - approved → released, wip
    - released → obsolete
    - superseded → obsolete
  - **System-only transition:** released → superseded.
- New revisions start in `wip`.
- Releasing revision *n*:
  - re-verifies its content object's hash;
  - refuses if a newer revision is already released;
  - moves the previously released revision to `superseded` and updates the document's
    `released_rev_id`, all in **one** entry.
- Optional policy `require_distinct_approver`: the actor moving a revision to `approved` must
  differ from its author. Default off, so small teams have no mandatory bureaucracy.
- Each transition is appended to the revision's `transitions[]` with actor, time and comment. The
  journal entry is the durable audit record.
- Released revisions stay immutable in content and metadata; only their state may later move to
  `superseded`/`obsolete`.

### ADR-013 Checkout / check-in — IMPLEMENTED for the single-writer model; network shares UNVERIFIED
- Checkout record id = document id, so at most one checkout per document can exist.
  - Fields: `{document_id, token, owner {user, host}, acquired, base_rev_id, base_sha256, note}`.
  - Created with `expect_rev=0` together with a document put at its `expect_rev`. Two concurrent
    checkouts therefore cannot both commit: the second gets `CheckoutConflict` naming the owner.
- Exclusive, **non-expiring**: a checkout never times out silently.
  - Release paths: check-in, cancel by the owner (token), or audited administrative release (exact
    token + reason).
  - The client stores its token in local state (`$XDG_STATE_HOME/revisionworx/checkouts.json` or
    `%LOCALAPPDATA%\RevisionWorx\state`). Losing it requires the administrative release.
- **Check-in** validates against authoritative state under the writer lock:
  - the checkout exists and the token matches;
  - the document's `latest_rev_id` still equals the checkout's `base_rev_id`;
  - the file is present and its content differs from the latest revision (unless explicitly
    allowed);
  - the content object is durable.
  Then **one** entry puts the revision (`expect_rev=0`), updates the document (`expect_rev`) and
  deletes the checkout (`expect_rev`). Any mismatch is a conflict and nothing changes.
- Working files are edited **in place** in the project folder, because rewriting CAD reference
  paths is not acceptable. Consequence: RevisionWorx cannot stop a user from editing a file without
  checking it out (a hidden metadata folder is not a permission system).
  - `document_status` detects this: missing, unchanged, modified while checked out, **modified
    outside RevisionWorx**, or unrevisioned. It uses a size/mtime fast path, or a full hash when
    asked.
  - Setting files read-only between checkouts is PROPOSED as an optional policy, not a default.
- Assembly-aware checkout is **not** automatic: checking out an assembly does not lock its
  dependencies. Multi-document checkout is a separate explicit operation (PROPOSED, Phase 4, once
  the dependency graph exists).
- Interrupted operations: crash before the entry → no revision, checkout still held, orphan content
  object; crash after → replayed. A failure after publication raises `CommittedNotApplied` and
  is queryable by `txid`.
- **Multi-user claim:** none. Correctness relies on the single-writer journal (ADR-004/005/008),
  which is verified only on local Linux filesystems. Production multi-user use waits for Agent D's
  findings (issue #1) and real SMB/NFS tests.

### ADR-014 Multi-user authority: optional LAN coordinator — APPROVED (director, 2026-11-01), NOT IMPLEMENTED
- **Decision:** the supported multi-user architecture is an optional on-premise coordinator (issue #5), following Agent D's design (PR #4). It provides authoritative write control, fencing generations checked atomically with each commit, ordered transactions, txid-idempotent recovery and audited override.
- **Standalone mode** keeps the current file journal (ADR-004/005/008). It is single writer at a time, is fully offline and needs no coordinator.
- **First target:** a Windows-hosted coordinator and SMB shares. Linux hosting and NFS come later.
- **Integration plan:**
  - extract a `CommitAuthority` interface from `Workspace.commit`, preserving its contract (`expect_rev`, `RecordCheck`, `RecordDelete`, `ConflictError`, `LockLostError`, `CommittedNotApplied`, `transaction_outcome`);
  - the file journal and the coordinator are two implementations of it;
  - the coordinator must evaluate `expect_rev` inside its commit transaction;
  - `documents.py` does not change.
- **Ownership:** Agent D owns the coordinator prototype and the SMB test rig. The lead architect owns the seam and its integration.
- **Release gate:** issue #8 (the stale-writer vulnerability; see ADR-008 residual risk) must be closed by coordinator fencing and verified on real SMB before any production multi-user claim.

### ADR-015 Revocation as a journal entry; fencing of revoked writers (issue #8) — IMPLEMENTED rev. 2 (standalone mode)

**Rev. 1 was insufficient.** Agent D and QA reproduced a pre-audit gap: the administrator moved
`writer.lock` *before* committing the audit, so a stale writer could publish in between, and its
entry preceded the revocation and took effect.

**Rev. 2 has a single linearization point.** `break_writer_lock` publishes the `workspace.lock.break`
entry, which carries `revokes: <token>` and the audit record, **directly into the journal** with
exclusive sequence publication. Revocation takes effect at that sequence. The lock-file move happens
afterwards and is only evidence.

| Interleaving | Outcome |
|---|---|
| Stale writer publishes before the revocation sequence | Its entry was committed while the lock was valid. Correct and expected. |
| Revocation claims the sequence the stale writer chose | The stale publish collides: `LockLostError`, nothing written. |
| Stale writer chooses a later sequence | Entry is **fenced**: ignored by readers, replay, the index and the writer itself (`LockLostError`); `transaction_outcome` = `fenced`; `verify` reports `fenced-entry`. |
| Revocation already in the journal at the writer's preflight | `LockLostError` before anything is published. |
| Admin paused or crashed after publishing, before moving the lock | The stale writer is refused or fenced. Acquisition treats a lock whose token is revoked as stale (evidence moved aside). |

**Residual risks:**
- A fenced entry can still exist **physically**. It has no logical effect, and `verify` reports it.
- Correctness relies on exclusive create (`link`/`rename`) and on journal listing being correct on
  the share, which is unverified on SMB.
- Multi-user production still requires the coordinator (ADR-014), with generation checks inside its
  commit transaction.

**Tests:** `tests/test_issue8_fencing.py`.

### ADR-016 Index rebuild snapshot (issue #9) and lock release on Windows (issue #10) — IMPLEMENTED
- **#9:** `LocalIndex.rebuild` samples the journal head before and after listing every kind, and
  retries until they match (bounded). The stored cursor therefore always describes exactly the
  indexed snapshot. Tested with a commit injected mid-snapshot, and with insert/delete/restart/rebuild.
- **#10:** `Workspace.close` releases its lock with bounded retries (50 × 20 ms) on Windows sharing
  violations (`PermissionError`, WinError 32).
  - Ownership is re-read before every attempt, so another writer's lock is never deleted.
  - If release still fails, the lock stays held and `WorkspaceError` is raised; an administrator can
    break it.
  - Verified locally by injected failures only. **The Windows multi-process run needs QA.**

### ADR-017 CommitAuthority contract and idempotent txid — IMPLEMENTED (file journal)
- `revisionworx/authority.py` defines the `CommitAuthority` protocol: `commit`,
  `transaction_outcome`, `head_seq`, `get`, `list`, `entries_after`. It has six semantics:
  atomicity, in-commit `expect_rev`, ordering/`_rev`, fencing, outcome, and idempotency.
  `documents.py` depends only on this.
- `tests/contract_commit_authority.py` is an executable conformance suite. The file journal passes
  it (`tests/test_commit_authority_file_journal.py`). **The LAN coordinator must pass the same
  suite before integration.**
- Defect found while writing the contract, reproduced first: retrying `commit` with the same `txid`
  appended a second entry. Now an identical retry returns the original sequence and writes nothing;
  a different payload under the same txid raises `TxidReuseError`; a fenced original raises
  `LockLostError`.
- Cost: txid lookup scans the journal (O(n)). Acceptable for standalone mode; the coordinator should
  index txids.
- **Content GC** (`doc gc`):
  - runs under the writer lock and is a dry run by default;
  - treats every revision record (including unapplied entries) as a reference;
  - keeps objects younger than 1 h;
  - an `--apply` run is journaled as an audit record *before* it deletes anything.
- **Conflict classification:** `authority.classify(exc)` maps exceptions to stable codes for a
  coordinator wire protocol.
  - `conflict`, `txid-reuse`, `fenced` and `busy` mean nothing took effect.
  - `committed` means it did.
  - `corrupt` and `error` require a `transaction_outcome(txid)` query.
  - Covered by the conformance suite.
- **Precondition identity (Distributed Systems finding, reproduced first):** `RecordCheck` preconditions
  are now journaled in each entry (`checks`, covered by the digest) and are part of the txid
  identity. The same txid with different preconditions raises `TxidReuseError`. Entries written
  before this change compare as having no checks.
- Not yet done: `Workspace` itself is not split into reader and authority. That refactor waits
  until QA accepts `325b7a8`, so code under review is not churned.

### ADR-018 Verified, bounded read of immutable revision content for integrations — IMPLEMENTED
- Requested by the Integration Engineer (PR #2, decision 1).
- `ContentStore.read_verified(sha256, max_bytes, expected_size)` and
  `documents.read_revision_bytes(ws, rev_id, max_bytes)` return the **exact bytes** of a revision.
  SHA-256 is computed over those bytes and the size is checked against `revision.size`.
- Refusals: objects over the limit (`ContentTooLarge`, not fully read), and missing or altered
  objects (`WorkspaceCorruptError`).
- Never reads the mutable working file. Integrations must use this instead of `path_for`, which
  remains an implementation detail.
- The historical extension comes from `revision.source_rel_path`.

### ADR-019 Lock release by atomic move-then-verify (successor-lock race) — IMPLEMENTED; rev. 4 awaiting independent review
**Defect.** Reproduced by Distributed Systems (run 41387552435) and here
(`tests/test_lock_release_race.py`, which failed on `d202b28`):
1. Writer A verifies the token in `writer.lock`, then pauses before its pathname `unlink`.
2. An administrator revokes A, and writer B acquires.
3. A resumes and deletes **B's** lock.

The cause: check-then-unlink is not atomic, and no portable "delete if content matches" primitive
exists. Adding more token re-checks only narrows the window.

**Design** (the only primitive relied on is an atomic rename):
1. If the journal shows our token revoked, release never touches `writer.lock`.
2. `os.replace(writer.lock, .releasing-<uuid>)` atomically takes whatever is at the name. We then
   inspect *that* file, which is exactly what we removed.
3. If it is ours, delete it. If it is someone else's, restore it with an exclusive create
   (`link` on POSIX, `rename` on Windows).
4. **Fail closed** if a third writer took the name during the microsecond gap and the restore fails:
   - publish a journaled revocation of the displaced token (the ADR-015 mechanism);
   - keep the displaced file as `writer.lock.displaced-*` evidence.

   The displaced writer can then never take effect, and the new holder keeps its lock.

**Crash after the rename (found while reviewing this ADR; reproduced first):**
- Scenario: A has moved B's lock aside and dies before restoring it. `writer.lock` is then vacant
  while B believes it still holds authority.
- Fix: every acquisition scans `locks/.releasing-*` *before* the new holder can write. Any token
  found there that is not yet revoked gets a journaled revocation, and the file becomes
  `writer.lock.orphaned-*` evidence.
- The same rule covers a live releaser racing an acquirer: whichever party notices first fences the
  token exactly once. The releaser tolerates its private file having been taken into custody.

**Unreadable orphan (Distributed Systems counterexample, run 41390605906; reproduced first):**
- A malformed `.releasing-*` file hides whose lock it held, so the scanner cannot fence that writer.
- Acquisition now **fails closed**: the new writer gives back its just-acquired lock and raises.
  Nobody can write until an administrator confirms the possible writer is stopped and runs
  `revisionworx lock clear-orphan <file> --reason …`. That keeps the file as evidence and journals
  the action.
- The displaced writer B stops at its next preflight, since `writer.lock` no longer holds its token.
- Residual risk: an administrator clearing the orphan while B is mid-commit, past preflight.
- **Rev. 2: audit-first, crash-resumable clear** (Distributed Systems run 41391444478, reproduced
  first). Rev. 1 moved the evidence *before* journaling the audit, so dying in between left neither
  orphan nor audit, and the next writer acquired.

  Now `clear_orphaned_release`:
  1. **publishes** the audit first (reason, file name, SHA-256 of the exact bytes, planned evidence
     name); that audit is the authorisation;
  2. **then** moves the file to the planned evidence name.

  Crash windows:

  | Crash point | Result |
  |---|---|
  | Before or during audit publication | Orphan intact, acquisition still refused, no audit. |
  | After the audit, before the move | The next acquisition (or re-running the command) completes the move under that audit, exactly once. |
  | During the move | `os.replace` is atomic. |
  | After the move | A retry is a no-op. |

  Changed bytes are never covered by an older audit; they need a new, separately audited decision.
  Documented; the same class as any manual override.

- **Rev. 3: custody-bound, serialized clear** (Distributed Systems review of `366d435`, run
  41393650443; both counterexamples reproduced first in `tests/test_orphan_clear_custody.py`, all
  16 tests fail on `366d435`).
  - *P1 (audit/bytes TOCTOU).* Rev. 2 hashed `locks/<name>` and then moved the **pathname**. A
    file swapped in between was archived under the audit, the barrier vanished and a new writer
    acquired. A second hash does not help: the race simply moves after it.
  - *P2 (duplicate authorisation).* Two administrators could both pass the "no audit yet" lookup
    and both publish `workspace.orphan.clear`, with different reasons and evidence names, for one
    orphan.

  Trust boundary: the audit authorises **bytes** (the SHA-256 the administrator inspected), not a
  pathname. The name only locates candidates. A file may be archived under an audit only if
  the archived bytes are, by construction, the audited bytes.

  Protocol (`clear_orphaned_release`, `_drive_orphan_clear`):
  1. **Authorise, serialized.** The decision ("is there an open authorisation for this file?") is
     taken against journal head *H*, and the authorisation is published exactly at *H*+1 by
     exclusive create (`_publish_admin_entry(expect_head=H)`). If anything was published after *H*,
     the publish fails and the decision is retaken (also when the file vanished meanwhile). There
     is therefore at most one open authorisation per orphan instance, and a competing request
     **adopts** it (its own reason is not recorded; the CLI says so). Each authorisation carries a
     `clear_id`; rev. 2 audits derive it from their evidence-name suffix.
  2. **Take custody.** `os.replace(<name>, .clearing-<clear_id>-<rand>)`: a fresh name that never
     clobbers. Whatever is now in custody is exactly what was moved, and custody files keep
     blocking writers.
  3. **Verify and archive from the verified buffer.** Read the custody file once. If
     `sha256(bytes)` equals the audit, publish the evidence file *from those bytes* (exclusive
     create). Later mutation of the custody file cannot reach the evidence. Existing evidence
     with different bytes stops everything: `WorkspaceCorruptError`.
  4. **One terminal result per clear** (`workspace.orphan.clear.result`, published by the same
     compare-and-swap):
     - `completed`; or
     - `refused`, with the observed SHA-256 and a fresh quarantine name `.releasing-<new>`. The
       mismatched bytes are moved there. A quarantined file is a new orphan instance: it still
       blocks (it is unreadable, or carries a token and is fenced) and needs its own audited
       decision. The old name can no longer be re-authorised.
  5. **Dispose of custody** only while it still holds the audited bytes. Anything else found there
     is quarantined, never deleted.

  Acquisition first settles every open clear and custody file, then scans `.releasing-*`, then
  `.clearing-*`, then `.releasing-*` again. Files only move `.releasing → .clearing → (evidence |
  .releasing quarantine)`, so a concurrent administrator cannot slip one past the barrier. Any
  custody file still present means a clear is in flight, and acquisition refuses ("retry").

  Token-bearing orphans are fenced by the same move-then-verify rule: the file goes to
  `.fencing-<rand>` first; the token is read from the moved file, revoked, then archived. An
  unreadable custody file is re-quarantined as a blocking orphan.

  Crash windows (each tested; "resume" means the next acquisition or a re-run of the command):

  | Crash point | State | Result |
  |---|---|---|
  | Before or during authorisation | orphan at its name, no audit | acquisition refused |
  | After authorisation, before custody | orphan at its name, open audit | resume takes custody under that audit |
  | After custody, before evidence | `.clearing-*` | blocks; resume verifies and archives exactly once |
  | After evidence, before terminal result | evidence + `.clearing-*` | blocks; resume publishes one `completed` |
  | After `refused` result, before quarantine | `.clearing-*`, refused result | blocks; resume moves the bytes to the recorded quarantine name |
  | After terminal result, before custody disposal | evidence + `.clearing-*` | resume disposes custody if the bytes still match |

  **Threat model (corrected after the Codex review of `e5b38a6`).** Rev. 3 as first published
  claimed defence against pathname replacement *at the custody names*. That was wrong for writer
  exclusivity, and this section replaces that claim.

  *Actors.* "Protocol processes" are RevisionWorx clients and administrators that touch
  `locks/` only through this code, and may crash at any instruction, pause for any time and
  interleave arbitrarily. Every name they create is fresh (`uuid4`), so no protocol process ever
  renames onto an existing `.releasing-*`, `.fencing-*` or `.clearing-*` name. An "outside actor"
  renames, replaces or deletes files in `locks/` by other means (a script, a user in Explorer,
  malware, an older incompatible build).

  | Property | Protocol processes + crashes | Outside actor |
  |---|---|---|
  | At most one effective writer (ADR-015/019) | **Design goal, rev. 4** (rev. 3's "guaranteed" was refuted on `d2b7a1c`; pending independent review) | **Not achievable** with a file-only authority |
  | Evidence archived under an audit equals the audited SHA-256 at archival | **Guaranteed** | **Guaranteed** (evidence is published from the verified buffer by exclusive create) |
  | Later alteration or removal of archived evidence | n/a | **Detected** by `verify()` (`orphan-evidence-altered` / `-missing`), not prevented |
  | One authorisation and one terminal result per orphan incarnation | **Guaranteed** (journal CAS) | **Guaranteed** while the journal itself is not forged |

  *Why exclusivity is out of reach for an outside actor.* The baseline needs no orphan code: one
  rename of a live `writer.lock` lets a second writer acquire while the first, paused after its
  preflight, still commits (`tests/test_lock_namespace_threat_model.py::test_boundary_baseline_*`).
  More generally, any file-only protocol must at some point remove a blocking file by pathname, or
  the blocking set grows forever. POSIX has no unlink or rename bound to an inspected object, so an
  actor who can rename onto that name at that instant substitutes a live lock. Both Codex
  counterexamples (`workspace.py:693` custody disposal, `:472` fencing archival) are exactly this.
  They are reproduced deterministically as `test_boundary_codex_*`, and a control with identical
  protocol moves and timing but no outside rename is safe. Re-hashing, identity checks (dev/ino)
  or extra renames only narrow the window, so none is claimed as a fix. Exclusivity against
  outside actors needs an authority the actor cannot write: OS permissions on `locks/` and the
  journal, or the ADR-014 coordinator. That is a Director decision, recorded here as **open**.

  *Orphan incarnation identity.* An incarnation is the authorisation's `clear_id`, bound to (file
  name, SHA-256) at a journal sequence. Name and digest alone are not an identity: identical
  bytes reappearing at the same name after a terminal result are a new incarnation with a new
  `clear_id` (tested). There is no authenticated principal: "same administrator" means same
  reason and same `user@host`. An identical request resumes idempotently (lost acknowledgement,
  crash, restart). Any other request raises `OrphanClearConflict` and records nothing.

- **Rev. 4: no stale decision can unfence a lock** (three protocol-only P1s on `d2b7a1c`, from Codex,
  QA and Distributed Systems, all reproduced first; rev. 3's claim of protocol-only exclusivity was
  wrong). Every failure had the same shape: a process read token A at `writer.lock`, an ordinary
  acquirer B then replaced A, and the delayed pathname move took **B's** live lock, while the file
  ended up outside the scanned namespace with B unrevoked. A successor C then saw nothing, and C and
  B (already past its preflight) both took effect.

  | Mover | `d2b7a1c` | Failure |
  |---|---|---|
  | Revoked-lock cleanup in acquisition | `writer.lock` → `writer.lock.revoked-*` | moved B, never revoked it |
  | `break_writer_lock` | `writer.lock` → `writer.lock.broken-*` | moved B, never revoked it |
  | `_restore_foreign_lock` | `.releasing-*` → `writer.lock.displaced-*`, *then* revoke | crash in between lost the obligation |

  **Invariant.** A token-bearing lock file stops blocking only after the token *read from that moved
  file* is durably revoked. Two rules deliver it:
  1. Nobody but the owner moves `writer.lock` except through `_displace_writer_lock`, which moves it
     to a fresh scanned `.releasing-*` name. An earlier read only gives permission to look.
  2. Token-bearing files reach an evidence name only through `_fence_token_orphan`: inspect the
     moved file, revoke, archive.

  Why that closes the race: the rename out of `writer.lock` happens before the name can be vacant,
  so any later acquirer's scan, which runs after its own exclusive create, finds the file. B's next
  publication lands after the revocation and is fenced, or lands before it and precedes C entirely.
  Evidence names are unchanged when the moved file is the expected one, and the audit's planned name
  stays accurate. A successor found instead gets `writer.lock.orphaned-*` from its own audit.
  Acquisition's retry is now a bounded loop (`LOCK_ACQUIRE_ATTEMPTS`), not recursion.

  Crash windows for all three movers (each tested at every filesystem step):

  | Crash point | State | Result |
  |---|---|---|
  | Before the move | `writer.lock` unchanged | blocked, or cleared if revoked |
  | After the move, before revocation | `.releasing-*` | next acquirer revokes the actual token before writing |
  | After custody, before revocation | `.fencing-*` | same |
  | After revocation, before archive | `.fencing-*` | next acquirer archives; the token is already fenced |

  **Decision: a displaced successor is revoked, not restored,** in cleanup and break. Restoring would
  keep B running but adds states and another exclusive-create race for a very rare case. The release
  path keeps its existing restore attempt. Consequence: B can lose a healthy session in this race and
  gets `LockLostError`. That is unavailability, not corruption, and it reopens.

  **Schedule exploration.** `tests/test_lock_protocol_schedules.py` runs three writers and an
  administrator over the real code with a seeded scheduler that can switch or kill an actor before any
  filesystem operation, and checks the property from the journal: authority never interleaves, no
  lost update, no live token in evidence. It finds the defect on `d2b7a1c` within a few seeds. It
  explores randomly and is not a proof; threads stand in for processes.

  **Not claimed:** SMB/NFS, power loss, Windows until run there, outside actors (unchanged from
  rev. 3), or a formal proof of the protocol. Independent QA and Distributed Systems review is pending.

- **Lock file reads (issue #10, after rev. 4).** On Windows, opening a file while another process renames
  it can fail with WinError 32. That says nothing about who holds the lock, but `close()` read it as "not
  mine" and leaked the lock, a live handle read it as "lost" and went read-only for good, and the orphan
  scan read a healthy orphan as "corrupt" and refused every writer. `_read_lock_file` now retries with a
  bound. If `close()` still cannot look it releases anyway, since release is move-then-verify and cannot
  take a successor's lock. A holder leaving mid-acquire is retried instead of reported as held by `?`.

  Retries alone were not enough. New 6- and 10-process tests on real Windows (Core CI run 41573956298, at
  `3cb2764`) showed the winner's release starved after 1 s, leaving every other process waiting 60 s and
  timing out. The cause is that Python's `open()` does not grant `FILE_SHARE_DELETE`, so every reader blocks
  a rename. `atomicio.read_json` now opens through `CreateFileW` with all share modes on Windows, so
  RevisionWorx's own readers never block a release. Outside programs can still hold a file open, so
  `atomic_write_bytes`, record deletes and the release wait up to about 5 s, and a release that still cannot
  finish keeps the lock and reports it. This uses `ctypes` from the standard library.

**Preserved:**
- ADR-015 journal-first revocation, now shared via `Workspace._publish_revocation`.
- ADR-016 bounded retry on Windows sharing violations (`PermissionError` on the rename).
- If the journal is unreadable, our own lock is still released (step 2 alone is safe).

**Tests:**
- successor keeps its lock *and* can commit;
- a revoked writer never touches the lock;
- third writer in the restore window: the displaced writer is fenced, the new holder keeps the
  lock, evidence is kept;
- normal and repeated release; crash, then admin break, then new writer;
- the three #10 cases, now injected at the rename.

**Limitations:**
- Step 4 can revoke a legitimate writer B, but only in the double race (admin revocation of A, B's
  acquisition and C's acquisition all inside A's release). B gets `LockLostError` and must reopen.
  That is unavailability, not corruption.
- Atomic rename and exclusive create on SMB are unverified. There is no multi-host claim.

### ADR-021 Versioned canonical request identity for txid idempotency — IMPLEMENTED
Adopts the Distributed Systems proposal (PR #7 comment, `docs/ADR017_FULL_REQUEST_IDENTITY_PROPOSAL.md`
on PR #4).

- **Identity v2:** `request_identity(op, ops, blob_refs)` is SHA-256 over
  `b"revisionworx/commit-request/v2\0"` plus the compact, recursively key-sorted UTF-8 JSON of
  `{version: 2, op, operations, blob_refs}`.
  - `operations` lists every RecordPut, RecordDelete and RecordCheck **in submitted order**, each with
    `type`, `kind`, `id` and an explicit nullable `expect_rev`. Put data has `_rev` stripped.
  - Non-finite numbers are rejected (`ValueError`, nothing written). Previously NaN could enter the
    journal.
  - Every new entry stores `request: {version: 2, digest}`, covered by the entry digest.
- **Retry rules:**

  | Situation | Result |
  |---|---|
  | Same txid, equal digest | Original sequence returned; nothing written (lost-ACK or restart retry). |
  | Same txid, different digest (changed, reordered or added operations, preconditions or payload) | `TxidReuseError`. |
  | Same txid, prior entry has **no v2 identity** (written before ADR-021) | **`IdentityUnverifiable`** (`classify` → `identity-unverifiable`); nothing written. |

  The caller uses `transaction_outcome(txid)`, which still answers committed/fenced/not-found for
  every entry. A legacy request is never silently treated as identical.
- **Migration:** none. Historical entries are immutable and never rewritten. Pre-ADR-021 txids lose
  only *retry replay*, not outcome lookup. The existing `checks` field stays for readability; identity
  comes from `request`.
- **Contract:** `RequestIdentityV2Contract` (reordering, check-only, key order, non-finite values)
  plus legacy-entry tests. The coordinator must implement the same identity bytes.

### ADR-022 Record files are a cache of the journal — IMPLEMENTED; awaiting independent review
**Defect** (found preparing core integration; `tests/test_materialisation_authority.py` failed on
`fe8a179`). Production code and one crash are enough:
1. Writer B publishes entry N, which is effective, and pauses before materialising it.
2. An administrator revokes B. C takes over and updates the same record.
3. B resumes and writes N's outdated data over C's record file, then dies before its `state.json` write.

`applied_seq` still claims C's sequence, so nothing replays. `get()` returned B's data until
someone ran `verify --repair`, and the next writer's `expect_rev` check passed against it: **a lost
update**. The journal stayed correct throughout, but decisions were not being taken from it. The same
late write could also resurrect a deleted record or remove a newer one. `commit()` made this easier by
replaying before it checked its lock.

**Decision.** No file primitive can stop the late write: the check and the write are never atomic, and
fencing only governs journal entries. So record files stop being trusted. They are a cache, and reads
are resolved against the journal:
- **Journal view:** per handle, in memory, built only from digest-verified entries and read forward, so
  each immutable entry is read once. `latest` maps each record to the (seq, deleted) of its last
  *effective* write; going forward, an entry is fenced exactly when its token is already revoked, which
  matches `is_fenced`. Discarded if the journal is shorter or the last entry seen changed (a restore).
- **`get` / `list`:** a file is used only if its `_rev` is at least the view's seq. Otherwise the answer
  comes from the journal entry. `expect_rev` goes through `get`, so it is covered too.
- **`_pending_overlay` is gone;** the view subsumes it. Revocations are served from the view as well.
- **`commit`** checks its lock before replaying, so a writer that lost its lock writes nothing there.

The files can still be wrong on disk until the next write of that record. `verify` still reports it, and
`verify --repair` still fixes it. That is deliberate: a damaged cache must stay visible.

**Damaged journal.** A view that cannot be built must not remove read access to a fully applied workspace
(CORE_API: recovery-blocked means read-only; this regression was caught by testing against the GUI branch).
If everything is applied, reads serve the files. If entries are unapplied, reads raise, because the files
would be behind. `commit` checks revocations first and raises either way, so no write decision sees this.

**Cost** (Linux, Python 3.13.18, one run each, local disk):

| N entries | | `fe8a179` | ADR-022 |
|---|---|---|---|
| 1,000 | 50 commits, open handle | 3,122 ms | 402 ms |
| 1,000 | fresh read-only open + first `get` | 2.9 ms | 59 ms |
| 3,000 | 50 commits, open handle | not run | 1,005 ms |
| 3,000 | fresh read-only open + first `get` | not run | 196 ms |

Commits get faster because revocations stop being rescanned, but **the first read on a fresh handle now
costs O(n)**. Previously that cost landed on the first commit or the first pending read instead. The
parked `perf-journal-index` branch (ADR-020) keeps the same "per-handle, in memory, never persisted" rule.
Its txid index and head probe still apply on top of this and would need redoing against it.

**Not claimed:** power loss, SMB/NFS, or outside actors. Threads stand in for processes in the schedule
tests. Independent QA and Distributed Systems review is pending.

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
| DocumentType, Document, Revision, Checkout, Lifecycle | IMPLEMENTED (Phase 2, single-writer) |
| Part, Assembly, BOM, DependencyEdge, ChangeRequest | PROPOSED |

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
