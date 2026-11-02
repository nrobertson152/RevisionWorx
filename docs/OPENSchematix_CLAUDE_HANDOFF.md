# OpenSchematix adapter — Lead Architect handoff (Phase 2 → Phase 5)

**Status:** isolated, read-only, fixture-tested implementation in draft PR #2 on
`integration/openschematix`. **Not a native BOM import and not approved for
authoritative PDM integration.**

## 1. What is implemented

`revisionworx_integrations.openschematix.inspect_bytes(raw: bytes, suffix: str) -> dict`
accepts immutable source bytes and `.schematix` / `.osch` suffix; the output is
a JSON-serializable **observation**, not a revision/part/dependency write.
`inspect(path)` is an alternate live-file convenience entry point with
bounded snapshot capture, regular-file/symlink validation and pre/post
descriptor metadata checks. Prefer **`inspect_bytes`** for revision indexing.

Extracted fields: native/legacy format identity, SHA-256 over the exact bytes,
project/sheet metadata, component instance inventory, custom component
properties, unresolved manufacturer-part references, incomplete part-number
candidate groups, coverage and limitations. No GUI, network, or database.

The input cap is **256 MiB**; uncompressed ZIP members are bounded by
**200 MiB** and aggregate declared size by **1 GiB**. This conservative
adapter additionally rejects archives containing over **1,000** members
(even though OpenSchematix supports a larger maximum), duplicate/unsafe
member names, corrupt JSON/ZIP data, and unsupported future schemas.

## 2. Proposed handoff at the actual Phase 2 API boundary

Reviewed `revisionworx/documents.py` and `revisionworx/content.py` on
`phase2-documents` (tested at commit
`47d81658764cd6bde60e391ac9aac24e722317e8`).

The authoritative document record has `id`, `project_id`, `rel_path`,
`type_id`, `latest_rev_id` and `released_rev_id`. The immutable revision
has `id`, `document_id`, `sha256`, `size`, `source_rel_path`, `label`,
etc. `ContentStore(ws).path_for(rev["sha256"])` finds its immutable
content object; `ContentStore.verify` independently verifies integrity.
Documents are registered only within managed projects. Working files stay
where they were. No adapter should alter those invariants.

**Proposed read-only future caller (not production code):**

```python
# Pseudocode for Claude to place in a separately reviewed indexing service.
rev = workspace.get("revision", requested_revision_id)
doc = workspace.get("document", rev["document_id"])
assert doc and rev["document_id"] == doc["id"]
assert doc["type_id"] == "schematic"

raw = ContentStore(workspace).path_for(rev["sha256"]).read_bytes()
if hashlib.sha256(raw).hexdigest() != rev["sha256"]:
    raise ContentIntegrityError("immutable revision hash mismatch")

report = inspect_bytes(raw, Path(doc["rel_path"]).suffix.lower())
if report["file_sha256"] != rev["sha256"]:
    raise ContentIntegrityError("adapter observed a different revision")

# Store a disposable, nonauthoritative index observation referencing
# (doc["id"], rev["id"], rev["sha256"], report["adapter"]).
# No part identity inference, no new revisions, no authoritative edge puts.
```

Production code must use a *bounded, verified content-read interface*
approved by Claude; the example is intentionally illustrative. In particular,
`path_for` is an implementation detail, and a content object read must
not be confused with examining the mutable document working path. The
caller must check the SHA-256 of **the exact bytes parsed** rather than
trusting a previous check on a different file read. Any failure marks the
index observation unavailable/stale; it does not change `Workspace`.

**Identity rules:** `doc_*` and `rev_*` are authoritative PDM IDs.
Native `project_uid/sheet_uid/object_id` are drawing-local instance
identifiers; copies may duplicate them. Legacy `.osch` has no stable
persistent sheet UID: upstream `open_any` creates new wrapper UIDs on
each open, whereas the adapter uses a content-hash fallback that changes
on edit. Never use either as a global cross-document identity. A drawing
title-block revision is not a RevisionWorx revision ID.

## 3. Native BOM oracle and demonstrated limitations

CI checks out OpenSchematix 1.1.0 **at fixed commit
`74e4c79053721997ae899eb9817ddbcb76eec5a0`** and executes real
`projectio.open_any(..., activate=False)` / `bom.generate` on all three
unchanged upstream fixtures. Native BOM quantities, refs, sheet filtering
and multiunit grouping are tested independently of our adapter.

| Upstream example | Native BOM lines | Explicit part-number candidate groups |
| --- | ---: | ---: |
| `demo_project.schematix` | 16 | 5 |
| `start_stop_latch.osch` | 5 | 0 |
| `hydraulic_cylinder_circuit.osch` | 7 | 0 |

Native `bom.generate` groups **physical devices**: coils/contacts
can share one designator across sheets and still count as **quantity 1**.
The legacy start/stop fixture has a K1 device with **three source units
but physical quantity 1**. Symbol-registry purchasability and excluded
symbols, terminal-block exceptions, duplicate slots, symbolic part
identity and multi-part assemblies matter. A line can have no
manufacturer part number, and a single device may occupy multiple
native lines. Sheet-filtered BOMs select devices using all-sheet
identity, not only the visible symbol subset.

`bom_candidates` intentionally reports only explicit
`(manufacturer, part_number)` groups with `quantity: null` and
`verification: "unresolved"`. These are **not** native BOM lines,
purchasing quantities, managed company-part identities or verified
dependency edges. The current parser does not run native symbol
resolution or ingest internal OpenSchematix history; no BOM parity
claim is made.

## 4. CI evidence and blockers

- **Adapter:** Python 3.11 and 3.13 on GitHub Actions (32/32 at
  `7ff3ca162`, [run 41380989161](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380989161)).
- **Native oracle:** separately runs the pinned OpenSchematix
  generator, [run 41381776932](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381776932).
- **Actual Phase 2 draft:** isolated temporary-workspace integration tests
  against `phase2-documents` pinned at `47d81658764cd6bde60e391ac9aac24e722317e8`;
  [run 41381903865](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381903865).
- **Latest regression additions:** source-file race injection and native
  multiunit/part-key assertions require latest-head CI confirmation.
- No Windows runtime validation; no SMB/NFS safety claim. Issue #8 and
  the separate multi-user authority design are outside adapter scope.

**Claude decisions needed before integration:**
1. Approve/adjust the immutable content-read boundary and where the exact
   revision SHA-256 is verified.
2. Specify index observation owner, refresh/invalidation policy and
   coverage reporting; local disposable cache must not authorize writes.
3. Confirm managed-part mapping/edge verification ownership (Phase 4/5)
   and use an authoritative native BOM bridge when quantities matter.
4. Approve PR #2 restacking relative to PRs #6/#7 and resolve the
   `docs/DEVELOPMENT_PROGRESS.md` conflict. Do **not** retarget/merge
   automatically.

The OpenSchematix repository and Claude's core branches have not been
modified by this work.
