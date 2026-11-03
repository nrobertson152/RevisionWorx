# Starting an agent by hand

Normally you don't need these: open an issue and the workflows do the rest. A hand-run agent has whatever GitHub access you give it. Give it a fine-grained token without Actions or Workflows permission (see `.agents/SECURITY.md`), not your full login. Use them when you want to run an agent yourself in Claude Code, Codex, ChatGPT or another tool that has access to the repository. The agent must have the repository checked out. Claude Code then loads `AGENTS.md` through `CLAUDE.md`; Codex loads `AGENTS.md` directly.

Every prompt ends the same way on purpose: the agent reports to GitHub, not to you.

## Implement an issue

> You are the ARF Implementer for issue #N in OWNER/REPO. Follow AGENTS.md and .agents/roles/implementer.md ("In an interactive session"). Work only within the issue's ARF contract. Open a PR with the required description, including your exact model id in Implemented-by-model, and add the `agent:review` label. Put all results, evidence and questions on the issue or PR. Your last chat message is only the PR link.

A commit you or a hand-run agent push has no ARF provenance, so the review workflow stops and asks you. Record it with **ARF Director approval** (kind: provenance) for that head commit; it is then reviewed as human-implemented. This is deliberate: nothing else can establish who wrote it.

If the issue has no ARF contract yet, add `agent:triage` to it and let the coordinator write one, or ask the agent to propose one as an issue comment first.

## Review a PR (must be a different model from the implementer)

Usually automatic. To force a re-review, add `agent:review` to the PR. To review by hand:

> You are the ARF Reviewer for PR #N in OWNER/REPO at head commit SHA. The implementer was MODEL; you must be a different model. Follow AGENTS.md and .agents/roles/reviewer.md. Post your verdict as a PR comment in the verdict format, with your exact model id. Do not modify the PR branch. Your last chat message is only the link to your comment.

A hand-posted verdict is a comment only. It can't set the `arf/cross-model-review` status, which only GitHub Actions can write; add `agent:review` to have the workflow produce one.

## Coordinate

> You are the ARF Coordinator for OWNER/REPO. Follow AGENTS.md and .agents/roles/coordinator.md. Read the open issues and PRs on GitHub, then apply your plan directly: contracts on issues, risk labels, `agent:implement` for dispatch, and the Director briefing issue. Never dispatch overlapping paths or exceed the parallel limit. Your last chat message is only the briefing issue link.

Prefer the **ARF coordinator** workflow (Actions tab → Run workflow): it validates the plan in code before applying it.
