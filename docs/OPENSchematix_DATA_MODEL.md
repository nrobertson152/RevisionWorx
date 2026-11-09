# OpenSchematix data model mapping

## Source records (confirmed in upstream model.py and projectio.py)

| OpenSchematix source | RevisionWorx concept | Identity / trust |
| --- | --- | --- |
| `project.json.uid` | foreign CAD project identifier | Source-declared; not global PDM identity |
| `project.json.meta` | descriptive project/document metadata | Not a part identity |
| `sheets/<uid>.json.uid` | sheet identity | Persistent in a native project |
| `components[].id` | drawn object identifier | Unique and not reused within a sheet |
| `components[].symbol` | symbol definition ID | Built-in or `user:...`; not purchasable part identity |
| `components[].designator` | engineering device tag | Can label multiple drawn units of one physical device |
| `components[].props.part_number` | manufacturer's part number candidate | Not a confirmed managed-part ID |
| `components[].props.manufacturer` | candidate manufacturer | May be empty or ambiguous |
| `components[].props` | source attributes | Preserve custom keys in index output |
| `components[].params` | source parametric configuration | Not a manufacturer identity |
| `components[].in_bom` | inclusion flag | Does not alone prove native purchasability |
| `wires[].start/end` | schematic terminal connectivity | Endpoint pair `[component id, terminal id]`; not extracted in prototype |
| `manifest.sheet_schema` | compatibility gate | Current known 2/3; reject future versions |
| `revisions/index.json` | CAD-internal snapshot history | Distinct from immutable PDM file revisions |

A symbol instance is a drawn unit. Multiple instances can represent one engineering device (relay coil and contacts). A company part is a separately governed RevisionWorx record; a manufacturer part number is descriptive external identification. Never merge them implicitly.

## Adapter output contract

`inspect(path: str | Path) -> dict` returns `adapter`, `source_format`, `container_version`, `sheet_schema`, `file_sha256`, `project_uid`, `project_metadata`, `sheets`, `components`, `dependencies`, `bom_candidates`, `bom_status`, `coverage` and `warnings`. No persisted state or side effects. `UnsupportedFormat` means unrecognized/newer format; `InvalidProject` means malformed or unsafe source. Keep errors separate from a successful but partial index.

A native instance ID is `project_uid/sheet_uid/object_id` for native containers, scoped to the registered RevisionWorx document. It survives ordinary saved edits/moves where the original ID is retained. A duplicate placed component gets a distinct object ID; a deleted-and-recreated object gets a new one. Source object IDs do **not** guarantee uniqueness across copied project files. For legacy drawings the generated fallback ID is only provisional, derived from the full content digest.

## Normalized dependency records

The prototype emits a `source_instance` key and `target_key: [manufacturer, part_number]`, with `target_kind: manufacturer_part_candidate`, `verification: unresolved`, `provenance: openschematix.component.props`, `target_revision: null`, `quantity: null`. These are observations, **not** final graph edges to a managed part.

Future confirmed edges require `revisionworx_document_id`, immutable `source_revision_id`, stable managed `part_id`, optional `target_revision_id`, `provenance` (explicit mapping vs native part field), `verified_at`, `coverage_scope` and `status` (verified, unresolved, stale, invalid). Existing settings should never silently infer the target revision from a drawing title-block revision, since that revision describes the drawing, not the part.

For BOM comparisons, group confirmed managed-part identities with revision and source-level quantity, compare added/removed/changed lines against two immutable document snapshots, and record any unknown quantities as unknown rather than zero. Highlight changes in incomplete or unresolved records separately.

## Immutable revision-content interface (prototype, 2026-11-01)

The adapter now exposes two read-only entry points:

- `inspect(path: str | Path) -> dict`: inspects one explicitly selected regular file, rejects symlinks, checks same-descriptor size/mtime/ctime consistency during the snapshot read, and parses/hash-consumes the captured bytes.
- `inspect_bytes(raw: bytes, suffix: str) -> dict`: inspects **already captured immutable bytes**, with original registered document suffix `.schematix` or `.osch`. It performs no filesystem access, no record mutations, no checkout, and no revision publication. It computes `file_sha256` over those exact bytes.

Recommended future Phase 2 handoff (subject to Lead Architect approval): retrieve a verified immutable source revision through the document service, pass those raw bytes and the registered document's original suffix to `inspect_bytes`, require result `file_sha256 == revision.sha256`, then attach `revision_id`, `document_id`, extractor version and observation time in the **disposable local index**. A path in `.revisionworx/content/sha256/...` lacks a suffix and must not be passed to `inspect(path)` as a workaround. The adapter must not import or write `revisionworx.workspace`, `documents.py`, GUI, or SQLite. Phase 4 is responsible for explicit manufacturer-candidate to managed-part identity mapping. A source drawing's title-block revision is never a PDM revision ID.

No native BOM authority is added by either entry point. Both return the same explicitly incomplete `bom_candidates` and `bom_status` fields.

## Native BOM comparison and unresolved candidate semantics (OpenSchematix 1.1.0)

**Source of native behavior:** `OpenSchematix/openschematix/bom.py` at upstream commit `74e4c79`, specifically `purchasable`, `designator_groups`, `unit_slot`, `devices`, `device_lines`, and `generate`. This is source-code analysis; no claim of full adapter parity follows.

| Concern | Native `bom.generate` | Current `bom_candidates` | Required for parity |
| --- | --- | --- | --- |
| Eligibility | `in_bom` and symbol registry/prefix; excludes nonphysical symbol IDs and unknown symbols | `in_bom` and explicit part number | Load the native symbol registry and project custom-symbol scope |
| Physical quantity | Counts physical devices; shared designator may merge coil/contact units across sheets if slots are distinct | Always `null` (unknown) | Implement device grouping, terminal slot disambiguation and conflict behavior |
| Duplicate identifiers | If unit slots clash, split devices; never silently reduce quantity | Not detected | Replicate `unit_slot` and validation warnings |
| Part identity | With part number: manufacturer + part number; without: symbol + description + rating + canonical params | Only explicit manufacturer + part number | Resolve symbolic part identities and parameters |
| Multi-part device | A single designator may contribute to multiple BOM lines when its units carry different part numbers | Independent candidates, no physical grouping | Preserve multiple BOM lines and accurate device counts |
| Sheet filtering | Device selected if any unit is on requested sheets; identity uses *all* units | All indexed sheets; no filter | Reproduce global-device identity with selected-sheet references |
| Provenance | `BomLine.refs` contains (sheet UID, component ID); designators and sheet names retained | Source instance strings and unresolved verification | Preserve native refs while keeping source IDs separate from managed part IDs |

**Real example baselines:** `demo_project.schematix`: 3 sheets/26 instances, native 16 lines vs 5 candidates; `start_stop_latch.osch`: 1 sheet/7 instances, native 5 lines vs 0 candidates; `hydraulic_cylinder_circuit.osch`: 1 sheet/9 instances, native 7 lines vs 0 candidates. Native counts were reported by the Lead Architect after calling upstream `projectio.open_any` and `bom.generate`. Candidate counts are asserted by our GitHub Actions fixture tests. The difference is expected: most purchasable devices can lack a manufacturer part number. Neither count alone establishes a quantity or identity match.

**Prohibited inference:** candidate count must not be used as native line count, quantity, completeness, purchasing eligibility or a verified company part match. Cross-project where-used on these candidates is a *potential manufacturer-part match*, not an authoritative engineering dependency. Full native BOM support requires independent upstream oracle tests, including duplicate designators, multiunit contacts, terminal blocks, excluded symbols, custom libraries and sheet filtering.
