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
- File hashing does not provide an atomic snapshot under concurrent edits; the current prototype should be called against immutable snapshots or a checked-out stable source. Future integration should read from a single file descriptor and verify pre/post metadata if live-file indexing is supported.
- No code or settings are changed in the OpenSchematix repository. Do not bind this provisional adapter to Claude's unfinished service/SQLite contracts without a shared interface decision.

## Run tests

`python -m unittest discover -s tests -v`

Tests cover a deterministic container v1 fixture, legacy file, duplicate IDs, schema rejection, malformed JSON, candidate grouping and no-write verification.

## Phase 1 detection boundary and integration contract (2026-11-01)

Inspected `revisionworx/filetypes.py` and `revisionworx/scan.py` on `phase1-foundation` (tree `23d6ecf0b818702e7a14f1d7a70ce66b0c5783f0`). `classify()` recognizes extensions only. `sniff_openschematix()` verifies the ZIP `mimetype` for `.schematix` and JSON `format` for `.osch`; `scan_scope()` sets `ScannedFile.verified_format = "openschematix"` only after that check. This is **format recognition, not complete parsing or dependency verification**. Scanning is explicitly scoped, skips links/junctions, and does not grant authority over unmanaged projects.

**Proposed adapter handoff (requires Lead Architect approval):** On an explicitly registered/indexed file with `verified_format == "openschematix"`, invoke the isolated `revisionworx_integrations.openschematix.inspect(path)` in a bounded worker. Persist the resulting source digest and extraction version as an *index observation*, not a released revision or authoritative project identity. Link observations to a managed document/revision ID only through the core registry. On hash disagreement, source change, parse failure, unsupported schema or incomplete coverage, mark extraction stale/unknown; never promote inferred part references to verified edges. Do not run adapter inspection on unmanaged directories as a background side effect.

**Important distinction:** OpenSchematix symbol instance ID and designator are local drawing identifiers. Source `manufacturer` and `part_number` identify vendor candidates, not stable RevisionWorx managed part IDs or company part numbers. Copied projects may duplicate native UIDs. For revision-to-revision BOM comparison, compare normalized physical-device groups *within pinned source revisions* and label unmatched/ambiguous identities; do not compare raw symbol counts. Where Used and Before You Revise must expose indexed-scope coverage and unresolved links.

**Lead Architect verification (PR #2 discussion):** On commit `af5307a`, Python 3.13.18/Linux, the Lead Architect executed six tests and reported **6/6 passed**. Three upstream examples matched sheet/component counts (3/26, 1/7, 1/9 respectively). Native BOM lines versus prototype `bom_candidates` differed (16/5, 5/0, 7/0), confirming that `bom_candidates` **is not a BOM**. The Lead Architect also identified a conflict with `docs/DEVELOPMENT_PROGRESS.md` on `phase1-foundation`; PR must remain draft and unmerged until coordinated. These are reported external verification results, not tests executed in this session.
