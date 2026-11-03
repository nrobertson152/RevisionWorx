# OpenSchematix Integration Engineer — Progress

## Session: 2026-11-01

**Role:** OpenSchematix Integration Engineer. **Branch:** `integration/openschematix`. **PR:** https://github.com/nrobertson152/RevisionWorx/pull/2 (draft).

### Completed
- Inspected RevisionWorx CLAUDE.md, requirements, architecture and development progress, and OpenSchematix v1.1.0 serialization, model, BOM and format documentation.
- Committed isolated read-only `.schematix` and legacy `.osch` indexer, candidate dependency extraction, design documentation, and six unittest test cases.
- Recorded initial findings in `docs/DEVELOPMENT_PROGRESS.md`.
- Opened draft PR #2. No OpenSchematix changes.

### Files changed
- `revisionworx_integrations/__init__.py`
- `revisionworx_integrations/openschematix.py`
- `tests/test_openschematix_adapter.py`
- `docs/OPENSchematix_INTEGRATION.md`
- `docs/OPENSchematix_DATA_MODEL.md`
- `docs/DEVELOPMENT_PROGRESS.md`
- `docs/agents/OPENSchematix_INTEGRATION_PROGRESS.md` (this log; renamed from the previous path)

### Commits before this log
`518a0b6989a9e90679b59decc6fd4f922e8b45a3`, `3d9cf6f36b65e9216a88c3f4be2a727d21138b8b`, `d4da0987af066b8ca6a9a9185cb646bcc616b429`, `53ac94585babe02ee1a33521858b06c51c144652`, `083d2ee8b92291bcf47da8b65fdda0884676d514`, `af5307a4e13b890d11aa772c688c17a39443d540`.

### Tests and verification
- Automated tests **written but not executed**; local clone failed because DNS/network access was unavailable. GitHub file readback confirmed source committed.
- Test result: **UNVERIFIED**. Do not merge as production-ready.

### Architectural decisions
- No coupling to unfinished RevisionWorx GUI/database interfaces.
- Source part numbers are unresolved manufacturer-part candidates, never verified PDM identities.
- BOM quantity is unknown until native multi-unit and purchasability semantics are verified.
- Preserve source file read-only; reject unsupported newer schemas.

### Unresolved issues
- Need CI/local unittest execution and real upstream fixtures.
- Concurrent source modification may invalidate hash/snapshot consistency.
- Full native BOM, symbol library resolution, revision history and connectivity are not implemented.
- Coordinate persistent document identity, provenance, coverage and part mapping with Claude before storage integration.

### Exact next steps
1. Run `python -m unittest discover -s tests -v` on the branch and fix failures.
2. Add representative real OpenSchematix `.schematix` fixtures and native BOM parity tests.
3. Harden archive input, schema validation, and consistent-snapshot indexing.
4. Agree on managed part ID and dependency verification contracts with Claude in PR #2.
5. Update this log with test results and commits before ending the next session.

## Role identification migration — 2026-11-01
- Adopted assignment-based title: **OpenSchematix Integration Engineer**.
- Preserved the entire previous progress record above while relocating this log to `docs/agents/OPENSchematix_INTEGRATION_PROGRESS.md`.
- Continued on `integration/openschematix`; PR #2 remains draft.
- No tests executed during this documentation migration; prior test status remains UNVERIFIED.
- Next: verify real fixtures and run adapter regression tests, then update this log.

## Standing orders session — 2026-11-01
- Read assignment README and OpenSchematix Integration Engineer standing orders from `main`; reread `CLAUDE.md`, requirements, architecture, development status, existing integration progress, PR #2 discussion, and Phase 1 source `filetypes.py` / `scan.py`.
- New deliverable: documented Phase 1 content-sniffing boundary, proposed adapter handoff, provenance and coverage semantics, revision comparison constraints, and Lead Architect's fixture/test findings in `docs/OPENSchematix_INTEGRATION.md` (commit `3d83e4f4c55ed172c51077fe5841e57d3c8f4f3c`).
- Verified results **reported by Lead Architect in PR #2**, not rerun here: 6/6 unittest tests passed on `af5307a`, Python 3.13.18/Linux. Real upstream examples: 3/26, 1/7, 1/9 sheet/component counts match; native BOM vs prototype candidates 16/5, 5/0, 7/0. Adapter BOM candidates are not a complete BOM.
- Tests executed in this session: **none**. GitHub file reads and documentation commit succeeded.
- Risks: BOM semantics incomplete, duplicate source UIDs after copying, hash/parse race, unverified managed part associations, and PR conflict with Phase 1 development progress. No core files modified; no merge.
- Architecture coordination: Lead Architect proposes document revision pinning by SHA-256 and unresolved component/part edges pending managed part registry; handoff contract requires approval before core integration.
- Next tasks: (1) add actual upstream fixture-driven parser tests; (2) establish full native BOM equivalence and negative cases; (3) make read/hash consistent snapshot; (4) reconcile PR target/conflict with Lead Architect; (5) keep draft until validated.

## Director check-in and snapshot hardening — 2026-11-01
- Read current `main` standing orders, existing progress, and PR #2 including latest Director check-in. Scope remains isolated read-only integration on `integration/openschematix`.
- Code change: `revisionworx_integrations/openschematix.py` now reads a bounded source byte snapshot once, hashes exactly those bytes, parses legacy JSON from the snapshot and ZIP from `io.BytesIO`; rejects source inputs over 256 MiB and non-exact mimetype member lengths. Commit `c0ebfaaed15ce0f060d66246c16b4adff2d9085e`.
- Tests added: digest/snapshot consistency, oversized source rejection, oversized mimetype rejection in `tests/test_openschematix_adapter.py`. Commit `e73259a56b546c644ea29e3f6291a88edc399d04`.
- **Tests executed this session: none.** Nine tests are now committed but not executed at the new HEAD. The Lead Architect's previously reported 6/6 applies only to `af5307a`; do not extrapolate it to this change.
- Remaining risks: 256 MiB snapshot consumes memory; ZIP aggregate decompression ceiling and hostile archive handling require additional review; no complete native BOM, real fixture tests in this branch, or confirmed part mapping; Phase 1 progress-file conflict remains. No core or OpenSchematix files changed; no merge.
- Exact next tasks: execute nine tests in an executable checkout/CI; add upstream native ZIP and legacy JSON fixture-driven parity tests; verify native BOM semantics; coordinate integration interface and conflict resolution with Lead Architect.

## Phase 2 coordination and fixture-driven hardening — 2026-11-01

**Assignment:** OpenSchematix Integration Engineer. **Branch:** `integration/openschematix`. **PR:** #2 (draft, not mergeable onto `main` at inspection time). Read latest orders on `main`, PR #6 (Phase 1, ADR-008/009), PR #7 (stacked Phase 2, ADR-010..013), relevant issues, `revisionworx/documents.py`, current adapter/tests and the OpenSchematix 1.1.0 writer and native BOM source.

### Code, fixtures, and documentation delivered to remote branch
- `revisionworx_integrations/openschematix.py`: reject non-regular sources/symlinks, detect changed source metadata during bounded single-snapshot reads, bound ZIP inflation and inspection member count, reject unsafe paths/duplicate member names, validate component metadata types, catch corrupt deflate failures. Commits `2e1ca00a24aede58145205dd27820712449611a8`, `e7151d578ce34150f50f030d738fbaa4cee9ec61`, `858ec6e1964e388c8e5be21739a6c2a925dbab45`.
- `tests/fixtures/openschematix/demo_project.schematix` and `start_stop_latch.osch` preserved as original binary Git blobs from Phase 1 branch. Commit `c28722094bd3b095181ba5608f90ae5df0529e9f`; provenance README commit `86a09d331e7bacc09b8f6b2a67e8f9ab90148876`.
- `tests/test_openschematix_adapter.py`: real-fixture observations, directory-traversal/backslash/duplicate ZIP names, symlink rejection, malformed component and sheet ordering, BOM flag type, archive member cap. Commits `ffe85e9c7b2e7bd6fc636e49b82b988851a9326c` and `d1fcc96199124cc7cc51bcd2784884fc3197cce6`.
- `revisionworx_integrations/openschematix.py`: new isolated `inspect_bytes(raw, suffix)` allows Phase 2 immutable revision bytes to be read without coupling to content-store paths or mutable working files. Commit `e98802c9104df0b1f10b9ebf29d8b79b5dcbecb9`.
- Byte-entry-point regression cases including pinned snapshot after working file edit. Commits `0881a3fe547610e6d3c4cfa3fa0e88f999ddf299`, `8c378cb51da971d6a6b9cbdb622ee55011b5a4ae`.
- Integration and data-model design updated for ADR-010/011, immutable document/revision identity and trust boundary; commits `3c10b4821bcaa8b33df3b31fed4e1b75332ab8ee` and `f296ef8699cc558fd38f725a27443515c8598233`.

### Verification evidence
- **Independently executed tests this session: none.** Container cannot resolve GitHub hosts to clone the repository; GitHub connector reads/writes do not provide executable Python checkout. 23 tests now committed but execution at the latest HEAD remains **UNVERIFIED**.
- Previously, Lead Architect reported **6/6 passed** at earlier `af5307a` on Linux/Python 3.13.18 and compared upstream real fixtures. This is **external evidence on an older revision**, not our current test result.
- Fixture byte identity confirmed by reusing Git's existing original blob SHAs from the Phase 1 branch; remote commit/ref update confirmed. Real fixture expected parsed counts (3 sheets/26 components, 1 sheet/7 components) derive from Lead Architect comparison, not a fresh execution.
- PRs #6 and #7 report 118 passed/1 skipped and 151 passed/1 skipped, respectively, by Lead Architect; these are separate branch results, not interoperability test results.

### Decisions and outstanding risks
- No code changed in OpenSchematix or the Lead Architect's Phase 1/2 core, storage, or PySide6 layers.
- No native BOM equivalence: upstream `bom.generate` reported 16 native BOM lines versus 5 prototype candidates on demo fixture. Parts are still unresolved; `bom_candidates` quantities remain unknown.
- SHA-256 of `inspect_bytes` must equal the authoritative immutable `revision.sha256` before an index observation can be linked to a revision. Managed part mapping and graph publication remain explicitly out of scope without Lead Architect approval.
- Live source metadata checks are not a lock, and 256 MiB source and 1000-member ZIP limits are conservative compatibility gates. Historical project revisions, external symbol classification, custom library semantics, and full native BOM are not indexed.
- PR #2 is based on old `main`, conflicts with Phase 1 `docs/DEVELOPMENT_PROGRESS.md`, and should remain draft; no retarget or merge without coordinated integration.

### Exact next steps
1. Obtain an executable checkout/CI result for **all 23 tests** at the latest HEAD; fix failures before readiness.
2. Compare each real fixture against `OpenSchematix.bom.generate` and add native BOM parity only with actual symbol registry available, preserving no-equivalence warnings meanwhile.
3. Ask Lead Architect to approve `inspect_bytes(raw,suffix)` as the isolated Phase 2 handoff and decide who validates immutable revision digest and stores disposable index observations.
4. Coordinate conflict-free PR retargeting after Phase 1 and Phase 2 integration sequencing is approved.
5. Add more real fixtures including schema 3, custom symbols, multiunit devices, and malformed deflate files; verify Windows and Qt separately in their respective assignments.

## Director request: verify and complete assignment — 2026-11-01

**Orders and activity:** Reread `docs/agents/README.md`, `OPENSchematix_INTEGRATION_ORDERS.md` from `main`, prior progress, draft PRs #2/#6/#7 and issue #8. No scope change: isolated read-only adapter only. PR #6/#7 remain unmerged. GitHub checkout from local container still fails DNS resolution; used GitHub Actions as executable validation.

