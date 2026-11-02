# Witnessed coordinator — isolated recovery and authorization experiment

**Status:** research only. No production coordinator, network transport, Windows identity integration, nonrollbackable witness device, or SMB durability certification exists in this branch. This experiment must not be substituted for `Workspace.commit`. Claude owns the production `CommitAuthority` seam.

## Architecture

```text
Test-only signed identity provider ── verified principal ──┐
                                                          v
Research authority ── SQLite BEGIN IMMEDIATE ── compare epoch, owner,
  generation and revision ── mutate metadata ── hash uncommitted state
                                |
                                v
                         append-only witness
                  (separately protected storage)
                                |
                     fsync witness entry first
                                |
                                v
                     SQLite COMMIT, then ACK
```

`prototype/witnessed_coordinator.py` demonstrates a **fail-closed witness protocol**. The database contains a witness-binding identity. The separate witness ledger contains a chain of SHA-256 digests, including each prospective authoritative database-state digest, previous witness digest, and epoch. Every transaction checks the previous witness head **under SQLite's exclusive writer transaction** before mutation, writes the next witness entry and fsyncs it **before** committing SQLite, then acknowledges success.

This is deliberately conservative. If the coordinator dies **after witness append but before database commit**, the witness advances while SQLite rolls back. On restart, the mismatch disables operations; an operator must reconcile exact state from trustworthy durable evidence. If SQLite commits, the witness has already been written. If an older database backup is restored while the external witness persists, the restored authority digest differs and access is refused, even when the epoch string is unchanged.

A timestamped authenticated-role *mock* audit is generated for successful and stale-generation administrative overrides; validation uses signed short-lived tokens issued by an in-memory test identity provider. A wrong password, forged token, nonadministrator, stale generation, or resource-owner mismatch cannot authorize the corresponding operation in the model.

## Limitations that prevent deployment

1. **Witness trust is assumed.** Ordinary local files do not prove protection against rollback. Restoring both the witness ledger and SQLite to an old consistent snapshot defeats the model; a real witness needs independently nonrollbackable storage and administrative access controls, plus verified power-failure persistence.
2. **No real Windows identity.** `TestIdentityProvider` simulates identity verification with in-memory test credentials and HMAC signatures. Windows integrated authentication, service identity, administrator group mapping, token confidentiality, revocation, endpoint authentication and network transport remain unimplemented.
3. **Not optimized.** The prototype hashes all authoritative rows and verifies the entire witness chain on every operation. It cannot meet large-workspace performance targets; production should use an incremental authenticated commitment, protected high-water checkpoint and bounded journal replay.
4. **No SMB blob coordination.** Neither the witness nor SQLite operation makes a separately stored SMB blob durable. A committed manifest may still refer to an object lost by the SMB server; actual content verification and quarantine/recovery remain mandatory.
5. **No automatic mismatch repair.** A witness ahead of SQLite (crash between witness append and SQLite commit) is intentionally blocked. Automatically overwriting the witness from the older database could reinstate revoked credentials. Only an explicitly authorized audited recovery process may establish a new epoch after reconciling acknowledged commits.
6. **SQLite local disk only.** Cross-process database serialization and hardlink or fsync tests on local Windows/Linux runners are not real two-host SMB certification.

## Recovery decision matrix

| Observed state | Interpretation | Model behavior |
| --- | --- | --- |
| DB digest matches witnessed head | Durable state consistent under modeled assumptions | Allow authenticated operation |
| DB older than witness | Crash before DB commit, older backup, or metadata loss | Refuse all writes and reads that assert authority |
| DB newer than witness | Missing witness write or unauthorized DB mutation | Refuse all authority operations |
| Witness file missing, truncated or digest chain broken | Witness storage integrity uncertain | Refuse all authority operations |
| Witness and DB both reverted to same old version | Cannot detect without an independent nonrollbackable anchor | **Unsolved release blocker** |
| Logged committed txid retried | Client lost response | Return already-committed, do not make another revision |
| Attempted override with wrong generation | Operator saw stale lock | Audit rejected outcome, preserve actual owner |

## Test and handoff

`python -m unittest -v prototype.test_witnessed_coordinator` runs local model tests for authentication, authorization, audit, two-process races, crash before/after commit, same-epoch backup restore, missing witness and tampering. CI runs them on Windows and Ubuntu with Python 3.11/3.13, but test success is not proof of physical or independent witness durability.

**For Claude:** The application-facing coordinator must bind authenticated identity, resource/epoch/generation and `expect_rev` checks to **one durable commit transaction**. No preflight authorization check may substitute for a transactional check. The witness ledger is an optional research mechanism that would require a separately approved and actually nonrollbackable storage design. Standalone single-user operation must remain coordinator-free.
