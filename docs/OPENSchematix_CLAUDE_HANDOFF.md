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
`phase2-documents`; the branch was subsequently updated for Issues #8/#9/#10.
The disposable integration contract now runs against pinned latest inspected
Phase 2 commit `325b7a801de15c082616781fe086bbd95cb350b6`.
Those defect-fix candidates are separate from this adapter and are *not*
independently certified by our interoperability smoke tests.

The authoritative document record has `id`, `project_id`, `rel_path`,
`type_id`, `latest_rev_id` and `released_rev_id`. The immutable revision
has `id`, `document_id`, `sha256`, `size`, `source_rel_path`, `label`,
etc. `ContentStore(ws).path_for(rev["sha256"])` finds its immutable
content object; `ContentStore.verify` independently verifies integrity.
Documents are registered only within managed projects. Working files stay
where they were. No adapter should alter those invariants.

**Lead Architect-approved bounded Phase 2 read contract (ADR-018), verified at
`phase2-documents` commit `ffcfb463bc050ff460279fd92f6758e0215f73e3`:**

```python
from pathlib import Path
from revisionworx import documents
from revisionworx_integrations.openschematix import MAX_SOURCE, inspect_bytes

def observe_revision(ws, revision_id):
    # Existing Phase 2 API: validates exact returned bytes, SHA-256 and
    # registered content size; rejects oversize without full read.
    rev, raw = documents.read_revision_bytes(ws, revision_id, MAX_SOURCE)
    doc = ws.get("document", rev["document_id"])
    if doc is None or doc["type_id"] != "schematic":
        raise ValueError("not a registered schematic document")
    # Historical extension is immutable; current document path can change.
    suffix = Path(rev["source_rel_path"]).suffix.lower()
    report = inspect_bytes(raw, suffix)
    if report["file_sha256"] != rev["sha256"]:
        raise ValueError("adapter digest mismatch")
    return {
        "document_id": doc["id"],
        "revision_id": rev["id"],
        "revision_sha256": rev["sha256"],
        "source_suffix": suffix,
        "adapter_version": report["adapter"],
        "observation": report,  # disposable, not a Workspace record
    }
```

The snippet demonstrates a **future caller**, not an enabled production index
service. The adapter itself remains independent of unmerged Phase 2 modules.
The approved `documents.read_revision_bytes` returns the verified original
bytes, or raises `ContentTooLarge`/`WorkspaceCorruptError` for excessive,
missing or corrupt content. The former `ContentStore.path_for` example is
**superseded** and must not be used for this integration contract.

The proposed cache key remains
`(document_id, revision_id, sha256, adapter_version)`; the observation is
read-only, disposable and never calls `Workspace.commit`. The caller must
not turn unresolved manufacturer part references into authoritative PDM
edges or purchasable BOM quantities.

**Identity rules:** `doc_*` and `rev_*` are authoritative PDM IDs.
Native `project_uid/sheet_uid/object_id` are drawing-local instance
identifiers; copies may duplicate them. Legacy `.osch` has no stable
persistent sheet UID: upstream `open_any` creates new wrapper UIDs on
each open, whereas the adapter uses a content-hash fallback that changes
on edit. Never use either as a global cross-document identity. A drawing
title-block revision is not a RevisionWorx revision ID.

## 2A. Explicit Lead Architect approval contract (PROPOSED, not implemented)

**Request** from a future local indexing scheduler (authoritative records
are looked up by the caller; the request cannot supply forged source hashes,
suffixes or Part IDs):

```json
{"document_id": "doc_<authoritative-id>", "revision_id": "rev_<authoritative-id>"}
```

The application may also use only `revision_id` and derive `document_id`.
Either way, it must verify the revision actually belongs to that document.
`doc_*` and `rev_*` are *illustrative prefixes*, not exact IDs assigned
by the adapter. Neither native `project_uid` nor a title-block revision
can authorize this lookup.

**Success response/observation envelope** from the proposed read-only
service; this is a data-flow proposal, **not** a function currently exported:

```json
{
  "status": "indexed",
  "document_id": "doc_<authoritative-id>",
  "revision_id": "rev_<authoritative-id>",
  "revision_sha256": "<verified 64-character sha256>",
  "source_suffix": ".schematix",
  "adapter": "openschematix-readonly-v1",
  "verification": "source_parsed_not_part_resolved",
  "coverage": {
    "indexed_sheets": 3,
    "indexed_components": 26,
    "external_projects": "not_scanned",
    "symbol_libraries": "not_resolved"
  },
  "observation": "<the exact inspect_bytes() report, not authoritative metadata>"
}
```

The two angle-bracket strings above are *illustrative placeholders*, not
literal serializable production values. The component/sheet counts are from
the pinned `demo_project.schematix` fixture, not a universal guarantee.
`verification` in the proposed envelope refers to source integrity and
parsing; each `manufacturer_part_candidate` inside the report retains its
own `verification: "unresolved"` and `quantity: null`.

**Error outcome taxonomy** for the scheduler (the adapter itself raises its
existing exceptions, and has no such status protocol yet):

| Outcome | Trigger | Required read-only behavior |
| --- | --- | --- |
| `revision_not_found` / `identity_mismatch` | Missing or inconsistent authoritative `doc_id/rev_id` | No observation; preserve revision state |
| `unavailable_content` | Missing content blob or read I/O error | No new observation; previous revision snapshots remain intact |
| `integrity_error` | Blob size or exact parsed-byte SHA differs from immutable `rev.size/sha256` | No new observation; flag for content integrity review |
| `unsupported_format` | Wrong original extension, future container or sheet schema | No stale success reused; retain original bytes and revision |
| `invalid_project` | Invalid JSON/ZIP, unsafe members, corrupted or inconsistent schematic | No partial observation or inferred edges |
| `over_limit` | Exceeds adapter source/member caps | Explicit incomplete/unavailable state; not "empty BOM" |
| `indexed` | Verified bytes parsed completely within known adapter limits | Disposable observation only; no PDM writes |

**Proposed cache key:** `(document_id, revision_id, sha256, adapter_version)`.
A new revision creates a different cache identity even when native project
UIDs are copied. Historical immutable observations remain valid for their
original content SHA; a "latest revision" *view* must be refreshed whenever
`document.latest_rev_id` changes. An adapter version bump invalidates or
rebuilds its derived observations; an I/O failure never reuses a previous
revision's report. Catalog coverage must state unmanaged projects and
unresolved external/custom symbols were not scanned. Any search hit for
a manufacturer-part key is a **candidate**, not a verified dependency.
No network, credentials or mandatory service are required.

**Lead Architect decisions received**, [PR #2 comment
6568970291](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6568970291)
at source SHA `ffcfb463bc050ff460279fd92f6758e0215f73e3`:

1. **Byte authority approved:** Phase 2 owns bounded exact-byte verification
   through `documents.read_revision_bytes(ws, rev_id, max_bytes)`, or
   `ContentStore.read_verified(sha, max_bytes, expected_size)`. Neither
   `path_for` nor a second unverified read should be used by the adapter
   caller. The adapter's `file_sha256` provides optional defense in depth.
2. **Suffix authority approved:** use immutable
   `revision.source_rel_path`; the mutable `document.rel_path` is not
   authoritative for historical formats.
3. **Source identity approved:** scope component observations with the
   authoritative `document_id` and `revision_id`; copied OpenSchematix
   native UIDs are not global identifiers.
4. **Observation ownership approved:** store only in the disposable index
   layer; neither successful nor failed indexing calls `Workspace.commit`.
   The approved cache key includes doc, rev, source SHA and adapter version.
5. **Candidate-part policy approved:** manufacturer part numbers remain
   unresolved with unknown quantity; no Part ID, native BOM equivalence or
   verified where-used assertion before Phase 4/5.
6. **Suggested merge sequence only:** #6 (with QA-accepted fixes) → #7 →
   #2 retargeted to Phase 2 after Director approval; QA #3 incorporated
   by its target. **No merge or PR retarget was authorized.**

