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
