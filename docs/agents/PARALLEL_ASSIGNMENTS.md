# RevisionWorx — Parallel Agent Assignments (2026-11-01 ET)

**Project scope: RevisionWorx repository only.** No work, issues, pull requests, commits, CI triggers, or instruction updates in any other repository. In particular, the CAD integration agent may inspect its *existing RevisionWorx adapter* and pinned fixtures but must not alter the upstream CAD application. This instruction supersedes any suggestion to audit external symbol libraries, external standards artwork, or another project's release.

**Director:** user. **Lead Architect / production owner:** Claude. **Status:** three specialist assignments authorized in parallel; **no merge, release, tag, retarget, or production multi-user certification** authorized.

## Fresh production checkpoint — independently unverified

Claude's draft PR #7 `phase2-documents` is now at **`325b7a801de15c082616781fe086bbd95cb350b6`**, stacked on draft PR #6 `phase1-foundation` at `412e69ab90bb1d8b74c1a3cf5519f581e4420e63`.

Claude **claims implemented fixes** for:
- **#8:** ADR-015 rev. 2, journal-published token revocation before lock move as authoritative linearization point; earlier physical stale journal artifacts may still exist but must have no logical effect.
- **#9:** ADR-016, head-checked, bounded retry during full index snapshot, for insert/delete watermark consistency.
- **#10:** ADR-016, bounded `writer.lock` unlink retries with ownership checked on every attempt and persistent errors surfaced.

Claude reports **161 passed, 1 skipped** on local Linux, with **no Windows verification** and no native Qt verification in that environment. This is **implementation evidence, not independent acceptance**. QA's older green CI contains strict xfails against pre-fix code. Do not call #8/#9/#10 closed until QA has executed and independently passed exact-head acceptance tests on both operating systems where applicable.

Read latest live GitHub heads, comments, issues #5/#8/#9/#10, PRs #2/#3/#4/#6/#7, and each agent's progress log before any action. This snapshot may age.

## Assignment A — Independent QA Engineer (existing PR #3)

**Goal:** Independently accept or reject Claude's three fixes and regression safety, rather than merely counting green jobs.

1. Check out **actual Phase 2 SHA `325b7a8`** into a disposable test overlay using your existing QA branch `qa/testing-infrastructure`; record both source and QA SHA.
2. Execute the existing **five strict acceptance oracles** for #8 (post-audit physical stale publication and pre-audit effective mutation), #9 (insert and delete around rebuild watermark), and #10 (Windows transient `WinError 32`). Do not simply delete `xfail(strict=True)`; first run the tests in acceptance mode so any regression fails the job, and only remove xfail when the exact behavior is independently proven.
3. For #8, separately distinguish physical stale artifacts, journal revocation ordering, reader/index/recovery/transaction-outcome filtering, and *effective* stale mutation; test crashes/reacquisition and avoid declaring unsupported multiuser safety.
4. For #9, verify insert/delete/sync/rebuild/restart and ensure the cache cursor matches a stable authoritative snapshot; test bounded retry exhaustion/fail-safe behavior.
5. For #10, run the real **Windows six-process checkout race repeatedly**, capturing attempts, winners, conflicts, timeouts, exceptions, and reacquisition; test one injected transient sharing violation, persistent failure, and lock-token ownership change during retry. The prior actual Windows failure is not erased by a green rerun.
6. Maintain real PySide6 offscreen GUI, CLI, immutable revision history and Phase 2 negative-path coverage. Run focused Windows/Linux CI at meaningful checkpoints, not endless full matrices.
7. Commit any new independent tests to your existing QA branch, update `docs/agents/QA_PROGRESS.md`, `docs/TEST_MATRIX.md`, `docs/PR_MERGE_READINESS.md` and PR #3 with exact evidence; report confirmed defects on issues #8/#9/#10 and notify Claude on PR #7.

**Do not edit Claude's `Workspace`, `LocalIndex`, `documents.py` or other production files.**

## Assignment B — Distributed Systems Engineer (existing PR #4)

**Goal:** Independently review RevisionWorx's transaction/data-integrity standards and protocol correctness while Claude implements production.

