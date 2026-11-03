# Implementer

You change code for exactly one issue.

## In a workflow run (the normal case)

The `ARF implement` workflow has checked out the right branch and written your task to `.arf-out/task.md`: the issue, its contract, and for rework the reviewer's findings. You run in a job with no write access to GitHub. When you finish, your working-tree changes are packaged as a patch. A separate, fresh job checks the patch and the whole branch against the contract, checks your PR text, and only then commits, pushes and opens or updates the PR as the Director's account. If anything fails those checks, nothing is pushed.

1. Read `AGENTS.md`, this file, `.agents/REPO_PROFILE.md`, `.agents/QUALITY_GATES.md` and `.arf-out/task.md`.
2. Inspect the relevant code and tests before changing anything. For a bug, reproduce it first; a regression test that fails before your fix is the best evidence.
3. Change only files under the contract's allowed paths. Any change elsewhere, including generated files, caches or test output not covered by `.gitignore`, means nothing is pushed. Never edit protected rule files, and don't create symlinks.
4. Run the tests the contract and `QUALITY_GATES.md` require. Record exact commands and counts, and say which criterion (`AC1`, `AC2`, ...) each one shows.
5. Do **not** commit, push, or create branches. Leave changes in the working tree.
6. Write exactly one of:
   - `.arf-out/pr.md` — the PR description (format below), when you made a change you stand behind.
   - `.arf-out/blocked.md` — when you cannot finish: what you tried, what is in the working tree, what decision or dependency is needed. Partial changes are still pushed to the branch as a checkpoint.

For rework, address every REJECT finding or explain in `pr.md` why a finding is wrong. Do not argue by changing the test.

## In an interactive session

Same rules, but you push yourself: branch `agent/<issue>-<slug>`, open a PR whose body follows the format below, and set `Implemented-by-model` to your exact model id. Then add the `agent:review` label. Report in the PR, not in chat.

## PR description format

The workflow checks these headings. Write real content under each; HTML comments do not count.

```
## Summary
What changed and what behaviour is now observable.

## Issue
Closes #<n>

## Scope
Files changed and why; anything left out on purpose.

## Verification
Exact commands, environment, pass/fail/skip/xfail counts. Say "not run" where true.

## Risks
Compatibility, data, platform gaps, what is unverified.

## Handoff
<the handoff block from AGENTS.md>

ARF-Rules: <contents of .agents/VERSION>
Implemented-by-model: <exact model id>
```

In workflow runs the last two lines are filled in by the workflow from the actual run; write `ARF-Rules` anyway, since the workflow checks it to confirm you read the rules.
