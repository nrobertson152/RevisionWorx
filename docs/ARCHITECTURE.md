# RevisionWorx — Architecture

**Status: PROPOSED, NOT YET IMPLEMENTED.** The assigned Coder (Claude or ChatGPT) must validate decisions against actual constraints and record architectural decisions here; independent ChatGPT review is required before Director approval.

## System map

```text
Desktop GUI (Windows/Linux)
   |-> application services: projects, drawings, revisions, parts, BOMs, impact
   |-> CAD/OpenSchematix adapter layer
   |-> local SQLite metadata/search cache (disposable, not authoritative)
   |-> workspace synchronization and transaction client
           |
           v
   Configured authoritative .revisionworx on shared filesystem
     registry / change journal / locks / transaction records /
     immutable revision content / integrity and recovery metadata
```

Unmanaged historical projects remain outside routine indexing. Registered projects retain their native directory structures.

## Core data entities

- Workspace: roots, configuration/version, storage capabilities and authorization policy.
- Project: stable ID, current physical locations, company number, customer, mode (unmanaged/indexed/managed), metadata.
- Document: stable ID, file path history, type, lifecycle state, source/derivative relationships.
- Revision: immutable ID, document ID, revision label, content digest, author, time, approval/release state.
- Part and Assembly: stable IDs plus configurable visible part numbers.
- BOM and BOMItem: hierarchical configuration, quantity, referenced part/revision, provenance.
- DependencyEdge: source, target, type, referenced revision, provenance, verification/coverage status.
- Checkout: document/assembly scope, owner, lease/lock state, working copy and authority proof.
- Transaction/AuditEvent: ordered operation, intent, durable outcomes, recovery markers.
- ChangeRequest: affected items and Before You Revise report snapshot.

Distinguish a part *identity* from an individual document file and from a specific revision. Avoid path-based identity.

## Shared filesystem coordination — open design gate

Do not assume atomic rename, lock/create semantics, close-to-open consistency, network lease behavior, durable fsync or directory-entry visibility are universal across SMB/NFS deployments.

Before writing shared-state transactions, document:
1. Supported filesystem and share configuration.
2. Atomic acquisition strategy that resists simultaneous clients.
3. Journal publication and ordering, including crash points and fsync behavior.
4. Lock ownership, explicit administrative recovery and stolen-lock prevention.
5. Recovery state machine for partially written content/metadata.
6. Cache replay and missed-event reconciliation.
7. Multi-machine integration and fault-injection tests.

**Fail closed** when required guarantees cannot be verified. An optional local-network coordination service is a contingency, not an internet/cloud service.

No multi-writer shared SQLite on a network share. Local SQLite caches are read/search accelerators, not checkout authority.

## Search and graph design

- Local SQLite indexes on internal IDs, company numbers, revision IDs, path hints and timestamps.
- FTS5 only for appropriate searchable metadata; use exact B-tree indexes for identifiers.
- Dependency graph: indexed forward and reverse edges, revision-specific references, direct/transitive traversal with cycle detection.
- Where Used and Before You Revise must show coverage and unverified relationships.
- Journal sequence or equivalent authoritative update cursor; detect gaps and rebuild targeted indexes.
- Lazy loading, pagination, background work and bounded memory for millions of files.

## CAD compatibility

Adapters must expose capability and provenance metadata: inspected reference, API-extracted reference, imported BOM association, explicitly declared relationship, inference or unknown. A filename match is not a verified dependency.

Project duplication, rename and move features must preview changes and preserve or safely rebind native CAD references; refuse unsafe automatic edits.

## Implementation decisions pending

- GUI/framework and language selection, with a comparative rationale.
- Shared filesystem support and verified lock/transaction protocol.
- On-disk versioned metadata format and migration policy.
- Revision blob retention, deduplication, quotas and backup consistency.
- Local cache synchronization lifecycle and cross-platform path normalization.
- Actual supported OpenSchematix file formats.
- Supported Inventor/SolidWorks adapters and verified capabilities.
- Access-control enforcement model on shared drives.
- Target operating systems, packaging and test matrix.

Update this document whenever one of these choices is implemented and tested.
