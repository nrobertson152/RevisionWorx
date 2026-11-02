# Architecture Decision — Administrative Lock Override and Fencing

**Status:** Project Director directive; mandatory for future lock/check-out and revision publication design.
**Owners:** Lead Architect (implementation), Distributed Systems Engineer (protocol review and independent tests), QA Engineer (regression verification).
**Scope:** RevisionWorx authoritative shared-folder writer locks, checkouts, transaction publication, and recovery.

## Decision
**Preserve administrative lock breaking.** Administrators must be able to forcibly release abandoned or problematic checkouts, including after crashes or when an engineer is unavailable. Do not remove or disable administrative override to conceal the stale-writer vulnerability.

## Mandatory safety properties
1. Every exclusive checkout or writer lease has an authoritative monotonically increasing fencing generation or an equivalent revocation-safe authority mechanism.
2. An administrative override atomically revokes the previous owner's publication authority and advances the relevant generation. A still-running or reconnected prior writer must be fenced out.
3. The publication/commit operation validates the writer's current authority **atomically with** authoritative publication. A separate preflight generation check is insufficient because of time-of-check/time-of-use races.
4. Stale writers must be rejected even if they retain an old lock file, token, cached state, open file descriptor, or disconnected working copy. Journal sequence checks alone are not sufficient proof of fencing.
5. Every override is auditable, including administrator identity (where reliably available), timestamp, resource, prior owner, reason, previous and new authority generation, and outcome. Never invent an authenticated administrator identity when no identity/authentication facility exists; document that limitation and establish an appropriate administrative authorization mechanism.
6. Revoked writers retain their local work for recovery, manual reconciliation, or a new authorized revision. Never silently overwrite another engineer's changes.
7. Crash recovery must preserve monotonic authority generations; rollback, journal replay, or restored caches must not resurrect revoked credentials.
8. The system must fail closed when authoritative fencing cannot be established. Do not claim production multiuser safety on SMB/NFS without verified filesystem guarantees and actual cross-host tests.

## Required verification
- An active writer cannot publish after its lock is forcibly broken.
- An override racing with a commit yields one well-defined serial order, never both a revoked writer's commit and an incompatible successor commit.
- Two contenders cannot both acquire authoritative checkout ownership.
- Reconnect, restart, cache rebuild, interrupted override, disk-full, and journal replay do not restore stale publication authority.
- Local multiprocess tests and actual SMB/NFS cross-host tests are reported separately; unexecuted tests are explicitly labeled.

## Implementation and coordination
- **Lead Architect:** Incorporate this decision into the Phase 1/2 authoritative store and checkout architecture. Preserve the administrator override workflow and reason/audit requirements.
- **Distributed Systems Engineer:** Specify a concrete fencing and atomic-publication protocol compatible with the actual Phase 1 implementation, assess the filesystem guarantees, prototype and test it independently, and document remaining limitations.
- **QA Engineer:** Add deterministic race, crash-recovery, and stale-writer regression tests, and identify network-share certification gaps.
- Coordinate technical findings through GitHub issues and pull requests. Do not merge or release without Project Director approval.
- Update relevant architecture, concurrency, failure-recovery and progress documentation with the decision and verified outcomes.

**Acceptance criterion:** Administrative lock override remains available, while a revoked writer has no path to publish authoritative changes.