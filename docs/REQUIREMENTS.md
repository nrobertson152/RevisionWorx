# RevisionWorx — Product and Engineering Requirements

**Status:** Product specification; implementation has not been verified.
**Product:** RevisionWorx — lightweight engineering Product Data Management (PDM)
**Platforms:** Windows and Linux
**License:** GNU GPL version 3, as carried in the repository LICENSE; check dependency compatibility before use.
**Related application:** [OpenSchematix](https://github.com/nrobertson152/OpenSchematix)

## 1. Mission and operating principles

Develop a fast, dependable, offline, accountless and telemetry-free desktop PDM system for small and medium engineering departments. Manage engineering projects, revisions, drawings, parts, BOMs, native CAD files, dependencies, and collaboration without imposing enterprise infrastructure or reorganizing existing engineering archives.

Priorities, in order: data integrity; non-destructive CAD reference preservation; safe collaboration; correct dependency/impact reporting; performance; usability.

All implementation claims require verification. Unsupported integrations must be labeled unsupported or experimental.

## 2. Universal workspace setup and gradual adoption

- Accept one or more user-selected local or network engineering roots; accommodate arbitrary hierarchies organized by customer, project, year, product or department.
- Use stable internal project/document/part/revision identifiers separate from paths and company-visible numbers.
- Support company-defined recognition patterns, identifiers, customer classifications, numbering conventions, revision schemes, folder templates, file associations, and exclusions.
- Never recursively index the entire engineering share at startup or initial setup.
- Shared authoritative metadata lives in a configured `.revisionworx` directory; it must not be replicated into each legacy project folder.
- **Unmanaged:** Ordinary old files and folders, untouched by PDM.
- **Indexed:** Searchable, read-only reference metadata without managed checkouts or asserted revision history.
- **Managed:** Full revision control, release records, checkout, BOM, parts and dependencies.
- Newly created projects default to Managed, subject to company policy.
- An explicit Adopt Existing Project action previews the selected scope, scans only that scope and explicitly chosen references, establishes an initial baseline, warns about ambiguity and preserves files and CAD paths. Never fabricate historical revisions.
- Optional historical discovery: targeted or full opt-in scanning with exclusions, throttling, progress, cancellation, resume and coverage disclosure. Never block startup.

## 3. Project and document management

- Create/register/open/search/archive/duplicate projects, assign customers and metadata, launch files through external applications, browse arbitrary nested folders, and maintain an engineering drawing register.
- Configurable nested folder templates, starter files, per-customer/project-type variants, import/export.
- Handle arbitrary file types even when contents cannot be parsed.
- Preserve original project layout; renames, relocations and project duplication require dependency-aware preview and validation. Never silently rewrite Inventor or SolidWorks references.
- Drawing register: number, title, project, customer, part/assembly, revision, owner, checker, approver, date, source file, associated exports, BOM and history.
- Configurable lifecycle with initial statuses Work in Progress, In Review, Approved, Released, Superseded and Obsolete; audit transitions, approvals and releases separately.
- Explicit association of native source drawings with PDF/DXF/DWG derivatives, including revision consistency, missing and potentially stale export warnings. Filename similarity is not proof of identity.

## 4. Revision control and check-in/check-out

- Stable document identities; separate working, committed and released revisions with configurable alphabetic/numeric/company conventions.
- Record author, time, change description, approvals, immutable released content snapshots and SHA-256 integrity hashes.
- Retain and retrieve historical revisions; safe restoration must create traceable new operations, not silently rewrite history.
- Atomic exclusive checkout ownership, timestamps, controlled working copies, cancel checkout, audit trail, administrative recovery and conflict detection.
- Handle multi-file and assembly-aware checkout explicitly; top-level assembly locking does not automatically lock dependencies.
- Never mark a revision published before metadata and file contents are durably consistent.
- Direct filesystem edits may bypass the program; detect unexpected edits and explain permission-enforcement limitations.
- Network disconnection permits viewing and isolated offline work, but not fresh authoritative checkout, check-in or publication. Reconnect must reconcile ownership, hashes and conflicting revisions.

## 5. Collaboration and authoritative shared data

Preferred deployment: local clients and local SQLite metadata/search caches, with a shared network `.revisionworx` authoritative registry, locks, immutable revisions, durable journals and recovery state. No required cloud or dedicated server.

A file share alone is **not** a transactional database. Design, prove and test atomic coordination semantics on supported SMB/NFS filesystem configurations, including race prevention, interrupted writes, disk-full, lost connectivity, permissions, competing clients and stale locks. Never use naive check-then-create locks, silently steal a stale lock, or place a single writable SQLite database on a network share for concurrent clients.

If safe lock and publication guarantees cannot be established on a filesystem, fail closed for writes and propose an optional on-premise LAN coordinator; do not market unsafe collaboration as reliable. Local indexes are caches only and must never authorize a checkout based on stale information.

## 6. Parts, BOMs and configurable numbering

- Stable internal part identities plus configurable company-visible part/drawing numbering rules (patterns, prefixes, suffixes, sequences, reserved ranges, imported identifiers, duplicate detection and manual exceptions).
- Part registry, assembly registry, manufacturer/supplier identifiers, descriptions and revision references.
- Structured engineering BOM: hierarchical parent-child items, quantities, units, part revisions, drawing/schematic links, CSV/XLSX import/export, historical comparisons. Distinguish engineering BOM from manufacturing/purchasing BOM.
- OpenSchematix schematic BOM adapters, plus explicit manual associations; never equate partial schematic component counts to a complete manufacturing BOM.

## 7. CAD and OpenSchematix integration

- Recognize Inventor (.ipt, .iam, .idw, .ipn, .ipj), SolidWorks (.sldprt, .sldasm, .slddrw), STEP/IGES, DXF/DWG, PDF and OpenSchematix (.schematix/.osch where confirmed).
- Index native CAD dependencies only through verified reference extraction, supported APIs or declared links. Clearly label unverified/unknown references.
- Never flatten assembly folders, rename references or rewrite files without native-aware verified operations.
- Inspect the actual [OpenSchematix repository](https://github.com/nrobertson152/OpenSchematix) to determine true serialization, symbol/component identities, BOM export, schema versions and interoperability opportunities.
- Register and open OpenSchematix projects; import supported BOMs and component associations; track schematic revisions and participate in where-used impact reports.
- Do not change OpenSchematix source without explicit approval.

## 8. Global dependencies and Where Used

Maintain an indexed, cross-project dependency graph among parts, documents, drawings, BOM items, schematics, models, assemblies, released configurations and projects, including stable IDs, edge type, referenced revision, quantity where known, provenance and verification state.

**Where Used** must answer "Find every known project that uses this part" via direct and transitive reverse relationships, with cycle detection and revision-aware results. Show project/customer, assembly, referenced revision, quantity where known, source and confidence/verification level.

Never report a company-wide absence of usage when unmanaged/unindexed projects were not searched. Display coverage scope clearly.

## 9. Before You Revise — core feature

Expose **Before You Revise** in part, drawing, document, assembly and revision workflows.

Before a user revises an item:
1. Query indexed direct and transitive downstream dependencies.
2. Show affected assemblies, projects, customers, BOMs, related drawings, schematic usages and released configurations.
3. Identify the **specific part/document revision** each dependent configuration references.
4. Flag missing, inferred, uncertain, stale or unindexed dependencies distinctly from verified ones.
5. Classify as directly referenced, indirectly referenced, released configuration to evaluate, drawing/BOM review suggested, stale derivative suspected, or verification required.
6. Offer a report export (PDF/CSV) and optional engineering change record.
7. Permit the engineer to proceed to controlled checkout/revision after reviewing the report, consistent with configurable policy.

An affected relationship is an engineering review signal, **not** proof of incompatibility or mandatory redesign. Do not suppress coverage warnings in export reports. Support impact comparison between revisions as data becomes available.

## 10. Search and scale

- Use local SQLite indexed tables for exact project, drawing and part lookups, SQLite FTS5 for appropriate text metadata, and indexed forward/reverse dependency edges.
- Incrementally synchronize changes using authoritative ordered journal/sequence data and targeted reconciliation, with missed-update detection and stale-cache indicators.
- Lazy-load project summaries and paginate or virtualize large tables. Background workers handle discovery, hashing, sync and slow I/O without freezing the GUI.
- Search must not traverse unmanaged customer folders.
- Benchmark representative datasets of 10,000 projects, 2,000,000 indexed files and 500,000 dependency edges, plus 50,000 unmanaged folders and concurrent clients.
- Aspirational on suitable hardware: exact lookup <50 ms; common indexed metadata search <100 ms; typical where-used <200 ms. Treat these as *targets*, never existing results.
- Test cold startup, incremental sync, latency, memory, loss of network, and deep/cyclic dependency graphs.

## 11. Integrity, backup and change management

- Project-scoped and selected-file SHA-256 verification, external modification and missing/broken reference detection. Never hash entire legacy shares automatically.
- Durable backup of mutually consistent authoritative metadata and revision blobs; checksum verification, retention, restoration preview, interrupted-operation recovery. Encourage separate physical backup destination.
- Engineering Change Request/Order workflow: reasons, affected artifacts, impact report, review/approval and release linkage. Make workflow configurable for small teams; no mandatory bureaucracy.

## 12. User experience

Modern Windows/Linux desktop GUI with projects, files, drawings, parts, BOMs, Where Used, Before You Revise, checkouts, changes, search, recovery and settings. Provide configurable lists, keyboard shortcuts, filters, quick search, pinned/recent items, useful progress and actionable errors. Do not freeze the UI during network I/O. Keep network and permission failures visible.

## 13. Security, reliability and acceptance tests

Honor operating-system and share permissions; a hidden metadata folder provides no security. Audit metadata/release actions. Avoid plaintext secrets. Validate paths, symlinks, junctions and Windows/Linux case and naming differences.

Require tests for:
- Non-destructive adoption across three unrelated company directory conventions.
- No startup traversal of 50,000 unmanaged directories.
- Concurrent checkout races on actual supported shares.
- Disconnection, crash, incomplete journal, permission and disk-full recovery.
- Historical revision immutability and restore consistency.
- CAD reference preservation, association verification and unsupported formats.
- Part numbering collisions and independent numbering strategies.
- Indexed where-used/Before You Revise direct, indirect, cyclic, cross-project, version-specific and partial-coverage cases.
- Backup restore consistency, incremental synchronization, scalability and packaging.

## 14. Delivery milestones

1. Repository foundation, architecture decision, app skeleton, workspace setup, opt-in registry, template/project creation, local index and tests.
2. Drawing register, lifecycle, immutable revision storage, verified checkout/check-in and failure recovery.
3. Tested shared-folder collaboration or demonstrably safe optional coordinator.
4. Parts/assemblies, numbering rules, dependency graph, Where Used and Before You Revise.
5. OpenSchematix adapter, BOMs, source/derivative associations.
6. CAD adapters, reference-safe duplication and assembly-aware operations.
7. Backups, engineering changes, advanced reports and administrative recovery.
8. Full regression, stress tests, documentation and cross-platform packages.

Ship milestones only when their data-integrity prerequisites are established. Maintain factual progress notes. Do not publish a release without user approval.
