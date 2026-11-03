# RevisionWorx — Development Progress

**Updated:** 2026-11-02 (session 10: ADR-019 rev. 4, protocol-only P1s; core owner handed over to a new session)
**Branches:**
- `phase1-foundation`: Phase 1 + review + PySide6.
- `phase2-documents`: Phase 2, stacked on `phase1-foundation`. **This file on `phase2-documents`
  is the most complete record.**

Nothing is merged, tagged or released; that awaits director approval.
**Status:** Phase 1 foundation implemented, reviewed and hardened, with automated tests on Linux.
The GUI was migrated to PySide6, but the Qt widgets have not been executed. No release exists.

## How to resume
1. `git checkout phase1-foundation` and read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md` (ADRs).
2. `python -m pytest -q`: on `phase1-foundation` expect **118 passed, 1 skipped**; on
   `phase2-documents` expect **151 passed, 1 skipped**. The skip is
   `tests/test_qt_gui.py`, which needs PySide6. With PySide6 installed, run
   `QT_QPA_PLATFORM=offscreen python -m pytest` and record the Qt results; they have never been run.
3. Continue with "Next concrete tasks" below. Record every session in the log at the bottom.

## Phase tracking
| Phase | Status |
| --- | --- |
| 1. Foundation, workspace configuration, registration, index | **Implemented, reviewed, tested on Linux. Qt GUI not executed; Windows untested** |
| 2. Drawing/revisions/checkouts | **Core implemented and tested on `phase2-documents` (single writer, local FS). Not production multi-user. Qt tab not executed** |
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

## Phase 2 — what exists on `phase2-documents` (verified by tests)
| Area | Module | Notes |
| --- | --- | --- |
| Design | ARCHITECTURE ADR-010..013 | Committed before code (`677dbe1`) |
| Document types | `documents.py` | Built-in drawing/cad-part/cad-assembly/schematic/pdf/manufacturing/general; custom types; extension-based suggestion |
| Document registry | `documents.py` | Managed projects only; file never moved/renamed/edited (snapshot-tested); case-insensitive path uniqueness; document numbers unique; links refused |
| Content store | `content.py` | SHA-256 objects; single-read copy+hash; fsync + exclusive publish; dedupe with re-verification; abort if the file changes mid-copy |
| Revisions | `documents.py`, `revlabels.py` | Immutable fields guarded; labels alpha (ASME letters skipped)/numeric/numeric2/manual; history; export with verification; metadata compare; restore = new revision with `restored_from` |
| Lifecycle | `lifecycle.py` | Configurable states/transitions; release re-verifies content and supersedes the previous release in the same entry; cannot release older than current; optional distinct approver |
| Checkout/check-in | `documents.py`, `clientstate.py` | Exclusive non-expiring checkout (`expect_rev=0`); 6-process race → one winner; check-in validated vs authoritative state in one entry; cancel keep/discard; audited admin release; stale tokens rejected |
| External edits | `documents.py` | missing / unchanged / modified / modified-externally / unrevisioned; touch-only detected as unchanged via hash |
| Drawing register | `documents.py`, CLI | Rows + CSV |
| Index | `index.py` (schema v2) | Documents (+FTS), revisions, checkouts; incremental == rebuild |
| CLI | `cli.py` | `doc …`, `rev …`, `doctype …`, `lifecycle …` |
| GUI | `gui/viewmodels.py`, `gui/qt/documents.py` | View-models tested headless; Qt Documents tab **not executed** |

## Session log

### 2026-11-02 (session 9) — Codex P1 findings on `e5b38a6`: threat model corrected; P2 conflicts; API contract
- **Codex `workspace.py:693` (custody disposal) and `:472` (fencing archival): both reproduced
  deterministically.** See `tests/test_lock_namespace_threat_model.py::test_boundary_codex_*`.
  - Each needs an *outside* rename of a live lock onto a custody name.
  - The same capability yields two effective writers with **no orphan code at all**
    (`test_boundary_baseline_*`: one rename of `writer.lock`).
  - A control with identical protocol moves and timing, but no outside rename, is safe.
  - Conclusion: there is no protocol-internal race; ADR-019 rev. 3 *overclaimed*. Its threat model
    is rewritten with a property-by-actor table. Exclusivity against outside actors is recorded as
    an **open Director decision** (OS permissions or the ADR-014 coordinator). No narrowing
    "fixes" were made.
- **DS P2 semantics:**
  - A different reason or `user@host` for the same orphan incarnation raises
    `OrphanClearConflict` and records nothing.
  - An identical request resumes idempotently (crash, lost ACK).
  - Identical bytes recreated after a terminal result are a new incarnation (new `clear_id`).
  - CLI exit code 2 on conflict.
- **Evidence tampering is now detectable:** `verify()` reports `orphan-evidence-altered` /
  `orphan-evidence-missing`.
- **`docs/CORE_API.md`:** the stable UI/integration contract for Claude B (issue #13).
- **Tests:** `python -m pytest -q` → **213 passed, 1 skipped (Qt)**, Linux, Python 3.13.18, 8+3
  consecutive full runs identical. With `e5b38a6`'s `workspace.py`/`cli.py`, 3 of the new tests
  fail (conflict, verify, process exit codes). The boundary tests reproduce on both versions, by
  design. Windows not executed here.

### 2026-11-02 (session 8) — ADR-019 rev. 3: custody-bound, serialized orphan clear (DS P1 + P2)
- **Reproduced first.** `tests/test_orphan_clear_custody.py`: all 16 tests fail on `366d435`.
  - P1: the evidence differs from the audited SHA-256, and a new writer acquires.
  - P2: two authorisations are published for one orphan.
- **Fix (ADR-019 rev. 3):**
  - **Authorisation** is a compare-and-swap on the journal head; competitors adopt it.
  - **Custody:** the orphan moves to a private `.clearing-*` name; evidence is written from the
    verified in-memory bytes.
  - **Terminal result:** exactly one per clear (`completed` / `refused`). Mismatched bytes are
    quarantined under a fresh blocking name.
  - **Acquisition** settles open clears, then scans releasing, clearing, releasing.
  - **Fencing:** token-orphan fencing now also moves the file before reading it.
- **Found under load and fixed:** a stale-view "does not exist" when a competitor authorised and
  moved the file between our head read and our file read. Regression test added.
- **Behaviour change.** An orphan whose bytes changed after authorisation is no longer re-cleared
  by its old name. It is quarantined, and the administrator clears the quarantine name (the CLI
  prints it). The rev. 2 test was updated accordingly.
- **Tests:** `python -m pytest -q` → **207 passed, 1 skipped (Qt)**, 10 consecutive full runs
  identical (Linux, Python 3.13.18). Windows **not executed here**; the Windows branch of
  `_quarantine` (`os.rename`) is exercised only by CI.
- **Independent review pending:** Distributed Systems' original counterexamples, then QA on
  Linux and Windows. The Phase 1 backport and `perf-journal-index` stay on hold until acceptance.

### 2026-11-02 (session 7) — P0: successor-lock deletion race fixed (ADR-019), awaiting QA
- **Reproduced first:** `tests/test_lock_release_race.py::test_paused_releaser_never_deletes_successor_lock`
  failed on `d202b28` (B's lock deleted).
- **Fix:** atomic move-then-verify release, with a fail-closed journaled revocation if the restore
  fails.
- **Tests:** full suite **177 passed, 1 skipped (Qt)** (Linux, Python 3.13.18). The checkout and lock
  multi-process races were repeated 3×, all passing. Windows not executed here.
- **Crash after the rename** (A takes B's lock aside, then dies): reproduced, then fixed. The
  acquirer fences tokens it finds in orphaned `.releasing-*` files before writing. Suite: **178 passed,
  1 skipped**; races repeated 3×.
- **Phase 1 backport authorization withheld** by the coordinator. `phase1-backport-8-9-10` must
  gain this fix once QA accepts it; PR #6 is unchanged.

### 2026-11-02 (session 6) — QA acceptance received; Phase 1 backport branch; ADR-018
- **QA accepted the #8/#9/#10 fixes on local filesystems** (run 41387311010; Linux and Windows,
  Python 3.13):
  - #8: pre-audit race fixed. The one strict xfail left is the physical fenced artifact allowed by
    design.
  - #9: insert and delete snapshot cases pass.
  - #10: Windows six-process race 5/5 (one winner, five conflicts each), and the injected
    WinError 32 case passes.
  - No SMB or two-host claim.
- **Backport:** prepared on `phase1-backport-8-9-10` @ `36d1957`; PR #6 itself is unchanged.
  Director decision pending (see the log above).
- **ADR-018:** verified bounded revision-content read for the OpenSchematix adapter.
- Added stable outcome classification (`authority.classify`) to the CommitAuthority contract, for the
  coordinator wire protocol.
- **Tests:** full suite **172 passed, 1 skipped (Qt)** (Linux, Python 3.13.18). No CI was triggered.
- **Next:** after QA accepts `03b3800`, split `Workspace` into reader and file-journal
  `CommitAuthority` behind the conformance suite. Fast-forward PR #6 if the director approves.

### 2026-11-02 (session 5) — CommitAuthority contract, idempotent txid (ADR-017)
- QA (PR #3) and Distributed Systems (PR #4) are verifying `325b7a8`. PR #6 is left unchanged, as
  the director directed.
- Added the `CommitAuthority` protocol and a reusable contract suite; the file journal passes it.
- Fixed a non-idempotent `txid` retry (reproduced first: the same txid produced 2 entries).
- Added orphan content GC (`doc gc`): dry run by default, grace period, referenced objects never
  removed, audited.
- Document history now walks the revision chain (O(revisions of the document)), with loop, break
  and count checks, instead of listing all revisions.
- **Tests:** full suite **170 passed, 1 skipped (Qt)**. No CI was triggered.

### 2026-11-01 (session 4) — issues #8 (rev. 2), #9, #10
- **#8 pre-audit gap closed:** revocation is now a journal entry, the single linearization point
  (ADR-015 rev. 2). Five interleavings are tested, including the admin pausing between revocation
  and the lock move, and acquisition over a revoked lock.
  - Also fixed: a revocation that took the stale writer's sequence used to surface as
    `WorkspaceCorruptError`; it now raises `LockLostError` and nothing is written.
- **#9:** index rebuild uses a stable head-checked snapshot (ADR-016).
- **#10:** bounded, ownership-checked retries when releasing the lock on Windows (ADR-016).
- **Tests:** full suite **161 passed, 1 skipped (Qt)**. The three race tests were repeated 3×, all passing.
- **Not executed here:** Windows (QA CI needed); PySide6 (PyPI still refused from this session).
- **Still open:** multi-user production remains gated on the coordinator and on SMB qualification.
  The fix is on `phase2-documents`; `phase1-foundation` (PR #6) does not have it yet. A director or
  QA decision is needed on whether to backport or retarget.

### 2026-11-01 (session 3) — issue #8 fenced (ADR-015)
- Reproduced Agent D's procedure as `tests/test_issue8_fencing.py`. Both tests failed first: the stale commit succeeded.
- Implemented journal-order fencing. The full suite gives **153 passed, 1 skipped**.
- Multi-user use is still gated on the coordinator and on SMB verification.

### 2026-11-02 (session 10) — ADR-019 rev. 4: three protocol-only P1s on `d2b7a1c`
- Took over as core owner (Claude A) from the previous session. Rev. 3 claimed protocol-only
  exclusivity; QA, Distributed Systems and Codex refuted it, and the QA reproducers were rerun here
  first (3 unsafe PASS + 3 strict XFAIL on `d2b7a1c`).
- Fix: `_displace_writer_lock` and revoke-before-archive in `_fence_token_orphan`. See ADR-019 rev. 4.
- New: `tests/test_lock_stale_decision_races.py` (crash at every step of each mover) and
  `tests/test_lock_protocol_schedules.py` (seeded schedule exploration with process kills).
- `tests/test_issue8_fencing.py`: a pause hook keyed on the old `broken` destination would have stopped
  firing silently, so it now matches any move of `writer.lock`.
- **Not accepted by its author.** Awaiting QA and Distributed Systems on the exact SHA. Windows not run
  here. API requests A1-A5 from Claude B (PR #7) are not yet answered.

### 2026-11-01 (session 2, part 3) — independent agents' work executed; decision pending
Agents B and D opened PR #3, PR #4 and issue #5 during this session. Their tests had not been run
against the application, so I ran them; nothing in their branches was modified.

- **PR #3 (QA, Agent B), `tests/qa`:**
  - On its base `23d6ecf`: **16/16 passed**.
  - Against `phase2-documents`: **15 passed, 1 failed**, `test_journal_publish_then_apply_failure_is_recovered`.
  - The failure is caused by intentional ADR-008 contract changes (`CommittedNotApplied` instead of
    the raw `OSError`; records carry `_rev`), not by a regression. The same scenario with those two
    adjustments was run and **passes**. Details and suggested updates are posted on PR #3.
- **PR #4 (concurrency, Agent D), `prototype/test_authority.py`:** **3/3 passed**. Its fencing is
  sound because the generation check and the commit are one local SQLite transaction. Review notes
  posted (unclosed connections; the payload must carry `expect_rev` semantics).
- **PR #2 (OpenSchematix, Agent C):** 6/6 passed; compared against upstream; see the session 2 log.
- **Issue #5, architecture decision: APPROVED by the director (20:36). See ADR-014 and the REQUIREMENTS §5 amendment.**
  - Recommendation: keep the serverless shared folder for single-writer use (not certified for
    concurrent writes), and make the optional LAN coordinator the supported multi-user mode, behind
    a `CommitAuthority` seam that implements the existing `Workspace.commit` contract.
  - This **amends REQUIREMENTS §5** (preferred deployment), so it was not decided unilaterally.
    Status for issue #1's six requirements was posted there.
- **Open draft PRs:**
  - #6 `phase1-foundation` → `main`;
  - #7 `phase2-documents` → `phase1-foundation` (stacked);
  - #2, #3, #4 from the agents.

  None merged.

### 2026-11-01 (session 2, part 2) — Phase 2 on `phase2-documents`
- **Commits:**
  - `677dbe1` design (ADR-010..013)
  - `f99281e` core (registry, content store, revisions, lifecycle, checkout)
  - `4b4cbe9` index/CLI/GUI
  - this documentation commit
- **Tests executed:** `python -m pytest -q` gives **151 passed, 1 skipped** (Qt, no PySide6). The
  three multi-process race tests (exclusive publish, writer lock, document checkout) were re-run
  3× each, all passing. A Python 3.11 CLI run registered a real OpenSchematix `.osch` as a schematic
  document, then checked it out and in (rev A → B); `verify` reported 0 problems.
- **Defects found by tests during development:**
  - Case-variant paths reached "file missing" before the duplicate check.
  - Registering a symbolic link silently registered its target.
  Both are fixed, with regressions in `test_register_refusals`.
- **Crash points covered:**
  - Failure while copying (source changed): nothing written.
  - Journal publish failure after the content object is stored: no revision, checkout kept, retry
    succeeds and reuses the re-verified object.
  - Failure after the commit point: `CommittedNotApplied`, queryable by `txid`, completed on reopen.
- **Not done / open:**
  - Garbage collection of orphan content objects.
  - Optional read-only-between-checkouts policy.
  - Assembly-aware multi-document checkout (needs the Phase 4 dependency graph).
  - Engineering change records.
  - Report export as PDF (CSV only).
  - Revision comparison is metadata-only.

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

### Known limitations / risks (Phase 2)
- **No multi-user production claim.** Checkout exclusivity rests on the single-writer journal,
  verified only on local Linux filesystems. Agent D's findings and real SMB/NFS tests are required
  first.
- **Working files are edited in place.** RevisionWorx cannot prevent edits made without a checkout;
  it detects them (`modified-externally`). Native CAD references are preserved because paths never
  change.
- **Checkout tokens live in local client state.** Losing that state requires an audited
  administrative release.
- **Orphan content objects** from interrupted check-ins accumulate until GC exists. They are
  harmless but use space.
- Admin-only operations (verify, gc, lifecycle change) still scan all revisions.

## Next concrete tasks
1. **The director has allowlisted PyPI.** Install PySide6 and run `QT_QPA_PLATFORM=offscreen python -m pytest`, which includes `tests/test_qt_gui.py`.
   Launch the window on Linux and Windows; fix whatever the stub smoke could not catch.
2. Run the full suite on Windows (NTFS) to verify the `rename`-based exclusive publish and path rules.
3. Add CI (GitHub Actions: Linux + Windows, Python 3.10 and 3.13) — needs director approval for workflow files.
4. Phase 2 follow-ups (content GC done in session 5): per-document revision storage for scale, PDF report export, engineering
   change record linkage, optional read-only policy.
5. Integrate Agent C's OpenSchematix adapter (PR #2) once retargeted. Schematic documents can then
   carry extracted component/part candidates as `unresolved` provenance until Phase 4 parts exist.
6. **Unblocked by the ADR-014 approval (Windows coordinator + SMB first; issue #8 is the release gate):**
   - Extract a `CommitAuthority` interface from `Workspace.commit`: the file journal is one
     implementation, and the LAN coordinator (Agent D) would be the other.
   - Add named failpoints for QA (issue #1).
   - Plan the SMB/NFS test rig, i.e. Samba + NFSv4 containers or two VMs (Agent D).
7. Ask Agent B to extend `tests/qa` to Phase 2 (checkout/check-in/lifecycle) and update the one test
   pinned to the old exception type.

## Persistent warnings
- Never conflate a local cache with authoritative lock ownership.
- Do not claim old unmanaged projects have been indexed or included in where-used coverage.
- Do not claim Inventor/SolidWorks integration or OpenSchematix compatibility without real parser/API tests.
- Never invent success or mark a phase completed based solely on documentation.
