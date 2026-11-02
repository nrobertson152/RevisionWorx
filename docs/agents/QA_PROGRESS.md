# QA Engineer — Independent QA Progress Log

**Assignment:** RevisionWorx quality assurance, integrity regression tests, verification planning.
**Working branch:** `qa/testing-infrastructure`
**Pull request:** https://github.com/nrobertson152/RevisionWorx/pull/3 (draft; base `phase1-foundation`)
**Coordination:** https://github.com/nrobertson152/RevisionWorx/issues/1
**Updated:** 2026-11-01
**Role identification:** QA Engineer; Lead Architect owns implementation and architectural decisions.

## Session resumption protocol
Before making changes, read this log, `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_PROGRESS.md`, current branch contents, open issues, PR comments and CI activity. Recheck branch HEAD before pushing. Never claim unexecuted tests passed.

## Completed work and files
- Established independent QA branch from `phase1-foundation`; draft PR #3 opened.
- Added `docs/QA_STRATEGY.md`, `docs/TEST_MATRIX.md`, and `docs/CONCURRENCY_FAILURE_PLAN.md`.
- Added `tests/qa/__init__.py`, `tests/qa/fixtures.py`, `tests/qa/test_fixture_contract.py`, and `tests/qa/test_integrity.py`.
- Added `benchmarks/README.md` and `benchmarks/generate_metadata.py`.
- Created issue #1 for transaction boundaries, stale-writer fencing, crash recovery, provenance, and shared-filesystem certification.
- This session: read current repository instructions and architecture/progress documents, inspected branch tree and open PRs; added this persistent agent log. No production implementation files modified.

## Commits
- Prior QA branch HEAD before this log: `d2a554e557c3bd7a17b5792c5b3a7d30761e6a61`. Consult GitHub history for individual earlier commits.
- Log commit: see GitHub history for this file; do not infer its SHA from this document.

## Test execution and evidence
- Previously reported in draft PR #3: standalone synthetic fixture test **1 passed** using Python 3.13 / pytest 9.0.2; standalone metadata-generator smoke generated 100 projects, 200 documents and 50 edges with cardinality checked.
- This session: **no tests executed**. These previous results were read from PR #3, not independently rerun in this session.
- Full Phase 1 integration suite, live Tk GUI, Windows/NTFS, real SMB/NFS, distributed concurrency, and application-level performance benchmarks: **not run**. Do not conflate metadata generation with application performance.
- Phase 1 development progress reports 95 passed / 1 skipped on its development environment; this is upstream-reported, not QA Engineer independent verification.

## Architectural decisions and findings
- Authoritative `.revisionworx` journal is the source of truth; local SQLite is a disposable index.
- Preserve unmanaged archive paths, contents and timestamps. Project adoption is opt-in.
- Writer lock break currently does not prove stale-writer fencing. Administrative lock recovery must not be treated as multiwriter-safe until a fenced protocol and real-share tests exist.
- Revisions, checkout ownership, parts/BOM/dependency graph and backup/restore are not implemented in the inspected Phase 1 baseline; corresponding tests are future acceptance gates, not passing coverage.
- No releases or merges authorized by this QA work.

