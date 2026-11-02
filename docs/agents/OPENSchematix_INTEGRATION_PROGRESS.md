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
