# Distributed Systems Engineer — Shared Filesystem and Distributed Systems Progress

**Session:** 2026-11-01  
**Owner:** Independent distributed systems / filesystem reliability research  
**Branch:** `research/shared-filesystem-protocol`  
**Status:** Protocol research and specification in progress; not approved for production.

## Repository baseline inspected
Read `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_PROGRESS.md` and main-branch file listing. The inspected baseline contains specifications but no implemented shared storage, synchronization, transaction or checkout subsystem.

## Completed and committed
- `docs/SHARED_FILESYSTEM_ARCHITECTURE.md`: authority/storage/cache separation, invariants, coordinator recommendation, interface.
- `docs/CONCURRENCY_PROTOCOL.md`: checkout and revision transaction state machines, fencing conditions and idempotent recovery.
- `docs/FAILURE_RECOVERY.md`: scenario matrix, detection and recovery.
- `docs/FILESYSTEM_COMPATIBILITY.md`: SMB/NFS/Windows/Linux/NAS assumptions and explicit certification gate.
- `prototype/README.md`: isolated prototype scope and limitations.

Commits on research branch (via GitHub contents API): `25115ea5a62de7740a545d613bb2b7f4f33e2412`, `93f5e43eafcc3cb85a3c34b0c73747b059ea2898`, `a4e0c3924bcae0bac9662c1d7ee492edcf9078fa`, `b8380a590ce6b6bd802675a0ffb2abd8ac9a5507`, `b0b5584850d01762cb56caffd4939d3336c749f8`.

## Verified tests
**No executable prototype committed or tests executed in this GitHub research branch yet.** Any previous informal reference to five tests passing is **not a verifiable result** and must not be treated as evidence. Real SMB, Samba, NFS, NAS, mixed OS and power-loss tests have not been run.

## Architecture decision
**Recommend B, optional on-premise LAN coordinator**, with a locally durable coordinator database and immutable shared blobs, for production safety and simple three-engineer operation. Architecture A is contingent on environment-specific cross-host, restart and durability certification; unproven mounts must fail closed. Never use local SQLite indexes as checkout or check-in authority. A shared network folder alone does not supply a multi-record ACID transaction.

## Unresolved problems
- Implement isolated demonstrator and independent-process fault-injection tests.
- Prove cross-machine mutex and durability behavior for each supported network-share configuration.
- Specify monotonic transaction sequence, fencing generations and epoch changes in production interfaces.
- Verify fsync, atomic marker and server power-loss recovery semantics on supported hosts.
- Define audited manual recovery without unsafe lock stealing, backup-restore quiescence and snapshot watermark.
- Implement authoritative transaction journal replay rather than trusting cached heads.
- Coordinate architectural selection with Claude before integration.

## Exact next steps
1. Commit isolated `prototype/` Python implementation and reproducible `unittest` suite on this branch.
2. Execute tests on actual implementation and record commands, environment, pass/fail counts and any limitations.
3. Extend tests for concurrent publishers, crash points, gap detection and stale checkout prevention.
4. Open or update draft PR targeting `main`, clearly identifying implementation status and unverified conditions.
5. Share the Architecture B recommendation through a GitHub issue for Claude.
6. Update this log at the end of the next development session with newly verified results and commit hashes.

No main-branch files modified; no merge performed.

## Role identification migration (2026-11-01)
- Progress log renamed to `docs/agents/DISTRIBUTED_SYSTEMS_PROGRESS.md` on existing research branch.
- Historical commits and findings preserved; previous log path removed.
- Role name for all future GitHub coordination: **Distributed Systems Engineer**.
- Draft PR #4 updated to role-based identification; Lead Architect retains application architecture ownership.

## Standing orders / Phase 1 inspection — 2026-11-01
- Read `docs/agents/README.md` and `docs/agents/DISTRIBUTED_SYSTEMS_ORDERS.md` from `main`.
- Inspected `phase1-foundation` versions of `docs/ARCHITECTURE.md`, `revisionworx/atomicio.py`, `revisionworx/workspace.py`, and `revisionworx/index.py`.
- Actual Phase 1 implements POSIX link-based exclusive journal publication, Windows rename alternative, temp-file fsync, directory fsync on POSIX, writer.lock token, manual lock break, journal recovery and local SQLite incremental cache.
- **High-severity confirmed code-path risk:** `Workspace.break_writer_lock` checks token then `os.replace` moves the lock without a coordination barrier; a previously opened `Workspace` checks only `self.writable` in `_require_writable`, not current token/epoch. Its `commit` may still publish after revocation. The Phase 1 architecture explicitly acknowledges this.
- **Additional review concern (not yet reproduced):** `LocalIndex.rebuild()` captures journal head before iterating materialized records and pending overlay. Concurrent commits may cause index contents newer than the saved cursor, so rebuild consistency should be tested under concurrent publication. This is a hypothesis, not a verified defect.
- No executable tests run this session. No cross-host behavior validated. No claims of production readiness.
- Next: build an isolated deterministic regression/fault-injection harness for stale-writer revocation and cache rebuild concurrency; record actual results; coordinate protocol interface with Lead Architect.
