# Two-host Windows/SMB certification plan (not yet executed)

**Target approved in issue #5:** Windows coordinator, Windows client(s), SMB shared immutable object store. This plan is for a physical test rig, not evidence of certification.

## Host and share prerequisites

Host A: Windows 11 Pro or Windows Server with local NTFS authority database, coordinator service, dedicated service identity, restricted inbound firewall rules and stable time. Host B: separate Windows machine and user session. SMB share may be on a third Windows Server host or on A, but it must be accessed by B over a real network path. Capture Windows build, SMB dialect, share/server type, NTFS/ReFS filesystem, oplock/leasing settings, antivirus/backup agents, client network adapter and SMB signing/encryption settings. Never locate SQLite WAL files on the SMB share.

Separate writable staging ACLs from read-only committed-object ACLs; verify unauthorized users cannot modify or delete published objects. All participants must use the coordinator for authoritative writes; direct SMB journal modification is forbidden.

## Test matrix and acceptance

| Scenario | Fault injection | Required evidence and acceptance |
| --- | --- | --- |
| Simultaneous checkout | Barrier across two physical clients, 1000 attempts | Exactly one authoritative winner per generation; no duplicated committed revision |
| Revoked paused writer | Pause A after preflight, admin revokes, resume A | A cannot publish; B can acquire new generation; audit persisted |
| Coordinator unavailable | Stop service mid-request and during commit | No new commit acknowledged without durable authority; txid lookup resolves uncertain result after restart |
| SMB disconnection | Disconnect NIC, SMB session, cable; restore | No committed manifest references absent/unverified objects; fail closed |
| SMB server restart | Restart SMB host during staging and immediately after write | Verify SHA-256 and bytes of every committed blob; no silent revision loss |
| Local disk full | Fill coordinator disk to safe threshold in disposable VM | No partial authority commit, correct error, successful recovery after free space |
| Coordinator power interruption | Hard power off VM/host at pre-commit and post-commit points | Either absent or exactly one committed txid, no duplicate |
| Backup restore | Restore previous coordinator DB snapshot | All prior credentials invalidated via trusted epoch/high-water; never resume from old generation |
| Two-client reconnect | Cache one client offline through several commits | Ordered cursor replay or full rebuild; no gap accepted as current |
| Share tampering | Remove/change committed blob from unauthorized test identity | Access denied; if tampered by admin, hash check quarantines reads |
| Administrative access | Standard user vs privileged operator | Only authorized operator can override; audit identity, reason, old/new generation preserved |
| Performance | 10k/100k/1M revisions; 2 clients | Measure p50/p95 checkout, commit, cache replay, document revision list; define release thresholds with Director |

## Required artifacts

Collect synchronized client and coordinator logs with txid, epoch, generation, sequence and monotonic timestamps; Windows Event Logs, SMB server/client counters, durable hashes, journal snapshots, fault timestamps, pass/fail results and exact OS/build/share configuration. Do not log secrets. Preserve failed artifacts. Repeat tests on each supported SMB server configuration and after material changes to storage/locking settings.

**Status:** zero physical two-host Windows/SMB tests executed. Local GitHub Windows runners do not qualify as a two-host SMB rig. Production multi-user certification remains blocked.

## Extra owner-release and administrator-overlap scenarios

The existing Windows `WinError 32` incident (issue #10) and the later token-check-to-`unlink` race are **separate mechanisms**:

- **Transient Windows sharing violation:** a contender holds an open handle to `writer.lock` while its owner attempts `Path.unlink`. On Windows this may raise `PermissionError: [WinError 32]`; bounded retries with owner-token revalidation address this transient availability failure. QA ran five Windows NTFS trials with six independent processes each on Phase 2 `f3c54386` and found one winner and five conflicts in each, plus a passing injected transient-unlink test. These are local Windows results, **not SMB certification**.
- **Successor pathname deletion after revocation:** an old owner reads its own lock token, pauses immediately before calling `unlink`; administrator publishes journal revocation and moves the old lock to an evidence path; a successor acquires a **new** `writer.lock`. The old owner resumes the pathname unlink and **deletes the successor's lock** despite its earlier token recheck. Independently reproduced against Phase 2 `f3c54386` in [run 41387552435](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387552435) using local Ubuntu Python 3.13. This is a genuine source-level filesystem ordering defect, **not** a measured Windows SMB result. A safe release design must prevent it.

A certification rig must test both scenarios independently on actual Windows + SMB:

1. Pause an owner immediately after its final token recheck, just before the lock release call. Publish an administrative revocation, allow another client to acquire, then resume the old release. **Acceptance:** successor's lock and ownership remain intact, no split-brain writer authority.
2. Hold a lock-file inspection handle open on the second physical host while the owner attempts legitimate release. Inject bounded sharing violations (or use a controlled handle share mode). **Acceptance:** retries complete safely, or an explicit actionable error preserves the lock; no silent discard.
3. Crash the original writer during release and the administrator during revocation, before/after durable audit. **Acceptance:** no unaudited ownership transfer, no old-credential publish after committed revocation and no permanent unresolvable lock.
4. On reconnect, validate the new epoch/generation and a contiguous ordered journal before allowing cached work to publish; the client must not switch automatically to shared-folder standalone writes.

Record both clients' Windows build/SMB dialect, original and successor tokens, exclusive-create semantics, share ACLs, exact fault-injection barrier, journal sequence, committed txids, stale outcome, and surviving lock-file identity. Run independently with (a) coordinator stopped, (b) normal coordinator, and (c) coordinator restart; only **coordinator-authoritative** multiuser writes may be accepted.

**None of the above physical two-host scenarios have been executed.** Existing GitHub Windows hosted runners do not provide a separate authenticated SMB client/server topology or a real power-loss durability oracle.
