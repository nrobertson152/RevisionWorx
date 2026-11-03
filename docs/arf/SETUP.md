# Setup (about 30 minutes per repository, plus the pilot)

Everything runs as your GitHub account. You need admin access to the repository and keys for both model providers. Cross-model review needs two vendors by default: Claude implements and Codex reviews, or the other way round.

## 1. Install the files

Copy `AGENTS.md`, `CLAUDE.md`, `.agents/`, `.github/`, `tools/` (including `tools/arf_tests/`) and `docs/` into the repository on a branch and open a PR. The `ARF policy` check skips itself on this PR because the checker isn't on the default branch yet. Merge it yourself.

If the repository already has an `AGENTS.md`, `CLAUDE.md`, PR template or issue templates, merge them rather than overwriting (see `the ARF framework's docs/ADOPTION_GUIDE.md`). A `CLAUDE.md` with project content just needs `@AGENTS.md` as its first line.

## 2. Create the token the workflows use to act as you

GitHub → Settings → Developer settings → Fine-grained personal access tokens → Generate new token:

- **Repository access:** only the repositories using ARF.
- **Read and write:** Contents, Issues, Pull requests.
- **Read-only:** Commit statuses, Metadata.
- **No access:** everything else, **especially Actions, Workflows, Administration and Secrets**. The security model depends on this token being unable to start workflows or change them.
- Set an expiry and a calendar reminder.

Save it as the repository secret **`ARF_GH_TOKEN`** (Settings → Secrets and variables → Actions). Only publish jobs, which never run a model, receive it.

This token is what lets the coordinator trigger the other agents. GitHub blocks chain-triggering only for its built-in workflow token; actions taken with your token start the next workflow.

## 3. Add model credentials (repository secrets)

- **Claude:** `ANTHROPIC_API_KEY` from a dedicated workspace with a monthly spend limit (recommended), or `CLAUDE_CODE_OAUTH_TOKEN` (uses your subscription; generate with `claude setup-token`).
- **Codex:** `OPENAI_API_KEY`, from a dedicated project with a spend limit.

Model jobs hold these keys, so treat them as exposed to the model and cap what they can spend (see `.agents/SECURITY.md`).

## 4. Set variables (Settings → Secrets and variables → Actions → Variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARF_DIRECTOR` | none | **Required.** Your GitHub username. Only this account can run **ARF Director approval** |
| `ARF_CODEX_MODEL` | Codex's default | **Set this** to an exact model id so verdicts record which model reviewed |
| `ARF_CLAUDE_MODEL` | `claude-opus-5-5` | Claude model for implementing and standard/full reviews |
| `ARF_CODEX_LIGHT_MODEL`, `ARF_CLAUDE_LIGHT_MODEL` | none | Cheaper model for **low-risk** reviews. Recommended: this is where most of the review savings come from |
| `ARF_COORDINATOR_MODEL` | `claude-sonnet-5-5` | Coordination is mostly triage; a mid-size model is enough |
| `ARF_IMPLEMENTER` | `claude` | `claude` or `codex`. The reviewer is automatically the other one |
| `ARF_REVIEWER` | `auto` | Force `claude` or `codex`. If that matches the implementer's vendor, set `ARF_CLAUDE_REVIEW_MODEL` or `ARF_CODEX_REVIEW_MODEL` to a different model |
| `ARF_COORDINATOR` | `claude` | `claude` or `codex` |
| `ARF_BOOTSTRAP_CMD` | none | Installs dependencies so agents can run tests, e.g. `pip install -e .[dev]`. No secrets in it |
| `ARF_MAX_PARALLEL` | `2` | Issues in flight at once |
| `ARF_MAX_REVIEW_ROUNDS` | `3` | Reject/rework rounds before asking you |
| `ARF_AUTO_MERGE` | `none` | `low` or `medium`: auto-merge validated cross-model ACCEPTs up to that risk. High and critical always wait for you |
| `ARF_STALE_HOURS` | `24` | When in-flight work counts as stuck |
| `ARF_MAX_TURNS` | `80` | Turn cap for implementer runs (review caps follow risk) |

## 5. Create the labels

Actions tab → **ARF setup** → Run workflow.

## 6. Protect the default branch

Settings → Rules → Rulesets → New branch ruleset, targeting the default branch:

1. Require a pull request before merging, with 0 approvals (you can't approve your own PRs).
2. Require status checks to pass:
   - **`pr-metadata`**, from GitHub Actions.
   - **`arf/cross-model-review`**, with its source set to **GitHub Actions**. This matters: it stops a status written with your token from satisfying the gate.
   - Your project's CI jobs.
3. Block force pushes and restrict deletions.
4. **Leave the bypass list empty.** The rules then apply to your account, and so to every agent acting as you.

For `ARF_AUTO_MERGE`, also enable Settings → General → Allow auto-merge.

## 7. Pilot before relying on it

Run `docs/arf/PILOT.md` on a **disposable** repository first. It exercises the whole cycle and every trust boundary in real GitHub Actions, then tears down. After installing on a real repository, repeat P1 and N1–N4 there before giving the agents real work.

## 8. Let the agents fill in the repository profile

Open an issue: "Fill in `.agents/REPO_PROFILE.md` from the actual code, CI and docs." Label it `rules-change` and `agent:triage`. When the PR passes cross-model review, read it yourself, since every later agent relies on it. Then approve it: Actions → **ARF Director approval** (kind: rules) → Run workflow on the default branch, with the PR number and the full head SHA you read. Then merge.

## 9. How you work from now on

- **To give work:** open an issue (Agent task or Bug report template). One sentence is enough.
- **To see status:** the **ARF: Director briefing** issue. Pin it.
- **To decide something:** filter by `label:needs:director`. Answer in a comment, remove the label, and add `agent:rework` (PR) or `agent:triage` (issue).
- **To merge:** PRs labelled `ready-to-merge` have a validated cross-model ACCEPT at their current head.
- **To approve a rules change:** run **ARF Director approval** (kind: rules) with the PR number and head SHA.

GitHub doesn't notify you about your own actions, and every agent acts as you, so you won't get notifications for agent activity. The briefing issue and the `needs:director` filter replace them.

## Costs

Typical model calls per issue:

- **Low risk:** one coordinator pass, often shared with other new issues. One implementer run. One light review on the cheaper model.
- **Medium risk:** as low, but with a standard review.
- **High and critical:** a full review, and possibly rework rounds (capped).

PRs that fail the free checks (description, rules version, round cap) never reach a reviewer model. Scheduled coordinator runs stop before the model when nothing needs attention, and a burst of new issues collapses into one coordinator run.
