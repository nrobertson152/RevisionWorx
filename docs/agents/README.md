# RevisionWorx — engineering coordination playbook

**Current roles and engineering rules:** [AGENTS.md](../../AGENTS.md). **Status-report format:** [COORDINATOR_REPORT_STANDARD.md](COORDINATOR_REPORT_STANDARD.md).

## Single-owner assignment

1. Read live `main`, open issues/PRs, their bases, latest head SHAs, reviews and CI before proposing work. Do not use an old roster or a hardcoded PR list.
2. Confirm Director authorization and a single assigned Coder (Claude or ChatGPT), acceptance criteria, base branch and dependencies. When code lives on stacked draft branches, preserve the stack and do not silently rebase or merge.
3. The Coder implements and reports exact-SHA test evidence; a separate ChatGPT Reviewer assesses the change and its integrity invariants. The Coordinator tracks handoffs and readiness only.
4. The Coder addresses blocking findings; the Reviewer rechecks the latest SHA. The Coordinator flags missing evidence and cross-PR conflicts to the Director.
5. The Director decides merges, releases, assignment changes and risk exceptions. Coordinator reports are advisory, not approvals.

Historical Claude A/B/C/D, ChatGPT A/B/C, Core, GUI, Distributed, QA and Integration role names in old issues, commits or PRs remain historical attribution. They do not authorize independent agents to resume work. PRs #22 and #23 propose older multi-agent checkout/locking arrangements and are **not** the current protocol; leave them unmerged unless the Director explicitly revisits them.

## Lightweight handoff

Keep work on an issue or PR with: scope, assigned Coder, base branch, latest commit, tests actually executed, review findings, blockers and next action. Avoid parallel checkouts of the same task, mandatory GitHub-ref locks, periodic check-in ceremonies or additional standing agent slots.

## Automation

No recurring coding/review/coordination runs are created by this document. A request for an update does not authorize resuming paused tasks. If the Director later authorizes a schedule, its prompt must follow this three-role policy and never treat historical agent orders as active.

## Release boundaries

A green PR CI result is not proof of safe shared-folder collaboration. Keep local unit tests, multiprocess tests, native Windows/Linux checks, real SMB/NFS cross-host testing, manual GUI validation and physical-failure testing separate in every report. Release and multi-user safety claims require Director approval supported by matching evidence.
