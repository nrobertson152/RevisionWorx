# RevisionWorx — combined integration acceptance and merge-sequencing inventory

**Owner:** Integration & Release Readiness Engineer, draft PR #2.
**Status:** Assessment only. No merge, retarget, rebase, installer release or production enablement is authorized.
**Snapshot:** 2026-11-01 ET / November 2 UTC, after the Chief Engineering Coordinator's PR #2 instruction [6569262114](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6569262114).

## 1. Live branches and dependencies

This is a snapshot, not a claim that later GitHub heads remain unchanged.

| PR | Branch and base | Inspected head SHA | Role |
| --- | --- | --- | --- |
| [#6](https://github.com/nrobertson152/RevisionWorx/pull/6) | `phase1-foundation` → `main` | `412e69ab90bb1d8b74c1a3cf5519f581e4420e63` | Claude: core Phase 1/GUI/CLI |
| [#7](https://github.com/nrobertson152/RevisionWorx/pull/7) | `phase2-documents` → `phase1-foundation` | `366d4355abcde7543122551fadaab01ac9832e01` (newer than tested target `d656884b`) | Claude: documents, immutable revisions, checkout/ADR-018/ADR-019 |
| [#3](https://github.com/nrobertson152/RevisionWorx/pull/3) | `qa/testing-infrastructure` → `phase1-foundation` | `6f0fb5a5b7b40de60cc81890c88e92349db8752d` | Independent QA |
| [#4](https://github.com/nrobertson152/RevisionWorx/pull/4) | `research/shared-filesystem-protocol` → `main` | `924917fe13c898ff7e9b5544f7bb2028eb5b78b2` | Isolated protocol research, NOT production coordinator |
| [#2](https://github.com/nrobertson152/RevisionWorx/pull/2) | `integration/openschematix` → `main` | Adapter SHA `312550804`; latest verified packaging SHA `ccb5520b` (Phase 2 `d656884b`) | CAD adapter, bounded revision compatibility, packaging experiment |

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
| P0 | Content durability, idempotency and GC / Claude+QA | Phase 2 `d656884b` includes revision/content and ADR-017 changes | **Historical P1 contract gaps:** QA/Distributed reproduced changed/reordered `RecordCheck` semantics. Claude has since committed ADR-021 versioned request identity on PR #7 head `366d4355`, which **requires new independent acceptance**; our packaging tests still pin `d656884b` | Claude's ADR-021 v2 candidate now needs exact-head QA acceptance for request identity and legacy compatibility; separately verify GC, schema and recovery |
| P0 | Recovery, migration, permissions, cross-version rollback / Claude+QA | Existing source integrity tests; disposable new copied-workspace harness added to #2 | **NOT EXECUTED** as real upgrade/install, crash recovery, Unix/Windows ACLs or rollback | Fully cloned older workspace, checksummed before/after actual upgrade, destructive failure injection in sandbox |
| P0 | Two-host SMB and authenticated coordinator / Distributed+Claude+QA | Research and local concurrency oracles in draft #4 | **NOT IMPLEMENTED / NOT PHYSICALLY QUALIFIED** | Atomic authority, durable/antirollback recovery and authenticated administration over real hosts/share |
| P1 | Immutable read-only adapter / Integration | [run 41390411127](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411127) on adapter SHA `312550804` and exact Phase 2 SHA `d656884b`: Linux/Windows parser+oracle plus Linux bounded revision compatibility | Windows Phase 2 revision/store tests remain **NOT EXECUTED** in this adapter workflow; no managed-part index writes | Obtain approved index/cache owner and test combined production branch after authorized stack integration |
| P1 | Native BOM and where-used / Integration+future parts owner | Pin-matched real native BOM oracle: 16/5/7 lines vs 5/0/0 unresolved candidates | Full BOM parity/managed Part ID/verified dependency graph **NOT IMPLEMENTED** | Separate approved Phase 4/5 design, quantity identity/source references, explicit incomplete coverage |
| P1 | Frozen cross-platform CLI+Qt GUI smoke / Integration | [run 41388877999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388877999) passed Linux/Windows Python 3.11, pinned earlier Phase 2 `ffcfb463`, 2 packaging safety tests/job + frozen CLI help + Qt offscreen start | **PASS** copied current-schema workspace Unicode/spaces snapshot and bundled Qt-offscreen/CLI against `d656884b` on Ubuntu and Windows Python 3.11, [run 41390661790](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390661790); **NOT an interactive installed GUI or real migration** | Check exact latest-run results and record Windows console UTF-8 treatment |
| P1 | Real installed desktop and signed packages / release engineering+QA | None: only experimental PyInstaller one-directory bundles | Native installers, visible GUI, signing, entitlement/accessibility, GPU, AppImage/deb and uninstall **NOT EXECUTED** | Manual checklist in `docs/INSTALLED_APPLICATION_ACCEPTANCE.md`; verify target OS packaging/licenses |
| P1 | Offline desktop deployment / QA | App architecture remains accountless/offline by requirement; process-local CI smoke | No network instrumentation or fully offline wheelhouse test | Verify no mandatory network activity, signed/hashes dependencies, disconnected install/update |
| P2 | CI cost and provenance / Integration | Focused path-filtered test workflows, distinct exact app/adapter SHA inputs | Full transitive hash locks, bitwise reproducibility and OS-family compatibility **NOT VERIFIED** | Freeze dependency hashes and audit Qt runtime/target glibc baseline |

**Important:** A GitHub workflow with a *strict xfail* is not proof that the expected failure's property passed. The QA-accepted **logical fencing** tests are narrower than physical nonpublication or network durability. Likewise, frozen Qt process startup is narrower than an installed interactive GUI acceptance.

## 3. Verified bounded revision boundary

Claude has explicitly approved ADR-018 on [PR #2](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6568970291): `documents.read_revision_bytes(ws, rev_id, MAX_SOURCE)` verifies exact returned content bytes, size and SHA-256, rejects excessive/corrupt/missing content and preserves immutable `revision.source_rel_path`. The adapter reads only those bytes via `inspect_bytes(raw, suffix)`. Observations are disposable and must not call `Workspace.commit`; identifiers are scoped by authoritative document/revision, not native copied CAD UIDs.

The adapter reports unresolved explicit manufacturer-part candidates with `quantity: null`, not native BOM lines. Legacy wrapper IDs are not stable. No native BOM purchasing claim is made.

**Test command in the isolated Phase 2 overlay:** `REVISIONWORX_PHASE2_SOURCE=<absolute pinned checkout> python -m unittest discover -s tests -p 'test_openschematix_phase2_contract.py' -v`. Full adapter job also runs `test_openschematix_adapter.py` and `test_openschematix_native_oracle.py`; package test uses `python tools/packaging/test_builder.py` then `build.py` and `smoke_workspace.py`. The latter operates only on a self-created disposable fixture and cloned existing-workspace copy.


### Acceptance evidence at the latest tested compatibility pair

- **Adapter** [run 41390411127](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411127): overlay/code SHA `31255080407518f7156e81722c40449bd26dc53d` and Phase 2 SHA `d656884b39401134149273dd0121ef620b76eb8e`. Ubuntu Python 3.11 and 3.13 each: `36 adapter + 4 native oracle + 4 bounded Phase 2 revision tests = 44 passed`. Windows Python 3.11 and 3.13 each: `36 adapter + 4 native oracle = 40 passed`. **168 test executions passed**, 0 failures. **The Phase 2 revision/checkout contract test was not run on Windows in this workflow.**
- **Packaging** [run 41390661790](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390661790): packaging code SHA `19ba7329e96ed76abf7cafe4cc9b23499798b145`, source SHA `d656884b39401134149273dd0121ef620b76eb8e`. Ubuntu and Windows Python 3.11 both: `python tools/packaging/test_builder.py` **4 passed**; frozen CLI help and Qt offscreen startup **passed**; `python tools/packaging/smoke_workspace.py` verified **five hashed files in a disposable copied workspace** and no changes following CLI status/GUI-open. The Windows Unicode error originally failed [run 41390411172](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411172) before the packaging-only UTF-8 mitigation; keep this negative evidence.
- **QA** of `d656884b` reported local ADR-019 acceptance in [run 41389199207](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389199207); its one strict xfail tracks *physical late journal publication*, not positive effective stale mutation. It is not physical nonpublication or production SMB certification. Distributed Systems/QA previously reproduced an ADR-017 full-operation txid replay gap at older Phase 2 heads. Claude has since proposed ADR-021 v2 at `366d4355`; it requires independent QA acceptance on that exact source before this gate can be marked closed.

Both workflow sources remain pinned to exact commits; documentation-only updates do not repeat expensive build jobs. No full native GUI interactions, true installer installation, migrator, PowerShell/ACL denial, hostile/long UNC path or actual network share was executed.

### Offline runtime and failure/recovery qualification

[Windows/Ubuntu run 41392572210](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210), packaging/workflow SHA
`ccb5520b31209eeb4e03943afc89b8b981474fc1`, pinned Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`:
- **Per OS:** 5 packaging-safeguard unit tests passed, 3 injected
  source-level failure/recovery tests passed, plus bundled CLI and Qt-offscreen
  startup, disposable read-only copied-workspace SHA checks, absent-path and
  invalid metadata rejection, and disposable backup rollback rehearsal.
- **Injected pre-commit:** ENOSPC and EACCES refused without publication.
  **Injected post-commit:** ENOSPC after journal publication reported
  `CommittedNotApplied`; reopening replayed exactly one committed revision.
  These are deterministic injections, not physical disk-full or power outages.
- **Ubuntu Linux:** network-disabled, Python-free Docker runtime launched
  frozen CLI/GUI. The image itself was constructed with network available.
  No attempt tracing; do not assert zero network activity or account/telemetry
  code in every path. Windows had Python removed from child `PATH` and
  unreachable proxy endpoints, **not** an internet-disconnected machine.
- **Windows:** extended-length path PASSED, **401-character local path
  PASSED**, localhost admin UNC read PASSED, byte-range-locked metadata read
  rejected, original file hashes preserved. This is **not** remote,
  concurrent two-host SMB certification or an actual denied ACL test.
- **Other:** older workspace migration, native installer, interactive UI
  render, real OS denial, power-loss, offline Windows VM, telemetry call
  audit and physical SMB remain **not executed**.

The complete evidence/limits are in
[`docs/OFFLINE_FAILURE_QUALIFICATION.md`](OFFLINE_FAILURE_QUALIFICATION.md).
The newer draft Phase 2 head `366d4355` includes substantial authority
changes, which were **not** in the pinned `d656884b` compatibility run.
Do not transfer test acceptance to it automatically.

## 4. Manual release qualification

See [`docs/INSTALLED_APPLICATION_ACCEPTANCE.md`](INSTALLED_APPLICATION_ACCEPTANCE.md) for specific actual-Windows and Linux GUI, permissions, installer/uninstaller, full content SHA integrity, old-workspace upgrade and rollback procedures. They are **not executed** by the current experimental onedir CI. Linux dependencies include the EGL/XCB runtime modules; Windows source CLI may use a legacy codepage when outputting Unicode paths. The packaging-only UTF-8 console entrypoint is a mitigation for frozen binaries; it does not alter source `revisionworx/cli.py`.

No release, artifact publication or integration permission is granted by the green prototype tests.