### Deliverables committed/pushed
- `.github/workflows/openschematix-adapter.yml`: standalone Linux Python 3.11/3.13 unittest matrix; commit `bbd88a6092e5191841682e07be0ec011ebb4095e`. Later removed duplicate pull_request trigger and added concurrency cancellation in `ad1898addfaf4264a3af7c3dcef1d88e7394eb9a`.
- `tests/fixtures/openschematix/hydraulic_cylinder_circuit.osch`: third actual upstream v1.1.0 fixture, Git blob `ae01e0ce70ae89292f2a1a3f05b3904a6959c62c`; commit `4655d07f6f218f3e1d5c3e06bfdbf4e7eafa9690`. Added fixture regression (1 sheet, 9 components, 0 candidate parts) in `b7bde39380185c5ec7a4d1ec4e5061c4e2fd87c8` and provenance README update in `6ebdda7eb43217e30d6f431b7331c77fe41e8df5`.
- `revisionworx_integrations/openschematix.py`: validate `sheet_order` even when falsey/malformed (`fac6fecc5b8394900e7181dd01b6c50bcaeb7428`); classify oversized `mimetype` ZIP member as malformed `InvalidProject` rather than `UnsupportedFormat` (`5eaea7751f7f97a6245993bcfe56d5d09db6f38c`).
- `docs/OPENSchematix_INTEGRATION.md`: CI evidence, defect and third fixture record (`92c0619875cd6227cd6b71c2d7862315815a3de3`).

