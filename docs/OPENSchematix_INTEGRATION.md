# OpenSchematix integration — isolated prototype

Status: **read-only prototype; not a complete native BOM adapter.**

## Verified source baseline

Inspected OpenSchematix main at 2026-11-01: `openschematix/__init__.py` reports 1.1.0; `projectio.py`, `fileio.py`, `model.py`, `project.py`, `bom.py`, `safejson.py`, `docs/FILE_FORMAT.md`, `docs/LIBRARY_FORMAT.md` and both example `.osch` documents. RevisionWorx currently has design documents but no application code on main. Review current upstream code before treating any interface as immutable.

## Data flow

An explicitly registered document path is passed to `revisionworx_integrations.openschematix.inspect(path)`. It hashes and parses only that file, and returns a JSON-serializable report with provenance, coverage, component inventory, candidate part references, and incomplete-BOM warning. No GUI, database, external service, library traversal, file rewriting, or OS file association is invoked. RevisionWorx's future registry can attach its own immutable document/revision identifiers to the report; do not persist its source path as identity. Treat outputs as an **index**, not authoritative shared metadata.

## File formats

- `.schematix`: ZIP, container format/version 1, first stored `mimetype`, `manifest.json`, `project.json`, `sheets/<uid>.json`, optional embedded `symbols/*.osym`, revision index/objects. `sheet_schema` 2/3; schema 3 enables boundary fills. The prototype reads the manifest, header and current sheets only; it deliberately does not decompress revision history or classify embedded symbol definitions.
- `.osch`: legacy UTF-8 JSON single sheet with `format: "OpenSchematix"`, sheet schema 1 (older exports may use 2/3). No enduring legacy sheet UID is guaranteed. The prototype uses a file-content-derived fallback which changes after edits; it must never be accepted as persistent cross-revision instance identity.
- Neither file has to be opened in the CAD GUI. The parser independently validates format identity, bounded member sizes, relevant types and duplicate IDs. It rejects unsupported container/sheet schema versions and damaged files instead of inventing migrations.
- Project code is the authority on writer constraints. Future format versions require inspection and an explicit compatibility change before support is enabled. Unknown JSON fields are not mutated (the source file is never written); the prototype does not round-trip arbitrary source JSON.

## BOM limitations

Upstream `bom.generate` groups units sharing a designator into devices where not conflicting, merges devices by manufacturer/part number or symbolic identity, handles contact/coil cases and excludes non-purchasable/unchecked symbols. The prototype **does not reimplement these rules** without the source symbol registry; it emits `bom_candidates` with unknown quantity and source instance references. Even the complete native BOM is a schematic BOM, not a manufacturing or purchasing BOM. Never sum candidate source instances to represent a verified quantity.

OpenSchematix `BOM_COLUMNS`: item, quantity, designators, description, manufacturer, part_number, rating, symbol, sheets. Native `BomLine.refs` maps `(sheet_uid, component_id)`. Future accurate BOM import should use a tested native headless BOM bridge and exact source version, or a carefully verified equivalent with known symbol definitions.

## Part association, Where Used and Before You Revise

`props.part_number` is the manufacturer part number field in current source, not an authoritative RevisionWorx part record. A tuple (manufacturer, part_number) yields an **unresolved candidate**, never a confirmed part match. RevisionWorx must store an explicit curated company-part mapping referencing a stable internal part ID and optional exact part revision before emitting a **verified** `document -> part` dependency. Each candidate carries the source instance key, source file SHA-256 and metadata revision. The future dependency graph must attach registration scope, source document revision, verification state, and observed timestamp, and separately report unindexed project coverage. Missing candidate records cannot establish a company-wide absence of use.

## Known gaps / coordination

- Native BOM quantities, symbol exclusions, terminal classification, and contact multi-unit semantics are not reproduced.
- No current revision snapshots, connections/netlist, related exports, external library resolution or managed PDM graph persistence.
- `project_uid/sheet_uid/object_id` is useful within a saved project, but copying an entire project can duplicate IDs in separate projects. RevisionWorx document identity is independently assigned and required for cross-project uniqueness.
- Hashing and parsing now operate on one bounded in-memory byte snapshot, avoiding hash/parse disagreement. Before/after descriptor metadata checks reject detectable concurrent edits during capture; however, those checks are not a filesystem lock, and a changed source can be missed if its size and timestamps are restored. For authoritative revision-linked extraction, prefer immutable content-addressed revision bytes, not a live working file.
- No code or settings are changed in the OpenSchematix repository. Do not bind this provisional adapter to Claude's unfinished service/SQLite contracts without a shared interface decision.

