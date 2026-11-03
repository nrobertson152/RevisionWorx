# RevisionWorx core: invariants and failure map

**Owner:** Claude A (core). **Scope:** Tier 1 only: one computer, a local filesystem, and only RevisionWorx processes
touching `.revisionworx`, which may crash at any instruction, pause for any time and interleave in any order.

**How to read it.** Each section states what must always be true, how the code keeps it true, what can interrupt it, and
which test would fail if it broke. Design reasoning is in `docs/ARCHITECTURE.md` (ADR-015, 016, 017, 019, 021, 022, 023).

**Not covered anywhere here:** SMB/NFS or more than one host, power loss, programs other than RevisionWorx editing
`.revisionworx`, and manual use of the installed GUI. Nothing below is a claim about any of those.

## 1. What is authoritative

| Thing | Status | If it is wrong |
|---|---|---|
| `journal/<seq>.json`, effective entries | **The only authority** | `WorkspaceCorruptError`; writes refused |
| `locks/` | Decides who may write. Says nothing about what is true | Fenced by the journal |
| `records/<kind>/<id>.json`, `state.json` | Cache of the journal | Reads fall back to the journal; `verify` reports it |
| `content/sha256/...` | Immutable bytes, named by their hash | Detected by hash on every use |
| Local SQLite index | Disposable cache of the journal, on this machine | Replaced automatically |
| Local checkout tokens | A convenience. The checkout record is authoritative | An administrator releases the checkout |

## 2. Writer ownership

**States of one writer token**

```
                 exclusive create at writer.lock
  (no token) ───────────────────────────────────► HOLDS, UNSCANNED
                                                      │ orphan scan finished
                                                      ▼
                                                    HOLDS ───── close ─────► RELEASED (file removed by owner)
                                                      │
                     displaced by someone else        │     revoked in the journal
                 ┌────────────────────────────────────┴───────────────┐
                 ▼                                                    ▼
      DISPLACED (.releasing-* / .fencing-*)  ── revoked ──►  REVOKED, then archived as evidence
                 │
                 └── restored to writer.lock if nobody took the name (release path only) ──► HOLDS
```

| # | Invariant | Kept by |
|---|---|---|
| W1 | At most one effective writer: effective entries never go token A, token B, token A. | W2, W3 and R1 together |
| W2 | **A token-bearing lock file stops blocking only after the token read from that file is durably revoked.** The owner removing its own file is the only exception. | `_displace_writer_lock`, `_fence_token_orphan` |
| W3 | A new holder writes nothing until it has scanned, so every token displaced before it took the name is revoked first. The rename out of `writer.lock` happens before the name can be free, so the scan cannot miss it. | `_acquire_writer_lock`, `_fence_orphaned_releases` |
| W4 | An earlier read of the lock is only permission to look. Anything moved is inspected after the move. | `_unlink_own_lock`, `_displace_writer_lock` |
| W5 | An unreadable blocking file stops all writers until an administrator's audited clear, and every refusal names a way out. | `fence_all`, `clear_orphaned_release`, `break_writer_lock --token ?` |
| W6 | A healthy holder is never told it lost the lock because of a transient read failure. | `atomicio.read_json` bounded retry |

**Interruption windows**

| Window | State left | Who finishes it |
|---|---|---|
| After moving `writer.lock`, before inspecting | `.releasing-*` | Next acquirer revokes the actual token |
| After custody, before revocation | `.fencing-*` | Next acquirer |
| After revocation, before archive | `.fencing-*`, token already fenced | Next acquirer archives |
| After exclusive create, before the scan finishes | `writer.lock` with a token that never wrote | Administrator break. **Locks are never stolen automatically** (ARCHITECTURE) |
| Holder dies normally | `writer.lock` with a dead holder | Administrator break |

**Tests:** `test_process_handoff.py` (real processes, real CLI, a kill at every step), `test_lock_stale_decision_races.py`,
`test_lock_protocol_schedules.py`, `test_process_crash_matrix.py::test_break_*`, `test_fencing_an_orphan_*`.

**Accepted cost:** a healthy writer can be revoked by a stale cleanup or break that displaced it. It gets `LockLostError`
and reopens. That is lost availability, never lost data.

## 3. Journal and effective commits

| # | Invariant | Kept by |
|---|---|---|
| R1 | An entry signed by a token revoked at a lower sequence never takes effect, for any reader, replay or writer. | `is_fenced`, `_journal_view` |
| R2 | One sequence, one entry: publication is an exclusive create of a complete, synced file. A crash cannot leave a partial entry. | `publish_exclusive` |
| R3 | Entries never change; a digest detects an altered entry. | `read_entry` |
| R4 | `expect_rev` is decided from the journal, never from a record file. | `_current_rev` → `get` → view |
| R5 | "Corrupt" is claimed only for an **effective** entry from another writer at our sequence. An administrative entry or a revoked writer's late publish is not corruption. | `commit` publish loop |
| R6 | A commit never skips an entry that landed before it. | `commit`: `recover()` when `applied_seq != seq - 1` |

**Commit steps and what a crash leaves**

| Step | Crash here means |
|---|---|
| Checks (lock, revocation, txid, `expect_rev`) | Nothing happened: `not-found` |
| Temp file written | Nothing happened; an old temp file is cleaned up later |
| Exclusive publish **← the commit point** | Everything before: nothing. Everything after: it happened |
| Records, then `state.json` | Committed; reads already come from the journal; the next writer finishes materialising |