**Actual verification:** Existing Linux/Windows matrix at
[run 41387383189](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387383189)
used the older Phase 2 SHA. The new bounded-reader contract tests were
committed at `da1f01e43628cf165f4fbac6bd3a3171d3657bd8` and
target the newer `ffcfb463`. Treat their results as **pending** until
the corresponding CI run completes.

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
  against the earlier `phase2-documents` snapshot `47d81658764cd6bde60e391ac9aac24e722317e8`;
  [run 41381903865](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381903865).
  Subsequent [run 41387214269](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387214269)
  verified the same three Linux Phase 2 contract tests on both Python
  versions using newer Phase 2 SHA `325b7a801de15c082616781fe086bbd95cb350b6`.
- **Expanded current matrix:** [run 41382647720](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382647720)
  at code/workflow commit `11abac8534231c6d7b029d29d75af8cd976a8f84`:
  Linux Python 3.11 and 3.13 each **36 standalone + 4 native oracle + 3
  actual Phase 2 contract = 43 passed**; Windows Python 3.11 and 3.13
  each **36 standalone + 4 native oracle = 40 passed**. All four jobs
  succeeded (**166 test executions**); the Windows Phase 2 contract step
  was intentionally skipped because Windows authoritative storage is a
  separate unverified core concern.
- **Additional identity fixtures:** tests confirm copied native projects
  retain identical project/sheet/object IDs while the PDM assigns different
  document/revision IDs; native legacy `.osch` wrapper UIDs change between
  opens even for identical bytes. Strict malformed/embedded symbol warning
  and schema-3 fill cases have been exercised without claiming symbol BOM
  classification. A deterministic live-file edit is rejected.
- No SMB/NFS or Windows **core storage** safety claim. Issue #8 and the
  separate multi-user authority design are outside adapter scope.

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

## 5. Exact PR diff overlap and safe integration sequencing (2026-11-01)

Verified live GitHub changed-file lists, relative to their actual PR bases:

- PR #6, `phase1-foundation` → `main`, owns the initial core and
  `docs/DEVELOPMENT_PROGRESS.md`; it also adds two upstream fixture blobs
  and the source-provenance `tests/fixtures/openschematix/README.md`.
- PR #7, `phase2-documents` → `phase1-foundation`, owns the incremental
  Phase 2 API and also updates `docs/DEVELOPMENT_PROGRESS.md`. Its new
  revision/content APIs are *not* in `main`.
- PR #2, `integration/openschematix` → `main`, owns isolated adapter,
  oracle tests, CI and design documents. The original PR #2 overlapped
  PR #6 in four paths: `docs/DEVELOPMENT_PROGRESS.md`,
  `tests/fixtures/openschematix/README.md`, `demo_project.schematix`
  and `start_stop_latch.osch`. It overlaps PR #7 only at
  `docs/DEVELOPMENT_PROGRESS.md`. Both existing upstream fixture
  blobs have *identical* Git SHAs across PR #2 and #6; no fixture-byte
  conflict exists.
- PR #2's old root `docs/DEVELOPMENT_PROGRESS.md` delta was only a
  historical **"tests not executed"** checkpoint appended to `main`.
  It is now obsolete and the role-specific progress log is the
  authoritative record of adapter activity. This branch restores that
  root file to `main` unchanged, removing the collision. The fixture
  README is restored byte-for-byte to Claude's Phase 1 version; extra
  adapter-specific provenance lives in
  `tests/fixtures/openschematix/INTEGRATION_PROVENANCE.md`. These changes
  were made only on the integration branch, **not** on Claude's branches.

**Proposed sequence, subject to Director/Claude approval:**
(1) Resolve QA and production blockers #8/#9/#10 in Claude's Phase 2
stack and get their own acceptance; (2) merge PR #6 to `main` when
authorized; (3) retarget/rebase PR #7 from `phase1-foundation` to
updated `main` after authorized #6 integration, preserving commits;
(4) integrate PR #7 when authorized; (5) rebase/retarget PR #2 onto the
approved core only *after* Claude confirms the byte/observation boundary,
and rerun scoped cross-platform adapter plus Phase 2 integration CI.
No agent may perform a merge, retarget or history rewrite solely from
this proposal. If PR #2 is independently merged earlier, it is still
isolated read-only research and does **not** imply production Phase 5
enablement. Keep all source and artifact paths unchanged.

