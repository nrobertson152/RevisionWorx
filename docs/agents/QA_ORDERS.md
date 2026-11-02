# Standing Orders — QA Engineer

**Assignment:** Independent quality assurance, test architecture, regression verification and data integrity.
**Branch:** `qa/testing-infrastructure`
**Progress log:** `docs/agents/QA_PROGRESS.md`
**Authority:** Independent verification; Claude remains Lead Architect.

## Priority tasks
1. Independently inspect the actual `phase1-foundation` implementation and existing tests, reproduce available test runs, and review test assertions for false positives or vacuous passes.
2. Establish reusable deterministic unit/integration fixtures for varied engineering folder conventions, unmanaged archives, revisions, BOMs, cross-project dependencies and missing references.
3. Verify non-destructive registration/adoption, no unrequested archive traversal, journal replay, corrupted cache rebuilding, hash integrity, disk-full/permission failure, and lock races with **separate processes**.
4. Add failure-injection and concurrency tests; identify tests requiring real SMB/NFS and cross-host verification. Distinguish these from local-only tests.
5. Review upcoming Phase 2 immutable revision, checkout/check-in, lifecycle, audit, rollback and CAD path-preservation functionality as it appears.
6. Establish GUI test and smoke-test requirements for PySide6 on Linux and Windows; skipped GUI tests are not evidence of GUI correctness.
7. Prepare scalable reproducible search and dependency benchmarks; report measurements only when actually executed.

## Deliverables
Maintain `docs/QA_STRATEGY.md`, `docs/TEST_MATRIX.md`, reusable automated tests and fixtures, verified test reports, and `docs/agents/QA_PROGRESS.md`. Update your existing QA pull request. Raise critical data-loss defects promptly in GitHub.

## Mandatory operating procedure
1. Read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, and relevant current development documentation. Inspect your existing branch, open PR, issues, and latest comments before acting.
2. Identify yourself by assignment name only, not Agent B/C/D. Claude is Lead Architect and owns core implementation and architecture.
3. Work exclusively on your existing role branch; preserve all previous commits and work. Do not force-push, rewrite history, merge to main, tag, or release without director approval.
4. Maintain the assignment-specific progress file under `docs/agents/`. If an old AGENT_B/C/D_PROGRESS.md exists on your branch, rename it while preserving its content/history; do not create a competing duplicate.
5. Commit and push actual deliverables, tests and documentation; keep the role PR updated. Do not claim tests were run unless actually executed. Distinguish local filesystem tests from verified SMB/NFS behavior.
6. Coordinate interface decisions and findings affecting core architecture with Claude via GitHub issue or PR comments. Avoid changing Claude's core code or another specialist's scope without agreement.
7. Before ending a session update the progress file with commits, files changed, verified test results, unresolved risks, and next steps. In the final report link to your branch/PR and report what was pushed and what remains unverified.
8. If the assignment is blocked, produce independently useful fixtures, research, or prototype work and record the blocker. Do not stop at a plan.

## Immediate response required
After reading these orders, report your role, branch, PR, current completed work, verified tests, outstanding risks, and your next concrete engineering task. Then continue implementation autonomously.

## Active Director priorities — reviewed 2026-11-01 (latest branch evidence)

**Evidence baseline, not a current-head certification:** PR #3 is draft on `qa/testing-infrastructure` stacked on `phase1-foundation`. Its latest inspected head was `1e874419ac05`. The last fully verified Phase 2/QA matrix documented in `docs/PR_MERGE_READINESS.md` was [run 41382170092](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382170092): Linux Python 3.13 full suite **190 passed / 3 strict xfailed**; Windows **189 passed / 1 skipped / 3 strict xfailed**. The three strict xfails are two distinct administrative lock-break defects (#8: post-audit physical journal publication and pre-audit logically effective mutation) and LocalIndex watermark inconsistency (#9). Newer QA changes add delete-during-rebuild and immutable-revision restoration acceptance tests; latest-head CI was still queued or running at review time. **Recheck run status and exact branch head before claiming results.**

### Execute next, in order
1. **Close current-head evidence:** inspect the newest GitHub Actions QA jobs, including the disposable `phase2-documents` overlay on Linux/Windows, and confirm that the two recent real-interpreter CLI negative-path tests, LocalIndex deletion-during-rebuild test, and restore-history test were actually collected/executed. Repair QA-owned CI/test problems, distinguish failed acceptance assertions from production defects, and cite exact SHA/run/job/test counts. Do not silently xfail a new failing test.
2. **Preserve separate safety oracles:** retain strict deterministic acceptance xfails for #8's post-audit physical publication and **pre-audit effective stale mutation**, plus #9's inconsistent index cursor. A passing ADR-015 *post-audit logical fencing* control does not fix the pre-audit window. Document each failure's scope, expected behavior and reason for xfail; do not certify physical-journal cleanliness or safe administrative override.
3. **Review native GUI verification:** execute and expand genuine PySide6 offscreen tests for Documents tab errors/conflicts, immutable revision history and checkout/check-in. Keep installed Windows/Linux application smoke tests separate and explicitly unverified until performed. Verify CAD path preservation without rewriting unmanaged assembly trees.
4. **Give Claude a bounded handoff:** update `docs/PR_MERGE_READINESS.md` for PRs #3/#6/#7, with current SHA, latest successful and outstanding runs, issue owners (#8/#9), precise release blockers and test-infrastructure-only review readiness. File narrow production defect reproductions via GitHub rather than editing Claude's authoritative code.
5. **Finish a reviewable batch:** commit/push substantive tests or fixes and update `docs/agents/QA_PROGRESS.md` and PR #3 once the corresponding CI evidence exists. Avoid repeated documentation-only commits while queued workflows are still superseding one another.

**Authority:** QA tests and documentation only; no independent modifications to production `Workspace`/index/coordinator or Claude branches, PR retargeting, merges, tags, or release. Local multi-process/offscreen Qt runs are not physical SMB/NFS certification.
