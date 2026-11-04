# RevisionWorx — engineering coordination playbook

**Current roles and engineering rules:** [AGENTS.md](../../AGENTS.md). **Status-report format:** [COORDINATOR_REPORT_STANDARD.md](COORDINATOR_REPORT_STANDARD.md).

## Single-owner assignment

1. Read live `main`, open issues/PRs, their bases, latest head SHAs, reviews and CI before proposing work. Do not use an old roster or a hardcoded PR list.
2. Confirm existing authorization or use delegated authority for bounded in-scope work, with a single assigned Coder, acceptance criteria, base branch and dependencies. When code lives on stacked draft branches, preserve the stack and do not silently rebase or merge.
3. The Coder implements and reports exact-SHA test evidence; a separate Reviewer assesses the change and its integrity invariants. The Coordinator manages handoffs, engineering decisions and gated merges.
4. The Coder addresses blocking findings; the Reviewer rechecks the latest SHA. The Coordinator resolves routine cross-PR conflicts, requests missing evidence and refuses unsafe merges.
5. The Coordinator decides routine assignments and verified PR merges under the delegated merge gates, recording evidence on GitHub. Only the Director decides releases, release tags and licensing changes; safety gates cannot be waived.

Historical Claude A/B/C/D, ChatGPT A/B/C, Core, GUI, Distributed, QA and Integration role names in old issues, commits or PRs remain historical attribution. They do not authorize independent agents to resume work. PRs #22 and #23 propose older multi-agent checkout/locking arrangements and are **not** the current protocol; leave them unmerged unless the Director explicitly revisits them.

## Lightweight handoff

Keep work on an issue or PR with: scope, assigned Coder, base branch, latest commit, tests actually executed, review findings, blockers and next action. Avoid parallel checkouts of the same task, mandatory GitHub-ref locks, periodic check-in ceremonies or additional standing agent slots.

## Automation

The Director authorized staggered hourly coding and coordination schedules on 2026-11-03. Their prompts must follow the three-role policy and never treat historical agent orders as active. Additional schedules require explicit authorization.

## Release boundaries

A green PR CI result is not proof of safe shared-folder collaboration. Keep local unit tests, multiprocess tests, native Windows/Linux checks, real SMB/NFS cross-host testing, manual GUI validation and physical-failure testing separate in every report. Release and multi-user safety claims require Director approval supported by matching evidence.

## Delegated merge checklist

Before merging, verify current-head required CI, a current independent review, no unresolved blocking findings, correct base/dependencies, regression and platform evidence proportionate to risk, and no draft/release-only scope. For filesystem integrity/concurrent authority changes, require actual appropriate failure-mode evidence; do not use hosted CI alone to claim production multi-user safety. Document SHA, evidence and merge rationale. The Director exclusively controls releases, release tags and licensing.
