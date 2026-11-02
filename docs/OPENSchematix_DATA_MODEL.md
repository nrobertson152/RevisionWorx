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
