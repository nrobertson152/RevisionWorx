# RevisionWorx — Claude Lead Architect Resume Handoff

**Director preparation:** 2026-11-01 (ET). This is a coordination document, **not** merge or release approval. Review latest live GitHub state and the existing `docs/DEVELOPMENT_PROGRESS.md` on `phase2-documents` before editing; do not repeat completed Phase 1/2 implementation.

## Current architecture and branch map

| PR | Branch → base | Last inspected head | Ownership |
| --- | --- | --- | --- |
| #6 | `phase1-foundation` → `main` | `412e69ab90bb` | Claude; PySide6 and Phase 1 |
| #7 | `phase2-documents` → `phase1-foundation` | `47d81658764c` | Claude; immutable documents/revisions/checkouts |
| #3 | `qa/testing-infrastructure` → `phase1-foundation` | `2a663091e416` | QA, independent acceptance tests and CI |
| #4 | `research/shared-filesystem-protocol` → `main` | `216d52c948e0` | Distributed Systems, isolated coordinator/fencing research |
| #2 | `integration/openschematix` → `main` | `6e4a1ea47248` | OpenSchematix Integration, isolated read-only adapter |

All PRs inspected remain **draft/open**. These heads are snapshots: verify them again before changing code or quoting test results.

**Approved decision:** Issue #5/ADR-014 approves an **optional, locally hosted Windows-first LAN coordinator with SMB** for supported *multi-user* writes. Standalone single-user mode must continue without cloud, mandatory coordinator, accounts, subscriptions or telemetry. Claude owns production `CommitAuthority` and authoritative storage interfaces; agent prototypes are not approved production integrations.

## Immediate implementation priorities, in order

1. **Issue #8 — atomic administrative revocation and publication (critical):** The current ADR-015 journal-order mitigation correctly ignores stale entries **after** a completed override audit, but two separate defects remain: (a) a physical stale journal entry may still publish after an audit, and (b) a **pre-audit interval** between renaming `writer.lock` and committing the audit can let a revoked writer's mutation actually take effect. Close the effective pre-audit gap with one authoritative linearization point for generation revocation and publish. Preserve token-checked, reason-required, durable audited administrator overrides and transactional `expect_rev`, idempotent `txid` semantics. Do not label plain-share concurrent multiwriter safe.
2. **Issue #9 — consistent LocalIndex snapshot/watermark:** `LocalIndex.rebuild` may mix inserted or deleted records at sequence N+1 with a journal cursor N. Implement bounded/stable journal replay or snapshot/restart; test both insert/delete, sync, rebuild and document/revision projections. The local index remains disposable, not authoritative.
3. **Issue #10 — Windows writer.lock close/release race:** An actual Windows six-process checkout CI run failed with `WinError 32` in `Workspace.close()`, producing one winner, two conflicts and three timeouts. Later reruns can pass; an injected transient sharing-violation acceptance test currently remains a **strict expected failure**. Fix close semantics with bounded safe retries while preserving token ownership, no deletion of another writer's lock, and administrator override behavior; independently repeat Windows concurrent checkout trials.
4. **Integrate independently reviewed QA, then adapter research as approved:** QA PR #3 has real PySide6 offscreen and Linux/Windows Phase 2 overlay tests, and five strict acceptance xfails across #8/#9/#10. Request QA retest after code fixes. Read `docs/OPENSchematix_CLAUDE_HANDOFF.md` in integration PR #2 for immutable revision read-only `inspect_bytes` contract, authoritative SHA validation, document/revision identity and unresolved part-number candidates. PR #2 must not invent managed part IDs or native BOM parity. Review exact merge/stack conflicts with PRs #6/#7 before authorizing any integration.
5. **Coordinator research review, not premature rollout:** Read `docs/COORDINATOR_INTEGRATION_CONTRACT.md`, `docs/COORDINATOR_WITNESS_RESEARCH.md`, `docs/DISTRIBUTED_SYSTEMS_HANDOFF.md` in PR #4. It has transactional generation/fencing, witnessed recovery and local authenticated-actor *test* prototypes. Ordinary witness files are **rollbackable** together with SQLite; test identities are **not** real Windows auth; no physical two-host SMB tests have been conducted. Defer production multiuser enablement until those gates are solved.

