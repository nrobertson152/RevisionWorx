# RevisionWorx — combined integration acceptance and merge-sequencing inventory

**Owner:** Integration & Release Readiness Engineer, draft PR #2.
**Status:** Assessment only. No merge, retarget, rebase, installer release or production enablement is authorized.
**Snapshot:** 2026-11-01 ET / November 2 UTC, after the Chief Engineering Coordinator's PR #2 instruction [6569262114](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6569262114).

## 1. Live branches and dependencies

This is a snapshot, not a claim that later GitHub heads remain unchanged.

| PR | Branch and base | Inspected head SHA | Role |
| --- | --- | --- | --- |
| [#6](https://github.com/nrobertson152/RevisionWorx/pull/6) | `phase1-foundation` → `main` | `412e69ab90bb1d8b74c1a3cf5519f581e4420e63` | Claude: core Phase 1/GUI/CLI |
| [#7](https://github.com/nrobertson152/RevisionWorx/pull/7) | `phase2-documents` → `phase1-foundation` | `d656884b39401134149273dd0121ef620b76eb8e` | Claude: documents, immutable revisions, checkout/ADR-018/ADR-019 |
| [#3](https://github.com/nrobertson152/RevisionWorx/pull/3) | `qa/testing-infrastructure` → `phase1-foundation` | `ee0b6978fe0a5459932e27b45e177fa4e8841f6d` | Independent QA |
| [#4](https://github.com/nrobertson152/RevisionWorx/pull/4) | `research/shared-filesystem-protocol` → `main` | `9df169bd435567b2ecdd2bd536c430485e2f0a22` | Isolated protocol research, NOT production coordinator |
| [#2](https://github.com/nrobertson152/RevisionWorx/pull/2) | `integration/openschematix` → `main` | `31255080407518f7156e81722c40449bd26dc53d` tested adapter SHA; later branch adds isolated packaging/docs | CAD adapter, bounded revision compatibility, packaging experiment |

All inspected PRs were **draft**. #7's base is #6, whereas #2 has not been stacked on either. QA #3 also targets #6. #4 remains independent research rather than an enabled network write service.

### Changed-file overlap checked through live GitHub PR changed-file APIs

- #2 ∩ #6: `tests/fixtures/openschematix/README.md`, `demo_project.schematix`, `start_stop_latch.osch`. Existing fixture contents have identical Git blobs; the README was restored to the exact Phase 1 blob. These are redundant shared additions to reconcile during an authorized integration, not an excuse to change CAD bytes.
- #2 ∩ #7: **none** on the inspected changed-file lists (the earlier root `docs/DEVELOPMENT_PROGRESS.md` edit was removed).
- #2 ∩ #3 and #2 ∩ #4: **none**. Distinct files do not guarantee semantic compatibility.
- #6 ∩ #7: twelve listed paths (shared source and history such as `revisionworx/workspace.py`, `revisionworx/index.py`, `revisionworx/cli.py`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_PROGRESS.md`, GUI and tests); this is **expected stacking**, not an independently mergeable #7 diff against main. Review the actual parent relation and final merged tree.
- #3 ∩ #7 and #4 ∩ #7: no overlapping names in the inspected PR changed-file lists, but their acceptance/contract dependencies are substantive.

**Director-only proposed order:** (1) require independent QA acceptance and backport/replay decisions for P0 core fixes; (2) approve a minimal Phase 1 backport on Claude's branch, then land #6; (3) integrate QA tests from #3 before or with their tested target; (4) update/review the Phase 2 stack, then land #7 only after P0 and P1 acceptance; (5) obtain Director approval to retarget/rebase #2 and rerun final combined validation; (6) keep #4 an independent research PR until real coordinator authorization and physical SMB qualification. No one is directed to merge anything from this proposal.

## 2. Risk-ranked acceptance matrix

| Priority | Requirement/owner | Verified evidence | Failed / not executed and dependency | Next acceptance gate |
| --- | --- | --- | --- | --- |
| P0 | #8 audited writer revocation and #10 Windows lock release / Claude+QA | QA [run 41389199207](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199207) on exact Phase 2 `d656884b`: independent local Linux and Windows positive ADR-019 tests, with separate strict xfail for physical late journal artifact | **Not** physical nonpublication, power-loss or two-host SMB safety | Independent final QA at approved production SHA; record P0 scope and remain single-user until separate network gate |
| P0 | #9 consistent LocalIndex watermark / Claude+QA | QA local CI tests in current Phase 2 acceptance suite; read latest QA report for exact state | No physical SMB consistency claim | Check accepted insert/delete, sync/rebuild/restart + error handling on approved source |
| P0 | Content durability, idempotency and GC / Claude+QA | Phase 2 `d656884b` includes revision/content and ADR-017 changes | **Known P1 contract gap:** altered `RecordCheck` with same `txid` can replay prior success; independently reproduced by Distributed Systems, [run 41389488005](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389488005). GC/chain integrity acceptance ongoing | Claude resolve full-operation txid request identity; QA retest loss/restart/GC, schema and corruption |
| P0 | Recovery, migration, permissions, cross-version rollback / Claude+QA | Existing source integrity tests; disposable new copied-workspace harness added to #2 | **NOT EXECUTED** as real upgrade/install, crash recovery, Unix/Windows ACLs or rollback | Fully cloned older workspace, checksummed before/after actual upgrade, destructive failure injection in sandbox |
| P0 | Two-host SMB and authenticated coordinator / Distributed+Claude+QA | Research and local concurrency oracles in draft #4 | **NOT IMPLEMENTED / NOT PHYSICALLY QUALIFIED** | Atomic authority, durable/antirollback recovery and authenticated administration over real hosts/share |
| P1 | Immutable read-only adapter / Integration | [run 41390411127](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411127) on adapter SHA `312550804` and exact Phase 2 SHA `d656884b`: Linux/Windows parser+oracle plus Linux bounded revision compatibility | Windows Phase 2 revision/store tests remain **NOT EXECUTED** in this adapter workflow; no managed-part index writes | Obtain approved index/cache owner and test combined production branch after authorized stack integration |
| P1 | Native BOM and where-used / Integration+future parts owner | Pin-matched real native BOM oracle: 16/5/7 lines vs 5/0/0 unresolved candidates | Full BOM parity/managed Part ID/verified dependency graph **NOT IMPLEMENTED** | Separate approved Phase 4/5 design, quantity identity/source references, explicit incomplete coverage |
| P1 | Frozen cross-platform CLI+Qt GUI smoke / Integration | [run 41388877999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388877999) passed Linux/Windows Python 3.11, pinned earlier Phase 2 `ffcfb463`, 2 packaging safety tests/job + frozen CLI help + Qt offscreen start | Newly added copied-workspace path/Unicode smoke against `d656884b` has separate current run; **NOT interactive installed GUI** | Check exact latest-run results and record Windows console UTF-8 treatment |
| P1 | Real installed desktop and signed packages / release engineering+QA | None: only experimental PyInstaller one-directory bundles | Native installers, visible GUI, signing, entitlement/accessibility, GPU, AppImage/deb and uninstall **NOT EXECUTED** | Manual checklist in `docs/INSTALLED_APPLICATION_ACCEPTANCE.md`; verify target OS packaging/licenses |
| P1 | Offline desktop deployment / QA | App architecture remains accountless/offline by requirement; process-local CI smoke | No network instrumentation or fully offline wheelhouse test | Verify no mandatory network activity, signed/hashes dependencies, disconnected install/update |
| P2 | CI cost and provenance / Integration | Focused path-filtered test workflows, distinct exact app/adapter SHA inputs | Full transitive hash locks, bitwise reproducibility and OS-family compatibility **NOT VERIFIED** | Freeze dependency hashes and audit Qt runtime/target glibc baseline |

**Important:** A GitHub workflow with a *strict xfail* is not proof that the expected failure's property passed. The QA-accepted **logical fencing** tests are narrower than physical nonpublication or network durability. Likewise, frozen Qt process startup is narrower than an installed interactive GUI acceptance.

## 3. Verified bounded revision boundary

Claude has explicitly approved ADR-018 on [PR #2](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6568970291): `documents.read_revision_bytes(ws, rev_id, MAX_SOURCE)` verifies exact returned content bytes, size and SHA-256, rejects excessive/corrupt/missing content and preserves immutable `revision.source_rel_path`. The adapter reads only those bytes via `inspect_bytes(raw, suffix)`. Observations are disposable and must not call `Workspace.commit`; identifiers are scoped by authoritative document/revision, not native copied CAD UIDs.

The adapter reports unresolved explicit manufacturer-part candidates with `quantity: null`, not native BOM lines. Legacy wrapper IDs are not stable. No native BOM purchasing claim is made.

**Test command in the isolated Phase 2 overlay:** `REVISIONWORX_PHASE2_SOURCE=<absolute pinned checkout> python -m unittest discover -s tests -p 'test_openschematix_phase2_contract.py' -v`. Full adapter job also runs `test_openschematix_adapter.py` and `test_openschematix_native_oracle.py`; package test uses `python tools/packaging/test_builder.py` then `build.py` and `smoke_workspace.py`. The latter operates only on a self-created disposable fixture and cloned existing-workspace copy.

## 4. Manual release qualification

See [`docs/INSTALLED_APPLICATION_ACCEPTANCE.md`](INSTALLED_APPLICATION_ACCEPTANCE.md) for specific actual-Windows and Linux GUI, permissions, installer/uninstaller, full content SHA integrity, old-workspace upgrade and rollback procedures. They are **not executed** by the current experimental onedir CI. Linux dependencies include the EGL/XCB runtime modules; Windows source CLI may use a legacy codepage when outputting Unicode paths. The packaging-only UTF-8 console entrypoint is a mitigation for frozen binaries; it does not alter source `revisionworx/cli.py`.

No release, artifact publication or integration permission is granted by the green prototype tests.
