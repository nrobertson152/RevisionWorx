# Mandatory agent assignment checkout / check-in (proposed enforcement)

Every interactive or scheduled engineering agent MUST acquire the same GitHub assignment lock before changing code, opening a work PR, or writing an assignment progress log. This applies to Claude A/B/C/D and all ChatGPT agents. Do not treat a status comment or separate branch as a lock.

## Atomic lock

Use `tools/agent_assignment_lock.py` with a GitHub token scoped to this repository (Contents: write). Lock acquisition uses GitHub's atomic **create ref** operation: `POST /repos/{owner}/{repo}/git/refs` with a fixed role ref `refs/heads/agent-locks/<role>`. A unique metadata-only Git commit, with parent `main`, records owner, task, session UUID and acquisition timestamp. If the ref already exists, acquisition FAILS CLOSED; no agent may assume ownership or overwrite it. Ref creation is the serialization point. Each role has one exclusive lock, shared by scheduled and interactive sessions.

## Mandatory workflow

1. Read GitHub orders and check the relevant role lock. If it exists, do not work on that role. Choose a different **unlocked** task or report blocked.
2. Run `python tools/agent_assignment_lock.py checkout --role core --owner <session-identifier> --task <issue-or-PR>`. Retain the returned receipt (role, lock SHA, owner). No checkout = NO assignment modifications.
3. Work only on your authorized branch. Check the lock SHA before each push or progress update. A missing or changed lock means stop; do not push.
4. In `finally` / before ending: commit and push work, update PR and progress log with exact SHAs, report remaining work, then run `python tools/agent_assignment_lock.py checkin --role core --sha <receipt-sha>`. A successful check-in is required before saying the assignment is complete.
5. If check-in fails, explicitly report **LOCK STILL HELD** and the exact lock ref/SHA. Do not silently abandon it. If a session crashes, the lock remains; only the Director may inspect and manually clear a stale lock. **No automatic TTL-based stealing**: an expired timestamp is advisory, not permission to take ownership.

Role names: `core`, `gui`, `qa`, `distributed`, `integration`, `bom`, `change-control`. A separate session of the same role cannot proceed while the ref exists. Agents may independently read, inspect and test upstream without an assignment checkout only if they make no assignment changes or claims of ownership.

## Boundaries and limitations

The lock is **cooperative**, not an operating-system or GitHub permissions barrier: an agent with repository write permission can still ignore it, and GitHub does not provide conditional ref deletion. The check-in client verifies SHA immediately before DELETE, but concurrent unauthorized ref mutation remains a race. To make bypass impossible, restrict direct repository writes and route all agent pushes through a protected GitHub Actions gate with an exclusive concurrency group, or enforce a GitHub ruleset/required status check. This protocol does not claim those controls are already deployed. Never use `create_branch` (which may update an existing branch) as a lock acquisition primitive.

All API calls use HTTPS to GitHub for **development coordination only**. RevisionWorx application runtime remains offline and has no network dependencies.
