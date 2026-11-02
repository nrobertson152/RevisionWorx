# RevisionWorx — Engineering Assignment Orders

These are the current standing orders issued by the Project Director for the three independent ChatGPT specialists. **Assignment names replace all alphabetic agent labels.** Claude remains Lead Architect.

| Assignment | Orders | Development branch | Progress file |
| --- | --- | --- | --- |
| QA Engineer | [QA_ORDERS.md](QA_ORDERS.md) | `qa/testing-infrastructure` | `QA_PROGRESS.md` |
| OpenSchematix Integration Engineer | [OPENSchematix_INTEGRATION_ORDERS.md](OPENSchematix_INTEGRATION_ORDERS.md) | `integration/openschematix` | `OPENSchematix_INTEGRATION_PROGRESS.md` |
| Distributed Systems Engineer | [DISTRIBUTED_SYSTEMS_ORDERS.md](DISTRIBUTED_SYSTEMS_ORDERS.md) | `research/shared-filesystem-protocol` | `DISTRIBUTED_SYSTEMS_PROGRESS.md` |

**Instructions:** Read your assignment's orders from `main` at the beginning of every session, even if your working branch does not yet contain these new files. Preserve and rename old progress logs on your existing branch. Do not replace your current branch with `main` or overwrite ongoing work just to read orders.

These standing orders supplement `CLAUDE.md` and repository requirements. The Project Director approves merges and releases. Agents must report material conflicts to the Lead Architect.

The existing Phase 1 implementation is on `phase1-foundation`; inspect it when evaluating actual behavior. Do not assume that Phase 1 is merged to `main`.

## Current assignment execution and evidence discipline

The **Active Director priorities** section in each role's order file is the active task queue. Read it from `main` at session start; inspect the newest branch head, PR, GitHub Actions and assignment progress log rather than repeating already completed tasks. Prefer one coherent implementation/test batch before refreshing status. For disposable cross-branch tests, record both the app SHA and the overlay SHA. Distinguish successful jobs from skipped, cancelled, queued or failed jobs; a green workflow may contain **intentional strict xfails** and cannot certify those requirements.

### Active production blockers and ownership

- **Issue #8:** administrator lock break is not atomic with stale-writer publication; ADR-015's positive post-audit logical fencing does not prevent an effective stale mutation in the pre-audit lock-rename window. Claude owns production correction; QA owns independent safety acceptance; Distributed Systems owns isolated authority/linearization research.
- **Issue #9:** `LocalIndex.rebuild` can include inserted or exclude deleted records newer than its saved journal watermark. Claude owns the production index fix; QA owns both acceptance cases.
- **Issue #10:** intermittent Windows `WinError 32` when releasing `writer.lock`, observed in actual six-process checkout tests. Claude owns production lock-release fix; QA owns repeatable regression/stress evidence; Distributed Systems may advise on OS semantics.

The Director approved an **optional on-premises Windows-hosted LAN coordinator with SMB** for future multi-user support (Issue #5/ADR-014). No prototype, local Windows runner or passing defect reproduction proves production SMB/NFS safety. Standalone single-user use must remain offline and coordinator-free. Physical SMB certification, production coordinator authentication/restore fencing, packaged desktop GUI smoke, and final integration remain separate qualification gates.

### Agent coordination and stop conditions

Record exact SHA, OS/Python, run/job links, commands, pass/skip/xfail and unresolved concerns in each PR/progress log. Strict expected failures are **open requirements**, not fixes; distinguish unsafe-behavior reproduction from safety acceptance. Communicate new material evidence through existing GitHub issues/PRs without duplicating repeated status comments. Claude owns production `CommitAuthority`, `Workspace`, `documents.py`, `LocalIndex` and architecture decisions; specialists own isolated, assigned work only. When remaining work is blocked on Claude's design decision, produce a succinct reviewable handoff and stop adding speculative features. No merges, releases or retargeting without Director approval.

## Resume coordination (2026-11-01)

**All agents must read [`CLAUDE_RESUME_HANDOFF.md`](CLAUDE_RESUME_HANDOFF.md) from latest `main` before the next work cycle**, in addition to their standing orders and the current live GitHub PR/issue/CI state. The handoff explains Claude's production-fix responsibilities, agent verification interfaces and cost-conscious CI execution. Focus on actionable #8/#9/#10 defect closure and approved API decisions; do not trigger redundant full CI merely to publish a progress note. OpenSchematix-HPS-Edition and other repositories are **outside the scope** of these RevisionWorx orders.
