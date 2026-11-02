# Coordinator integration contract — research proposal for Lead Architect

**Decision:** Project Director approved optional offline LAN coordinator for supported multi-user deployments in issue #5. Initial host: Windows; shared immutable objects: SMB. Standalone single-user mode remains coordinator-free. This document is a proposal, not an implemented production interface. Claude owns the `CommitAuthority` integration seam.

## Authority and trust boundary

A Windows-hosted coordinator owns the sole writable authority database **on its own local disk** (not SMB). Only the coordinator can allocate checkout generations, revoke them, assign global commit sequence numbers, validate expected revisions and acknowledge commits. Clients may read an SMB content store but cannot write the coordinator's journal or SQLite database directly. A local SQLite cache is always disposable and never decides ownership. A coordinator outage must make new checkouts, administrative revocations and commits **fail closed**; cached reads may continue with an explicit stale indicator.

The research API is deliberately small:

| Method | Request | Successful response | Required behavior |
| --- | --- | --- | --- |
| `acquire` | resource, authenticated principal, idempotency key | opaque lease credential with epoch, generation, owner, resource | Serialize; reject conflicting owner |
| `verify` | resource, credential | current revision and epoch | Advisory only; not permission to commit later |
| `release` | resource, credential | released or already released | Atomic generation advance; reject stale credential |
| `override` | resource, expected generation, reason, authenticated admin identity, request ID | new generation and durable audit ID | Authz; compare-and-revoke in one transaction; retain previous owner evidence |
| `publish` | txid, credential, expected record revisions, immutable object manifest, operations | committed sequence and resulting revisions | **Atomic** credential and expected-revision checks with commit marker insertion |
| `transaction_outcome` | txid and authenticated principal | committed sequence, rejected reason, or unknown | Resolve lost acknowledgments without duplicating writes |
| `changes_after` | epoch, sequence cursor, page size | contiguous changes, high watermark, next cursor | Reject missing sequences or changed epoch; rebuild from authoritative journal |

The prototype's `actor_claim` is **not authentication**. Production must bind administrative identity to an authenticated local-LAN session and authorize overrides. “Accountless” means no mandatory cloud identity or subscription, **not** anonymous privilege to revoke colleagues' work. Exact Windows integrated authentication/ACL policy needs architectural approval and threat-model review.

## Publication state machine

1. Client computes immutable content hashes and uploads to an SMB staging namespace inaccessible to other writers. Coordinator verifies SHA-256, length and durable object existence; never trust client-provided hashes without reading bytes.
2. Client submits one `publish` request with txid, expected revisions and manifest references. Coordinator obtains exclusive authority transaction, checks current epoch/generation/owner, validates all expected revisions, and commits exactly one ordered manifest/journal marker.
3. Only after authority durability is confirmed may coordinator return committed. Clients refresh local cache by contiguous journal replay. If response is lost, `transaction_outcome(txid)` must return the same committed outcome; no second revision.
4. Incomplete staging without a committed marker is garbage-collectable **only after** a safe grace period and proof that no in-flight transaction references it. Never remove objects referenced by a committed manifest.
5. Recovery scans authoritative commit markers, verifies referenced blobs and marks the workspace **read-only/quarantined** on a missing/corrupt committed object. Do not silently “repair” by using a different revision.

**Unresolved atomicity boundary:** SQLite commit and SMB object durability are separate systems. Staging must be verified before the commit, and SMB server crash durability must be certified. An SMB acknowledgment is not automatically proof of power-loss survival. This is a production release blocker.

## Revocation linearization

`override` and `publish` must serialize on the same durable authority transaction. If `publish` commits first, the subsequent override cannot undo it; if `override` commits first, the old generation cannot publish. A separate preflight `verify` is never sufficient. A revoked user's working files remain locally recoverable for a fresh checkout/rebase, never automatically overwritten.

## Epoch and backup restore

The `prototype/coordinator_research.py` separate epoch guard detects a **missing or mismatched guard**, but a database restore from an earlier snapshot in the *same epoch* can resurrect an old owner/generation. The research test intentionally demonstrates this unresolved vulnerability. Before production, implement a trusted **nonrollbackable high-water mark or epoch authority**, or a documented restore procedure that disables all old credentials and rotates to a new epoch before serving requests. Guard and DB cannot both be restored to the same old state. No production safety claim until fault-injection tests cover this.

## Failure-closed conditions

Unknown epoch, absent authority, changed generation, stale expected revision, missing journal sequence, inconsistent cache watermark, corrupt object hash, ambiguous txid, incomplete manifest, disk full, failed durable flush, and unauthorized admin request must not produce an acknowledged new revision. Reconnect requires epoch validation and replay from a known contiguous cursor.

## Acceptance and ownership

Lead Architect: extract the application `CommitAuthority` interface and own `Workspace.commit` integration. Distributed Systems Engineer: isolated coordinator, durable manifest, epoch and Windows/SMB research rig. QA Engineer: independently reproduce issue #8 and validate crash, network, power and multiuser acceptance. The prototype does not implement authenticated transport, production object durability or the approved deployment as a working product.


## Concrete transaction request, outcome, and error contract

The Lead Architect should retain existing `Workspace.commit(op, ops, blob_refs, txid)` semantics at the application-service boundary. Proposed coordinator adapter command (illustrative only, not a deployed wire format):

