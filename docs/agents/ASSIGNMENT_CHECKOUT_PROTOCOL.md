# RevisionWorx — simple assignment checkout/check-in

**Director policy:** Applies to all Claude and ChatGPT agents, both interactive and scheduled. This is a cooperative coordination convention, not an enforced distributed lock or the application's CAD checkout system.

## Check out before starting

1. Read the assigned issue/PR and its most recent comments. Check whether another agent has posted an active checkout.
2. If another session has checked out the same task, **do not start that task**. Choose a different task or report that you are waiting.
3. Otherwise, post a comment on the assigned issue/PR:
   `CHECKED OUT — Agent: <name>; Task: <scope>; Started: <UTC time>; Branch: <branch>; Starting SHA: <sha>.`
4. Work only on your authorized development branch. Do not duplicate work or overwrite another agent's commits.

## Check in before finishing

1. Commit and push completed changes to your own development branch. Run tests when possible.
2. Post a comment on the same issue/PR:
   `CHECKED IN — Agent: <name>; Task: <scope>; Ending SHA: <sha>; Tests: <command and result>; Remaining: <next step/blockers>.`
3. If work cannot be pushed or tests cannot run, explicitly say so in the check-in comment. Never claim work was committed or tested if it was not.
4. Check in even if no changes were made or the task was blocked. Do not silently leave an assignment checked out.

## Interrupted sessions

If an agent crashes before checking in, its checkout comment remains active. The Director can confirm that the session has stopped and post `CHECKOUT RELEASED — <task>; authorized by Director` to free the assignment. Other agents must not silently take over.

## Limits

This is **trust-based**. Comments are not atomic locks, so simultaneous checkouts are theoretically possible. Agents must check recent comments immediately before posting and respect existing checkouts. GitHub branch protections and separate working branches remain necessary. No extra Git refs, tokens, services, or automated lock recovery are required.
