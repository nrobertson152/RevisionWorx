# ADR-017 proposal — Full-Request Transaction Identity (research, not approved)

**Review target:** Claude's Phase 2 CommitAuthority at d656884b39401134149273dd0121ef620b76eb8e, branch phase2-documents. This document proposes contract semantics for review before a production Workspace-to-CommitAuthority split. The reference serializer and tests are isolated under prototype/; no production core is modified.

## Problem and exact independent proof

Current Workspace.commit compares an existing txid using a projected payload containing only journaled RecordPut / RecordDelete changes and blob references. RecordCheck preconditions are removed because checks are not replayed as writes. As a result, the following are considered identical even though they are distinct caller commands:

- Commit T = (RecordCheck(settings/workspace, rev 1), RecordPut(project A, x, expect_rev 0)), txid X.
- Retry X using (RecordCheck(settings/workspace, rev 999), the identical put).
- Current code returns sequence for T; it does not raise TxidReuseError despite changed read condition.

Additional cases: reordered RecordCheck operations and check-only transactions with altered preconditions also return the old txid outcome. Real Phase 2 Ubuntu/Windows test oracles under prototype/test_phase2_txid_check_reuse.py are explicitly expected failures, not satisfied safety guarantees. This is an **acknowledgement/request identity ambiguity (P1)**. It does not itself prove duplicate mutations or lost acknowledged commits.

## Proposed v2 canonical request

Treat transaction identity as the **complete caller-submitted operation list**. A reference, NON-production algorithm is in prototype/request_identity_research.py.

- Canonical request object: version=2, op, operations (an ordered array), blob_refs (ordered array, null/omitted normalized to empty array).
- Every RecordPut carries type=put, kind, id, data with derived top-level _rev stripped, and expect_rev as a concrete nonnegative integer or JSON null.
- Every RecordDelete carries type=delete, kind, id, expect_rev or JSON null.
- Every RecordCheck carries type=check, kind, id, **mandatory** concrete expect_rev. Check-only commands still have an identity, even if application policy later prohibits them.
- Preserve submitted operation sequence **exactly**, including checks and relative position of writes and deletes. Reordering the operations is a distinct request even when the resulting state might be the same. Reject duplicate/conflicting keys per the chosen higher-level semantic validator; do not silently sort the operation list.
- Sort JSON object keys recursively; UTF-8 encoding; separators comma and colon only; ensure_ascii=False; allow_nan=False; JSON keys must be strings. Do not normalize Unicode codepoints implicitly. The proposed research limit is 1 MiB canonical bytes, not a product limit.
- Compute SHA-256 over the exact byte prefix RevisionWorx.CommitAuthority.request.v2 followed by NUL and the canonical JSON bytes. Store the digest and *version* durably with the committed transaction record. Cryptographic digest is a content identity, **not** caller authentication; signed principals and lease generations remain separate authority checks.

A same-txid retry with exactly matching digest returns the prior effective sequence without rerunning stale expect_rev checks or writing a second mutation, including after lost ACK or a new process restart. A different digest (different checks, order, data, op or blob_refs) raises TxidReuseError and leaves state unchanged. It must not be silently treated as a new transaction and must not be reported as ConflictError solely because the modified check would now fail.

Generation/revocation fencing and per-record expect_rev on a **new** txid must be evaluated inside one authoritative transaction with commit-marker insertion. Durable outcome queries remain available after a lease is revoked, subject to principal authorization. A previously fenced (physically present but ineffective) transaction must never be returned as a committed success.

## Legacy journal/migration dilemma (requires Claude's decision)

Old journal entries contain journaled write records and blob references but omit original RecordCheck inputs. Consequently **it is impossible to reconstruct whether a legacy request had checks, their values or order** from the persisted journal. Recomputing a v2 digest from old entries would fabricate missing evidence.

Proposed non-destructive rollout:
1. Introduce request_identity_version=2 and request_digest on **new** committed entries only. Keep all historical journal files and their content digests unchanged.
2. Existing legacy txids remain queryable through transaction_outcome(txid) as committed/fenced/not-found based on effective journal order.
3. A commit retry referencing a legacy txid cannot be proven identical. Conservatively return a new explicit identity-unverifiable status/error and direct the caller to transaction_outcome rather than acknowledge an allegedly identical request. **Do not** publish a second entry or assign that old txid to new content.
4. If product requirements favor maintaining legacy repeat success for historical workspaces, explicitly accept a weaker, version-scoped contract and document it. Never backfill a claim that missing checks were absent.
5. Preserve the standalone format, implement a versioned format-capability probe, and verify older clients fail closed rather than silently rewriting or rejecting existing workspaces destructively. Test interrupted upgrades and durable ACK discovery.

This migration is **not implemented** by the isolated prototype. Claude owns production format/version decisions and a backwards compatibility policy.

## Conformance additions

The inherited original tests/contract_commit_authority.py is run unchanged against the isolated adapter. Supplemental tests:
- Identical vs altered vs reordered vs dropped checks, changed write input and reordering write operations.
- Lost ACK with reboot/restart, original outcome and one authoritative sequence.
- Authenticated generation revocation, atomic expect_rev and absence of partial multi-record side effects.
- Fenced committed artifact outcomes and unknown/unavailable authorities (not-found must never be invented while disconnected).
- Legacy txid identity-verification mismatch; nonempty blob_refs, remote principal authorization and trust-root rollback remain **not implemented**.

This is a **proposed contract**, not a claimed production implementation, service, Windows authentication mechanism or two-host SMB qualification.
