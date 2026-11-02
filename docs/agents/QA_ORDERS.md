# Standing Orders — QA Engineer

**LATEST ORDERS:** [RevisionWorx Parallel Assignments](PARALLEL_ASSIGNMENTS.md) — independently retest Claude's new PR #7 fix SHA `325b7a8` for #8/#9/#10. Earlier strict xfail status is a historical baseline, not proof of the new code. RevisionWorx repository only.

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

## Active Director priorities — 2026-11-01, latest verified checkpoint

**Branch and scope:** `qa/testing-infrastructure`, draft PR #3, based on `phase1-foundation`; Claude's draft PR #7 remains stacked on PR #6. Inspected QA head `2a663091e416`. Both workflows at that exact QA head completed successfully: [run 41383307946](https://github.com/nrobertson152/RevisionWorx/actions/runs/41383307946) ran the eight-job Linux/Windows × Python 3.11/3.13 matrix including disposable Phase 2 integration; [run 41383313202](https://github.com/nrobertson152/RevisionWorx/actions/runs/41383313202) passed four standalone jobs, with the Phase 2 overlay intentionally skipped on the PR event. Do not confuse a workflow's green conclusion with a safety acceptance passing.

**Verified detailed baseline:** [run 41382811232](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382811232) executed the Phase 2 overlay on both OSes and Python versions; the agent documented Linux 191 passed/4 strict xfailed (full suite) and Windows 190 passed/1 skipped/4 strict xfailed. [Run 41382943849](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382943849) confirmed a **fifth** Windows-only strict xfail for the transient lock-file sharing violation. These are distinct test revisions; inspect exact job logs for any newly quoted counts.

### Execute next, in order

1. **Make Issue #10 actionable for Claude:** independently repeat the *actual* six-process Windows checkout race, recording trial counts, worker outcomes, tracebacks and machine/Python details. Preserve the deterministic Windows `Workspace.close` transient-`WinError 32` strict acceptance xfail. Test rightful-lock-owner preservation, bounded retry/cleanup and subsequent reacquisition **after Claude's fix**; never implement unsafe retries or suppress permission errors in the QA branch.
2. **Keep five separate acceptance oracles:** #8 post-audit physical publication and pre-audit *effective* stale mutation; #9 concurrent insert and delete vs index watermark; #10 Windows sharing-violation release. A green run with strict xfails is *not* a product pass. ADR-015's positive post-audit logical-fencing test must not obscure its surviving pre-audit defect.
3. **Strengthen Phase 2 negative-path and native GUI tests without duplicating prior successes:** maintain the disposable Linux/Windows Python 3.11/3.13 overlay; verify real subprocess CLI tests, immutable CAD restoration append-only history, document lifecycle, checkout conflicts, and actual PySide6 offscreen widgets. Create new focused cases only for uncovered assertions. Packaged Windows/Linux GUI/manual UI qualification and physical SMB two-host testing remain unverified.
4. **Prepare a concise Claude acceptance handoff:** revise `docs/PR_MERGE_READINESS.md` and `docs/TEST_MATRIX.md` to give, for each PR #3/#6/#7 and each defect #8/#9/#10, the exact acceptance invariant, reproducer/test path, current expected outcome, fix owner, last verified SHA/run, and retest procedure. Distinguish *QA infrastructure reviewability* from *single-user release readiness* and *multi-user qualification*. The latter needs the approved coordinator, atomic authority, recovery, and physical SMB certification.
5. **Execute and close a coherent batch:** run/check current-head CI (do not endlessly trigger superseding documentation commits), commit/push concrete tests or evidence, update `docs/agents/QA_PROGRESS.md` and PR #3, and give Claude only material new findings in the existing issues. If no new code is justified, supply a final verified handoff rather than inventing more tests.

**Authority:** independent QA tests, workflows, fixtures and documentation. No production `Workspace`, `LocalIndex`, coordinator or Claude-owned changes; no merges, retargeting, tagging or releases without Director approval. Do not claim installed-GUI or physical SMB certification from CI.
