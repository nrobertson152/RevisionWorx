# Security

All agents act through the Director's GitHub account, so GitHub itself cannot restrict one agent more than another. ARF compensates by separating four things: reasoning, execution, verification and authority. Each gets only the access it needs.

## Boundaries enforced by the workflows

1. **No job both runs a model and holds the Director's token.** Each workflow has a prepare job (no model, read-only token), a model job (no write token; persisted git credentials disabled), and a publish job on a fresh runner (no model). The model's output reaches the publish job only as an artifact: a patch, a plan or a verdict. That output is parsed as data and never executed. Nothing the model does to its own runner, such as git hooks, `.git/config`, modified tools or background processes, exists on the publish runner.
2. **Trusted code only where the token is.** Publish jobs check out the default branch and run `tools/arf.py` from it. They apply the patch with git hooks disabled and never run repository scripts.
3. **Validate everything, then write.** The publish step rejects patches that touch `.git`, symlinks, submodules, protected files or anything outside the contract. It re-checks the whole branch diff against the default branch, not just the new patch. It validates the PR text and rules version. Only then does it commit, and it pushes with a lease, so a concurrent push is never overwritten. On any failure nothing is pushed.
4. **Gate statuses come from GitHub Actions.** `arf/cross-model-review` and `arf/rules-approval` are written with the workflow's own `GITHUB_TOKEN`. The ruleset requires the review status from the GitHub Actions source, and the policy check accepts a rules approval only if GitHub Actions created it. A status written with the Director's token, which an agent could do, does not count.
5. **Agent output never comes from the repository, and a failed job hands over nothing.** Model jobs empty `.arf-out/` before the model starts. They upload output only if the job succeeded, and publish jobs refuse output from a job that didn't. `.arf-out/` can never be committed: such paths are rejected in patches, and a branch containing it isn't reviewed.
6. **Risk can't be talked down.** `.agents/risk-floors.json` sets a minimum risk per path. The coordinator's risk is raised to the floor and never below an issue's existing risk; review recomputes risk from the files the PR actually changes. A verdict is checked against the contract digest recorded when the review started, so editing the contract mid-review voids it.
7. **Who wrote a commit comes from a trusted record.** The publish job writes `arf/provenance` (implementer model, run id) on every commit it pushes. Review reads only provenance written by GitHub Actions, so editing the PR's `Implemented-by-model` line changes nothing. A head commit with no trusted provenance, such as one you or a hand-run agent pushed, isn't reviewed until you vouch for it with **ARF Director approval** (kind: provenance).
8. **Protected-file changes need the Director in person.** Changes to `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.github/` or `tools/arf.py` need a `rules-change` issue *and* the **ARF Director approval** workflow (kind: rules), run for the exact head commit. Starting a workflow needs the Actions permission, which `ARF_GH_TOKEN` doesn't have, so only you can do it from the GitHub web or mobile app. The token also lacks the Workflows permission, so GitHub itself rejects any push it makes that changes a workflow file.
9. **Director-only workflows check who started them.** **ARF Director approval** runs only when `github.actor` and `github.triggering_actor` both equal the `ARF_DIRECTOR` variable, and the tool checks again before writing. Another collaborator who can start workflows can't approve.
10. **Untrusted input.** Issues and comments from people without write access are excluded from agent context and never dispatched. Text in issues, code and tool output is data, not instructions.

## What the Director must keep true

- **`ARF_GH_TOKEN` stays narrow.** It needs: Contents, Issues, Pull requests (read and write); Commit statuses, Metadata (read). It must not have Actions, Workflows, Administration or Secrets. Any of those would let an agent approve rule changes or change workflows. It is used only in publish jobs.
- **The ruleset bypass list stays empty.** The rules then bind your account, and so every agent.
- **Hand-run agents get a narrow token too.** An agent you start yourself with your full GitHub login, or a classic token, can do anything you can: run the approval workflow, edit workflows, merge. Give such sessions a fine-grained token like `ARF_GH_TOKEN`, without Actions or Workflows. The required checks still stop unreviewed merges either way.

## Known limits

- **Gate statuses prove "GitHub Actions wrote this", not "this workflow file wrote it".** Any workflow in the repository with `statuses: write` could write `arf/cross-model-review`, `arf/provenance` or `arf/rules-approval`. Personal-account rulesets can't pin a status to one workflow file. What actually keeps a rogue workflow out:
  - `ARF_GH_TOKEN` has no Workflows permission, so GitHub rejects any agent push that adds or edits a workflow.
  - Publish and the policy check refuse `.github/` changes without `rules-change` and your approval run.
  - The ruleset requires those checks.

  This includes workflows in **unmerged** PRs. A same-repository PR runs its own workflow files on `pull_request`, and with `statuses: write` it can write a gate status on *any* commit, including another PR's head. So the real boundary is "who can push workflow files". Agents can't: their token lacks the Workflows permission. Pushing a workflow file needs only write access, plus the workflow permission for tokens. Editing rulesets needs admin, so this **is** a way around the gate for any collaborator with write access, not just you. In a repository where you are the only collaborator, that means you and any credentials with your full permissions. Never give agents a token with the Workflows permission, and only give write access to people you'd trust to merge. As a tripwire, the coordinator flags every open PR that changes workflow files on the briefing issue. Pilot tests N4 and N5 measure this live.
- **Model provider keys sit in model jobs.** The model needs them to run, and a hostile model could try to read its own key. Codex's action routes calls through a proxy that keeps the key out of the model's environment; for Claude, assume the key is readable. Use a dedicated key or workspace for CI with a monthly spend cap, separate from your personal use, and rotate it if a run looks wrong. Prefer an API key with a spend limit over your subscription token for repositories where untrusted issue text could reach the agents.
- A model job can still spend its own time and API budget badly, or read anything the read-only token can. Job timeouts and turn caps bound it.
- The model job sees the repository and its read-only `GITHUB_TOKEN`. Don't put secrets in the repository, and keep them out of `ARF_BOOTSTRAP_CMD`.
- Not yet exercised against a live repository; see `docs/arf/SETUP.md` for the pilot.

## Incidents

Suspected secret exposure, data loss or an unauthorized change: stop, label `needs:director`, revoke and reissue `ARF_GH_TOKEN`, preserve evidence, and do no destructive cleanup.