## Run tests

`python -m unittest discover -s tests -v`

Tests cover a deterministic container v1 fixture, legacy file, duplicate IDs, schema rejection, malformed JSON, candidate grouping and no-write verification.

## Phase 1 detection boundary and integration contract (2026-11-01)

Inspected `revisionworx/filetypes.py` and `revisionworx/scan.py` on `phase1-foundation` (tree `23d6ecf0b818702e7a14f1d7a70ce66b0c5783f0`). `classify()` recognizes extensions only. `sniff_openschematix()` verifies the ZIP `mimetype` for `.schematix` and JSON `format` for `.osch`; `scan_scope()` sets `ScannedFile.verified_format = "openschematix"` only after that check. This is **format recognition, not complete parsing or dependency verification**. Scanning is explicitly scoped, skips links/junctions, and does not grant authority over unmanaged projects.

**Proposed adapter handoff (requires Lead Architect approval):** On an explicitly registered/indexed file with `verified_format == "openschematix"`, invoke the isolated `revisionworx_integrations.openschematix.inspect(path)` in a bounded worker. Persist the resulting source digest and extraction version as an *index observation*, not a released revision or authoritative project identity. Link observations to a managed document/revision ID only through the core registry. On hash disagreement, source change, parse failure, unsupported schema or incomplete coverage, mark extraction stale/unknown; never promote inferred part references to verified edges. Do not run adapter inspection on unmanaged directories as a background side effect.

**Important distinction:** OpenSchematix symbol instance ID and designator are local drawing identifiers. Source `manufacturer` and `part_number` identify vendor candidates, not stable RevisionWorx managed part IDs or company part numbers. Copied projects may duplicate native UIDs. For revision-to-revision BOM comparison, compare normalized physical-device groups *within pinned source revisions* and label unmatched/ambiguous identities; do not compare raw symbol counts. Where Used and Before You Revise must expose indexed-scope coverage and unresolved links.

