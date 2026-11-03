# Orphan custody and exactly-once administrative recovery review

Research specification, not an adopted production design. Production pin:
`366d4355abcde7543122551fadaab01ac9832e01`. Backport pin:
`36d1957fe8d7800659b488106ef6cec1fa0c6267`.

## Required logical semantics

One orphan incarnation must have at most one effective recovery authorization
and one terminal custody outcome. Identify the incarnation using workspace
identity plus a durable recovery identifier: pathname alone is reusable, and
pathname plus digest cannot distinguish recreated identical bytes. Claude must
define the incarnation/identity contract before production extraction.

Requests with the same recovery identifier and identical authorized payload
return the original outcome after restart or lost acknowledgement. A different
reason or principal must not silently replace the original authorization or
create another effective clear. Reject it as a conflict or append an explicitly
non-authorizing supplemental observation. Concurrent admins must serialize the
check and authorization at one authority transaction. Independent journal slot
allocation does not implement this compare-and-set.

Proposed states: guarded -> authorized/pending custody -> verified custody ->
complete. Authorization records bind the incarnation, exact digest, evidence
identity, authenticated administrator and reason. Completion requires verification
of the bytes actually placed in custody. Duplicate invocation resumes the same
transition. Missing, ambiguous, mismatched or multiply claimed evidence remains
quarantined; absence of the original pathname cannot establish completion.
A durable pending barrier must survive crashes independently of source-file
existence and prevent acquisition until completion is established.

## Deterministic observations and remaining tests

The original in-place byte-swap reproducer is preserved unchanged. New probes
in `prototype/test_phase2_orphan_clear_protocol.py` establish:

| Injection | Pinned local result |
| --- | --- |
| Replace source after authorization, before final hash read | Mismatch leaves source and blocks acquisition |
| Replace source object after final hash read, immediately before custody rename | Different bytes archived; successor can commit (unsafe) |
| Hold original file descriptor while replacing pathname on POSIX | Descriptor reads original bytes; archived object contains replacement bytes |
| Independent process exits immediately after evidence move | Existing test finds prior audit and retained evidence |
| Overlapping clear requests with different reasons | Existing test finds two effective clear audits for one original orphan |

An open descriptor binds an object, not a later pathname lookup. Atomic rename
linearizes a namespace move; it does not atomically bind an earlier content hash,
make file contents immutable, or prove persistence across power loss. Post-move
verification alone is insufficient if a crash removes the guard before that
verification. Even correct verification cannot protect against unrestricted
post-completion mutation: the design must state who can modify evidence and
enforce that boundary. Windows handle sharing and POSIX open-file replacement
are different; the Windows branch of the new probe closes the descriptor before
replacement and makes no claim about share-delete handles.

When a corrected SHA exists, rerun the unchanged original unsafe interleaving
first, then inject replacement before/during/after the new custody transition,
including destination collision, pending-state restart, repeated authorization
and disappearance of both source and evidence. Use positive fail-closed oracles;
do not interpret a baseline-specific unsafe reproducer failing as acceptance.
No corrected SHA was available at this checkpoint. No physical SMB, malicious
remote server, power-loss or Windows run was executed in this session.

## Phase 1 applicability and coordinator boundary

Successor ownership preservation and unknown-owner fail-closed behavior apply
to Phase 1 too. Its frozen backport still deletes by pathname after checking a
token, and is not acceptable. Phase 2 audit-first custody, restart reconciliation
and ADR-021 identity depend on APIs absent from that backport; verify a proposed
backport's actual authority and journal facilities rather than copying helpers.

ADR-021 interoperability and the existing authority adapter remain locally
passing. Preserve full submitted operation ordering, RecordCheck preconditions,
legacy IdentityUnverifiable behavior and lost-ACK replay in any future extraction.
Recovery identity, authenticated outcome-query scope, external nonrollbackable
authority and evidence mutation permissions remain design decisions. No network
coordinator is implemented or approved by these tests.
