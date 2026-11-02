# Standing Orders — OpenSchematix Integration Engineer

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

## Active Director priorities — reviewed 2026-11-01 (latest branch evidence)

**Evidence baseline, not a current-head certification:** Draft PR #2 implements read-only `inspect(path)` and `inspect_bytes(raw, suffix)`. [Run 41382170644](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382170644) verified **38/38** on each Linux Python 3.11/3.13: 33 adapter, 3 pinned upstream native BOM oracle and 2 real Phase 2 immutable-revision tests. Since then the agent added custom-symbol/schema edge tests, copy/legacy identity regressions, and a **Windows CI matrix**. The latest inspected head was `11abac853423`, and its CI was still queued. The newer cases and Windows coverage are **not yet verified** by the older 38-test run.

### Execute next, in order
1. **Verify new Windows-inclusive CI:** inspect the current-head adapter workflow jobs, test discovery/counts, pinned upstream OpenSchematix SHA and Phase 2 SHA, and actual Windows and Linux results. Diagnose failures within this adapter and tests; do not count cancelled or superseded runs as evidence. Record exact per-platform commands, SHA, pass/fail/skip counts, and links.
2. **Lock down identity semantics:** maintain passing tests proving native legacy wrapper sheet IDs can be regenerated for identical bytes, copied schematics can share source-local UIDs, and `project_uid/sheet_uid/object_id` cannot replace authoritative PDM `document_id/revision_id`. Clearly distinguish local source identity, a manufacturer part-number *candidate*, and a verified managed-part association.
3. **Maintain the native BOM boundary:** preserve real `bom.generate` oracle tests over unchanged upstream fixtures (native lines **16/5/7**, unresolved manufacturer candidate groups **5/0/0**). Validate physical grouping, duplicate-unit rules, quantities, source refs, sheet selection and custom-symbol limitations. Do not assert that `bom_candidates` equals native BOM or create authoritative PDM dependencies.
4. **Finalize Claude's integration handoff:** review `docs/OPENSchematix_CLAUDE_HANDOFF.md` and specify exactly who retrieves immutable Phase 2 bytes, verifies `revision.sha256` and adapter `file_sha256`, owns/discards observations, invalidates stale observations, and handles unsupported/custom source schemas. Include tested revision content APIs and precise PR #2 stacking/overlap questions for Claude; document interface changes rather than modify his production code.
5. **Close as one coherent batch:** update progress log and PR #2 with *current* CI evidence, limitations and approval requests. Commit/push meaningful changes without causing avoidable superseded workflow runs. Leave PR draft until Claude/Director approve the integration sequence.

**Authority:** read-only isolated adapter, tests and documentation only. No writes to the OpenSchematix repository, RevisionWorx authoritative revision/checkout/part records, merges, unapproved PR retargeting, or release. A successful Linux source oracle does not demonstrate full native BOM parity or certify Windows/SMB production.
