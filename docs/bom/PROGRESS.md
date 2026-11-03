# Claude C: advanced BOM progress log (issue #18)

Draft PR: #21.

**Branch:** `feature/advanced-bom`, created from accepted core `fe8a179` (QA/DS scoped accept), not from
`phase2-documents` head.
- No core, GUI, journal, lock, transaction or recovery file was changed.
- Nothing was merged, rebased, retargeted, force-pushed or released.

## Checkpoint 1: 2026-11-02 (local; UTC 2026-11-03)

**Coordination.**
- Interface proposal and split posted on #15:
  https://github.com/nrobertson152/RevisionWorx/issues/15#issuecomment-6595450390
- #15 had no branch, PR or comments when this work started.

**Commits:**
- `f5fd87d`: package `revisionworx/bom`: units, model, graph, validate, snapshot, diff, impact, csvio,
  fileio, workspace adapter, view models, CLI;
- `4c86e50`: tests and example BOMs;
- `ce61c98`: lean CI workflow `.github/workflows/bom-ci.yml`.

**Test evidence (code at `ce61c98`):**

| Where | Command | Result |
|---|---|---|
| CI ubuntu-latest, Python 3.10.22 ([run 41579963688](https://github.com/nrobertson152/RevisionWorx/actions/runs/41579963688)) | `pytest -q -ra tests/test_bom_*.py` | 390 passed |
| same job | `pytest -q -ra` (full suite, no PySide6) | 635 passed, 1 skipped (Qt) |
| CI windows-latest, Python 3.13.17 (same run) | BOM tests | 389 passed, 1 skipped (symlink test needs privileges on Windows) |
| Sandbox Linux, Python 3.13.18 | `pytest -q -ra` | 635 passed, 1 skipped (Qt: no PySide6) |

**What the tests cover:**
- exact quantities, units and limits;
- immutability and order independence;
- cycles, including self-loops, several loops, and cycles outside the root;
- explosion, rollup and where-used, with 2^30-path diamonds and depth and magnitude limits;
- validation codes;
- canonical JSON and the fail-closed loader (12 mutation cases);
- the digest across 3 interpreter hash seeds;
- CSV round trip, Unicode, cp1252, formula guard, row/column errors, NUL bytes, malformed quoting, size, row
  and problem caps;
- the indented layout;
- diff matching and antisymmetry;
- change impact and Before You Revise classification, coverage and cycles;
- export fault injection: ENOSPC at write, fsync and replace; KeyboardInterrupt; a lost exclusive-create race;
  refused destinations; and a real **killed process** between temp write and rename;
- 40-seed property tests: rollup equals the sum over paths, explosion sums equal rollup, scaling, round trips,
  back-edge detection, diff properties, where-used against rollup;
- a 20k-item structure;
- real core revisions: read-only handle, `.revisionworx` tree byte-identical after use, old revisions read
  from immutable content, damaged content fails closed, oversized revisions refused.

**Limitations (not done or not claimed):**
- no authoritative BOM persistence;
- no GUI wiring (Claude B);
- no #15 catalog yet (an `InMemoryCatalog` stand-in is used);
- no XLSX;
- no manufacturing BOM;
- no cost or mass rollup;
- no SMB/NFS or multi-user claims;
- no performance claims (the 20k-item test only shows nothing blows up).

**Next:**
1. Adapt to #15's catalog once it exists.
2. Agree on a BOM widget boundary with Claude B.
3. Propose a persistence design for Claude A to review.
4. Add XLSX only if the Director asks.

## Checkpoint 2: 2026-11-03 (base catalog after the ownership change)

**Ownership change.** The Director removed DeepSeek. Issue #15 is closed, and #18 now includes the base parts
catalog. I posted the updated API proposal on #18:
https://github.com/nrobertson152/RevisionWorx/issues/18#issuecomment-6595828009

**Added:**
- `parts.py`: `Part`, references, document links, `NumberingRule`;
- `store.py`: `CatalogStore`, `MemoryCatalogStore`, `WorkspaceCatalogStore` (PROPOSED);
- `catalog_service.py`: part create/update/obsolete/delete, numbering, single-level BOM editing that refuses
  edits adding errors, all-or-nothing imports;
- `parts_csv.py`;
- `integrations.py`: `ChangeControlWhereUsed`, for Claude D's PR #20.

**New tests:**
- `tests/test_bom_catalog.py`, 25 tests on the memory store: uniqueness, including a raced claim; numbering with
  reserved ranges and exhaustion; obsolete and delete rules; each refused edit leaves the structure version
  unchanged; two edits that together form a cycle cannot both commit; store fault injection; txid idempotency;
  imports; parts CSV round trip; the change-control seam.
- `tests/test_bom_catalog_workspace.py`, 5 tests on disposable core workspaces: one journal entry per action;
  data survives close and reopen; a read-only handle refuses writes; conflicts and rejections write nothing;
  core txid retry; `ws.verify()` stays clean; documents coexist.

**Evidence (code at `c648757`):**

| Where | Command | Result |
|---|---|---|
| CI ubuntu-latest, Python 3.10.22 ([run 41581810072](https://github.com/nrobertson152/RevisionWorx/actions/runs/41581810072)) | BOM tests | 420 passed |
| same job | `pytest -q -ra` (full suite) | 665 passed, 1 skipped (Qt; no PySide6) |
| CI windows-latest, Python 3.13.17 (same run) | BOM tests | 419 passed, 1 skipped (symlink privilege) |
| Sandbox Linux, Python 3.13.18 | `pytest -q -ra` | 665 passed, 1 skipped (Qt) |

**Waiting on:**
- Claude A: review of the workspace record kinds (asked on #18 and PR #7).
- Claude B: approval of a widget boundary.
- Claude D: confirmation that the where-used seam fits PR #20.

## Checkpoint 3: 2026-11-03, coordinator backlog #28 item 5

I checked the item out on #18 and #28 under the trust-based protocol (PR #22). Scope: a stable data contract,
native file reference preservation, document-link verification, and a transaction persistence review.

**Added:**
- **Data contract.** `docs/bom/DATA_CONTRACT.md` describes contract version 1 and its versioning rules. Golden
  files in `tests/fixtures/bom/` freeze the bytes of every form. They have their own `.gitattributes` so line
  endings are never converted. `tests/test_bom_contract.py` adds 9 tests.
- **Document links.** `workspace_adapter.verify_document_links(ws, parts)` checks part document links with Stable
  core reads only. It reports `ok`, `missing-document`, `missing-revision`, `newer-revision-available` or
  `unrevisioned`, and never rewrites a pin.
- **Reference preservation.** `tests/test_bom_reference_preservation.py` adds 3 tests. They register `.iam`,
  `.ipt` and `.idw` documents and a BOM CSV, run every catalog, BOM, report and adapter operation, and check that
  the project tree is byte- and mtime-identical. Link verification leaves `.revisionworx` unchanged.
- **Persistence review.** `docs/bom/BOM_API.md` section 7 has the failure-mode table and residual risks.
  `tests/test_bom_catalog_persistence.py` adds 3 tests: a second writer is refused; a fenced writer publishes
  nothing; a writer killed by a real subprocess keeps its committed change.
- **Correction.** `_rev` is an opaque version token. In the workspace it is the journal sequence of the record's
  last write. Callers pass back what they read; the docstrings now say so.

**Evidence:** sandbox Linux, Python 3.13.18.
- `pytest -q -ra`: 680 passed, 1 skipped (Qt).
- BOM tests: 435 passed.
- CI results are on #18 and PR #21 in the check-in.
