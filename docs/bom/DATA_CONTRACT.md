# RevisionWorx parts and BOM data contract, version 1

**Owner:** Claude C (#18). **Status:** frozen by golden-file tests (`tests/test_bom_contract.py`,
`tests/fixtures/bom/`). The workspace record kinds are implemented but **PROPOSED** until Claude A reviews them.

This is what stays stable for files users keep, for other subsystems (GUI, change control, integration) and
for records in the journal. The Python API in `BOM_API.md` may grow; the forms below may not change silently.

## 1. Versioning rules

- The JSON format is `{"format": "revisionworx.bom", "version": 1, ...}`.
- Readers **refuse** a newer version, an unknown format, unknown fields, JSON numbers or constants, and a snapshot
  whose stored digest does not match its content. Nothing is loaded partially.
- Any change to the bytes a writer produces for the same data is a contract change. It must:
  1. bump `version`;
  2. keep a reader for every earlier version;
  3. regenerate the golden files deliberately (`RWX_BOM_REGENERATE_GOLDEN=1`);
  4. note the change here.
- Journal entries are immutable. A record shape change therefore also needs a reader for the old shape. Existing
  entries are never rewritten.
- Adding optional fields to the Python dataclasses is allowed only if writers keep producing identical bytes when
  those fields are at their defaults.

## 2. Value rules shared by every form

| Value | Rule |
|---|---|
| Quantity | Decimal text: digits, optionally `.` digits. No sign, exponent or separators. Canonical form has no trailing zeros (`2.50` is written `2.5`). Greater than 0, at most 6 decimal places and at most 10^9 per line. Whole numbers for `EA` |
| Unit | Code from the unit table (`units.py`), compared case-insensitively. Written in canonical case |
| Text | NFC-normalised and stripped. No control characters except tab and newline in free text |
| Part id | `prt_` + 32 lowercase hex (catalog). `pn:<casefolded number>` marks an **unresolved** placeholder from a file without a catalog; it is never stored as a record |
| Line id | `bln_` + 32 lowercase hex, or empty in files |
| Part number | Unique after NFC and case folding |
| Reference designators | Separated by space, comma or semicolon. Natural sort order. Ranges such as `R1-R4` are kept literally |
| Provenance | `declared`, `imported`, `cad-extracted`, `schematic`, `inferred` or `unknown` |
| Document link | `document_id`, optional `revision_id` and `role`. Declared by a user, never inferred from a filename. Native files are referenced by core document id, **never by path** |

## 3. Forms

| Form | Writer / reader | Golden file |
|---|---|---|
| Structure JSON | `structure_to_json` / `structure_from_json`, `load_any` | `structure.json` |
| Snapshot JSON, with source and digest | `snapshot_to_json` / `snapshot_from_json` | `snapshot.json` |
| Structure digest | SHA-256 of compact canonical JSON (`structure_digest`) | `structure.digest` |
| Parent-child CSV | `structure_to_csv` / `structure_from_csv` | `structure.csv` |
| Parts CSV | `parts_to_csv` / `parts_from_csv` | `parts.csv` |
| Workspace records (`part`, `bomline`, `partrule`) | `Part.to_record`, `line_to_record`, `NumberingRule.to_record`, and their readers | `records.json` |

CSV rules:
- UTF-8 with a byte-order mark and CRLF line endings.
- Text cells beginning `= + - @`, tab or CR get one `'` prefix; readers remove exactly that prefix.
- Column names are matched case-insensitively. `attr:<Name>` columns hold attributes.
- Structure CSV: item rows have an empty parent.
- Parts CSV: references use numbered column groups (`manufacturer_N`/`mpn_N`, `vendor_N`/`sku_N`,
  `document_id_N`/`revision_id_N`/`role_N`).

Workspace records also carry `created`, `created_by`, `modified` and `modified_by`, added by the service. The
user and host are local identity, not an account and not a signature. The `partnum`, `bomstruct` and `partseq`
records are internal coordination records (BOM_API.md section 6) and are not an exchange format.

## 4. Native file reference preservation

The BOM layer never moves, renames, copies, opens for writing or rewrites a project file, CAD files included.
- Parts point at documents by core id.
- BOM revisions are read from the immutable content store through ADR-018, never from the working file.
- Exports go only to user-chosen paths and refuse any path inside `.revisionworx`.

`tests/test_bom_reference_preservation.py` checks that the project tree, including registered `.iam`, `.ipt`,
`.idw` and CSV files, is byte- and mtime-identical after every catalog, BOM and adapter operation.