## Verification status and CI discipline

- QA: current-head workflows [41383307946](https://github.com/nrobertson152/RevisionWorx/actions/runs/41383307946) and [41383313202](https://github.com/nrobertson152/RevisionWorx/actions/runs/41383313202) completed successfully; the first has eight Linux/Windows × Python 3.11/3.13 jobs including disposable Phase 2 verification. Historical full suite evidence includes Linux 191 passed/4 xfailed and Windows 190 passed/1 skipped/4 xfailed; a separate Windows fault injection adds a **fifth strict xfail**. These xfails indicate unresolved defects, **not fixes**.
- Integration: [41382904935](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382904935) passed four Linux/Windows adapter/native-oracle jobs at `6e4a1ea`. The preceding verified matrix ran 43 tests per Linux job (including 3 actual Phase 2 revision cases) and 40 per Windows job (Phase 2 storage **not** executed on Windows). The native BOM lines 16/5/7 are not the adapter's unresolved candidate groups 5/0/0.
- Distributed: [41383055792](https://github.com/nrobertson152/RevisionWorx/actions/runs/41383055792) passed all six jobs; previously [41382608414](https://github.com/nrobertson152/RevisionWorx/actions/runs/41382608414) verified 42/42 local research tests per Ubuntu/Windows Python configuration, plus real-code Phase 1/Phase 2 **unsafe-behavior reproductions**.
- **GitHub Actions allowance:** The user received a 100%-usage notification. Most of the billable burst was observed in a **separate private repository**. RevisionWorx is public, so standard hosted public-repo Actions generally remain free. Nonetheless avoid duplicate push+PR matrix triggers, excessive documentation-only CI, and unnecessarily rerunning full matrices. Check actual account billing before assuming any further paid runners are available. Prefer precise focused CI during development and full cross-platform regression before accepted merge/release candidates. Do not change private HPS Edition or other projects as part of this RevisionWorx assignment.

## Session operating plan

1. Read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, current `docs/DEVELOPMENT_PROGRESS.md` from your own branch, current `docs/agents/README.md` on `main`, and this handoff. Inspect live branches, issues #5/#8/#9/#10 and agent PRs #2/#3/#4.
2. Choose the smallest **production defect fix** with clear QA acceptance, initially #10 if atomic authority #8 requires design agreement; otherwise prioritize #8 by safety severity. Document which decision you made and why.
3. Implement actual code and tests on your existing Claude-owned branch, run focused local tests, then strategically scheduled CI. Preserve existing data and all history; do not modify agent branches or public OpenSchematix.
4. Request independent QA verification of exact SHA through GitHub; the QA agent owns acceptance evidence and may not unilaterally declare production multiuser safety.
5. Commit/push, update `docs/DEVELOPMENT_PROGRESS.md` and the applicable PR/issue; list verified vs blocked results. Do **not** merge, tag, release or call multi-user production-ready without the Director's explicit approval.

## Agent responsibilities for next cycle

- **QA (#3):** reproduce and retest #8/#9/#10 on Claude's new code; maintain per-PR merge readiness; verify real Qt/CLI and Windows process contention without altering production.
- **Distributed Systems (#4):** refine transactional publication/revocation and nonrollbackable witness trust boundary; provide implementable interface and separate Windows SMB test plan, not a production coordinator.
- **OpenSchematix Integration (#2):** hold green read-only adapter with pinned native BOM oracle and Phase 2 immutable revision tests; submit specific API approval and stacking decisions instead of speculative feature expansion.

**No autonomous agent can start another agent's ChatGPT session.** This file is an authoritative handoff to be consumed when each agent is resumed.