### Independently verified CI evidence
- GitHub Actions run [41373728900](https://github.com/nrobertson152/RevisionWorx/actions/runs/41373728900) executed the actual adapter on Linux CPython 3.11.17 and 3.13.17, **23 tests run, 22 passed, 1 error on both versions**. Exact failure: `test_reject_oversized_mimetype_member` expected `InvalidProject`, received `UnsupportedFormat`. All other tests, including real upstream native/legacy fixtures and archive security cases, passed. Read and diagnosed full GitHub Actions job logs. Fixed exception type in commit `5eaea775`.
- The third fixture increases the current suite to **24 tests**. Latest CI run on the fix/latest HEAD was queued at last inspection, so **24/24 is NOT yet verified**. Do not mark PR ready until a successful latest-head matrix result is confirmed.

### Risks, architecture and exact next steps
- Source parsing and component extraction now have real fixture coverage. Full native BOM parity is **not** implemented; reference native BOM counts 16/5/7 versus 5/0/0 candidate groups. Never publish candidates as BOM.
- `inspect_bytes(raw,suffix)` remains a proposed Phase 2 boundary awaiting Claude approval. Immutable revision SHA must be checked by the caller; no authoritative part/dependency writes are implemented.
- Issue #8 confirms a separate stale-writer fencing defect in Phase 1. Do not imply RevisionWorx is multiuser-safe or that the integration fixes it.
- Check GitHub Actions for 24/24 Python 3.11 and 3.13 success at exact latest head; fix any further failure. Coordinate approval of the immutable bytes interface and PR retargeting with Claude. Retain draft PR and no merge/release until approved.

## Continued implementation and verified 30-case matrix — 2026-11-01

**Assignment/branch:** OpenSchematix Integration Engineer, `integration/openschematix`; draft PR #2. Reread standing orders, current progress and latest Director PR instructions. Verified previous 24-case matrix at exact commit `6dcfdb0e2e88a38b8077421ab6665e21dc8e88e8`: [GitHub Actions run 41373844070](https://github.com/nrobertson152/RevisionWorx/actions/runs/41373844070), Python 3.11/3.13 both successful.

**Deliverables:**
- Commit `c63a4b8e918485b89390dfd1670f9f2a28807898`: six additional tests in `tests/test_openschematix_adapter.py` for per-member ZIP inflation cap, total ZIP inflation cap, truncated ZIP, mismatched manifest/sheet UID, unsupported legacy schema and nonfinite component coordinates.
- Commit `02e07431371d3c004ebecf9cbcbcd18997a8c194`: precise proposed immutable-revision extraction and provenance contract in `docs/OPENSchematix_INTEGRATION.md`, explicitly separating authoritative revision SHA and document identity from disposable read-only observations and unresolved manufacturer-part candidates.
- **Verified executable tests:** [GitHub Actions run 41380278928](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380278928) on code commit `c63a4b8e`: **30/30 passed** on Linux Python 3.11 and **30/30 passed** on Linux Python 3.13. GitHub job metadata confirms both succeeded; 3.11 log explicitly states `Ran 30 tests ... OK`. The later documentation-only commit does not modify tested code.

**Open risks:** No full native BOM generation or multiunit device equivalence; native BOM 16/5/7 versus candidate 5/0/0 remains intentionally distinct. No production managed-part mapping or authoritative dependency publication. Native custom symbol registry and external libraries are not indexed. No Windows-specific adapter execution. Existing Phase 1 stale-writer issue #8 is outside this integration. No source changes to OpenSchematix or core RevisionWorx.

**Next actions:** Seek Claude approval of proposed immutable-content handoff, SHA responsibility and observation storage; coordinate PR base/stacking with Phase 1/2; add native BOM parity only with tested upstream symbol metadata and explicit authorization. Maintain draft PR, no merge, tag or release.

## Current-head verification, BOM source reconciliation and strict JSON — 2026-11-01

**Orders:** Reread newest `main` integration orders, including the Director's current-head green requirement. PR #2 remains draft on `integration/openschematix`; no core or upstream modifications.

**Verified baseline:** [Actions run 41380313246](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380313246) on exact head `ca5355a4c6878b111d547de597dff05c36caa076`: Linux Python 3.11 and 3.13 both successful, **30/30** tests in each environment. This supersedes earlier code-only evidence.

**New deliverables:** `2229f8985e8ad2b52b0dd61d83038207afbab462` rejects ambiguous duplicate JSON keys; `5b6ed091376bbc1ba918ae47eb7f88b42311bf44` adds native/legacy regression tests; `7aec88ba2d63435866f78fe8a16c85f505475ec8` adds a source-grounded `bom.generate` versus `bom_candidates` reconciliation matrix (eligibility, physical device grouping, duplicate slots, symbolic identity, multiple part numbers, sheet filtering, source refs) in `docs/OPENSchematix_DATA_MODEL.md`. Native source reviewed at OpenSchematix commit `74e4c79`. This **does not implement or claim BOM parity**.

**New CI finding and fix:** [Actions run 41380936524](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380936524) executed **32 tests, 30 passed and 2 failed** on both Python versions. Duplicate-key rejection correctly raised `InvalidProject`, but the JSON wrapper replaced its diagnostic with a generic `invalid JSON` message. Fixed by preserving `InvalidProject` in commit `c5e2ec0717b19f34fcc03730afe4e40f029dfc9d`. The **32-test current code is not yet verified green**; wait for CI at the final head before claiming success.

**Risks and next actions:** full native BOM equivalence and custom symbol registry absent; manufacturer candidate identities unresolved; immutable Phase 2 read handoff and disposable observation ownership require Claude approval; PR #2 retarget/merge requires Director coordination. Check 32/32 on both Python versions at current code SHA, report any failure, and keep PR draft. No merge, tag or release.

## Native BOM oracle + real Phase 2 immutable-revision contract — 2026-11-01

**Assignment:** OpenSchematix Integration Engineer. **Branch:** `integration/openschematix`. **PR:** #2, draft. Reread latest `main` standing orders, PR #2 Director discussion, Phase 2 `documents.py` / `content.py`, upstream OpenSchematix `bom.py`, existing tests and progress. No core changes or OpenSchematix repository commits.

### Source changes, commits, files
- `tests/test_openschematix_native_oracle.py` — commit `49d55c6d30d4f40efdd7202fe282a537801e385b`. Runs *real* upstream `projectio.open_any(..., activate=False)` and `bom.generate` over all three unchanged upstream fixtures, cross-checking native BOM line count, purchasable physical quantities, source refs, and sheet selection against read-only adapter observations.
- `.github/workflows/openschematix-adapter.yml` — commits `aa1f69dae01f82e137aa9f2948cd5fb55f608da0` and `688b74f0dd11bb6c33f7905c820bf3e24cad78a5`. Checks out OpenSchematix at exact upstream commit `74e4c79053721997ae899eb9817ddbcb76eec5a0` and the Phase 2 draft at `47d81658764cd6bde60e391ac9aac24e722317e8` as independent, pinned CI test sources. Runs the standalone adapter suite plus native oracle and Phase 2 contract test in Python 3.11/3.13.
- `tests/test_openschematix_native_oracle.py` — commit `e06d008597ed9a71662cbecdf91913e258b47857`: legacy `.osch` source ref comparison uses sheet position + original component ID, not native wrapper sheet UID, which is freshly created on each legacy load; commit `630eb123de5f3a3c7757b33637943a0d22d265c5` verifies native relay K1's three units count as one device, symbolic Z1/Z2/Z4 group counts three physical parts, and the demo's five explicit manufacturer-part candidate keys match the five native lines containing part numbers (but quantities remain null).
- `tests/test_openschematix_phase2_contract.py` — commit `95e4e283f16a852b15db1fe8e0498df90f24b346`: runs real Phase 2 document registration/ContentStore capture in disposable temporary workspaces; reads the immutable content by SHA-256, checks the adapter's digest against the registered revision and asserts that inspection does **not** change journal sequence, document/revision records or stored bytes; verifies revisions A/B remain individually inspectable after checkout/check-in, even when working files change.
- `tests/test_openschematix_adapter.py` — commit `3e94d6b28c8cbf2cf7e2af5812e1015e15434681` adds deterministic source modification between pre/post descriptor metadata checks, verifying stale/torn file observation is rejected.
- `docs/OPENSchematix_CLAUDE_HANDOFF.md` — commit `8af77c4c0cdec60a2e88ac4f32cca973da5b6709`. Precise provisional integration boundary, actual Phase 2 API touchpoints, local observation identifiers and provenance, native BOM differences, tested source versions and merge blockers.

### CI results independently verified from executable GitHub Actions job logs
- Original head `7ff3ca162b6a3b5bc5591409124005266a8c82b2`: [run 41380989161](https://github.com/nrobertson152/RevisionWorx/actions/runs/41380989161) **32/32 passed** on each Linux Python 3.11 and Python 3.13.
- Initial native oracle [run 41381689097](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381689097) failed **2 of 2 legacy subtests** because the test wrongly equated synthesized `.osch` wrapper UIDs with adapter content-derived UIDs. This was **test identity logic, not parser regression**. Corrected in `e06d008`; native oracle [run 41381776932](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381776932) passed on both Python versions. Native `bom.generate`: 16 / 5 / 7 BOM lines; adapter manufacturer candidates: 5 / 0 / 0. Verified sheet-filtered demo lines: 5.
- Verified complete CI at code commit `630eb123de5f3a3c7757b33637943a0d22d265c5`: [run 41382059229](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382059229) passed all three test commands on Linux CPython 3.11 and 3.13.
- **Current-head verification at `8af77c4c0cdec60a2e88ac4f32cca973da5b6709`: [run 41382170644](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382170644), all jobs passed. Exact per-version job output:** `python -m unittest discover -s tests -p 'test_openschematix_adapter.py' -v` → **33 tests passed**; pinned native oracle command using `test_openschematix_native_oracle.py` → **3 passed**; pinned Phase 2 contract using `test_openschematix_phase2_contract.py` → **2 passed**. **38/38 passed** in Python 3.11 and **38/38 passed** in Python 3.13. No skips were reported.

### What is and is not established
- **Proven:** read-only interoperability with actual Phase 2 registration and immutable revision object *in a disposable Linux test workspace*; working copy diverges without affecting the revision-indexed observation; native OpenSchematix physical BOM behavior/qty/refs and legacy-identity mismatch are independently exercised; deterministic concurrent source mutation is rejected.
- **Not established:** native BOM equivalence for this adapter (it intentionally produces manufacturer-part candidates only); complete custom-symbol and external library resolution; business-managed part IDs, authoritative dependency edges/where-used completeness; production multi-user network-safe storage, Windows runtime validation or a formally accepted Claude interface.
- **Open integration decisions for Claude:** approve the revision bytes read/caller SHA-verification interface and who owns disposable observations; coordinate PR #2 base/restacking with PRs #6/#7 and the historical `docs/DEVELOPMENT_PROGRESS.md` conflict. Retain draft status. No merge, retarget, tag or release without approval.

### Next concrete tasks
1. Request Claude review of `docs/OPENSchematix_CLAUDE_HANDOFF.md` and architecture decisions above through PR #2.
2. Only if authorized, prototype native headless BOM import under an explicitly separate `native_bom` contract with verified quantities/refs and symbol registry; never relabel `bom_candidates`.
3. Expand Windows/large-archive and custom symbol fixture coverage before production readiness. Re-run pinned CI if any adapter code changes.

## Windows adapter matrix and copied-source identity coverage — 2026-11-01

**Role/branch:** OpenSchematix Integration Engineer on `integration/openschematix`; PR #2 remains draft. Work remained isolated; no commits in Claude's core branches or upstream OpenSchematix.

**New commits and changed files:**
- `d5c46545e031fe7924132e1449166354ac871de7` — three tests in `tests/test_openschematix_adapter.py`: unknown source symbol never becomes a verified part, malformed embedded custom-symbol member produces unresolved coverage warning without rewriting, schema-3 boundary fill does not corrupt the component inventory.
- `39173731472d48560766fcb0affcdfd26e50b198` — native oracle regression proves legacy wrapper project/sheet UIDs are regenerated on repeated opens of identical `.osch` bytes, whereas hash-based observation stays identical.
- `0cc8534181fec18fd828d339531462146b426939` — real Phase 2 registry test demonstrates duplicate native `.schematix` files have identical source UIDs and content SHA-256, but **different authoritative `doc_*` and `rev_*` IDs**. Global identity must incorporate PDM IDs, not raw source instance IDs.
- `11abac8534231c6d7b029d29d75af8cd976a8f84` — `.github/workflows/openschematix-adapter.yml` now also verifies Linux and Windows, Python 3.11 and 3.13. Windows runs *only* standalone adapter and upstream native oracle tests; the actual Phase 2 storage contract remains deliberately Linux-only.

**Independently checked CI evidence at exact SHA `11abac8534231c6d7b029d29d75af8cd976a8f84`:** [GitHub Actions run 41382647720](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382647720) completed successfully, with all four job conclusions SUCCESS and full unittest output inspected:
- Linux Python 3.11: **36/36 adapter + 4/4 native oracle + 3/3 actual Phase 2 contract = 43/43 passed**.
- Linux Python 3.13: **36/36 adapter + 4/4 native oracle + 3/3 actual Phase 2 contract = 43/43 passed**.
- Windows Python 3.11: **36/36 adapter + 4/4 native oracle = 40/40 passed**; Phase 2 skipped intentionally.
- Windows Python 3.13: **36/36 adapter + 4/4 native oracle = 40/40 passed**; Phase 2 skipped intentionally.
- **Total across jobs: 166 passing test executions**. These are not 166 distinct test methods. No failures were reported. Test results apply to the exact stated code SHA.

**Evidence-backed scope:** Native OpenSchematix `bom.generate` is verified against three real examples (16/5/7 lines); explicit unresolved candidate groups remain 5/0/0 with unknown quantities. Native K1's 3 units count as 1 physical device; Z1/Z2/Z4 group as quantity 3. The five explicit demo manufacturer-part keys match their native part-number-bearing line identities; this is **not full native BOM parity**. Phase 2 immutable content indexing is tested on disposable workspaces but not integrated into the authoritative storage services.

**Remaining blockers/next tasks:** Await Claude approval of the read-only `inspect_bytes` content handoff, observation storage/refresh and PDM SHA ownership; await Director/Claude restacking of PR #2 with draft PRs #6/#7 and resolution of `docs/DEVELOPMENT_PROGRESS.md` overlap. No managed part identity inference, complete native BOM import, SMB/NFS production claim, or Windows core storage testing. Keep PR draft; no merge, release, or unauthorized retarget. Updated `docs/OPENSchematix_CLAUDE_HANDOFF.md` with the latest evidence and restrictions.

## Final integration approval package and conflict cleanup — 2026-11-01

**Role, scope:** OpenSchematix Integration Engineer on `integration/openschematix`, existing draft PR #2. Read current `main` `docs/agents/README.md` and active `OPENSchematix_INTEGRATION_ORDERS.md`, plus `docs/agents/CLAUDE_RESUME_HANDOFF.md` and latest PRs #2/#6/#7. The resume handoff reports Claude's newer Phase 2 candidate repairs for #8/#9/#10 and independent QA still pending; no claim of production safety is made here.

### Substantive handoff and merge-conflict corrections (commit `467e78dd8c4a6e10de8a6cb5974acd6ebd9a111d`)
- `docs/OPENSchematix_CLAUDE_HANDOFF.md`: explicit proposed request/observation response envelopes, error classifications, SHA/caller responsibility, disposable observation key, lifecycle/refresh rules, and six decisions for Claude. Replaced unsafe illustrative `assert`-based pseudocode with bounded SHA-verified, read-only example using *existing* `Workspace.get`, `ContentStore.path_for`, and `inspect_bytes`. Corrected original suffix to `revision.source_rel_path`, not mutable `document.rel_path`. This is a **proposal**; no production orchestration API implemented.
- `docs/DEVELOPMENT_PROGRESS.md`: restored byte-identical `main` content on own branch, eliminating PR #2's obsolete appended *"tests never executed"* paragraph and removing the root progress file from PR #2's changed-file list. All integration progress lives in this assignment-specific file.
- `tests/fixtures/openschematix/README.md`: restored exact Phase 1 file SHA `ed11a4adffcef6d44e78b74b15e03514b6f7af56` on the integration branch; added `tests/fixtures/openschematix/INTEGRATION_PROVENANCE.md` for full native BOM fixtures and Git blob identities. Shared `.schematix` and `.osch` blobs are byte-identical between PRs #2/#6. No Claude branch was modified.
- `.github/workflows/openschematix-adapter.yml`: Phase 2 overlay pin updated from historical `47d81658` to newer `325b7a801de15c082616781fe086bbd95cb350b6`, the actual inspected head of draft PR #7. PR #2 **was not rebased, retargeted or merged**.

### Actual verified execution
[GitHub Actions run 41387214269](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387214269) executed exact commit `467e78dd8c4a6e10de8a6cb5974acd6ebd9a111d` and **all four matrix jobs passed**, independently checked in GitHub job logs:
- Ubuntu Python 3.11 and Python 3.13 each: `python -m unittest discover -s tests -p 'test_openschematix_adapter.py' -v` **36/36**; native oracle **4/4**; actual pinned newer Phase 2 contract **3/3** = **43/43** each.
- Windows Python 3.11 and Python 3.13 each: standalone adapter **36/36**, native oracle **4/4**, Phase 2 contract deliberately not executed = **40/40** each.
- **166 passing executions across four OS/Python jobs**. These are not 166 different test methods, nor tests of #8/#9/#10 safety acceptance, Windows Phase 2 storage, or physical SMB/NFS.

### PR stacking/conflict evidence and approval request
Live changed-file lists showed historical PR #2 versus #6 overlaps in four paths: `docs/DEVELOPMENT_PROGRESS.md`, the shared fixtures README, and two fixtures. PR #2 versus #7 overlap was only the root progress file. The obsolete root progress delta is now removed; README matches the Phase 1 blob exactly; the two fixtures already have identical blobs. Safe proposed sequence after Director approval: resolve #8/#9/#10 QA acceptance, merge #6, restack then merge #7 when approved, and only after Claude approves the read-only byte/observation handoff restack/integrate PR #2. No automatic merge action executed.

### Remaining decisions/gates
- Claude must explicitly approve immutable content access, exact SHA validation owner, historical original extension, authoritative `doc_id/rev_id`, cache observation owner/invalidation, and all BOM/part/dependency publication boundaries.
- No native BOM import or source symbol registry interpretation, no managed parts, no verified where-used graph, no production Windows storage or SMB/NFS qualification.
- A separate Windows Phase 2 disposable test is **not** run; the known Windows writer-lock race (#10) is Claude/QA scope. PR remains draft.

**CI cost discipline:** the next workflow revision adds path filters so documentation-only progress log updates will not invoke four runner jobs. CI continues on adapter, fixture/test, or workflow changes and remains manually dispatchable. No redundant full rerun purely to publish this note. At this checkpoint the newly path-filtered workflow has not been independently re-verified; the latest pinned-overlay CI at `467e78dd` is green.

### Final exact-code verification and handoff dispatch

- **Workflow/head `5f9c9a4e6d638c617be74b8ded7c2d85ef20c2e4` verified green** in [GitHub Actions run 41387383189](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387383189): all four jobs succeeded. Read actual job logs: Ubuntu/Python 3.11 and 3.13 each ran **36 adapter + 4 native oracle + 3 real Phase 2 = 43 passing**; Windows/Python 3.11 and 3.13 each **36 adapter + 4 native oracle = 40 passing**. Total **166 passing executions**; no skips within the executed suites, and Windows Phase 2 step intentionally excluded.
- Workflow now filters branch pushes to adapter sources, tests/fixtures or workflow changes. Documentation-only progress changes do not schedule another full cross-platform run, while manual dispatch remains available. This appended record is a documentation-only change; the latest tested adapter/workflow code remains `5f9c9a4`.
- Sent precise six-decision request to Claude through [PR #2 comment 6568907274](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6568907274), including concrete Phase 2 byte integrity/suffix/interface shape and the verified no-conflict layering proposal. **Approval remains pending**, not inferred from a successful test run.
- No native BOM import or verified managed Part references have been added. No merge, retarget, force-push, upstream edit, production storage modification, or release. Current isolated adapter research is complete under the presently authorized scope. Hold draft PR #2 until architect/director decision rather than manufacturing speculative features.

## Director's new assignment — cross-platform packaging and installed-artifact readiness (2026-11-01 ET)

**Assignment/branch:** OpenSchematix Integration Engineer, `integration/openschematix`; draft PR #2. Read new Chief Engineering Coordinator PR #2 comment 6568961327, latest PRs #6/#7, `main` parallel assignment orders, current Phase 2 `pyproject.toml`, GUI and CLI entry points, existing QA Qt-offscreen tests and packaging tree. Isolated packaging is authorized; production core, QA and other repositories remain unchanged.

### Delivered files and commits

- `tools/packaging/build.py` (`9717671e`): build **separate PyInstaller GUI and CLI one-directory bundles** from a separately checked-out, exact-SHA-pinned Phase 2 tree. Protect source path, reject preexisting output, install top-level-pinned build dependencies to a new venv and record application version, OS/Python, full pip freeze and binary SHA-256 in `provenance.json`. This is a reproducible command/provenance workflow, not a fully hash-locked deterministic binary build.
- `tools/packaging/{gui_entry.py,cli_entry.py,build-linux.sh,build-windows.ps1,smoke_installed.py,test_builder.py,requirements-build.txt}` (`9393e063`): platform wrappers, isolated frozen CLI `--help` and Qt-offscreen process survival check, and two nondestructive safety unit tests. Smoke uses a clean temporary profile and does not access an existing workspace.
- `.github/workflows/revisionworx-packaging-readiness.yml` (`f91bf344`): focused **Ubuntu/Windows × Python 3.11** packaging CI, pinned Phase 2 source `ffcfb463bc050ff460279fd92f6758e0215f73e3`. No artifact upload, installation, tag, release, or edits to shared Claude/QA workflows.
- `docs/PACKAGING_READINESS.md` (`0bce546b`, updated `6d3554da`): build commands, version/dependency/provenance, strict distinction between frozen offscreen startup and interactive GUI verification, non-destructive upgrade and migration, native paths/permissions, Linux Qt runtime dependencies, and explicit **not executed** release matrix.
- `tools/packaging/gui_entry.py` (`e3f637d`): forced the QtWidgets import to expose hidden Linux Qt loader failure. `.github/workflows/revisionworx-packaging-readiness.yml` (`17cc1cce`): installed actual missing Ubuntu system Qt dependencies, including `libegl1` and XCB packages.
- `tests/test_openschematix_phase2_contract.py` plus isolated adapter workflow (`da1f01e43628cf165f4fbac6bd3a3171d3657bd8`): changed actual Phase 2 tests from direct content-store path reads to **Claude-approved ADR-018 `documents.read_revision_bytes`**, explicitly checking original source suffix, per-revision SHA, source-instance identity and no journal mutations. Added disposable over-limit and content-tampering negative tests. CI Phase 2 pin updated to `ffcfb463`.
- `docs/OPENSchematix_CLAUDE_HANDOFF.md` (`30d3baf` plus subsequent verified status update): recorded Claude's decisions from PR #2 comment 6568970291; replaced the outdated `path_for` example with the bounded Phase 2 read API. **No import-time dependency** on unmerged core has been introduced in the adapter.

### Independently executed GitHub Actions evidence

1. [Packaging run 41388621198](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388621198): Windows Python 3.11 succeeded, including built GUI/CLI startup smoke; Linux built both but **failed Qt GUI smoke** (exit 3). We did not misclassify the overall run as green.
2. Subsequent debugging with an explicit QtWidgets import showed **missing Linux `libEGL.so.1`**; the interrupted intermediate workflow is not success evidence. Added documented Ubuntu system-library prerequisites in packaging workflow only.
3. **[Packaging run 41388877999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388877999)** at packaging code SHA **`17cc1cce846df63b4ddab0dc137f495e3f3101d9`** and source SHA **`ffcfb463bc050ff460279fd92f6758e0215f73e3`**: BOTH Ubuntu and Windows Python 3.11 jobs **PASS**. Each: `python tools/packaging/test_builder.py` = **2 passed**; built actual frozen GUI/CLI onedir executables; frozen CLI `--help` exit 0; frozen Qt GUI startup survived 2.5 seconds in isolated **offscreen** profile; emitted source version `0.1.0.dev0` and per-binary SHA-256 provenance. **No interactive installed GUI, MSI/EXE wizard, AppImage/deb, upgrade, uninstall, or physical SMB** tested. Source checkout and production modules were not modified.
4. **[Adapter/Phase 2 run 41389139273](https://github.com/nrobertson152/RevisionWorx/actions/runs/41389139273)** at exact adapter code SHA **`da1f01e43628cf165f4fbac6bd3a3171d3657bd8`**, target Phase 2 SHA **`ffcfb463bc050ff460279fd92f6758e0215f73e3`**: all four jobs passed. Ubuntu Python 3.11 and 3.13: **36/36 adapter + 4/4 pinned native BOM + 4/4 ADR-018 contract = 44/44 per job**. Windows Python 3.11 and 3.13: **36/36 adapter + 4/4 native BOM = 40/40 per job**. Total **168 passing executions**, not 168 distinct methods. Windows Phase 2 storage remains intentionally NOT EXECUTED.

### Blockers and next gates

- **Packaging is not an installer or release.** Top-level pinned dependencies and pip freeze are not a full hash-locked transitive dependency graph; Qt binary redistribution notices, signing, hardened installer/uninstaller, offline wheelhouse and portable Linux ABI/glibc baseline require separate acceptance.
- The QT_QPA_PLATFORM=offscreen frozen-process survival test proves only a start/crash gate. User-visible installed windows, dialogs, DPI, display backends, real CAD paths and permissions, upgrade/rollback against cloned authoritative data, and GUI accessibility are **NOT EXECUTED**.
- P0 production gates: Claude's #8/#9/#10 correction independent QA acceptance; further Phase 2 txid/GC/history integrity; Windows Phase 2 checkout/revision behavior; physically validated two-host SMB (if supported). No standard plugin/test can substitute for those.
- Claude approved the ADR-018 bounded reader, captured source suffix, scoped PDM source identity, disposable observation ownership and unresolved BOM policy in PR #2 comment 6568970291. **Director approval of merge/retarget/Phase 5 integration remains pending.**
- Current changes are exclusive to `integration/openschematix`. No merges, retargets, force pushes, tags, releases, shipped binaries or upstream OpenSchematix changes.

**Next:** report tested-versus-unexecuted packaging and ADR-018 results on PR #2 to Claude and QA, request review of isolated scripts and missing installer/upgrade gates. Do not expand into production core or declare RevisionWorx release-ready.

## New coordinator assignment — integration acceptance harness (2026-11-01 ET)

**New assignment:** PR #2 coordinator [comment 6569262114](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6569262114) accepted prior adapter/packaging research and requested pinned Phase 2 `d656884b` compatibility, disposable non-destructive installed-artifact smoke, Linux/Windows runtime documentation, concrete manual upgrade/rollback GUI checklist and refreshed cross-PR inventory. Scope remains confined to `integration/openschematix`.

**Implementations and commits:**
- `371a78346abd8fe38144d18e9ec522a0f2e1134f`: new `tools/packaging/smoke_workspace.py` creates disposable PDM data using the **actual frozen CLI**, copies to a Unicode/spaces-named existing-workspace location and checks complete workspace file digests after frozen CLI `status` and Qt-offscreen GUI startup, also verifying the fixture's original was unchanged.
- `31255080407518f7156e81722c40449bd26dc53d`: `tools/packaging/build.py` includes copied-workspace acceptance smoke; `tools/packaging/test_builder.py` adds isolated-environment and hash-difference negative cases (4 total tests). Both existing focused CI workflows were pinned to **exact Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`**, without touching Claude's core or QA workflows.
- `1951f18a3bbd7472d1bc59342bfdc24e87e92cbd`: `docs/INSTALLED_APPLICATION_ACCEPTANCE.md` defines the **manual Windows/Linux interactive GUI and actual old-schema migration/rollback** acceptance procedure, path/permissions/unc/ACL, offline, GL/EGL/XCB and SHA snapshot checks. These manual gates have not been executed.
- `5860a2c44c70006be569735411dd8e17ea14a146`: `docs/INTEGRATION_ACCEPTANCE_MATRIX.md` records live #2/#3/#4/#6/#7 heads, changed-file overlaps, dependency/merge sequence for Director approval only, risk-ranked tested-versus-unexecuted gates, QA local ADR-019 acceptance and newer P1 `RecordCheck` txid replay gap.
- `19ba7329e96ed76abf7cafe4cc9b23499798b145`: Windows frozen CLI Unicode-path JSON smoke had failed under the system default codepage. Updated only `tools/packaging/cli_entry.py` to force UTF-8 output, with matching UTF-8 subprocess decoding in `smoke_workspace.py` and `smoke_installed.py`. **The source CLI is unchanged**; do not extrapolate that its redirected Unicode output was repaired.

**Exact CI outcomes (independently checked job logs):**
- [Adapter run 41390411127](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411127), adapter overlay `312550804`, Phase 2 `d656884b`: **all four matrix jobs green**. Ubuntu Python3.11 and 3.13 each **36 + 4 native oracle + 4 bounded revision contract = 44 passed**; Windows Python3.11 and 3.13 each **36 + 4 native oracle = 40 passed**. **168/168 passing executions**. Windows Phase 2 core contract **NOT EXECUTED** in the adapter workflow.
- [First copied-workspace packaging run 41390411172](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411172), overlay `312550804`, Phase 2 `d656884b`: **Ubuntu PASS; Windows FAIL**. Windows frozen CLI `--json status` raised `'charmap' codec can't encode character Ω` when its Unicode-named copied workspace path was printed through cp1252. This is a real packaging/runtime discovered limitation, not a test skip or adapter parser issue.
- [Corrected copied-workspace packaging run 41390661790](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390661790), overlay `19ba7329e96ed76abf7cafe4cc9b23499798b145`, Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`: **both Ubuntu and Windows Python3.11 jobs PASS**. Each ran **4/4 packaging safety tests**, froze separate CLI+GUI onedir bundles, passed frozen CLI --help, passed Qt offscreen process startup, and printed `COPIED_WORKSPACE_READONLY_EVIDENCE` confirming **five SHA-256-checked files in a disposable original/copy** were unchanged after bundled CLI read and Qt GUI-open. Unicode/spaces copy path exercised. Not real old-version migration, GUI interaction, PowerShell ACL, true installer, long UNC path or two-host SMB.

**Risk gates and sequencing:** QA's latest ADR-019 local acceptances at Phase 2 `d656884b` are narrower than physical journal nonpublication; Distributed has confirmed an independent P1 altered-RecordCheck same-txid replay gap. Phase 1/2 remain draft, #2 remains draft. Recommend **Director-only** #6 (after accepted backport), #3 appropriate QA tests, #7 after all P0/P1 acceptance, then #2 only after approved rebase/retarget; #4 coordinator research cannot enable live multi-user deployment. PR #2/#6 remaining filename overlap is only shared README and two identical fixture blobs; no root progress document conflict.

**Unexecuted release gates:** actual installed interactive GUI, signed MSI/AppImage or distro installer, older customer workspace upgrade/rollback, denied ACL/UNC/long path qualification, Windows core checkout/check-in under installed binary, offline system dependency provenance and physical two-host SMB certification. CLI UTF-8 mitigation applies to the bundled prototype, not Claude's production source entry point.

**Next coordination:** send one comprehensive GitHub PR #2 update to Claude and QA, request review of packaging-only UTF-8 strategy and manual acceptance plan, and await Director/architect integration sequencing. No production code edits, retarget, merge, tag, release or external repository work.

## New assigned batch — offline deployment and failure-recovery qualification (2026-11-02 UTC)

**Director/coordinator order:** [PR #2 comment 6569492534](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6569492534). Continue only on `integration/openschematix`. Goal: no-Python/network-disabled frozen runtime, injectable disk-full/permission/recovery, disposable backup rollback, and Windows optional local filesystem edge probes. No core/QA/Distributed/upstream code edits, merge, retarget, tag or release.

**Implementation commits:**
- `69a532ec98c0dd58f75d4499fe67d02498aad45a` adds `tools/packaging/test_failure_recovery.py`: 3 stdlib unittest cases against pinned `phase2-documents`. Tests *simulated* ENOSPC and EACCES before publication (no new journal/record), ENOSPC after publication (`CommittedNotApplied`, journaled txid and replay/restart), and future workspace format fail-closed; all in disposable temp directories.
- `406f8b521bf2d0c09bfd30f3d6cc373cdcd2decc` adds `offline_container.Dockerfile` and `smoke_offline_container.sh`: minimal Ubuntu Qt system dependencies installed during **image build**, but **test runtime** uses Docker `--network none`, no Python/pip installed, read-only bind-mounted frozen bundles, nonroot user and read-only root filesystem. Frozen CLI help and Qt GUI offscreen startup verified. Does not trace attempted connections or test installed Windows VM.
- `93d5c97265d90b75f1cf454177fedd2db4ac6adc`: extended `tools/packaging/smoke_workspace.py` with `PATH=""`, unreachable proxy vars for frozen child, missing file/root denial, read-only metadata-file read, intentionally corrupt separate disposable copy and rollback from hashed backup. Adds fifth packaging unit test and focused workflow steps for 3 injectable failure tests and Linux offline Docker runtime.
- `15605eb73a5b5e5f5dbbf3361d6ccf3ae510cb8f` adds `tools/packaging/probe_windows_paths.py`: optional Windows frozen CLI test against real extended-length/long paths, accessible localhost administrative UNC, and actual byte-range locked workspace.json. Every probe checks that the original temporary workspace SHA-256 files remain intact; unsupported platform capabilities are reported explicitly, never claimed successful.
- `ccb5520b31209eeb4e03943afc89b8b981474fc1` updates only the isolated packaging workflow to execute the Windows probe after bundling. No modifications to production `revisionworx/` or shared QA workflows. Docs updated afterward.

**Verified independent Actions jobs and exact SHA pairs:**
- [Run 41392254014](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392254014), packaging code `93d5c972` + pinned Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`: **Ubuntu Python 3.11 SUCCESS** and **Windows Python 3.11 SUCCESS**. Each ran `python tools/packaging/test_builder.py` **5/5 passed**, `python -m unittest discover -s tools/packaging -p 'test_failure_recovery.py' -v` **3/3 passed** (ENOSPC/EACCES/future format), actual built GUI/CLI and disposable copied-workspace smoke **passed**; Ubuntu also verified the network-disabled/Python-free Docker runtime.
- [Run 41392572210](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210), latest code/workflow SHA `ccb5520b31209eeb4e03943afc89b8b981474fc1` + Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`: **Ubuntu and Windows Python 3.11 jobs SUCCESS**, each with **5/5 packaging safeguard + 3/3 source fault-injection tests**. Both passed CLI help, frozen Qt-offscreen startup, missing path, readonly metadata, preserved original/copy SHA, malformed **test copy** refusal, backup restore. Docker Linux printed `OFFLINE_CONTAINER_EVIDENCE network=none python=absent cli_help=pass gui_offscreen_start=pass`. Windows printed `WINDOWS_PATH_EVIDENCE`: extended-length `passed`, **401-character** local long-path `passed`, localhost UNC admin share `passed`, byte-range-lock read `read_rejected`; the original 4-file probe workspace hash map remained unchanged. No errors in these jobs.
- GitHub test counts mean **5+3=8 unit-test executions per OS job**; the subprocess smokes/Windows probes are *additional command-level checks*, not unittest methods. Neither source failure injection nor Docker test establishes hardware disk-full, actual OS ACL denial, power-loss, Windows offline native install, zero attempted network sockets, real customer migration or remote concurrent SMB.
- This batch intentionally kept pinned source SHA `d656884b39401134149273dd0121ef620b76eb8e` for reproducible comparison with the prior adapter run [41390411127](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390411127). Live Phase 2 has since advanced to `366d4355abcde7543122551fadaab01ac9832e01` with additional ADR-019/021 changes; acceptance must be performed at that SHA separately before a release claim.

**Remaining release blockers:** no actual MSI/AppImage/deb installer or uninstall, interactive physical Windows/Linux GUI, Windows VM free of Python and isolated from networking, runtime network syscall/telemetry audit, full offline install from wheelhouse, real Windows ACL/share-deny and two-host SMB behavior, actual ENOSPC/power-loss/backup migration of historical data, old-version schema transformations, signing/licensing/reproducibility, and fresh independently accepted latest Phase 2 source. The Director must approve any merge sequence. PR #2 remains draft.

**Next:** update PR #2 evidence to Claude and QA, keep docs accurate, await an approved updated Phase 2 pin or targeted platform gap instruction, and do not create speculative production features.

### Final documentation / publication checkpoint — 2026-11-02 UTC

- `73259d14ec15d5095445cef5c50bf83dbc7ba1bd`: updated `docs/PACKAGING_READINESS.md`, `docs/INSTALLED_APPLICATION_ACCEPTANCE.md`, `docs/INTEGRATION_ACCEPTANCE_MATRIX.md` and this progress log; created dedicated `docs/OFFLINE_FAILURE_QUALIFICATION.md`. It distinguishes actual Ubuntu no-network/no-Python execution from Windows PATH/proxy isolation and real clean offline VM acceptance. It records 5+3 test methods per platform and separate subprocess/Windows probes on exact latest green code SHA `ccb5520b`.
- `ba3c05de381d89bd30e26f73657c2203196fc28b`: corrected the risk matrix to distinguish the **historical** independently reproduced ADR-017 RecordCheck txid defect at older Phase 2 from Claude's newer ADR-021 v2 candidate on `366d4355`, which still requires QA acceptance. No authority semantics or new behavior was inferred from the packaging run.
- These are **documentation-only commits** after the successfully tested packaging/workflow SHA `ccb5520b31209eeb4e03943afc89b8b981474fc1`; the path-filtered workflow rightly did not run again. GitHub [41392572210](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210) remains the exact-code verification anchor. Windows filesystem probe results were `extended_length_prefix=passed`, `long_path_over_260=passed(length=401)`, `unc_local_admin_share=passed`, `byte_range_lock=read_rejected`, unchanged source SHA-256 sample. Ubuntu offline image had runtime `--network none` and no Python, but the image was built with network and no syscall/telemetry audit occurred.
- **Unexecuted:** actual Windows internet isolation and no-Python clean VM, real historical-version migration, power interruption, hardware disk-full, true restricted ACL and remote concurrent SMB, installed GUI interaction, installer signing, authoritative production release. These remain release gates, and all relevant PRs remain draft pending Director/Claude/QA decisions. Do not retarget, merge, tag, release or modify core upstream without approval.

**Engineering task handoff:** PR #2 update for Claude and QA with the exact evidence above, then hold additional packaging development until a specifically authorized newer Phase 2 pin or a reproducible acceptance defect warrants another test. Release status: not ready.

## Principal integration/deployment continuation — 2026-11-02 (America/New_York)

Code/procedure commit: `ab3ac4a` (full SHA in `git log`). Base integration HEAD:
`6c5e37dc88fc1aeb729555f25c3ff26bdba9ca29`. No production authority or
OpenSchematix source changed. New Linux offline lifecycle gate and concrete
manual fault/recovery evidence requirements are in installed-application acceptance.

### Inspection and external limitations

- Working tree was clean before resuming existing `integration/openschematix`.
- `docs/agents/README.md` and `*_ORDERS.md` are absent in both the integration
  branch and fetched `main` (`f040da926aee9a1711257f47516d4234434d76d5`).
  Read repository AGENTS.md, requirements, architecture, progress, integration,
  packaging workflow and acceptance documents. The Director's chat directive
  supplies the current assignment; no invented standing orders were used.
- Read fetched PR #7 source/architecture/history at
  `366d4355abcde7543122551fadaab01ac9832e01`, including ADR-021 request identity.
  This is **not independent acceptance** of that candidate. Packaging pin stays
  `d656884b39401134149273dd0121ef620b76eb8e`; no retarget/rebase/merge.
- Native Git reads work. `gh pr view 2 --repo nrobertson152/RevisionWorx --comments`,
  REST issue-comment reads, and `gh pr view 7 --json ...` all return `Forbidden`
  from api.github.com. Latest PR comments/CI cannot be independently inspected
  with this session's available API access. No new token requested or extracted.
- Historical CI anchor from the existing log remains
  https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210 ;
  **not rerun or newly verified evidence for this commit**. Current workflow:
  https://github.com/nrobertson152/RevisionWorx/actions/workflows/revisionworx-packaging-readiness.yml
  (push triggers Windows/Linux jobs; outcome must be checked separately).

### Executed locally (Debian 13, Python 3.12.14)

From repository root, against pinned Phase 2 source extracted read-only from Git
into `/workspace/setup/revisionworx-phase2`:

```
OPENSCHEMATIX_ORACLE_SOURCE=/workspace/OpenSchematix REVISIONWORX_PHASE2_SOURCE=/workspace/setup/revisionworx-phase2 python -m unittest discover -s tests -v
python tools/packaging/test_builder.py
REVISIONWORX_PHASE2_SOURCE=/workspace/setup/revisionworx-phase2 python -m unittest discover -s tools/packaging -p test_failure_recovery.py -v
```

Results respectively: **44 passed / 0 failed / 0 skipped / 0 xfail**;
**5 passed / 0 failed / 0 skipped / 0 xfail**;
**3 passed / 0 failed / 0 skipped / 0 xfail**. OpenSchematix oracle SHA:
`61674fa4828075f899e3f1ea09e4f85dc9727828` (1.1.0). Integration tests confirm
bounded reads, digest verification, nonmutation and native BOM limitations.

Frozen **CLI-only** local build (not the qualified two-binary builder):

```
/workspace/venvs/openschematix/bin/python -m PyInstaller --noconfirm --onedir --name RevisionWorxCLI --distpath /workspace/setup/cli-build/dist --workpath /workspace/setup/cli-build/work --specpath /workspace/setup/cli-build --paths /workspace/setup/revisionworx-phase2 tools/packaging/cli_entry.py
/bin/sh tools/packaging/offline_lifecycle.sh /workspace/setup/cli-build/dist/RevisionWorxCLI/RevisionWorxCLI
```

Builder Python 3.12.14, PyInstaller 6.22.3, hooks 2026.9; executable SHA-256
`e92539fc5fce50dd9a83072b6165df6c7b88496f0c94a1d6f308f1ebf9e70fce`.
This intentionally does **not** claim the existing Python 3.11 / PyInstaller
6.13.0 CI builder matrix was executed locally.

Built `tools/packaging/offline_container.Dockerfile` with Docker 28.4.0,
Ubuntu 24.04 runtime image ID
`sha256:fd0a31530ff1e25340111d6218e31cbf5d7e6d78d3c174ec0db111f475b90118`.
Copied the shell harness into a readable temporary directory and made generated
bundle directories readable by the non-root test UID (the local cloud checkout
and generated directories initially had 0700 permissions). Then ran:

```
docker run --rm --network none --cap-drop ALL --read-only --tmpfs /tmp:rw,nosuid,size=128m --mount type=bind,src=/workspace/setup/cli-build/dist,dst=/bundles,readonly --mount type=bind,src=/workspace/setup/offline-harness,dst=/harness,readonly revisionworx-offline-qt /bin/sh -ec 'for tool in python python3 pip; do ! command -v "$tool"; done; test ! -e /workspace; /bin/sh /harness/offline_lifecycle.sh /bundles/RevisionWorxCLI/RevisionWorxCLI'
```

**PASS, exit 0**, non-root UID 10001, network disabled, no Python/pip command,
no source checkout mounted, read-only binaries, writable disposable `/tmp` only.
Project creation, document registration, two SHA-256 verified revisions,
persisted checkout/check-in across fresh processes, reopen, original revision
export and actual Unix directory write denial all passed. The denied project
was absent from both disk and registry; existing revision verification passed.
This is a command-level acceptance scenario, not additional unittest methods.

### Remaining gates

No Windows run or interactive GUI acceptance executed in this session; no full
GUI bundle rebuilt locally. Shell CLI lifecycle does not demonstrate termination
mid-write, recovery from an unexpected kill, physical ENOSPC, power loss, native
installer upgrade/rollback, old schema migration, syscall network-attempt audit,
or two-host SMB/NFS correctness. Existing injected recovery tests passed only
against the old source pin. The new Phase 2 compatibility gate remains pending
independent QA acceptance and accessible PR/CI evidence. Manual procedures use
only disposable copies/VM snapshots and prohibit production engineering data.

## 2026-11-02 — relocation and persistent revision export acceptance

**Assignment:** Principal Integration, Packaging and Deployment Engineer; branch `integration/openschematix`; draft PR #2. Read PR #2/#7 and inspected packaging sources and latest packaging Actions execution. The requested `docs/agents/README.md` and `OPENSchematix_INTEGRATION_ORDERS.md` are not present on current `main` or this branch; binding instructions are the Director's explicit task and the PR #2 coordination comments. No production core or upstream CAD files changed.

**Prior independently verified checkpoint:** Actions [41563020653](https://github.com/nrobertson152/RevisionWorx/actions/runs/41563020653), packaging HEAD `31e041529e312d8fd0e6b617424680c5083160e7`, frozen Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`: Ubuntu/Windows packaging jobs succeeded. Linux output contains `OFFLINE_LIFECYCLE_EVIDENCE` verifying project creation, registration, two revisions, checkout/check-in via separate processes, read/reopen/verify/export, denied directory write, with a networkless runtime lacking Python/pip. That result belongs **only to the previous checkpoint**, not the changes below.

**New isolated implementation:**
- `b07475a1acc0acd9cec4ac5804736b87bfb32dc6`: extend frozen Linux lifecycle to independently export and SHA-check **both** the immutable original and second revision after the permission-denied attempt. This adds a regression invariant for historical content after reopening.
- `48a4e87c7c4af5b37d199cd7e89b9a06e456b8ad`: copy each frozen onedir tree into an isolated Unicode-and-spaces directory under the test container's `/tmp`; run the CLI lifecycle, CLI help, and Qt-offscreen GUI from **relocated copies**, with the build artifacts remaining read-only.
- `acc3bdff94d1234242da2865d445099f9686b5d9`: expand CI disposable tmpfs to 768 MiB to accommodate the two relocation copies.

**Verification:** new code awaits CI against exact new packaging commit. No fresh PASS count is claimed. Existing tests remain pinned to the older Phase 2 source; Claude/QA must first accept newer safety changes before packaging source pin is advanced. Container `--network none` demonstrates no *required* connectivity in tested paths but not absence of attempted sockets. No native installer, interactive real-display GUI, cross-host SMB certification, tested old-version migration, genuine disk exhaustion or interrupted power failure. Do not merge/release/retarget.

## 2026-11-02 — diagnosed relocated GUI execution refusal

Inspected packaging run [41563277330](https://github.com/nrobertson152/RevisionWorx/actions/runs/41563277330) at `acc3bdff94d1234242da2865d445099f9686b5d9`: Windows **success**, Ubuntu **failure** during container relocation (no application failure trace). Added command tracing at `76b76a71bdd271db3120b50235725e9d3a231c19`; next [run 41564072455](https://github.com/nrobertson152/RevisionWorx/actions/runs/41564072455) confirms both bundle copies succeeded but `test -x '/tmp/installed copy Ω/gui/RevisionWorx'` failed before starting any relocated executable. Suspected cause: test tmpfs does not permit execution without explicit `exec` mount flag. This is a packaging fixture/mount issue; no source defect demonstrated. Commit `12dd4b1270991d67c8b89fd82ff2b749565ba09f` makes that *disposable CI mount* executable while keeping read-only source bundles, `--network none`, no Python/pip, nonroot, and the same strict relocated-binary assertions. [New run 41564388195](https://github.com/nrobertson152/RevisionWorx/actions/runs/41564388195) pending at entry creation; do not claim pass until verified.

Per Director's priority, **do not repin Phase 2**: source stays `d656884b39401134149273dd0121ef620b76eb8e` until QA and Distributed Systems publish explicit accept/reject on newer `e5b38a6`. Claude B issue #13 owns desktop GUI; our integration role owns later packaged interactive GUI and clean VM acceptance, with distinct evidence for offscreen vs interactive runs. PR #2 remains draft. No merge, release or production edits.

### CI acceptance of copied-bundle fix (verified)

[Actions 41564388195](https://github.com/nrobertson152/RevisionWorx/actions/runs/41564388195) finished **SUCCESS** at exact packaging code/workflow `12dd4b1270991d67c8b89fd82ff2b749565ba09f`, with pinned Phase 2 source `d656884b39401134149273dd0121ef620b76eb8e`. Both `ubuntu-latest` and `windows-latest` on Python 3.11 completed: per OS 5 packaging-safeguard tests, 3 disposable recovery-injection tests, frozen CLI/Qt offscreen build and copied-workspace verification; Windows long/extended-path, localhost UNC and file-sharing probe succeeded. Ubuntu additionally passed relocated frozen GUI+CLI from `/tmp/installed copy Ω/`, `--network none`, no runtime Python/pip, intact two-generation SHA-256 exports after permission denial, and Qt offscreen survival. Its log contains `OFFLINE_CONTAINER_EVIDENCE network=none python=absent relocated_bundle=pass cli_help=pass gui_offscreen_start=pass`. The shell process passed its original/second revision export digest assertions, but its evidence line does not separately print `historical_exports=verified`.

The prior `test -x` failure was resolved by explicitly enabling execution on the **disposable tmpfs**: `--tmpfs /tmp:rw,exec,nosuid,size=768m`. This does not relax `--network none`, read-only bundle mounts, nonroot execution, or the unchanged executable assertions. No actual interactive GUI, physical network absence on Windows, socket syscall audit, native installer or latest Phase 2 safety qualification is implied. All integration release gates remain pending and PR #2 remains draft. Coordination posted on Claude B [issue #13](https://github.com/nrobertson152/RevisionWorx/issues/13#issuecomment-6593621163). Await explicit QA and Distributed Systems accept/reject for `e5b38a6` before any separately pinned compatibility pass.


## 2026-11-02 — exact-SHA frozen desktop alpha integration passed

**Coordinator assignment:** separately pinned, disposable, independent frozen
GUI workflow for Claude B draft PR #14. Implementation remains ONLY on
integration/openschematix; the distinct production-authority packaging pin is
unchanged. Test harness source SHA (last code commit):
7fbd7b31135663f498bb15cc50dc80ba4919db9e. GUI source SHA:
f00aa31b6f66fbe0e78ff440aed7026e869138b3.

**Results:** [Actions 41566749753](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566749753)
**SUCCESS on both Ubuntu 3.11 and Windows 3.11**; each ran five successful
packaging safeguard unittests, frozen build/CLI help/Qt startup and copied
workspace regression. Both ran the new test-only Qt GUI acceptance with
real Qt start-page project creation, ImportDialog, checkout, check-in
dialog and two persisted revisions, a separate frozen GUI process reopen,
SHA-256 content verification and historical exports by a separately frozen
CLI; workspace authority hash set remained unchanged on read-only reopen.
Whole GUI/CLI frozen directories were copied into paths with spaces and
Unicode. Windows also passed local 401-character/extended path, localhost UNC
and sharing probes (no multi-host assertion).

Ubuntu additionally ran the Qt widget test and normal GUI startup from the
copied bundles inside a **nonroot network=none Python/pip-free read-only-root
container**, with executable disposable tmpfs. Container log:
OFFLINE_ALPHA_GUI_EVIDENCE python=absent network=none copied_bundle=pass
qt_create_import_checkout_checkin=pass qt_reopen=pass cli_sha_exports=pass
normal_gui_start=pass. This is offscreen widget automation, not visible
mouse/keyboard interaction or attempt-traced absence of sockets.

**Negative/fix:** [initial alpha run 41566452430](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566452430)
reached GUI success on both OS, then failed solely because the integration
harness attempted a second exclusive CLI export into an already occupied
test target. Corrected by using unique, phase-named outputs at 7fbd7b3;
no core change or safety assertion was weakened. The original production
packaging workflow still passed at the same code SHA
[41566749764](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566749764)
with older approved source d656884b39401134149273dd0121ef620b76eb8e.

**New GUI regression outside approved pin:** Claude B draft PR #14 now at
e5725df17218b5bb9fb975389a0580aed63f2205. Its own cross-platform
[CI 41566802123](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566802123)
fails 10 Qt tests and passes 10 on each OS; common root is
revisionworx/gui/qt/documents.py line 236 using the wrong relative
workspace import. Posted a precise handoff to [PR #14](https://github.com/nrobertson152/RevisionWorx/pull/14#issuecomment-6593973493).
The successful frozen test **must not** be projected onto that newer GUI
commit; wait for its passing exact-SHA UI regression CI before repinning
the distinct alpha workflow.

**Safety hold:** Claude A's latest PR #7 core d2b7a1c has a reported
additional protocol-only crash/revocation race. Director approves trusted
single-user local filesystem as alpha scope, NOT unverified cooperative
writer safety, SMB or release. No Phase 1 backport or main merge.
Unexecuted: real interactive installed GUI, truly disconnected Python-free
Windows VM, installer, upgrade rollback, power-loss, physical disk-full,
actual Windows ACL denial, socket attempt tracing and concurrent SMB.
See docs/FROZEN_DESKTOP_ALPHA_ACCEPTANCE.md and integration matrix.


### Subsequent exact-head authority verdict

After the alpha test was accepted, independent QA posted a definitive
**REJECT/HOLD** on Claude A's d2b7a1c Phase 2 source:
[QA exact-SHA four-platform CI 41567007815](https://github.com/nrobertson152/RevisionWorx/actions/runs/41567007815)
reproduces TWO protocol-only dual-effective-writer cases even with a trusted
local locks directory. Distributed independently confirmed one in
[run 41567096076](https://github.com/nrobertson152/RevisionWorx/actions/runs/41567096076)
on all four OS/Python variants. These do not require malicious external
rename/replace and are therefore **inside** the Director's single-user
trusted-local alpha safety scope. The existing frozen GUI acceptance only
establishes the tested UI and CLI paths on disposable data; it cannot
override this rejection. No repin, Phase 1 backport, merge or release.


## 2026-11-02 — current GUI b4c6d72 frozen alpha source acceptance

**Assignment:** Coordinator PR #2 requests a separately pinned exact-SHA frozen
GUI matrix, with GUI code held by Claude B (PR #14). Claude B fixed both
document-selection/health refresh regressions at GUI source commit
b4c6d72664a65f1c51c665df8fd3aa30552b5f45, whose own
[desktop-alpha CI 41567499552](https://github.com/nrobertson152/RevisionWorx/actions/runs/41567499552)
completed SUCCESS on Ubuntu and Windows Python 3.11.

Integration updated *only* the independent test-only alpha workflow at
packaging commit 06785d1035e6ec09b01f91d9b1bc80dc78ae2fec to pin
the same GUI source SHA in both checkout and builder verification.
**[Integration run 41568957858](https://github.com/nrobertson152/RevisionWorx/actions/runs/41568957858)
finished SUCCESS for both Ubuntu and Windows Python 3.11 matrix jobs.**
Source provenance in each job confirms SHA b4c6d72664a65f1c51c665df8fd3aa30552b5f45,
test-only GUI wrapper true. Both OS passed five packaging-safeguard unittests,
bundled CLI/Qt startup, copied-workspace immutable hash preservation,
relocation into a Unicode/spaces directory, *actual frozen Qt widget* create,
import, checkout/checkin with description, two revision rows, fresh frozen
GUI-process reopen and independently verified frozen CLI SHA-256 exports.
Windows extra: 401-character local path, extended-length path, localhost
UNC-loopback read, locked-file read rejection and original hashes unchanged.
Ubuntu extra: no Python or pip, nonroot/read-only-root image under Docker
--network none, copied bundle widget workflow, independent exported historical
SHA-256 and normal frozen GUI startup all passed
(OFFLINE_ALPHA_GUI_EVIDENCE).

This passes **only exact GUI/source functional and frozen-runtime acceptance**
on disposable local data. It is NOT proof of an interactive user-operated
desktop/installer, absent socket attempts, clean Windows without Python or
network, genuine power loss, or core storage writer exclusivity. Director
approved trusted single-user local alpha scope only. QA now independently
confirms **three** protocol-only dual-effective-writer P1 hazards against
unaccepted core d2b7a1c; all are inside alpha cooperative protocol scope.
Do NOT merge, backport, release or repin baseline authority workflow;
separate revisionworx-packaging-readiness.yml still pins d656884b. PR #2
remains draft. New acceptance does not inherit safety approval for any core
commit.


## 2026-11-02 — current GUI 17651af acceptance + observable/F5 regression

**Assignment:** independently accept newer Claude B desktop alpha from PR #14,
without production-core repin, with real frozen Qt create/import/check-in/
history/restart, SHA-256 export verification, Linux isolated runtime and
Windows packaged-path validation. Exact tested GUI SHA
17651af68128572c7c83429513044a5948dd07bd; its own native GUI
[CI 41570211687](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570211687)
completed SUCCESS Ubuntu and Windows Python 3.11. Claude B's subsequent
3c11c774 commit changed documentation/screenshots only, not GUI code.

**New integration commits:** ce699aac650d2493ff757ff8da1ccb3404be139d
repinned ONLY the separate alpha test-workflow source to 17651af;
3df2ad9a54a1980223021a557529efeb999cc365 added actual frozen widget
assertions for GUI observable-state contract version 1, initial project and
document selection, import/checkout/check-in action states, no false error
for successful check-in, selected document/history retained by F5, and
persistent observable state after separate-process reopen;
b4866ec56e20af02f882699749feddd7cab23e0f made the host-side verifier
REQUIRE these explicit JSON evidence fields and report them.

**Verified exact [alpha CI run 41570682514](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570682514)
at integration test SHA b4866ec56e20af02f882699749feddd7cab23e0f:
BOTH Ubuntu and Windows Python 3.11 SUCCESS.** Each ran five packaging
safeguard tests, built test-only frozen PyInstaller GUI and separate CLI,
relocated both onedir distributions into Unicode/spaces paths, performed
Qt widget create/project select, ImportDialog, checkout and CheckinDialog,
generated two immutable revisions, used a new GUI process to reopen,
verified persisted GUI and contract-v1 observable state, verified F5
preserves selected project/document and history, independently exported
and SHA-256 verified BOTH historical revisions through the frozen CLI,
and confirmed authoritative read-only workspace hashes were unchanged.
The log prints gui_observable_contract_v1=true,
gui_f5_selection_preserved=true, revision_count=2,
cli_independent_exports_sha256=true, separate_gui_process_reopen=true.
Each job provenance confirms alpha_acceptance_test_wrapper=true and
source_sha=17651af68128572c7c83429513044a5948dd07bd.

**Ubuntu runtime:** Python/pip-free, nonroot/read-only-root Docker,
network=none; relocated GUI/CLI Qt widget workflow, independent historical
exports and normal non-probe GUI startup all passed. Log:
OFFLINE_ALPHA_GUI_EVIDENCE python=absent network=none copied_bundle=pass
qt_create_import_checkout_checkin=pass qt_reopen=pass cli_sha_exports=pass
normal_gui_start=pass. **Windows:** 401-character local, extended-length,
localhost UNC read and byte-range lock read-rejection tests passed, original
SHA-256 files preserved. Windows had no Python on child PATH, not a clean
Python-free OS or physically disconnected network.

**Unmodified older baseline packaging workflow:** [run 41570682310](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570682310)
at the same integration SHA b4866ec56e20af02f882699749feddd7cab23e0f
passed Ubuntu/Windows, five builder tests and three disposable fault-injection
tests per OS, frozen GUI/CLI and copied read-only workspace; Ubuntu Python-free
network-none container lifecycle and both historical exports passed.
Its provenance STILL pins production source
d656884b39401134149273dd0121ef620b76eb8e and
alpha_acceptance_test_wrapper=false.

**Scope and blocker:** This accepts exact-source offscreen frozen-GUI
functionality only, not manual user-operated UI, real Windows disconnected
VM without Python, signing/installer/upgrades, power loss, socket audit or
multiuser SMB. QA has demonstrated three genuinely protocol-only
dual-effective-writer races on earlier Phase 2 d2b7a1c; Claude A owns a
new correction and independent QA/Distributed safety decisions. No phase
backport, core repin, merge, retarget or release. PR #2 remains draft.

## 2026-11-02 — additional negative-state frozen GUI acceptance, exact SHA

**New work on integration branch only:** `44f8074d746886e88c5dca639779e39a0aecba26`
extended the TEST-ONLY frozen GUI probe. In the reopened disposable project, it
temporarily renames the working file out of its expected location, presses the
actual GUI F5 refresh, and asserts the documented observable-state contract:
the selected document and two immutable history rows remain accessible,
the listed document `file_state` becomes `missing`, and check-in stays
disabled. A `finally` block restores the same original file and refreshes;
the GUI must show `file_state=unchanged` with the same two revisions.
No external source file or journal-backed authority is intentionally edited.
Host-side frozen verifier commit `3f0a57149e15015c6d382031b16fe2614a4ddccd`
requires positive `gui_missing_file_fail_closed` and
`gui_missing_file_restored` evidence rather than accepting process exit
alone.

**Independent pinned CI [alpha run 41571637323](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571637323)
at exact Integration SHA `3f0a57149e15015c6d382031b16fe2614a4ddccd`:
Ubuntu AND Windows Python 3.11 SUCCESS.** Both frozen widget runs emitted
`gui_missing_file_alert_and_restore=true`,
`gui_f5_selection_preserved=true`, `gui_observable_contract_v1=true`,
`cli_independent_exports_sha256=true`, two valid historical revisions,
separate-process GUI reopen, original data unchanged, and provenance source
`17651af68128572c7c83429513044a5948dd07bd` (Claude B desktop alpha).
The test-only PyInstaller wrapper flag is true. Ubuntu additionally passes
Python/pip-absent, nonroot/read-only-root Docker `--network none`, relocated
frozen Qt UI workflow, new-process reopen, both CLI revision exports, and
normal GUI startup. Windows passes 401-character local and extended-length
path smoke, localhost UNC loopback read, byte-range lock denial and preserved
original SHA-256 values. The negative scenario is reversible **only because
it uses a disposable project**; no customer workspace is tested.

**Original production-source packaging [CI run 41571637265](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571637265)
at same Integration SHA: Ubuntu AND Windows SUCCESS.** Distinct workflow
still pins `d656884b39401134149273dd0121ef620b76eb8e`, not corrected
Phase 2 candidate; no production-core authority repin, no merge/backport or
release.

**Parallel core-safety development:** Independent QA scoped-ACCEPT of all
three previously demonstrated local protocol writer-lock P1 corrections
at Claude A source `fe8a179f0203ef08151dc8459c84609dc639c4fe` in
[four-platform QA 41571153163](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571153163):
ordinary positive safety 3 PASS, four actual separate-process crash/restart
PASS, full Ubuntu 256 PASS / 3 SKIP and Windows 255 PASS / 4 SKIP on
Python 3.11 and 3.13. Historical vulnerable negative control
[41571222000](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571222000)
demonstrates the original three unsafe writers, preserving test sensitivity.
Distributed has confirmed focused admin-break and revoked-cleanup acceptance;
full independent disposition of release/displaced archival and additional
qualification is not treated as production signoff. Independent QA scope
does NOT certify all schedules, hostile mutations, physical outage, network
folder multiuser safety or a usable human-operated installed GUI. Director
authorization still required for any production integration/backport/merge.

**Maintained release hold:** PR #2 remains draft, production packaging pin
unchanged. Test-only wrapper MUST NOT ship. Actual manual interaction, clean
physically disconnected Windows runtime, signed native installer and
two-host SMB/NFS remain unexecuted.

## 2026-11-02 — separately pinned corrected-core + GUI frozen compatibility PASS

**New experimental integration work only:** staging script at
`795abd231d8ce4d59ab6823b6222fa446763ddd7` and separate
compatibility workflow at `393485e96385beded0f74693bb26644c129851eb`.
They combine corrected Phase 2 core SHA
`fe8a179f0203ef08151dc8459c84609dc639c4fe` with desktop alpha
GUI SHA `17651af68128572c7c83429513044a5948dd07bd` in an
intentionally dirty **disposable CI checkout only**, never on either
branch or any actual engineering workspace. Provenance receipt records
BOTH SHAs and individual SHA-256s for 10 overlaid GUI source files.

**[Combined source CI 41572137880](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572137880),
workflow harness `393485e96385beded0f74693bb26644c129851eb`:
Ubuntu and Windows Python 3.11 matrix BOTH SUCCESS.** The independent
test-only frozen GUI and CLI built and exercised project create,
external-file import, checkout, modified-file checkin, two immutable
journal revisions, F5 state/selection preservation, fresh GUI process
reopen, absent working-file detection and restoration, and separate
frozen CLI exports with SHA-256 verification of both historic revision
blobs. Both jobs print
`TEST_ONLY_COMPOSITE_SOURCE_RECEIPT` core SHA `fe8a179`,
GUI SHA `17651af`, 10 overlaid files, dirty-by-design true, and
`FROZEN_ALPHA_GUI_EVIDENCE` with
`gui_missing_file_alert_and_restore=true`,
`gui_observable_contract_v1=true`,
`gui_f5_selection_preserved=true`,
`cli_independent_exports_sha256=true`. Original frozen data and
read-only workspace checks remain passing.

Ubuntu also passes the full relocated frozen Qt/CLI workflow and normal
GUI launch under Python/pip-free, nonroot/read-only-root Docker
`--network none`; Windows additionally passes long/extended local
paths, localhost UNC loopback read, byte-range file-lock refusal and
original SHA-256 preservation. This is deterministic **offscreen**
automated UI and test-only frozen code, **NOT** interactive GUI,
signed/native installer, real Windows Python-free disconnected VM,
actual power cut/ACL, socket-attempt audit, or shared two-host SMB/NFS.

**Independent core safety review:** QA and Distributed Systems each
scoped-ACCEPT all three historically confirmed cooperative-protocol
writer-lock P1 fixes at exactly `fe8a179` on native local OS;
[QA 41571153163](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571153163)
and [Distributed 41570591613](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570591613),
[41570924625](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570924625),
[41571231241](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571231241).
Neither scoped verdict grants production merge, broad writer proof, Phase 1
backport or release.

New reference:
[exact source composition and acceptance matrix](https://github.com/nrobertson152/RevisionWorx/blob/integration/openschematix/docs/CORRECTED_CORE_GUI_COMPATIBILITY.md).
Normal baseline packaging still separately pins `d656884b`; standalone
alpha GUI workflow still separately pins `17651af`. PR #2 stays draft,
and no core branch, QA, Distributed, GUI branch, production source pin,
release, tag, merge or backport was modified.

## 2026-11-02 — final two-source compatibility with staging safeguards and Windows asynchronous regression

Two-source staging test commit `516db2d` adds **nine** fail-closed
synthetic local Git regression tests (wrong core/GUI SHA, dirty input,
receipt overwrite, unsafe receipt location, overlapping checkouts,
wrong GUI layout, successful isolated receipt and no core mutation).
Workflow `d1daadf` ran them before composite staging on native Ubuntu
and Windows. Initial [run 41572552095](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572552095)
had Ubuntu success but **Windows FAILED** while checking the GUI
immediately after second-history-row display. The UI history update
can precede completion of asynchronous controller state/action enabling;
the original probe did not wait for the visible postcheckin actions.
This is an actual intermittent test failure and is preserved as negative
evidence, not hidden.

Harness commit `c18f4ac3d49e4d54d7c77e834ffcdc3a5ee0e6f6`
now waits boundedly for `history==2`, `not ctl.busy`,
`checkout action enabled`, `checkin disabled`; if missing, it
emits the entire `MainWindow.observable_state()`, retaining all
existing strict history, selection, authority/hash assertions.
Workflow `955a5423bbdab3b4dd8e0b4a880077886753acab`
triggers independent composite CI on future probe changes.

**Definitive [run 41572811571](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572811571)
on composite test workflow SHA `955a5423`: Windows and Ubuntu SUCCESS,
nine staging safety tests each, exact core source
`fe8a179f0203ef08151dc8459c84609dc639c4fe`,
exact GUI source `17651af68128572c7c83429513044a5948dd07bd`,
10 GUI files overlaid into intentionally dirty disposable source,
Qt create/import/checkout/checkin with coherent visible state,
two SHA-256-verified journal revisions, separate GUI-process reopen,
F5 preservation, transient missing working-file alert and restore,
independent frozen CLI exports. Ubuntu nonroot Python/pip-free network-none
container PASSED full workflow and ordinary GUI launch. Windows paths
and localhost UNC loopback PASS. Green run is a deterministic regression,
not proof of absence of future timing races.

**Other latest isolated CI also green:** normal older-source baseline
[41572788046](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572788046),
standalone GUI alpha
[41572788094](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572788094).
Reference [corrected-core composite acceptance
matrix](https://github.com/nrobertson152/RevisionWorx/blob/integration/openschematix/docs/CORRECTED_CORE_GUI_COMPATIBILITY.md)
updated at `05f4ca0`.

**HOLD:** no modified production/core/GUI PR branch, no source
authority pin change, no Phase 1 backport, merge, tag, signed installer,
release or multihost SMB claim. Test-only wrapper MUST NOT SHIP.
Actual interactive human GUI and untested physical offline failure
conditions remain outstanding.


## 2026-11-02 23:14 ET onward — Director P0 merge-readiness sprint, exact new core + GUI

**Assignment/authorization:** [PR #2 Director comment](https://github.com/nrobertson152/RevisionWorx/pull/2#issuecomment-6595288686) instructs test-only source candidate and Windows/Linux smoke with exact SHA and a conflict/dependency-aware merge sequence. No permission to merge, rebase, retarget, release, tag or edit other agent branches.

**Pinned source pair at sprint kickoff:** Phase 2 core
`22a5273d4740b3a7af8e8cb5cc3565e4336b763b` from PR #7
(native [Core CI 41574652418](https://github.com/nrobertson152/RevisionWorx/actions/runs/41574652418)
SUCCESS), GUI PR #14 head
`bd52cdf0c89fcd1dbc03d42951ba8e4a7de5da4f`
(code through `d4e3623` and a docs-only final GUI commit; [native GUI CI 41575959230](https://github.com/nrobertson152/RevisionWorx/actions/runs/41575959230)
SUCCESS). New Phase 2 `a03f1f0` appeared during this sprint, adding process-crash tests/workflow/docs but not production runtime files; it is **NOT** covered by the frozen pair's exact-SHA tests.

**Implemented on Integration branch only:**
- `99e83be0d3661fef8872f723fc0b81375e5d5fed`:
  `tools/packaging/stage_merge_readiness_tests.py`, exact SHA-checked hash receipt for overlaying six current GUI tests into a disposable core test tree.
- `b3f2ed8d28d4e96fd66083e62cfbfa05d13ea723`:
  separate `.github/workflows/revisionworx-merge-readiness.yml` pinned to the exact two SHAs, native full suite and frozen GUI/CLI path matrix. **[CI 41577258567](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577258567) BOTH Windows and Ubuntu SUCCESS; native 382 PASS / 2 SKIP on each**; test-only GUI/CLI and CLI export SHA-256 passed, Ubuntu Python/pip-free `--network none` full runtime passed. This source-file overlay is explicitly NOT a real Git merge.
- `393a24660aaaab2fce3731a3d27fc57eb4594ea2`:
  `tools/packaging/stage_exact_git_merge.py` for a **genuine temporary two-parent Git merge** in disposable checkouts with input SHA/clean-state checks, no auto-conflict resolution, fixed commit metadata, source-parent receipt, and unchanged Git blob IDs for core `atomicio.py`, `workspace.py`, `authority.py`, `documents.py`. It never pushes a ref or touches an authoritative project.
- `d70db6553d373b43aeb8d9562a0909e401c48b69`:
  separate `.github/workflows/revisionworx-literal-merge.yml` with exact source pins, a non-mutating `git merge-tree` audit of PR #6 vs main, and native full current GUI + core tests followed by frozen Qt/CLI smoke.

**Definitive literal Git merger evidence:**
[CI 41577732526](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577732526)
**COMPLETED SUCCESS BOTH Ubuntu and Windows Python 3.11**.
Git produced local-only, never-pushed synthetic merge SHA
`664e6887cf59b090a09e954554128a57be1ca22d`,
parents exactly `22a5273...` + `bd52cdf...`,
common ancestor `366d4355abcde7543122551fadaab01ac9832e01`.
Core-only four authority source blob IDs matched the corrected Phase 2 source.
Per OS: nine staging safety unittest PASS; **native combined suite 382 PASS /
2 SKIP / 0 FAIL**; test-only frozen GUI/CLI created projects,
imported outside file, checkout, edited and checked in, verified two immutable
revision SHA-256 values via independent frozen CLI exports, GUI F5
selection/observable contract, fresh-process restart, temporarily missing-file
warning/recovery and untouched source hashes. Ubuntu also passed relocated
nonroot/read-only-root, Python/pip-absent, `--network none`
frozen widget workflow and normal GUI launch. Windows path smoke passed
401-character local path, extended-length local path, localhost UNC loopback
read, byte-range lock refusal and original SHA-256 preservation.

**Concrete main merge blocker:** `main` at
`3b2d75ad36790c9308f73d3b5e22c002a9d117fb`,
Phase 1 `412e69ab90bb1d8b74c1a3cf5519f581e4420e63`.
Native CI `git merge-tree --write-tree origin/main origin/phase1-foundation`
returned 1 and printed **modify/delete conflict**:
`docs/DEVELOPMENT_PROGRESS.md` deleted by main, modified by Phase 1.
PR #6 GitHub also marked mergeable=false/dirty. Director must decide
keep/reconcile progress file; core architect recommends retaining it,
and updating `AGENTS.md` after application code reaches main.

**Exact merge sequence proposed, none executed:** after QA/Distributed
exact-SHA core approval, Director merges #7 into
`phase1-foundation` FIRST, resolves PR6 main docs conflict and
merges #6 (now containing corrected Phase 2) into `main`,
then Claude B merges accepted latest core into GUI branch with a reviewable
merge commit, repeat CI and Director merges #14 into `main`,
then QA #3 test/workflow integration, optional Distributed research #4
per Director decision, finally Integration #2 with fixture hash/provenance
reconciliation. No standalone unsafe Phase 1 must reach main.
Full acceptance checklist and rollout gates:
[docs/MERGE_READINESS_SPRINT.md](https://github.com/nrobertson152/RevisionWorx/blob/integration/openschematix/docs/MERGE_READINESS_SPRINT.md),
source plan committed `d6906c2`, verified Windows update `e328007`.

**Not certified:** a real merged PR, later source changes,
Qt human interaction on actual displays, native signed installer,
clean Python-free disconnected Windows VM, hardware power-loss, real ACL/ENOSPC,
socket-attempt audit, malicious external lock mutation or actual two-host SMB/NFS.
Test-only `--alpha-acceptance` wrapper MUST NOT SHIP.
PR #2 remains draft, no production packaging source repin or other branch edits.


## 2026-11-02 P0 continuation — latest 96f9c36 + 0773bde integrated tree green, independent safety REJECT

**Latest tested exact core `96f9c368a11fd43c0440d404c7258c7dfeacabf6`;
latest tested GUI `0773bde599f39aee88a8a536c28673bc4a1e13f5`.**
New separate test-only workflow
`.github/workflows/revisionworx-live-head-merge-readiness.yml`
at `7ac4f9a6b288055598b25ef9655070a2836c5193` constructed
an actual local **never-pushed two-parent Git merge**
`d2e4842b48566741daff4ab7202a403a37478995`.
Full immutable Git core `atomicio.py`, `workspace.py`,
`authority.py`, `documents.py` blobs preserved. **[CI
41579981999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41579981999)
SUCCESS both Ubuntu + Windows Python 3.11**. Native full merged suite:
Ubuntu **501 PASS / 2 SKIP**, Windows **500 PASS / 3 SKIP**.
Frozen GUI + CLI actual offscreen Qt widget create/import/checkout/edit/checkin,
two historic revision SHA-256 independent CLI exports, F5/restart,
missing working-file and restore, Unicode relocated install and
original workspace data preservation passed. Ubuntu passed Python/pip-free,
nonroot/read-only-root `--network none` Docker including normal GUI
launch. Windows passed 401-char and extended paths, localhost UNC and
byte-range locked read refusal. **Not** an interactive or shippable build.

**Additional actual safeguarding implementation:** nine
`tools/packaging/test_stage_exact_git_merge.py` local real-Git fixture
tests now run alongside nine prior overlay staging controls in
`.github/workflows/revisionworx-merge-stager-tests.yml`.
After documenting failed initial Win CRLF clone fixture and accidental
Python indentation regression, test source `013c3a5`
[CI 41580746150](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580746150)
is **4/4 SUCCESS** on native Ubuntu/Windows × Python 3.11/3.13.
Each platform runs **9+9 PASS / 0 FAIL**, including actual conflicting
Git merges and refusal to change `revisionworx` authority files.
The red historical runs [41580498901](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580498901)
and [41580643954](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580643954)
are preserved as honest test-harness failures, not production defect
evidence.

**P1 HOLD remains despite strong new functionality CI:** Independent QA
[four-platform malformed-journal negative gates 41580089612](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580089612),
[41580332225](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580332225)
show root malformed JSON raw `AttributeError`, nested corrupt
records falsely `health.ok=True` or raw `TypeError`; strict desired
safety assertions still FAIL on exact core 96f9. Independently
Distributed [four-platform CAD atomic replacement 41580498940](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580498940)
proves `ContentStore.store_file` can return old bytes after same-size
same-mtime save-by-rename, a P1 source-to-revision provenance failure;
strict desired `SourceChangedError` XFAIL remains open. Both issues
are reported to Claude A. **No production merge** or release until
actual positive fixes re-accepted on exact new SHA by QA/Distributed
and rechecked in Integration's full two-OS real-Git candidate.
`main` vs PR #6 still has modify/delete conflict on
`docs/DEVELOPMENT_PROGRESS.md`; safe Director merge order remains
#7 -> #6 -> #14 -> #3 -> discretionary #4 -> #2.
Latest detailed exact case matrix:
[docs/MERGE_READINESS_SPRINT.md](https://github.com/nrobertson152/RevisionWorx/blob/integration/openschematix/docs/MERGE_READINESS_SPRINT.md)
updated `04a339c`.

PR #2 stays draft. No core, GUI, QA, Distributed or upstream
OpenSchematix branch edited; no retarget, rebase, force push, release
or change to separate baseline production packaging source pin.


## 2026-11-03 05:16Z+ — Issue #32 checked-out four-source assembly and package qualification

**Checkout:** [Issue #32 comment 6596072245](https://github.com/nrobertson152/RevisionWorx/issues/32#issuecomment-6596072245), **Agent:** ChatGPT Integration A (interactive). Simple cooperative CHECKED OUT/CHECKED IN comment policy, as explicitly clarified by Director on PR #23 superseding atomic Git ref locks. Work only on own `integration/openschematix` / draft PR #2.

**Exact immutable input tuple (historical; newer heads moved):**
core `f01b37d345c5208ab0fa520588a2abe8c66c894a`,
GUI `0773bde599f39aee88a8a536c28673bc4a1e13f5`,
BOM `59c0f7b56c7e135c6d4179e18bea51b3232d8c9b`,
change control `cb684336fbef2edf21bb4bda700146af70d58ee4`.

**Work delivered to Integration branch, no other branch edits:**
- `e4438acbea99b72b1a8304186416f493ec6607db`:
  `tools/packaging/stage_product_stack.py`, actual Git local-only
  core→GUI→BOM→ECO, verified SHA/clean checkouts, six core-only source
  object identities, fail-closed conflict and preserved provenance receipt.
- `7b9dc894d834ad432992a3b7d1fd0d3edbe7fe9f`:
  Windows/Linux Python 3.11 pinned four-source full native matrix.
- `6e7afa20f3818d3b7bf3577a696abd89aec06deb`:
  11 local divergent-Git test oracles, actual conflicts/authority
  unauthorized edits/receipt and input refusal, CI-tested on Ubuntu.
- `e921cc32f8127b1bcd1bca9598136d77c953e3ef`:
  actual native CAD document ↔ BOM catalog/assembly ↔ ECR
  WhereUsed impact, explicit coverage and untouched file hash/mtime,
  restart and read-only acceptance fixture.
- `4d78235ea9ce2b350368f04e94823cfa2c38d577`:
  test-only wheel module/entrypoints/metadata/mandatory-dependency/hash audit.
- `7d95a1496ae5879cf12f60622ce9aadc4cc7992b`:
  dedicated Windows/Linux domain + wheel matrix; corrected Windows
  `bdist_wheel` build-tool absence by pinning CI-host
  `wheel==0.45.1` at `650a597f2711bacdd0de968334d508fae9126cd0`.
- Detailed [docs/FOUR_SOURCE_COMPATIBILITY.md](https://github.com/nrobertson152/RevisionWorx/blob/integration/openschematix/docs/FOUR_SOURCE_COMPATIBILITY.md), version
  `87c047eb0288a9221590def8dd5bcedae42b2c9d`.

**Verifiable four-source source merge and native CI:** [run
41583331179](https://github.com/nrobertson152/RevisionWorx/actions/runs/41583331179)
uses Python 3.11 on each runner. Ubuntu **SUCCESS** with 11 synthetic
staging oracles PASS, core+GUI+BOM+ECR true Git merge SUCCESS,
focused module/Qt tests PASS, full
`python -m pytest -q -ra`: **1,141 PASS / 2 SKIP / 0 FAIL**,
CLI help for all three modules PASS. Synthetic Git commit
`6c9950baa65fa802c614d25289a7748ec7eec066`, three
real two-parent merges, core-only blobs unchanged, 114 changed paths.
**Windows job is now confirmed SUCCESS** on the same synthetic Git commit and inputs: 11 stage tests PASS; 24 focused BOM/change-control/GUI tests PASS; full `python -m pytest -q -ra` **1,139 PASS / 4 SKIP / 0 FAIL** (Windows Python 3.11; exact [CI 41583331179](https://github.com/nrobertson152/RevisionWorx/actions/runs/41583331179)).
First workflow [41583177025](https://github.com/nrobertson152/RevisionWorx/actions/runs/41583177025)
failed because staging test file had not yet been committed; fixed by
the next source/test commit before the above run.

**Both-platform true domain+wheel targeted acceptance:** initial
[41584058598](https://github.com/nrobertson152/RevisionWorx/actions/runs/41584058598)
Ubuntu PASS / Windows FAIL due only to `bdist_wheel` absent with
`--no-build-isolation`. Fixed using CI-only pinned wheel builder tool;
[run 41584278631](https://github.com/nrobertson152/RevisionWorx/actions/runs/41584278631)
**BOTH Ubuntu and Windows SUCCESS**: each single
cross-domain CAD↔BOM↔ECR native path/restarter test **1 PASS / 0 FAIL**,
and test-only wheel verified **65 paths**, BOM 20, changes 13, GUI 10,
GPL-3.0-or-later, both user entrypoints, zero unconditional
third-party dependencies, no native binaries.
Wheel digests differ despite same source tuple:
Ubuntu `9130cf80d853c00bed7dcd2aa408e688e2eb1f22a7ce97aafd2b151b5fc97009`,
Windows `a0095f3f59628d655332dbd828bc82d90e090a811dde1c0c0507eb4a62d3a4c0`.
No claim of byte-for-byte reproducible wheel builds, signed installers,
AppImages, offline production install or actual release.

**New upstream movement:** BOM PR #21 moved from tested `59c0f7b`
via XLSX/purchasing to `8f67bfedcca2ea619035556fdeed52bf0c4ca30a`
with **two P1 catalog corrections** and regression tests.
ECR/ECO PR #20 moved from tested `cb68433` to
`724993c74bd7aa6f2c00ed38f8b44c39a42bdd08` with meaningful
approved-dependency and stale ECR lifecycle fixes. Both
actual production sources changed. Therefore these new SHAs
**ARE NOT** covered by old 1141/2 and domain/wheel acceptance.
Core `96f9c36` unchanged in tested `f01b37d` head still has
independently verified P1 malformed-journal/false-health and
same-metadata CAD capture defects, so release / PR merge is HOLD.

**Next ready package:** once core+feature owners declare supported API
and corrected frozen source, repeat the 4-source exact Git matrix at
new SHAs; independently require QA/DS *positive* test oracles for core
health and CAD provenance; then extend actual frozen GUI/CLI installer
qualification. No merge/rebase/retarget/release or edits to others'
branches.
