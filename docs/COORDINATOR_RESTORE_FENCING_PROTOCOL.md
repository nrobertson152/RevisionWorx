# Coordinator restore fencing and recovery protocol (design review)

**Status:** proposed protocol, **not implemented**. The existing `EpochGuard` research prototype deliberately fails to detect an older SQLite snapshot restored inside the same epoch. Its test documents this gap; it must not be deployed.

## Governing invariant

No checkout credential issued before a coordinator restore may authorize any publication after restore, even if the restored database contains an older generation or a transaction ID previously committed. A restore must not silently erase an acknowledged committed revision. The coordinator must therefore remain **offline/read-only** until both credential invalidation and an authoritative journal/content reconciliation are complete.

## Preferred controlled restore procedure

1. **Quiesce:** stop accepting checkout, commit, administrative override and release requests; close active sessions. Mark service in recovery mode before opening restored authority data.
2. **Establish nonrollbackable recovery authority:** obtain a trusted external epoch/high-water record from a separately protected medium or administrative recovery service. It must not be restored from the same snapshot as SQLite. If this record is missing, stale, unverifiable, or itself rolled back, **do not start writable service**.
3. **Verify durable committed history:** compare the restored database's latest sequence, txid set and hash-chained commit manifest with the external high-water record and available immutable objects. A database snapshot missing acknowledged commits must be reconciled from independently durable journal/backup data. Never invent or silently drop acknowledged revisions.
4. **Rotate epoch before serving:** create a cryptographically random new epoch and durably record it in the nonrollbackable authority. Old epoch credentials and any prior authenticated sessions must be rejected, regardless of generation or owner. Do not merely increment a generation inside the restored database.
5. **Quarantine and reindex:** validate all referenced object hashes, audit integrity and ordered sequences; rebuild disposable local caches from the authoritative journal. Missing committed content or a sequence gap forces read-only quarantine.
6. **Explicit operator signoff:** record authenticated administrator, reason, backup source, old/new epochs, last verified commit sequence, reconciliation evidence, timestamps and any data-loss exception. A data-loss exception requires Director approval and must be visible to every client.
7. **Resume:** only after all checks pass may clients reconnect, discard stale cache cursors and obtain fresh checkout credentials. Uncommitted local working copies remain recoverable but cannot be auto-published.

## Backup and crash matrix

| Failure | Required behavior |
| --- | --- |
| Crash during epoch rotation | Remain offline until trusted epoch state and DB agree |
| Restored DB from before a revoked checkout | Old epoch rejected before any write |
| Restored DB missing acknowledged commit | Quarantine; reconstruct verified history or disclose loss |
| External high-water record unavailable | Fail closed; no administrative bypass that reuses old epoch |
| Both DB and external guard rolled back | Cannot prove safety; must use independently nonrollbackable authority |
| Committed manifest references missing SMB blob | Quarantine; restore exact hash-verified bytes |
| Lost commit response | Query authoritative txid outcome; never blindly retry with a new txid |
| Malicious operator edits audit | Tamper-evident audit anchored outside restored DB required |

## Open implementation choices for Claude / Project Director

A Windows coordinator could store the trusted high-water record on a separately managed, append-only storage device or an independent administrative authority. Windows DPAPI/TPM-backed keys can help protect identity but **do not by themselves provide an arbitrary nonrollbackable commit counter**. Ordinary local files, mirrored files, or a second SQLite database backed up/restored together are insufficient.

Before choosing storage, define an operational recovery-point objective, acknowledged-commit durability requirements, backup scope, offline identity model, administrator authorization and recovery ownership. Validate power-cut and rollback fault injection on a real Windows host and SMB server. Until then, the prototype is a safety model only.

## What the research code now tests (not production restore)

`prototype/witnessed_coordinator.py` has a controlled `rotate_epoch` **on an intact, healthy authority**. The routine verifies a signed test administrator role and mandatory reason, advances the generation of all existing checkout resources, clears owners, writes per-resource UTC audits, updates the epoch guard and hash-chained witness, and commits SQLite. Independent-process fault injection after replacing the epoch guard but before the database commit proves that startup **fails closed**, rather than silently using the old epoch. A successful controlled rotation makes all previous epoch credentials invalid, while retaining prior committed txids.

This is **not a complete backup-restore procedure**. A restored database missing acknowledged transactions cannot be repaired by simply rotating the epoch; the lost history must first be reconstructed from independently trustworthy journal/manifest evidence, or the authority must remain quarantined. Likewise, a crash in the multi-file rotation may require supervised reconciliation rather than an automatic retry.

The test `test_rollback_of_both_db_and_witness_resurrects_revoked_owner` proves why **ordinary file copies are inadequate**: after revoking a writer, restoring both SQLite and the external-looking witness directory to the same old snapshot makes the old credential valid again. That **intentionally passing vulnerability reproduction** is a hard production trust-root gate. A separately named folder, second drive, DPAPI-protected file or different SQLite database is not evidence of nonrollbackability.

The model bounds witness entries to 4,096 and authoritative state rows to 10,000, with fail-closed saturation tests. Production requires a checkpoint/compaction design with a durable nonrollbackable anchor, operational restore key custody, administrator signoff and physical power-loss evidence before using any such mechanism to authorize writes.
