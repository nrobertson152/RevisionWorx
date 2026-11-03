# RevisionWorx — Coordinator Update Report Standard

**Status:** Director-approved reporting format. **Scope:** Whenever the Director asks the RevisionWorx coordinator for an "update", "status", "progress report", or equivalent.

## Mandatory workflow before reporting

1. Inspect **live GitHub** rather than repeating a previous report. Read current `main`, every open PR (currently #2 Integration, #3 QA, #4 Distributed, #6 Phase 1, #7 Phase 2, #14 Desktop Alpha), head SHAs, draft/mergeability, latest comments, agent orders/progress logs, relevant issues, and latest CI evidence. Discover new or closed PRs rather than treating this list as exhaustive.
2. Separate **verified** evidence (exact SHA, native OS/Python, tests and CI run) from agent assertions, in-progress work, and unverified changes. A green job that merely reproduces an unsafe condition is **not** safety acceptance. Distinguish scoped acceptance from whole-product approval.
3. Report the five roles explicitly: Core Engineering (Claude A), Desktop GUI (Claude B), QA (ChatGPT A), Distributed Systems (ChatGPT B), Integration (ChatGPT C). Identify active/paused/unknown status only when supported, latest work, blockers, and whether each should continue, remain paused, or resume.
4. Read scheduled-task state if discussing automation. Never infer it from GitHub activity. The Director's preference is **pause scheduled ChatGPT agents while actively chatting; resume only on explicit "I'm leaving"**. The scheduler cannot automatically detect presence. Do not activate or deactivate tasks based solely on a request for "update".
5. Do not merge, rebase, force-push, retarget, backport, tag, release, or alter another agent's branch without explicit Director authorization. A report request does not authorize any such action.

## Required report layout

### RevisionWorx — GitHub Update
Start with **one or two sentences** stating the most important verified changes since the previous update, or "No new GitHub changes detected" if appropriate. Avoid generic progress narration.

### Pull request status
Compact table with columns **PR / Role or subsystem / Latest short SHA / Status or blocker**. Cover **all** open PRs; identify draft status, merge conflicts, dependencies, and new unverified commits. Link to each PR.

### Latest verified achievements
Report only newly established evidence when possible, with **exact source SHA, operating system, Python version where relevant, test counts, and clickable CI run**. Distinguish:
- corrected core protocol/local writer safety;
- native Windows/Linux regression and adversarial tests;
- frozen GUI/CLI packaging and integration;
- manual interactive installed GUI testing;
- SMB/NFS, multi-host, physical power-loss and release qualification.

Do not imply tests on an older GUI/core SHA certify a newer SHA. If a test-only disposable composite is used, say it is **not** an integrated production commit.

### Agent recommendations
List **all five roles**, each with current activity, next concrete assignment, and **CONTINUE / RESUME / PAUSE / BLOCKED** recommendation. Distinguish Claude agents from ChatGPT agents and avoid directing ChatGPT agents to modify Claude-owned branches.

### Automation status
State whether scheduled ChatGPT tasks are paused or active **only if checked**, or say "not checked." An ordinary update must not resume them. Report scheduler/coding-environment limitations accurately; a scheduled task does not guarantee code execution or GitHub write capability.

### Overall disposition
One or two sentences giving merge/release readiness, highest-priority blockers, and the next gate. Never claim full acceptance from a narrowly scoped test.

## Style
Direct, concise, factual; no filler. Use a compact table and short sections, with clickable PR/CI links. Prioritize deltas over repetition. When the Director requests a deeper audit, expand the evidence and risks.

## Historical baseline (not a substitute for live verification)
As of the latest known coordinator report: #6 Phase 1 has merge conflicts; #7 core `fe8a179` has narrowly scoped local-protocol acceptance, not blanket release approval; #14 GUI has advanced beyond the older `17651af` accepted by Integration; #2 test-only composite CI 41572811571 passed on Ubuntu/Windows against older GUI `17651af`; #3 QA and #4 Distributed provide independent focused evidence. All PRs were draft. These facts may become stale: **always check GitHub again**.
