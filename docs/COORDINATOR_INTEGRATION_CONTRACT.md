# Coordinator integration contract — research proposal for Lead Architect

**Decision:** Project Director approved optional offline LAN coordinator for supported multi-user deployments in issue #5. Initial host: Windows; shared immutable objects: SMB. Standalone single-user mode remains coordinator-free. This document is a proposal, not an implemented production interface. Claude owns the \`CommitAuthority\` integration seam.

## Authority and trust boundary

A Windows-hosted coordinator owns the sole writable authority database **on its own local disk** (not SMB). Only the coordinator can allocate checkout generations, revoke them, assign global commit sequence numbers, validate expected revisions and acknowledge commits. Clients may read an SMB content store but cannot write the coordinator's journal or SQLite database directly. A local SQLite cache is always disposable and never decides ownership. A coordinator outage must make new checkouts, administrative revocations and commits **fail closed**; cached reads may continue with an explicit stale indicator.

The research API is deliberately small:

| Method | Request | Successful response | Required behavior |
| --- | --- | --- | --- |
| \`acquire\` | resource, authenticated principal, idempotency key | opaque lease credential with epoch, generation, owner, resource | Serialize; reject conflicting owner |
| \`verify\` | resource, credential | current revision and epoch | Advisory only; not permission to commit later |
| \`release\` | resource, credential | released or already released | Atomic generation advance; reject stale credential |
| \`override\` | resource, expected generation, reason, authenticated admin identity, request ID | new generation and durable audit ID | Authz; compare-and-revoke in one transaction; retain previous owner evidence |
| \`publish\` | txid, credential, expected record revisions, immutable object manifest, operations | committed sequence and resulting revisions | **Atomic** credential and expected-revision checks with commit marker insertion |
| \`transaction_outcome\` | txid and authenticated principal | committed sequence, rejected reason, or unknown | Resolve lost acknowledgments without duplicating writes |
| \`changes_after\` | epoch, sequence cursor, page size | contiguous changes, high watermark, next cursor | Reject missing sequences or changed epoch; rebuild from authoritative journal |

The prototype's \`actor_claim\` is **not authentication**. Production must bind administrative identity to an authenticated local-LAN session and authorize overrides. “Accountless” means no mandatory cloud identity or subscription, **not** anonymous privilege to revoke colleagues' work. Exact Windows integrated authentication/ACL policy needs architectural approval and threat-model review.

## Publication state machine

1. Client computes immutable content hashes and uploads to an SMB staging namespace inaccessible to other writers. Coordinator verifies SHA-256, length and durable object existence; never trust client-provided hashes without reading bytes.
2. Client submits one \`publish\` request with txid, expected revisions and manifest references. Coordinator obtains exclusive authority transaction, checks current epoch/generation/owner, validates all expected revisions, and commits exactly one ordered manifest/journal marker.
3. Only after authority durability is confirmed may coordinator return committed. Clients refresh local cache by contiguous journal replay. If response is lost, \`transaction_outcome(txid)\` must return the same committed outcome; no second revision.
4. Incomplete staging without a committed marker is garbage-collectable **only after** a safe grace period and proof that no in-flight transaction references it. Never remove objects referenced by a committed manifest.
5. Recovery scans authoritative commit markers, verifies referenced blobs and marks the workspace **read-only/quarantined** on a missing/corrupt committed object. Do not silently “repair” by using a different revision.

**Unresolved atomicity boundary:** SQLite commit and SMB object durability are separate systems. Staging must be verified before the commit, and SMB server crash durability must be certified. An SMB acknowledgment is not automatically proof of power-loss survival. This is a production release blocker.

## Revocation linearization

\`override\` and \`publish\` must serialize on the same durable authority transaction. If \`publish\` commits first, the subsequent override cannot undo it; if \`override\` commits first, the old generation cannot publish. A separate preflight \`verify\` is never sufficient. A revoked user's working files remain locally recoverable for a fresh checkout/rebase, never automatically overwritten.

## Epoch and backup restore

The \`prototype/coordinator_research.py\` separate epoch guard detects a **missing or mismatched guard**, but a database restore from an earlier snapshot in the *same epoch* can resurrect an old owner/generation. The research test intentionally demonstrates this unresolved vulnerability. Before production, implement a trusted **nonrollbackable high-water mark or epoch authority**, or a documented restore procedure that disables all old credentials and rotates to a new epoch before serving requests. Guard and DB cannot both be restored to the same old state. No production safety claim until fault-injection tests cover this.

## Failure-closed conditions

Unknown epoch, absent authority, changed generation, stale expected revision, missing journal sequence, inconsistent cache watermark, corrupt object hash, ambiguous txid, incomplete manifest, disk full, failed durable flush, and unauthorized admin request must not produce an acknowledged new revision. Reconnect requires epoch validation and replay from a known contiguous cursor.

## Acceptance and ownership

Lead Architect: extract the application \`CommitAuthority\` interface and own \`Workspace.commit\` integration. Distributed Systems Engineer: isolated coordinator, durable manifest, epoch and Windows/SMB research rig. QA Engineer: independently reproduce issue #8 and validate crash, network, power and multiuser acceptance. The prototype does not implement authenticated transport, production object durability or the approved deployment as a working product.
