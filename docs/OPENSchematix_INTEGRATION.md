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
