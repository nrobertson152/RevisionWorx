# RevisionWorx — Engineering Coordinator report standard

**Status:** Current Director-requested reporting format. Applies whenever the Director requests an update, status or progress report. The Coordinator is the **third role**, alongside one assigned Coder and one independent ChatGPT Reviewer. See [AGENTS.md](../../AGENTS.md) and [the coordination playbook](README.md).

## Before reporting

1. Inspect **live GitHub**: current `main`, every open PR (including stacked PRs), head SHAs, draft and mergeability status, latest comments, reviews, unresolved threads, issue dependencies and CI results. Do not rely on a fixed list of PR numbers.
2. Separate **verified evidence** (exact SHA, OS/Python/Qt where applicable, commands, counts and CI run) from author claims, untested changes and historical baselines. A green job that reproduces an unsafe condition is not safety acceptance.
3. Report the three current roles: **Coder (Claude or ChatGPT)**, **Reviewer (ChatGPT, independent)**, and **Engineering Coordinator**. Give each current assignment, latest work, blockers and next action. Old five-agent or seven-agent labels are historical only.
4. Check scheduled-task state if discussing automation. Do not infer it from GitHub activity. **An ordinary status request does not resume paused agents.**
5. Never merge, rebase, force-push, retarget, backport, tag, release, delete branches, change rules or modify another owner's PR without explicit Director authorization.

## Required report

### RevisionWorx — GitHub update
One or two sentences with the most important newly verified changes, or say that no new changes were found.

### Pull request status
A compact table covering **every** open PR: PR link, purpose, base and head SHA, draft/mergeability, current-head CI, independent review, unresolved findings, dependencies and **READY / BLOCKED / IN REVIEW / DRAFT**. Identify conflicts and missing evidence.

### Latest verified achievements
Record exact SHA and environment for core transaction/revision safety, GUI and CLI, Windows/Linux checks, packaging, manual installed-GUI verification, and actual SMB/NFS cross-host evidence as applicable. Do not imply tests on one SHA certify another. A disposable integration composite is not a merged production commit.

### Three-role handoff
- **Coder:** assigned issue/PR, latest implemented SHA, tests, next action.
- **Reviewer:** current review queue, accepted or blocking findings, SHA last examined.
- **Engineering Coordinator:** dependency/integration decision, readiness recommendation, items needing Director action.

### Automation status
Report scheduled tasks as active, paused or not checked **only if observed**. Never resume them as part of an update. Scheduling alone does not guarantee an agent can write code or access GitHub.

### Overall disposition
One or two sentences with merge/release readiness, the highest-priority blocker and the next verification gate. Do not claim product-wide or multi-user safety from narrow tests.

## Style
Concise and evidence-driven. Prioritize new facts over repeated historical reports. Provide clickable PR/CI links and explicitly distinguish unknown from failed or passed.