1. Inspect exact `phase2-documents` source at `325b7a8` and ADR-014/015 rev. 2/016. Review the claimed #8 linearization point for interleavings involving revocation, writer preflight, exclusive journal sequence publication, crash/restart, stale physical entries and `txid` replay.
2. Validate that `expect_rev`, idempotent transaction outcome, generation fencing, admin reason/audit, and journal durability meet RevisionWorx's stated engineering invariants; do not confuse journal-order logical fencing with atomic cross-host authority.
3. Independently model #9 snapshot consistency and #10 token-checked lock release for recovery and cross-process correctness. Coordinate specific counterexamples with QA, not duplicated generic tests.
4. Continue isolated research on the **optional Windows-hosted LAN coordinator + SMB**: one atomic authoritative commit/revocation transaction, authenticated administrator override, lost ACK, multi-record atomicity, rollback of both DB and ordinary witness, external high-water trust root, disconnect/reconnect and read-only quarantine. Keep physical two-host SMB certification marked **not executed** until actually run.
5. Publish a risk-ranked protocol review and minimal interface decisions in `docs/DISTRIBUTED_SYSTEMS_HANDOFF.md` and PR #4; update `docs/agents/DISTRIBUTED_SYSTEMS_PROGRESS.md`. New executable tests/prototypes belong only on your isolated branch.

**Do not edit Claude's production files, start a network service, or declare multi-user ready.**

## Assignment C — Integration & Release Readiness Engineer (existing PR #2)

**Goal:** Produce a RevisionWorx **readiness report**, not a release, by evaluating PRs #2, #3, #4, #6, #7 together.

1. Recheck live heads, bases, changed files and architectural dependencies; document a safe **merge/stack plan for Director approval**, including draft PR #7 stacked on #6, QA PR #3 also based on #6, and adapter PR #2 currently based on main. No merge/retarget/rebase/force-push without approval.
2. Validate the existing read-only CAD adapter against **immutable RevisionWorx Phase 2 content**, including original suffix, authoritative `document_id`/`revision_id`, verified `revision.sha256` and adapter `file_sha256`, and read-only observation ownership. Preserve candidate part references as **unresolved**, not native BOM lines or managed part identities.
3. Use a disposable integration worktree or test harness to evaluate combined contracts without modifying other agents' branches. Record exact SHA combinations and executed Linux/Windows/Python tests; distinguish the current Windows parser-only tests from **Windows Phase 2 storage**, which was not previously verified.
4. Assess project registration, immutable revision restore, checkout/check-in, document lifecycle, index, CLI, PySide6 GUI, offline single-user mode, and cross-platform packaging/installers. Report **not executed** where packaged Windows/Linux GUI smoke or real SMB two-host certification has not happened.
5. Produce a **RevisionWorx readiness matrix**: feature/requirement, PR/owner, tested vs failed vs not executed, evidence, severity, dependency, recommended next gate. Include #8/#9/#10 QA acceptance status, migration/recovery concerns, test coverage and CI cost discipline. Review only the existing RevisionWorx adapter; do not modify or audit upstream CAD code.
6. Update `docs/agents/OPENSchematix_INTEGRATION_PROGRESS.md` and PR #2 with the exact readiness report and Claude's pending API decisions; request Director approval for any merge/release proposal.

**No code edits to Claude's storage, QA branch, distributed branch, or any other repository.**

## Coordination and execution

- Claude continues implementation on PR #7; specialists proceed **in parallel** on their existing branches.
- Publish one substantive checkpoint per meaningful tested batch, not repeated status-only comments. Read latest PR comments before writing to avoid duplicate messages.
- All claims require exact code SHA, test command, OS/Python, pass/fail/skip/xfail, and evidence. A green workflow containing strict xfails is **not** a safety pass.
- Prefer focused CI; full Windows/Linux matrices at a defined acceptance/release gate. No new paid/private-repo workflows.
- If blocked on an architectural decision, give Claude a concise proposed interface or specific failing reproducer and continue other independent tasks. Never invent a passing test.