```json
{
  "api_version": 1,
  "txid": "stable-128-bit-random-id",
  "workspace_epoch": "authority-issued-epoch",
  "checkout": {"resource": "doc-id", "generation": 27, "opaque_token": "secret"},
  "expected": [{"kind": "document", "id": "doc-id", "rev": 47},
               {"kind": "checkout", "id": "doc-id", "rev": 12}],
  "ops": [{"type": "put", "kind": "document", "id": "doc-id", "data": {}},
          {"type": "put", "kind": "revision", "id": "rev-id", "data": {}},
          {"type": "delete", "kind": "checkout", "id": "doc-id"}],
  "manifest": {"sha256": "digest-of-verified-content-reference-list"}
}
```

Every `RecordPut`, `RecordDelete` and `RecordCheck` precondition must be checked against **the coordinator's own authoritative record versions inside the same serialized transaction** that inserts the commit entry and updates all affected record states. Checks must not use local cache materialization. A multi-record check-in is *one* operation, never separate document, revision, and checkout updates. The first successful `txid` freezes its request digest and committed sequence; a byte-identical retry yields the original outcome; reusing `txid` with altered content is rejected.

Suggested result union: `committed(sequence, updated_revisions, txid)`, `already_committed(sequence, txid)`, `conflict(kind, id, expected_rev, actual_rev)`, `fenced(resource, generation)`, `rejected_auth`, `unavailable`, `quarantined`, and `unknown_outcome(txid)`. If the client loses the response after the commit point, it must query `transaction_outcome(txid)` before retrying. An `unknown_outcome` cannot be translated to `not committed` without authoritative evidence. `CommittedNotApplied` remains meaningful if local materialization fails **after** authoritative commit; no automatic duplicate publication.

## Fail-closed coordinator state machine

```text
STARTING --verify authority, witness/epoch, blobs, ordered journal--> READ_WRITE
STARTING --mismatch/corrupt/missing recovery proof-------------> QUARANTINED
READ_WRITE --authority service lost / network disconnected-----> CLIENT_OFFLINE
READ_WRITE --blob missing / journal gap / rollback detected-----> QUARANTINED
CLIENT_OFFLINE --reconnect + epoch + cursor + credential reconcile--> READ_WRITE
CLIENT_OFFLINE --cannot reconcile / epoch changed-------------> READ_ONLY
QUARANTINED --verified supervised restore + fresh epoch--------> STARTING
```

No client may silently fall back from coordinator mode to an uncoordinated writable shared-folder journal; that would create split-brain authority. During outage clients may inspect last known local data, clearly marked **stale**, and preserve private working files; they may not acquire checkouts, release another user's checkout, or check in revisions. No automatic lock stealing. Administrative override is itself an authoritative serialized operation and must fail when the coordinator is unavailable.

## Standalone-to-coordinator adoption and fallback rules

1. **Quiesce** standalone writes, refuse new writer locks, and inventory outstanding checkouts and unknown commit acknowledgments. A still-active writer must be closed or explicitly revoked through a verified mechanism before cutover.
2. **Snapshot at a proven journal watermark** `N`. Verify every journal digest/sequence and all immutable blobs; rebuild materialized state by replay through exactly `N`. Do not copy an unbounded enumeration of independently moving records.
3. **Initialize coordinator authority** from that snapshot in a fresh epoch. Retain stable workspace/document/revision IDs and original CAD paths. Import prior journal audit and revision history without inventing historical checkout ownership. Issue new fencing generations and force every client to discard previous credentials.
4. **Double-check parity** of canonical record revisions, journal sequence, immutable content hashes, and audit history. Only after all checks succeed set workspace collaboration mode to coordinator-backed and begin accepting writes. Keep the standalone journal read-only; never allow two writable authorities.
5. **Reconnect clients** with epoch negotiation and contiguous journal replay or full cache rebuild. Preserve any offline working files for manual reconciliation.
6. **Rollback** to single-writer standalone mode only with a separate, Director-approved supervised reverse migration from a quiescent coordinator state that imports **every acknowledged coordinator commit**. It is not an automatic error-handling fallback.

The initial Windows/SMB acceptance environment must identify whether the coordinator service can read/verifiably flush each content object, how share ACLs prevent clients from modifying committed blobs, and what happens if the SMB server acknowledges staging but loses those bytes after restart. A local SQLite proof does not establish SMB content durability.

## Research findings affecting integration

An independently executed [Phase 2 ordering probe](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381819173) found that ADR-015's post-audit token filtering does **not** protect an old writer who publishes between the administrator's `os.replace(writer.lock,...)` and the subsequent `workspace.lock.break` audit commit. The future coordinator's `override` and `publish` must be serial transactions with one linearization point, not two ordered filesystem steps. See issue #8 and `prototype/test_phase2_lock_break_order.py`.

The additional `prototype/witnessed_coordinator.py` is an **isolated** exploration of binding the authoritative DB to a hash-chained external witness, with a test-only signed-principal provider and audit. It detects same-epoch database rollback only if an independently protected witness survives unchanged; ordinary files do not provide that external guarantee. See `docs/COORDINATOR_WITNESS_RESEARCH.md`. No Windows integrated administrator authentication is implemented.
