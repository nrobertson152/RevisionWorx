# ADR-019 — Unreadable abandoned lock evidence defeats token-only fencing

**Status:** Confirmed P0 integrity counterexample against Phase 2 d656884b39401134149273dd0121ef620b76eb8e (Claude-owned production code). Distributed Systems research branch only. No merge, retarget, production code edit or claim of physical SMB certification.

## Source-level cause

During an interrupted release, the old owner A can move a valid successor B's writer.lock into a private .releasing-* path. A new contender C then creates writer.lock. ADR-019's Workspace._fence_orphaned_releases scans every .releasing-* path, reads the JSON holder token, records a durable workspace.lock.break revocation for each token, and moves raw evidence into writer.lock.orphaned-* custody before allowing C to publish.

However, if atomicio.read_json(orphan) raises OSError or ValueError, the method substitutes holder={} and **skips the revocation** because there is no token. It still moves the file to evidence custody and grants C effective write authority. This behavior presumes the old displaced B cannot complete a previously preflighted in-flight commit.

That presumption is false: B may have passed _check_lock_still_held while writer.lock still named B. Its later journal publication is only logically fenced if a **prior journal entry revokes B**, which is impossible to produce from an unreadable orphan under the current token-only model.

## Deterministic tested interleaving and actual effects

1. Administrator revokes A; B acquires its own valid token and writer.lock.
2. B starts commit and successfully passes its **last lock-token preflight**.
3. The abandoned release displaces B's lock into .releasing-corrupt; fault injection makes the holder bytes malformed JSON.
4. C acquires writer.lock; orphan scanner archives malformed evidence without journaling any B-token revocation.
5. C publishes project C and releases.
6. The paused B resumes and publishes project B. Both records become effective and visible; transaction_outcome for B is committed; B's token is absent from the journal revocation set.

**Executed:** [CI run 41390605906](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390605906), exact source d656884b, research overlay 9df169bd. Both **Ubuntu and Windows Python 3.13.17** actual Phase 2 jobs completed successfully; the explicitly named test test_unreadable_orphan_permits_two_effective_writers_UNSAFE_reproduction **passed, demonstrating the vulnerability**. A separate positive safety assertion is deliberately marked expected failure on both OS. These are local filesystem tests with injected malformed bytes, not measured physical spontaneous corruption or SMB power interruption.

**Severity:** P0 for RevisionWorx engineering-data integrity. Prior QA local acceptance of well-formed orphan recovery remains valid, but does not cover unknown-owner recovery. The test named malformed_orphan_archived_without_token_fence_UNSAFE_control confirms current permissive behavior and must never be counted as safety acceptance.

## Safety requirements for Claude and independent QA

- An unreadable, truncated or untrusted .releasing-* file that might carry an active displaced writer must not permit a new holder to publish merely because token extraction failed.
- Keep immutable or non-destructively moved original bytes for forensic inspection, classify the workspace as **recovery blocked**, and record a durable audit trail of the ambiguity before reopening it.
- A simple error on C's acquisition is necessary to avoid immediate dual-writer authority, but not sufficient to justify a later admin break followed by a new holder. A previously preflighted unknown B can still publish unless the recovery protocol creates a **global or epoch-scoped journal fencing barrier** that affects old tokens, or a supervised procedure can prove those old sessions stopped. Current token-specific revocations cannot express an unknown identity.
- If the journal format changes to include authority epochs/generations and a global fence, ensure stale physically published entries are ignored by all readers, recovery, index rebuild and transaction_outcome. Preserve legacy journal safely; no silent auto-migration or truncation.
- QA should first preserve the independent strict positive oracle: the two effective records must NEVER coexist after a new holder commits. Run on Linux and Windows at exact corrected Phase 2 SHA, test multiple restarts/unknown owner files, and report whether the correction sacrifices availability. Actual SMB server caching, share ACLs and power-loss behavior remain NOT EXECUTED.

**Ownership:** Claude fixes production ADR-019. QA performs independent safety acceptance. Distributed Systems only supplies reproducible local protocol counterexample and design constraints.
