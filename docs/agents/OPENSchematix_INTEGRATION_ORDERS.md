# Standing Orders — OpenSchematix Integration Engineer

**LATEST ORDERS:** [RevisionWorx Parallel Assignments](PARALLEL_ASSIGNMENTS.md) — produce RevisionWorx PR #2/#3/#4/#6/#7 integration and release-readiness report; preserve the existing read-only adapter scope. No external repository work or release.

**Assignment:** Read-only OpenSchematix interoperability, BOM and dependency extraction.
**Branch:** `integration/openschematix`
**Progress log:** `docs/agents/OPENSchematix_INTEGRATION_PROGRESS.md`
**Repositories:** `nrobertson152/RevisionWorx` and `nrobertson152/OpenSchematix`.
**Authority:** Integration adapter research and isolated implementation; Claude remains Lead Architect.

## Priority tasks
1. Inspect actual OpenSchematix source, sample project files, schema/version handling, symbols, component identifiers, custom libraries, BOM generation and exports. Ground claims in source/tests, not guessed formats.
2. Inspect Phase 1 content-based OpenSchematix detection on `phase1-foundation` and identify how to extend it without violating the authoritative metadata design.
3. Define normalized **read-only** project, document, component, BOM and dependency extraction records, with provenance and verified/inferred/unknown status.
4. Distinguish symbol instance IDs, engineering part IDs, company part numbers and manufacturer part numbers. Evaluate identity stability across save, duplicate and copy operations.
5. Specify revision-to-revision BOM comparison, cross-project where-used and Before You Revise support, including limits of unmanaged project coverage.
6. Build isolated parser/adapter prototypes and fixture-driven tests where possible. Preserve unknown schema fields and never rewrite user schematic files while indexing.
7. Propose any needed OpenSchematix changes as documented requests only; **do not modify the OpenSchematix repository without director approval**.
8. Coordinate the adapter interface with Claude before coupling it to unfinished RevisionWorx core modules.

## Deliverables
Maintain `docs/OPENSchematix_INTEGRATION.md`, `docs/OPENSchematix_DATA_MODEL.md`, adapter interface specification, compatibility and BOM/dependency analysis, isolated tests/prototypes, and `docs/agents/OPENSchematix_INTEGRATION_PROGRESS.md`. Update your existing integration PR.

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

**Branch and scope:** `integration/openschematix`, draft PR #2. The inspected head `6e4a1ea47248` is **green** in [GitHub Actions run 41382904935](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382904935): four jobs successful on Ubuntu/Windows × Python 3.11/3.13. The agent's detailed earlier [run 41382647720](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382647720) establishes **43 tests per Linux job** (36 adapter + 4 pinned native BOM + 3 real Phase 2 revision contract) and **40 per Windows job** (36 adapter + 4 native BOM). That's **166 executions**, not 166 distinct methods. The Phase 2 storage contract is **not run on Windows**; standalone adapter and native BOM *are* Windows tested.

**Preserve actual source facts:** native OpenSchematix `bom.generate` produces **16/5/7** BOM lines for the three pinned fixtures; the adapter reports **5/0/0** unresolved manufacturer-part candidate groups, not authoritative native BOM lines or managed part associations. Copied `.schematix` sources can share native UIDs while representing *different* PDM documents/revisions, and legacy `.osch` wrapper UIDs can change on reread.

### Execute next, in order

1. **Complete Claude's interface decision package:** maintain `docs/OPENSchematix_CLAUDE_HANDOFF.md` with explicit request/response shapes for immutable content access, caller verification of `revision.sha256`, adapter `file_sha256` verification, original suffix, authoritative `document_id/revision_id` identity, read-only observation owner, refresh/invalidation, error handling, and unsupported custom library/schema cases. Include small executable usage examples grounded in the inspected APIs. Request Claude's review in existing PR #2; do not preempt approval.
2. **Plan PR #2 integration without altering core branches:** inspect current `main`, PR #6 and stacked PR #7 for path collisions and identify the exact file conflict(s), especially historical `docs/DEVELOPMENT_PROGRESS.md`. Provide Claude/Director a minimal safe stacking order; do not retarget, merge, or rewrite history.
3. **Finish only meaningful adapter gaps:** preserve pinned upstream BOM oracle, physical quantity grouping, references and sheet-selection tests; maintain unresolved candidate vs verified managed part distinction. Expand fixtures only where a concrete defect is demonstrated. Avoid implementing native BOM import, managed-part creation, dependency edges or arbitrary custom-symbol evaluation without explicit architecture approval.
4. **Platform boundaries:** preserve the green Windows parser/native-oracle matrix. Propose a *separately marked* disposable Windows Phase 2 contract run if feasible without production changes; distinguish any new failures in Phase 2 core (including Windows lock race #10) from adapter defects. Never silently describe unexecuted Windows storage as tested.
5. **Deliver a reviewable batch:** verify CI at any new code SHA, update `docs/agents/OPENSchematix_INTEGRATION_PROGRESS.md` and PR #2 with current evidence and the exact blocking decisions for Claude. If the adapter is complete under present scope, explicitly mark it awaiting architectural integration rather than manufacturing further features.

**Authority:** isolated read-only adapter, oracles and documentation only. Do not modify upstream OpenSchematix, authoritative RevisionWorx revision/checkout/part storage, another agent's branch, or unapproved merge targets. No merges or releases.