**Tests:** `test_process_crash_matrix.py::test_commit_*`, `test_core_reliability.py` (collisions, skipped entries).

## 4. Transaction identity and retries

| # | Invariant | Kept by |
|---|---|---|
| T1 | One txid takes effect at most once. | Single writer, plus the check against the view before publishing |
| T2 | Same txid and same request returns the original result. A different request is refused. | `commit`, `_earlier_checkin` |
| T3 | `transaction_outcome` reports the attempt that took effect if any did. A hung attempt landing later does not change the answer. | `attempts` in the view |
| T4 | A fenced attempt is finished: it never took effect, the retry says so, and a new attempt takes a new txid. | `commit`, `_earlier_checkin` |
| T5 | A check-in retry captures nothing new, whatever the working file holds by then. | `_earlier_checkin` runs before any file is read |

**Tests:** `test_process_crash_matrix.py::test_checkin_*`, `test_core_reliability.py::test_checkin_*`, `::test_two_attempts_*`.

## 5. Revisions, checkout and materialisation

| # | Invariant | Kept by |
|---|---|---|
| D1 | Content is durable before any journal entry refers to it. | `store_file` runs before `commit` |
| D2 | A revision, the document update and the checkout release are one journal entry. | `checkin` |
| D3 | Stored content is never modified. A damaged object is detected, never reused. | Exclusive publish, re-hash on reuse |
| D4 | A check-in never modifies the working file. Revert and restore replace it atomically from verified content, or not at all. | `_write_working_from_store` |
| D5 | GC removes only unreferenced content older than its age limit, and records the intent first. | `gc_content` |
| D8 | A published revision never changes: immutable fields stay, transitions only append, nothing is deleted. `commit` cannot enforce this, so an audit of the journal reports any breach. | `_guard_immutable`, `audit_history` (`doc verify`) |
| D6 | **A read equals the journal.** A record file is used only if its `_rev` is exactly the journal's last effective write. A late write by a stale writer cannot change an answer or a decision. | `get`, `list`, ADR-022 |
| D7 | A damaged journal keeps read access to a fully applied workspace and refuses writes. | `_effective_writes`, `commit` |

**Known limits**
- Revert and restore replace the file, then commit. If the commit then fails, the edits the user asked to discard are gone
  while the checkout remains. Reversing the order would mean a CAD program holding the file fails *after* the commit,
  which is worse. Left as is, deliberately.
- GC running in a process that has lost its lock could delete an orphan a new writer has just started reusing. It would
  need an administrator to break the lock of a live GC during that same instant. Not closed; recorded here.
- A file altered by an outside program while keeping its `_rev` is served until `verify` reports it (ADR-022).

**Tests:** `test_process_crash_matrix.py` (each operation), `test_materialisation_authority.py`, `test_documents.py`.

## 6. Local index

| # | Invariant | Kept by |
|---|---|---|
| I1 | The index never authorises anything. | Nothing in the write path reads it |
| I2 | The cursor and the contents change together. | One SQLite transaction |
| I3 | A rebuild's cursor equals what it read. | Head sampled before and after listing |
| I4 | The cache proves which journal it describes. | Digest anchor, ADR-023 |
| I5 | Busy is not damaged: a database in use is never deleted. | `_is_busy`, `IndexBusyError` |
| I6 | Damage replaces the cache rather than raising forever. | `_healing`, `sync` |
| I7 | Coverage admits what it could not read. | `baselines_unavailable` |

**Not detected:** a bit flip that still parses. **Not covered by I6:** callers using `idx.db` directly (Claude B's A5).

**Tests:** `test_index.py`, `test_core_reliability.py` (timelines, busy, every damaged page, races).

## 7. Windows file sharing

| # | Invariant | Kept by |
|---|---|---|
| S1 | RevisionWorx's own readers never block a rename or delete. | `FILE_SHARE_DELETE` in `atomicio.read_json` |
| S2 | Another program holding a file costs a bounded wait, never authority. | `retry_sharing`, about 5 s, not nested |
| S3 | A release that cannot finish keeps the lock and says so. It never pretends. | `_unlink_own_lock` |
| S4 | If a holder cannot read its lock, it still releases: the move is inspected afterwards, so it cannot take a successor's. | `close` |
| S5 | Failing to remove a temp file never hides the real result or blocks a workspace from opening. | `remove_quietly`, `cleanup_temp_files` |

**Tests:** `test_windows_sharing.py` (injected everywhere, real on Windows), `test_process_handoff.py::test_readers_*`,
`::test_soak_*`. Real share-mode behaviour is only proven by the Windows CI jobs.

## 8. How the evidence is built

| Technique | Finds | Limits |
|---|---|---|
| Real process killed before every filesystem change (`test_process_crash_matrix.py`) | Crash-consistency gaps, in every operation | Process death only, not power loss |
| Deterministic pauses across real processes (`test_process_handoff.py`) | The known dangerous interleavings, run for real | Only the interleavings written down |
| Scheduled threads with kills (`test_lock_protocol_schedules.py`) | Interleavings nobody thought of | Random, not exhaustive; threads stand in for processes |
| Soak across real processes | Timing no one scripted | Not repeatable; green proves little, red proves a lot |
| Negative controls on historical sources | That a test can fail at all | Only as good as the historical defect |