## Unresolved issues and risks
1. Independently run complete QA and baseline suites against an exact commit; capture interpreter, platform, command, failures and skips.
2. Validate injected fault tests at each durable write boundary and ensure cleanup does not mask journal corruption.
3. Define and verify stale-writer fencing and multi-client lock semantics on supported SMB/NFS/Windows/Linux configurations (issue #1).
4. Add future regression suites for checkout/revision immutability, graph provenance, impact analysis, BOMs, and backup/restore when implementation lands.
5. Establish reproducible latency/memory benchmarks against actual application operations, not only fixture generation.
6. Confirm CI policy and add approved cross-platform jobs; GUI and network share tests require appropriate runners.

## Exact next steps
1. Read this log and latest PR #3 activity; compare QA branch with `phase1-foundation`.
2. Clone or materialize exact QA commit into an executable environment and run `python -m pytest -q`; run `python -m pytest -q tests/qa` separately. Record complete results.
3. Review QA test assertions for false positives and reproduce at least one fault-injection regression.
4. Update test matrix and PR #3 with independently verified coverage and blockers; coordinate protocol findings through issue #1.
5. Before each session ends, append a dated entry with changed files, commit hashes, tests/results, blockers and next steps; commit and verify the pushed branch HEAD.

## 2026-11-01 — Role identification and log migration
- Renamed the QA progress record to `docs/agents/QA_PROGRESS.md`; preserved all previous entries and commit references.
- Files changed: QA progress log renamed only; no application source or tests modified.
- Tests executed this session: none (documentation-only change).
- Unresolved issues: existing release blockers and independent execution requirements remain unchanged.
- Next steps: independently run the QA and baseline test suites, document exact results, and update draft PR #3; communicate architectural implications to the Lead Architect through issue #1.

## 2026-11-01 — Standing orders and fixture regression hardening
- Read standing orders from `main`: `docs/agents/README.md` and `docs/agents/QA_ORDERS.md`; inspected QA branch, PR #3 and its comments, current Phase 1 source, QA fixtures and requirements.
- Changed `tests/qa/test_fixture_contract.py`: added same-size/same-mtime content mutation and file rename/deletion detection checks. Test commit: `2d0d6729e14df19af9556210ad673759cc1ca34b`.
- **Execution attempt:** `git clone --depth 1 --branch qa/testing-infrastructure https://github.com/nrobertson152/RevisionWorx.git /tmp/revisionworx-qa && python -m pytest -q` failed before checkout because this execution environment could not resolve `github.com`. **No tests executed; no passing result claimed for the new checks.**
- Existing verified results remain limited to the standalone fixture and generator smoke previously reported in PR #3. The full Phase 1 suite and new regression checks are still unverified in this session.
- Risk: QA fixtures currently use `os.walk(..., followlinks=False)`; directory symlink metadata may be omitted, so non-destructive archive checks need a dedicated symlink regression and potential fixture correction. This is a QA oracle risk, not a production-code finding.
- Next: obtain an executable checkout; run the complete baseline and QA suites; inspect fixture symlink behavior; capture exact environment/results; update PR #3 and coordinate any architectural defects through issue #1.
- This session modifies QA tests and this progress log only. No core source, release, merge, or branch history rewrite.

## 2026-11-01 — Director check-in: symlink oracle fix and independent isolated verification
- Read current `main` standing orders, QA log, Phase 1 instructions and architecture, current QA fixture/tests, PR #3, and director comment. No change to assigned role or branch.
- Files changed: `tests/qa/fixtures.py` now fingerprints directory symlinks by link target without following them; `tests/qa/test_fixture_contract.py` adds symlink retargeting/deletion tests. Commits: `54428af4a690a1cc77c9af4139995c6a7a25bc27`, `0d0593911cb226ced571a656e181334cb5e6fed6`.
- **Executed:** local isolated reconstruction of the five QA fixture contract tests under Python/pytest, `python -m pytest -q` from `/tmp/qa_oracle`: **5 passed in 0.10s**. This reconstruction used manually transcribed code corresponding to the GitHub fixture and test files; it is **not** an exact Git checkout or an application integration run, and does not verify the repository's full test suite.
- Attempted `git ls-remote https://github.com/nrobertson152/RevisionWorx.git HEAD`; failed `Could not resolve host: github.com`. Full repository tests remain **not run**. Windows GUI, SMB/NFS and multi-host safety remain unverified.
- QA oracle risk mitigated for directory symlinks. Other archive metadata (permissions, hardlinks, ownership) is not part of the current fingerprint contract; evaluate scope with Lead Architect before extending.
- Next: obtain executable checkout of exact QA HEAD and Phase 1 HEAD; execute full suite and separate-process races; add stale-writer-after-admin-break test as an expected-failure or demonstrator without making a release gate spuriously green; report results to PR #3 and issue #1. Do not merge.

## 2026-11-01 — Latest standing orders, Phase 1/2 review and QA implementation
**Assignment:** QA Engineer only. Remained on `qa/testing-infrastructure`; draft PR #3 remains based on `phase1-foundation`. No core application files or other specialist's branch edited.

**Sources inspected:** latest `main` `docs/agents/README.md` and `docs/agents/QA_ORDERS.md`; `CLAUDE.md`, requirements, `docs/ARCHITECTURE.md` ADR-008…013, `docs/DEVELOPMENT_PROGRESS.md`, the original QA branch and progress log; draft PRs #3, #6 and #7, including PR #3's Lead Architect review; issues #1 and #5 (pending multi-user authority decision); Phase 2 `revisionworx/documents.py`, `workspace.py`, `tests/test_documents.py`, `tests/test_reliability_review.py`, `tests/conftest.py`.

**Completed changes / commits:**
- `13b1436`, `b7c024d`: updated QA commit-point recovery regression for `CommittedNotApplied`, txid and `_rev`; added deterministic administrative-lock-break race oracle.
- `4c98a63`: created `tests/qa/test_phase2_integrity.py` with five Phase 2 integrity tests: CAD path/bytes preservation, historic revision retention, same-size/same-mtime external edits detected by explicit hashing, prepublication failure leaving checkout and journal unchanged, and stale checkout token after audited admin release.
- `872d40a`: expanded `docs/TEST_MATRIX.md` for Phase 2 and safety gaps.
- `2831d72`: restricted the TOCTOU expected-failure marker to assertion failures and explicitly checks whether a stale writer is accepted and whether it publishes.
- `030cbe2`: updated `docs/QA_STRATEGY.md` for Phase 1 review, PySide6, Phase 2, and distinction between reported and independently verified results.
- `ae197ba`: corrected a compatibility error found by inspecting the QA branch after committing modern-contract changes. The QA branch's original Phase 1 lacks `CommittedNotApplied`, `LockLostError`, `strip_rev`, `transaction_outcome`, and `_check_lock_still_held`; the durable recovery test now branches on availability, and the TOCTOU regression explicitly SKIPS on that older code instead of breaking test collection.
- `304500f`: added `.github/workflows/qa-integrity.yml` proposing Linux/Windows, Python 3.11/3.13, separate QA and full-suite GitHub Actions jobs on QA branch / PR. No workflows have yet executed.
- Updated draft PR #3 description and posted QA findings to PR #3 and coordination issue #1. No merge/release performed.

**Execution and evidence:** This session's local `git ls-remote https://github.com/nrobertson152/RevisionWorx.git HEAD` failed due `Could not resolve host: github.com`; the actual repository could not be cloned into the local Python environment. `python -m pytest --version` reported pytest 9.0.2, but neither the full suite nor these new QA regressions was executed. The Actions runs API for the current branch returned **0 runs** when checked. Thus **no newly added tests are claimed passing**. Previous isolated QA reconstruction (5 passed) and original fixture smoke are only historical evidence; Claude's reported 118 passed/1 skipped on PR #6, 151 passed/1 skipped on PR #7, and Claude's earlier QA runs (16/16 old base; 15/16 Phase 2, before API adjustment) are not independent QA execution.

**Architectural conclusions / blockers:** The lock token recheck and journal publish are not one atomic operation: ADR-008 documents a remaining stale-writer TOCTOU and the local probe is strictly xfailed only where the new lock-check seam exists. Director-required audited administrative override must remain. Issue #5 is explicitly undecided; QA made no coordinator/protocol implementation. PR #7's checkout is safe only under the tested local single-writer assumptions; network shares and Windows/Qt remain unverified. Phase 2 tests skip on this QA branch until a Phase 2-including integration checkout exists; do not count skips as coverage. No GitHub Actions results yet. Existing project tests may require updating when the new code is integrated.

**Exact next steps:** (1) Inspect Actions runs for the new QA workflow; if jobs start, read job-level logs and address true failures without assuming a green status. (2) Obtain an exact executable QA+PR #6/PR #7 integration checkout with Lead Architect coordination, then run separately `python -m pytest -q -ra tests/qa` and `python -m pytest -q -ra`, recording SHA, Python, OS, passes/failures/skips/xfail. (3) Verify deterministic TOCTOU oracle fails for the correct reason on modern code; retain as release gate until architecture approval and real fencing. (4) Add independently executed immutable-release tamper and lifecycle transition tests, CLI/drawing register, PySide6 offscreen smoke, Windows/NTFS validation and real multi-host SMB/NFS rig. (5) Keep PR #3 draft; coordinate decisions through issues #1/#5; never merge or release without approval.

## 2026-11-01 — Live CI findings, Qt provisioning fix, and stacked Phase 2 validation
- Added GitHub-hosted exact-revision QA execution on Ubuntu/Windows Python 3.11/3.13. **Verified** completed push workflow [run 41371072088](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371072088), QA HEAD `304500f709d62c35701a234fbd52e4514625f86b`: all 4 jobs **success**. QA-only command `python -m pytest -q -ra tests/qa`: **16 passed, 2 skipped** on all four combinations. Full command `python -m pytest -q -ra`: **111 passed, 3 skipped** on all four combinations. Skips are deliberate missing Phase 2 module and absent ADR-008 seam; third full-suite skip is old GUI without display on Linux or case-insensitive-filesystem test on Windows. These results are for the older Phase 1 source present directly on the QA branch.
- For the GitHub-hosted `pull_request` **merged code** combining QA with modern PR #6 `phase1-foundation`, run [41371072451](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371072451), observed Linux Python 3.11 and 3.13 QA-only: **16 passed, 1 skipped, 1 xfailed**. The strict expected failure is the still-open lock-break TOCTOU (not a passing requirement). The full suites were blocked in collection by **`ImportError: libEGL.so.1`** while importing PySide6. Observed Windows Python 3.13 job: QA-only **16 passed, 1 skipped, 1 xfailed**, full **136 passed, 2 skipped, 1 xfailed**. This Windows CI evidence includes Qt widget execution under the test runner, not a native installed desktop certification.
- Corrected GitHub runner configuration in commit `d3a4378ff6945c4ddc81e512ec758ab59257dba2`: install Linux Qt system runtime dependencies and set `QT_QPA_PLATFORM=offscreen`. Corrected runs were queued/running at the time of log update; **do not claim they passed**.
- Added an isolated, disposable Phase 2 integration test job in commit `c079b3c10cc0612fdd4cb608400274bdd8a99def`. It checks out `phase2-documents` and the QA branch separately, copies only `tests/qa` into the runner's Phase 2 checkout, logs both exact source SHAs, then runs QA and full Phase 2 tests. It does **not** merge branches or mutate remote commits. The Phase 2 run was queued at log update, **not verified**.
- Extended `tests/qa/test_phase2_integrity.py` with three more tests in commit `883f92441ad5dfdbd8300c8a241980a9d18654ff`: lifecycle supersession preserving history and audit, corrupt stored content rejecting release without commit, and drawing register/CSV preserving drawing path, revision and title. **Eight newly added Phase 2 tests, not yet run**.
- Updated the existing draft PR #3 description with specific CI run links, test counts, runner limitations and dependencies. No changes to production `revisionworx/`, no architectural approvals, no merge or release.
- **Risks remaining:** fail-closed stale-writer fencing not solved; live SMB/NFS never certified; latest offscreen Qt Linux validation pending CI; Phase 2 independent suite pending disposable integration job; Windows binary GUI/manual testing still outstanding; backups/dependency graph have no implemented tests.
- **Immediate next steps:** inspect newest GitHub Actions runs and per-job logs; fix actual test defects or CI environment issues on QA branch; verify all eight Phase 2 regressions under exact two-revision checkout; report any Phase 2 failures to Claude through issue #1 or PR #7; update matrix/strategy and progress log with concrete results. Keep PR #3 draft.
