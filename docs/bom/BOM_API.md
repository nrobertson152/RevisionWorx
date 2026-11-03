# RevisionWorx advanced BOM and assembly layer: design and API (issue #18)

**Owner:** Claude C. Since 2026-11-03 this includes the base parts catalog that was formerly issue #15 (DeepSeek
removed; #15 closed). **Branch:** `feature/advanced-bom`, based on accepted core `fe8a179`.

**Status:** implemented and tested as an isolated package. It is **not integrated** with the GUI. The workspace
persistence adapter (section 6) is implemented and tested on disposable workspaces, but stays **PROPOSED until
Claude A reviews it**.

Package: `revisionworx/bom/`. It uses the standard library only and works offline. It changes no core file.

## 1. Boundaries

| Concern | Owner | This package |
|---|---|---|
| Part identity, part numbers, numbering rules, manufacturer/vendor data | Claude C (this package) | `parts.py`, `catalog_service.py`, `parts_csv.py` |
| Single-level BOM editing and catalog/BOM persistence | Claude C; Claude A reviews the store | `catalog_service.py`, `store.py` (PROPOSED) |
| Journal, locks, transactions, revisions, recovery | Claude A (core) | Untouched. The workspace store calls only `register_kind` and `Workspace.get/list/commit`; the revision adapter is read-only |
| Change control (ECR/ECO) | Claude D (#19, PR #20) | `integrations.ChangeControlWhereUsed` implements its `WhereUsedProvider` |
| GUI widgets and main window | Claude B (#13) | Toolkit-free view models only (`viewmodels.py`) |
| OpenSchematix BOM adapter | Integration (PR #2) | Provides `Provenance.SCHEMATIC`. Candidates stay unresolved (PR #2 decision 5) |

Core calls used, all read-only:
- `documents.list_documents` and `documents.history` (CORE_API.md section 3, Stable);
- `documents.read_revision_bytes` (ADR-018, the verified bounded read for integrations). It is not yet listed
  in CORE_API.md, so a request to add it is on PR #7.

## 2. Domain invariants (each one is tested)

1. **Exact quantities.**
   - Quantities are `Decimal` values, never `float`.
   - All arithmetic runs in a context that traps `Inexact` and `Rounded`, so a result that would need
     rounding raises `QuantityError` instead.
   - Text is strict, such as `2` or `0.25`. Signs, exponents, decimal commas and thousands separators are
     refused, each with a reason.
2. **Line quantity.**
   - Each line's quantity is greater than 0, at most 10^9 and has at most 6 decimal places.
   - Count units (`EA`) must be whole numbers.
   - Totals are capped at 10^15 (`LimitExceeded`).
   - All limits live in `Limits` and can be configured.
3. **Units.**
   - A line's quantity is per one of the parent's **own** unit, its `PartInfo.unit`.
   - Totals for an item are kept in that item's own unit, converted exactly from the line units. For
     example, 500 mm of a part stocked in m adds up as 0.5 m.
   - Converting across dimensions raises `UnitError`.
4. **Structure is a graph.**
   - A shared subassembly is defined once.
   - Rollup propagates totals in topological order in O(items + lines), so 2^30 paths are not a problem.
   - The indented explosion lists every path and stops at `max_explosion_rows`.
5. **Cycles.**
   - `find_cycles` (Tarjan) reports one named loop per strongly connected group, rotated and sorted.
   - Explosion and rollup refuse a reachable cycle with `CycleError(cycle)`.
   - Where-used and impact never fail on a cycle. They report quantity "unknown" instead.
6. **Determinism.**
   - `Structure` sorts its lines and parts canonically. Input order never changes equality, serialization,
     the digest, report rows or validation output.
   - Text is NFC-normalized.
7. **Immutability.** Structures, lines, parts and snapshots are frozen. Edits return new values.
8. **Honest identities.**
   - Without a catalog, a part number imported from CSV becomes `pn:<casefolded number>` with
     `resolved=False`, and validation reports it as `unresolved-identity`.
   - Lines with provenance `inferred` or `unknown` are flagged `needs-verification`. The flag carries through
     rollup, where-used and impact.
9. **Coverage.**
   - Impact reports list every snapshot searched.
   - They never claim an item is unused anywhere.
   - They state that an affected relationship is a review signal, not proof of incompatibility (REQUIREMENTS.md
     section 9).

## 3. API (`from revisionworx import bom`)

**Values**

```python
PartInfo(part_id, number="", description="", unit="EA", revision="", kind="", resolved=True)
BomLine(parent_id, child_id, quantity, unit="EA", find_number="", reference_designators=(),
        child_revision="", line_id="", provenance="declared", note="", attributes=())
Structure.build(lines, parts)
  .children(parent) / .usages(child) / .top_level_ids / .assembly_ids / .item_ids
  .with_lines(*l) / .without_line(l) / .replacing_line(old, new) / .with_parts(*p)
  .with_single_level(parent, lines) / .restricted_to(root) / .merged_with(other)
BomSnapshot(structure, root_id, label="", source=SourceRef(...))   # .digest .released .display_name
SourceRef(kind, document_id, revision_id, revision_label, revision_number, sha256, path, state)
```

**Algorithms**

```python
find_cycles(s) -> list[tuple[str, ...]]
topological_order(s, root=None) -> list[str]                         # CycleError
explode(s, root, quantity=1, max_level=None, cancelled=None) -> Explosion(rows: ExplosionRow...)
rollup(s, root, quantity=1, leaves_only=False) -> Rollup(rows: RollupRow...)
where_used(s, item) -> WhereUsed(usages: Usage..., cycles)
validate(s, catalog=None, roots=()) -> ValidationReport(issues: Issue...)    # .ok .errors .warnings
compare(a, b) -> BomDiff(changes: LineChange..., part_changes)       # field-level, deterministic matching
compare_rollups(a, root_a, b, root_b) -> tuple[QuantityDelta, ...]
revision_impact(snapshots, item, current_revision="") -> RevisionImpactReport   # Before You Revise
change_impact(before, after, context_snapshots) -> ChangeImpactReport           # what-if, nothing written
```

Validation codes:
- **error:** `self-reference`, `cycle`, `invalid-quantity`, `unknown-unit`, `unit-mismatch`,
  `duplicate-find-number`, `duplicate-refdes`, `duplicate-part-number`, `dangling-reference` (with a catalog),
  `unknown-root`, `too-deep`, `too-many-lines`;
- **warning:** `duplicate-child`, `refdes-count-mismatch`, `orphan-assembly` (with roots), `missing-part-info`;
- **info:** `unresolved-identity`, `needs-verification`.

**Serialization**

```python
structure_to_json(s) / structure_from_json(b)      # canonical, sorted keys, quantities as decimal text
snapshot_to_json(snap) / snapshot_from_json(b)     # verifies the stored digest
load_any(b)
structure_digest(s)                                # SHA-256 of canonical compact JSON
```

The JSON format is `revisionworx.bom`, version 1. Loading fails closed. Each of the following raises
`BomFormatError` and loads nothing:
- an unknown format;
- a newer version;
- unknown fields;
- JSON numbers, NaN or other constants;
- invalid values;
- a digest mismatch.

**CSV** (`bom.csvio`). A `bytes` argument is file content; a `str` or `Path` argument is a file path.

```python
structure_to_csv(s, formula_guard=True, excel=True) -> bytes        # lossless parent-child layout
structure_from_csv(src, catalog=None, encoding="utf-8-sig", ...) -> Structure
structure_from_indented_csv(src, root_number="", ...) -> (Structure, root_id)   # CAD level layout
explosion_to_csv / rollup_to_csv / diff_to_csv / quantity_deltas_to_csv / issues_to_csv
revision_impact_to_csv / change_impact_to_csv                       # reports include coverage rows
save(data, path, overwrite=False)                                   # all-or-nothing write
```

- **Parent-child layout.**
  - Rows with an empty parent declare parts: number, id, description, `part_unit`, revision, kind and
    resolved.
  - The other rows are lines.
  - Extra columns named `attr:<Name>` are kept as line attributes.
  - Common header spellings are accepted, such as `Qty`, `UOM`, `Part Number` and `RefDes`.
- **Errors.** Import is all-or-nothing. `CsvImportError.problems` gives the physical line, the column and a
  message, capped at 200 problems.
- **Refused input.** NUL bytes, malformed quoting, ragged rows, duplicate headers, unknown encodings, and files
  over 64 MiB or 1,000,000 rows are each refused with a reason.
- **Unicode.** UTF-8 is read with or without a byte-order mark. Other encodings must be named, such as
  `encoding="cp1252"`.
- **Formula guard.** Exported text cells that begin with `= + - @`, tab or CR get a `'` prefix. Import
  reverses exactly that, so the round trip is lossless.
- **Line endings.** Exports use CRLF line endings (RFC 4180).

**Safe writes** (`bom.fileio.write_file`). The data is written to a hidden temp file in the destination
folder and fsynced. Then:
- with `overwrite=True`, `os.replace` swaps it in, with a bounded retry for Windows sharing violations;
- otherwise it is published exclusively: `link` on POSIX, `rename` on Windows.

On any failure the temp file is removed and the destination is unchanged. The function refuses:
- any path inside a `.revisionworx` folder (checked case-insensitively);
- a symbolic link;
- a folder;
- a missing parent folder.

This mirrors `revisionworx.atomicio` but does not import it, because that module is core-internal.

**Workspace adapter** (`bom.workspace_adapter.WorkspaceBomReader(ws, catalog=None, max_bytes=32 MiB)`)

```python
.bom_documents(project_id=None)          # registered .csv/.json documents
.revisions(document_id) -> [BomRevision]
.snapshot(revision_id, root_number="", layout="auto")      # SourceRef carries doc/rev/label/sha256/state
.compare_revisions(rev_a, rev_b) -> (snap_a, snap_b, BomDiff)
.latest_snapshot(document_id)
```

- A read-only handle is enough.
- Content comes from the immutable revision, verified against its SHA-256, never from the working file.
- Damaged content raises the core's `WorkspaceCorruptError` unchanged, so the adapter fails closed.
- Content over the size limit raises `BomSourceError`.

**GUI view models** (`bom.viewmodels`, `VIEWMODEL_CONTRACT = 1`). These are for Claude B.
- Functions: `indented_rows`, `summary_rows`, `change_rows`, `issue_rows`, `where_used_rows`. Rows hold
  display strings plus raw values and flags (`unresolved`, `needs-verification`).
- `BomSession` is an in-memory working set. It can load files, validate, compare, show summary change and run
  impact. It exports only when asked.
- The functions are pure, so they can run in the controller's background jobs. `explode` accepts a
  `cancelled()` callback.

**CLI**: `python -m revisionworx.bom [--layout L] [--root PN] [-o FILE [--overwrite]] <command>`

- Commands: `validate`, `explode`, `rollup [--leaves-only] [--quantity Q]`, `diff [--summary] A B`,
  `impact PART FILES...`, `change-impact BEFORE AFTER [CONTEXT...]`, `convert`.
- It works on files only and never opens a workspace.
- Exit status: 0 for success, 1 when validation finds errors, 2 for unreadable input or a failed command.

## 4. Examples

`docs/bom/examples/`:
- `hpu-frame-rev-A.csv` and `hpu-frame-rev-B.csv`: two revisions of a frame with a shared sub-assembly and a
  hose measured in metres;
- `hpu-skid.csv`: a product that uses the frame;
- `pump-indented.csv`: a CAD-style indented export.

```sh
python -m revisionworx.bom diff docs/bom/examples/hpu-frame-rev-A.csv docs/bom/examples/hpu-frame-rev-B.csv
python -m revisionworx.bom diff --summary docs/bom/examples/hpu-frame-rev-A.csv docs/bom/examples/hpu-frame-rev-B.csv
python -m revisionworx.bom impact BOLT-M10-30 docs/bom/examples/*.csv
python -m revisionworx.bom --root HPU-FRAME change-impact docs/bom/examples/hpu-frame-rev-A.csv \
    docs/bom/examples/hpu-frame-rev-B.csv docs/bom/examples/hpu-skid.csv
python -m revisionworx.bom explode docs/bom/examples/pump-indented.csv
```

## 5. Integration contract and open items

- **Catalog.** `PartsCatalogService.catalog()` returns a `CatalogReader`. With it:
  - CSV import resolves numbers to stable ids;
  - validation reports `dangling-reference`;
  - `import_structure` turns unresolved `pn:` placeholders into catalog parts.
- **Authoritative BOM persistence.** `WorkspaceCatalogStore` (section 6) is implemented and tested but stays
  PROPOSED until Claude A reviews it. Teams can also keep BOMs as ordinary revisioned CSV/JSON documents; the
  adapter compares their revisions.
- **Change control (Claude D).** `ChangeControlWhereUsed(service.structure)` returns PR #20's
  `{"edges", "coverage"}` shape. The coverage always says what was not searched.
- **GUI (Claude B).** A BOM tab or dialog can call the view models from a background job. No main-window code
  was touched. The widget boundary needs Claude B's approval.
- **Core request (PR #7).** List `documents.read_revision_bytes` in CORE_API.md as Provisional or Stable.
- **Not done:**
  - XLSX import/export;
  - manufacturing/purchasing BOM views (REQUIREMENTS.md section 6 separates them from the engineering BOM);
  - cost and mass rollup;
  - persisted where-used index;
  - OpenSchematix native BOM mapping.
  None of these is claimed.

## 6. Base parts catalog (formerly issue #15)

**Values** (`bom.parts`):

```python
Part(part_id="prt_<32 hex>", number, description="", unit="EA", kind="part|assembly|material|document",
     status="active|obsolete", revision="", manufacturers=(ManufacturerRef(manufacturer, mpn),),
     vendors=(VendorRef(vendor, sku),), documents=(DocumentLink(document_id, revision_id="", role),),
     attributes=(), manual_number=False)          # .info() -> PartInfo, .to_record()/.from_record()
NumberingRule(rule_id, prefix="", digits=6, suffix="", start=1, reserved=((lo, hi),))
```

- The id never changes. The number can be renumbered. Numbers are unique case-insensitively after NFC.
- `manual_number=True` marks a number outside every saved rule, for example a legacy or imported number.
- Document links are declared by a user. A filename match is never turned into a link.

**Service** (`bom.PartsCatalogService(store, actor=..., clock=...)`). Each mutation is **one** store commit and
takes an optional `txid`.

| Call | Refused (nothing written) when |
|---|---|
| `save_rule(rule)`, `preview_number(rule_id)` | |
| `create_part(number=... or rule_id=..., ...)` | number already used (also by the `partnum` claim record); rule exhausted |
| `update_part(part_id, expect_rev, **changes)` | stale `expect_rev`; new number taken; unit changes dimension while BOMs use the part |
| `set_status(part_id, status, expect_rev)` | stale `expect_rev` |
| `delete_part(part_id, expect_rev)` | any BOM line uses it, or it has its own BOM. Use obsolete instead |
| `add_line(parent, child, qty, unit=None, ...)` | unknown or obsolete child; the edit adds a validation error (cycle, self-reference, invalid quantity, unit mismatch, duplicate find number or reference designator) |
| `update_line(line_id, expect_rev, **changes)`, `remove_line(line_id, expect_rev)` | stale `expect_rev`; parent or child change (remove and add instead); new errors |
| `replace_bom(parent, lines, expect_version=None)` | stale structure version; new errors |
| `import_structure(structure, replace_existing=False, create_missing=True)` | an assembly already has a BOM; parts missing with `create_missing=False`; new errors |
| `import_part_rows(...)` / `parts_csv.import_parts(service, csv, update_existing=False)` | any row conflicts (all-or-nothing) |

Reads: `parts()`, `part(id)`, `part_rev(id)`, `find_by_number(n)`, `catalog()`, `structure()`, `bom(parent)`,
`snapshot(root)`, `rules()`, `structure_version()`.

Edits are refused only for **new** errors. Problems already in imported legacy data do not block unrelated edits;
validation still reports them. `EditRejected.issues` lists the cause.

**Stores** (`bom.store`):
- `MemoryCatalogStore` has the same semantics in memory, for tests, GUI view-model tests and what-if sessions.
- `WorkspaceCatalogStore(ws)` (**PROPOSED**) registers the kinds below and calls only `Workspace.get/list/commit`
  with `RecordPut`, `RecordDelete` and `RecordCheck`. Each action is therefore one journal entry under the
  writer lock, fenced, replayed after a crash and checked by `ws.verify()`. Core `ConflictError` becomes
  `StoreConflict`. Other core errors propagate unchanged and follow CORE_API.md section 4: `ReadOnlyError`,
  `LockLostError`, `CommittedNotApplied` (the change *is* saved) and `WorkspaceCorruptError`.

| Kind | Id | Role |
|---|---|---|
| `part` | `prt_…` | part record plus created/modified stamps (local user and host, not accounts) |
| `partnum` | `pnum_<sha256(casefold number)[:32]>` | number claim, put with `expect_rev=0`, so duplicates cannot both commit |
| `bomline` | `bln_…` | one parent-child line, quantity as decimal text |
| `bomstruct` | `default` | structure version, bumped by every line change, delete and unit change, so edits that are each acyclic but form a cycle together cannot both commit |
| `partrule`, `partseq` | rule id | numbering rule and its counter, which issues each number once |

**Retries.** After an ambiguous failure (crash, lost connection), ask `ws.transaction_outcome(txid)`, as in
CORE_API.md section 4. The store applies an identical request with the same txid once. The service rebuilds
time stamps, so a blind service-level retry is a new request, and its duplicate-number check refuses it.

**Parts CSV** (`bom.parts_csv`). One row per part, with `part_id` and `number` columns. Numbered column groups
hold the references: `manufacturer_N`/`mpn_N`, `vendor_N`/`sku_N` and `document_id_N`/`revision_id_N`/`role_N`.
`attr:<Name>` columns hold attributes. Export then import is lossless. The CSV safety rules from section 3 apply.
