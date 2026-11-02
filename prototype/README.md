# Distributed Systems Engineer — isolated transaction-authority research

This folder contains **research code only**, independent of RevisionWorx core. It is not a production coordinator, and no protocol selection has been approved. Claude owns the application's prospective `CommitAuthority` integration interface.

## Model and actual behavior

`authority.py` stores local SQLite authority generations, per-resource owner, revision preconditions, ordered commit rows, transaction IDs and an administrative revocation audit. `BEGIN IMMEDIATE` serializes acquisition, override and publication; the current generation and revision are validated **inside the same database transaction** that inserts the commit row. All SQL connections are explicitly closed. The database must reside on the coordinator's **local disk**; never put a writable SQLite authority database on SMB/NFS.

`test_authority.py` uses Python standard-library unittest and independent processes to verify local authority properties. Run from the repository root:

```sh
python -m unittest -v prototype.test_authority
```

An opt-in regression `test_phase1_lock_race.py` imports the **real** `revisionworx.workspace` from a separate `phase1-foundation` checkout. The test forces a writer to pause *after* its lock preflight, performs an audited administrative lock break, and checks whether the stale writer then manages to append a journal entry. This deliberately demonstrates the existing defect, not safe production behavior.

```sh
REVISIONWORX_PHASE1_CHECKOUT=/path/to/phase1-foundation \\
  python -m unittest -v prototype.test_phase1_lock_race
```

Both opt-in Phase 1 test modules skip when the separate checkout is not supplied. The second probe, `test_phase1_cache_watermark.py`, tests whether `LocalIndex.rebuild()` materializes newer records while retaining an older captured journal cursor. Neither has been executed with the real Phase 1 code in the present local environment.

## Verification record (2026-11-01)

Linux, CPython 3.13.5: `python -m unittest -v prototype.test_authority` — **14 passed, 0 failed**, approximately 5 seconds. The separate opt-in Phase 1 reproducers — **2 skipped** without a Phase 1 checkout. The first three prototype tests were also separately executed by the Lead Architect on another Linux Python 3.13 environment, reported in PR #4; this is not a substitute for the tests above.

The earlier README described an **unimplemented mkdir/COMMIT-marker experiment** and claimed related local tests. That description was inaccurate and has been replaced with the actual implementation.

## Outstanding safety gaps

* No identity authentication, authorization or secure remote transport. Audit stores a caller-provided **unverified actor claim**, not an authenticated administrator identity.
* No real RevisionWorx journal adapter, multi-file manifest, SHA-256 content verification, blob preparation or durability acknowledgment.
* No external epoch/high-water protection against restoring an older coordinator database and resurrecting revoked credentials.
* No network partitions, server crash during commit, power loss, disk-full, SMB/NFS or Windows testing.
* No protocol decision: optional offline local-LAN coordinator remains a proposal under GitHub issue #5, not an approved implementation.

Fail closed for authoritative multi-user writes until these gaps and real deployment qualification are resolved.
