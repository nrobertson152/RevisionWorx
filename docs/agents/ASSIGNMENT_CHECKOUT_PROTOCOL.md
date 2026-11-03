# RevisionWorx — mandatory engineering assignment checkout/check-in

**Status:** Director policy. Applies to interactive sessions, scheduled automations, Claude, ChatGPT and other agents. This coordinates *engineering tasks*, not RevisionWorx's product-level CAD checkout.

## Principle

A task is exclusively owned by the session holding its GitHub checkout ref. **No code edits, branch pushes or issue/PR mutations for an assignment without a successful checkout.** Reading and reporting are allowed without a checkout. All sessions, including scheduled tasks, must use the same protocol. A comment, issue assignee, local file, timestamp or ordinary label is not an exclusive lock.

## Atomic checkout

GitHub's create-ref operation is atomic: only one session can create a particular ref. Use a fixed ref for each assignment, not a session-specific ref:

- `refs/heads/assignment-locks/core`
- `refs/heads/assignment-locks/gui`
- `refs/heads/assignment-locks/qa`
- `refs/heads/assignment-locks/distributed`
- `refs/heads/assignment-locks/integration`
- `refs/heads/assignment-locks/bom`
- `refs/heads/assignment-locks/change-control`

For a finer-grained task shared across roles, use a single agreed `assignment-locks/issue-<number>` ref *in addition* to the role ref. Always acquire multiple locks in lexicographic order to avoid deadlock. Never reuse another role's lock as your own.

1. Generate a fresh random 128-bit session identifier locally. Prepare actor, assignment, issue/PR, current source SHA, start time and expected deliverable; do not post a checkout comment until the atomic ref creation succeeds.
2. Before any mutation, atomically **create** the fixed lock ref from a known existing commit with GitHub's create-ref API (`POST /repos/{owner}/{repo}/git/refs`) or an equivalent Git push that fails if the remote ref exists. A successful new-ref creation is the exclusive checkout. After acquisition, post the prepared checkout comment including the session identifier and lock SHA. **Do not** use any connector operation that may update an existing branch; do not force-push or overwrite the lock ref. If creation fails or existence cannot be verified, treat the task as locked and do no mutations.
3. Re-read the remote ref and confirm it still points to the created SHA before every push or material external write. Keep the lock ref immutable during the session.
4. If the lock already exists, do not work on that assignment. Report the holder when known, choose an independently unlocked task, or perform read-only review.
5. The session must not rely on its own clock to reclaim an expired lock. A stale lock is **fail-closed** until the Director explicitly authorizes recovery after checking for active sessions. This prevents a long-running agent from being displaced by a second session.

## Mandatory check-in (finally block)

Every session must attempt check-in before finishing, including after tests fail or work is blocked:

1. Save and push completed work **only to its own authorized development branch**. If unable to push, explicitly report uncommitted/unpushed work and do not claim completion.
2. Post a check-in record on its assigned PR/issue: session ID, checked-out assignment, exact starting and ending SHAs, commit IDs, test command/results, unfinished tasks, blockers, next handoff, and whether the lock was released.
3. Release only the exact fixed lock ref acquired by this session, using a deletion that is **conditional on the expected SHA** (Git `--force-with-lease=refs/heads/assignment-locks/<role>:<expected_sha>` with an explicit deletion refspec, or a service that provides equivalent compare-and-delete). GitHub's ordinary DELETE ref endpoint is not compare-and-delete; if used, first verify ownership and understand the residual race. **Never delete an unfamiliar ref or another session's lock.**
4. Verify the remote lock ref is absent. If release or verification fails, mark the assignment **LOCKED / MANUAL RECOVERY REQUIRED** and notify the Director. Never falsely claim check-in.
5. On process crash, no finally block is guaranteed. The persisted lock intentionally blocks later agents until manual recovery. Check-in is mandatory agent behavior, **not a guarantee of cleanup after power loss or tool termination**.

## Safety and integration

- Never automatically clear stale locks based only on age.
- Do not merge, release, rebase, retarget, or modify other agents' branches without Director authorization.
- All agents must honor lock refs created by other sessions. If a task cannot create/delete refs with available credentials, it must not mutate that assignment. Report the missing capability instead.
- The lock only coordinates cooperating agents. GitHub repository branch permissions and protection remain the real security boundary.
- Keep progress in GitHub, not only in chat. Do not commit lock state to source files; Git refs are the lock authority.