**Lead Architect verification (PR #2 discussion):** On commit `af5307a`, Python 3.13.18/Linux, the Lead Architect executed six tests and reported **6/6 passed**. Three upstream examples matched sheet/component counts (3/26, 1/7, 1/9 respectively). Native BOM lines versus prototype `bom_candidates` differed (16/5, 5/0, 7/0), confirming that `bom_candidates` **is not a BOM**. The Lead Architect also identified a conflict with `docs/DEVELOPMENT_PROGRESS.md` on `phase1-foundation`; PR must remain draft and unmerged until coordinated. These are reported external verification results, not tests executed in this session.

## Phase 2 interface review and real fixture coverage (2026-11-01)

Reviewed draft PR #6 (Phase 1 reliability and PySide6 migration), stacked draft PR #7 (Phase 2), ADR-010 through ADR-013 and `revisionworx/documents.py`. Managed document records use stable `doc_*` IDs; immutable revision records use `rev_*` IDs and `sha256` content digests with content objects stored under `.revisionworx/content/sha256/<aa>/<digest>`. Native schematic working files remain in original project paths; indexed-only projects are read-only, and document registration is restricted to managed projects. These decisions are owned by the Lead Architect and are **not** changed by this adapter.

**Proposed, not yet approved, integration boundary:** `inspect(path)` returns a content-addressed, read-only observation. A later adapter caller may attach `document_id`, `revision_id`, and source SHA-256 only if they match the authoritative revision record and verified content store object. No adapter call may create/release a revision or mutate checkout state. A live working-file observation with the same path as a registered document is still not proof that the content equals its immutable revision. Unknown, unresolved and stale manufacturer references must remain unverified until explicit company part mapping.

**Real upstream example fixtures:** `tests/fixtures/openschematix/demo_project.schematix` and `start_stop_latch.osch` were copied byte-identically from the Phase 1 fixture blobs (original OpenSchematix examples). Tests assert three sheets/26 components/five part-number candidates for the native example, and one sheet/seven components/zero candidates for legacy. The Lead Architect previously reported native BOM counts of **16** and **5**, respectively; those are reference expectations for future native BOM equivalence tests, **not** quantities returned by the prototype. No BOM line parity is claimed.

**Hardening:** The adapter now rejects symlinks and non-regular files, reads one bounded snapshot, verifies same-descriptor metadata before/after reading, refuses oversized input and ZIP manifests, guards individual decompression reads, rejects duplicate or unsafe archive member names (including traversal and backslashes), and rejects overly numerous members. Each check is regression-targeted. No network paths are scanned automatically; no SMB/NFS guarantees are implied.

**Open issue for Claude:** Clarify whether Phase 5 callers should inspect the immutable content store path directly (best for revision correspondence) versus a temporary copy with the registered suffix (the parser currently dispatches by suffix). An approved public revision-content read interface would avoid binding the adapter to the content store's filesystem layout. Also coordinate retargeting PR #2 after Phase 1/2 merges; the old `docs/DEVELOPMENT_PROGRESS.md` edit conflicts with Phase 1.

## GitHub Actions verification and third real fixture (2026-11-01)

A dedicated Python 3.11/3.13 workflow now runs the isolated adapter tests on pushes to `integration/openschematix`: `.github/workflows/openschematix-adapter.yml`. This provides actual executable branch verification rather than extrapolating the Lead Architect's old six-test result. The first observed CI execution (run `41373728900`) found 22/23 passing, with a single incorrect exception classification for an oversized `mimetype` ZIP member; the adapter now raises `InvalidProject` for structurally invalid MIME member length and reserves `UnsupportedFormat` for an otherwise valid-length but unrecognized MIME value (fix `5eaea775`). The current head requires a new successful run before the branch can be called verified.

Added `hydraulic_cylinder_circuit.osch` directly from upstream OpenSchematix 1.1.0 blob `ae01e0ce70ae89292f2a1a3f05b3904a6959c62c`. The parser test expects one sheet, nine components, and zero manufacturer-part candidates. Upstream `bom.generate` reports seven BOM lines for this fixture (Lead Architect verification). This is additional evidence that candidate extraction is **not** a native BOM. A future complete native BOM adapter must account for the upstream symbol registry and physical designator grouping; do not infer BOM line count from candidate count.

## Proposed immutable-revision indexing contract (awaiting Lead Architect approval)

**Boundary:** `inspect_bytes(revision_content: bytes, suffix: str) -> dict` is a pure read-only extraction operation. It takes the **exact bytes of an already-registered immutable document revision**, not a mutable working-copy path or a workspace directory. It does not perform PDM writes, register parts, resolve dependencies or alter OpenSchematix files.

**Caller preconditions:** obtain the document/revision identifier, original filename extension, and expected SHA-256 from the authoritative RevisionWorx revision record; retrieve its immutable content; verify `sha256(content) == expected_revision_sha256` before attaching any observation to the revision. Reject a mismatch, unavailable content, or unsupported extension without publishing an index record. This is a proposed caller responsibility, not implemented inside the adapter.

**Output:** a JSON-serializable observation carrying `adapter`, `source_format`, `file_sha256`, optional native `project_uid`, sheet/component metadata, `dependencies`, `bom_candidates`, `bom_status`, `coverage`, and `warnings`. The index owner should associate it with the authoritative `document_id`, `revision_id`, source SHA-256 and adapter version. These associations are **external** to the report. No `instance_id` is globally unique: native `project_uid/sheet_uid/object_id` may be cloned by copying a project; legacy IDs are content-derived. Distinguish source-instance identity from managed part identity.

**Trust states:** `verification: unresolved` means the source supplies a manufacturer/part-number candidate but no verified RevisionWorx managed part association. `quantity: null` means unknown, not zero. `bom_status: candidate_only_not_equivalent_to_native_bom` prohibits labeling candidate groups as an engineering BOM. `coverage` reports observed sheets/components but never asserts complete library, external-project or revision-history coverage. Unknown source properties are preserved in the observation and not interpreted as authoritative PDM facts.

**Failure behavior:** `UnsupportedFormat` means a future or unrecognized supported-extension schema/format; `InvalidProject` means malformed, inconsistent or resource-limit-exceeding content. On either exception the caller must leave prior authoritative revision state untouched, record indexing failure separately, and avoid reusing a stale observation under the new digest. Indexing can be retried from the same immutable bytes. The adapter never requires the OpenSchematix GUI or networking.

**Approval gates:** Claude must approve (1) the immutable content retrieval and SHA verification boundary, (2) ownership and retention of disposable observations, (3) future managed-part matching and native BOM semantics, and (4) sequencing with Phase 1/2 PRs. This document is an interface proposal, not an authorization to modify core storage.
