# Distributed Systems Engineer — Phase 2 authority, fencing and scalability audit

**Evidence inspected:** `phase1-foundation` PR #6 and `phase2-documents` PR #7, including
`revisionworx/workspace.py`, `revisionworx/index.py`, `revisionworx/documents.py`,
`docs/ARCHITECTURE.md`, and the Project Director's
`docs/ADMINISTRATIVE_LOCK_FENCING_DIRECTIVE.md` on main.

**Status:** independent source review and an isolated executable research prototype; no approval
to introduce a production coordinator or modify the core implementation.

## 1. Actual commit path and critical revocation window

Phase 1/2 mutations ultimately call `Workspace.commit(op, ops, txid=...)`. Its relevant order is:

1. Check that this instance was opened writable.
2. Repair pending materialized journal projections if needed.
3. Call `_check_lock_still_held()`, which reads `locks/writer.lock` and compares token.
4. Validate `expect_rev` with the current materialized/overlaid records.
5. Choose the next journal sequence and publish with `atomicio.publish_exclusive()`.
6. Materialize records and checkpoint applied sequence.

Meanwhile `Workspace.break_writer_lock()` checks the expected token, moves
`writer.lock` to evidence, then acquires a new writer lock and journals the override audit.
The old writer can be paused **after step 3** and resumed **after the override**. Its
stored `self.writable` flag and previous check cannot prevent a new journal publication.
Any token recheck immediately before the `publish_exclusive` call still leaves a race.
**No unguarded file operation is an atomic compare-generation-and-publish primitive.**

A reproducible harness is `prototype/test_phase1_lock_race.py`. It injects a pause in the
real `Workspace._check_lock_still_held`, runs the actual administrative break, resumes
the writer, and searches the authoritative journal for both events. To run it, supply a
separate `phase1-foundation` checkout using `REVISIONWORX_PHASE1_CHECKOUT`. A
skipped test without the checkout is **not evidence** that the race was reproduced.

This does not render Phase 1's local single-writer file journal useless: it documents
a specific limitation. It **does** block a claim that shared-file multiwriter authority
remains safe after administrative override.

## 2. Atomic authority contract (proposed, not approved)

The isolated `prototype/authority.py` performs `BEGIN IMMEDIATE` on a SQLite database
hosted on one coordinator's **local** disk. Acquisition, administrative revocation, and
publication all obtain the same transactional serialization gate.

Within one transaction, publication must:
- Resolve `txid` idempotently, rejecting reuse with different operation content.
- Compare the current checkout owner and monotonic fencing generation.
- Compare the current authoritative expected record revision(s).
- Validate referenced immutable content is already staged and verified.
- Append exactly one durable ordered commit record and advance its record-revision state.
- Commit before acknowledging the transaction.

An administrative override in the same serialized authority advances generation and
records old generation, new generation, previous owner, reason, operator claim, and outcome.
The operator claim is **not authenticated** in the prototype. Production must authenticate
authorization and must retain local work from the revoked owner for reconciliation.

This provides a linear order: either a writer publishes first, then the override becomes
effective, **or** the override happens first and the old writer's publication fails.
There is no intermediate state in which the old writer publishes *after* revocation.

The local prototype stores opaque payloads; it does **not** implement the actual
multi-record `RecordPut`, `RecordCheck`, `RecordDelete` semantics, content hashes,
real journals or a transport. Claude owns the application `CommitAuthority` seam.
Do not swap this prototype into `Workspace` or `documents.py` without approval.

### Durability and restored-backup caveat

SQLite `WAL` with `synchronous=FULL` and local-disk transaction atomicity helps, but
does not alone solve restored-backup fencing. If an operator restores an older database,
its generations can move backwards and old credentials can become plausible again.
Before accepting any post-restore write, a trusted external epoch/high-water registry or
a controlled system-wide quiescence plus unforgeable new epoch must invalidate every
old credential. No such mechanism has been implemented or tested.

For real immutable CAD content, require pre-published, SHA-256-verified objects and
a manifest whose durable authoritative commit marker references only confirmed objects.
Do not acknowledge success before both authority commit and content durability are
established. Ambiguous response loss must resolve via `transaction_outcome(txid)`.
Cross-device and cross-machine atomicity of the blob store and coordinator DB is an
unsolved integration decision; reconciliation/recovery must fail closed.

## 3. Cache and revision-list audit

`revisionworx/documents.py:revisions_of(ws, doc_id)` scans
`ws.list("revision").values()` and then filters by document ID, so the operation is
**O(total revisions)** rather than **O(revisions for one document)**. Repeated
`_next_label` calls compound the work for large engineering archives.

`Workspace.journal_seqs()` enumerates and sorts the complete journal directory;
`head_seq()`, `entries_after()`, and `transaction_outcome()` invoke full or
reverse scans. This is acceptable for small Phase 1 workspaces but should be benchmarked
before claiming scalability to hundreds of thousands or millions of revisions.

**Research recommendation:** maintain a disposable local SQLite index
`revision(document_id, number, seq, label, state)` with an index on
`(document_id, number)`, updated transactionally with the cache's journal cursor.
A visible stale indicator must distinguish an indexed answer from authoritative
current checkout state. Rebuilding must replay the **journal** to a stable watermark;
do not combine a head cursor captured at one time with materialized records read
from a later instant.

This is a design recommendation, **not** a reproduced LocalIndex corruption defect.
Validate the snapshot-cursor hypothesis using a reproducible concurrent publication
test before opening a defect report.

## 4. Independent test evidence and remaining certification

Local Linux CPython 3.13.5: `python -m unittest -v prototype.test_authority` initially
passed 10 tests, then was extended to **14 passing tests** covering mandatory
`expected_revision`, simultaneous override-versus-publication, and independent-process crashes before and after the SQLite commit point. The real Phase 1
test and cache watermark probes were **skipped** locally without a separate checkout. GitHub Actions workflow
`.github/workflows/distributed-authority-research.yml` was added to execute
the prototype on Linux/Windows and the opt-in Phase 1 reproduction from its actual
Git branch; record workflow results separately when completed.

Real two-host SMB/Samba/NFS operations, power loss, network partitions, disk-full,
backup rollback, operator authentication, and restart-with-in-flight-requests
remain unverified. Phase 1 and Phase 2 are **not certified multi-user deployments**.

## 5. Coordination / decision gate

Project Director issue #5 remains open: choose certified serverless shares with clear
limitations, or authorize an optional offline LAN coordinator as a new supported
multi-user deployment. The latter changes the original preferred-deployment requirement.
Claude owns implementation of `CommitAuthority`, compatibility exceptions, and core
record semantics. Distributed Systems Engineer owns this independent prototype and
test research only. Do not merge or ship either architecture without authorization.
