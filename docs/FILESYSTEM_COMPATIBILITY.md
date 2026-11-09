# Filesystem Compatibility and Deployment Gate

| Target | Expected features, subject to configuration | Known concern | Status |
|---|---|---|---|
| Local ext4/XFS | Atomic directory create, same-filesystem rename; POSIX fsync | Hardware and mount durability caveats | Local test only |
| Windows NTFS | Exclusive creation, share mode locking, FlushFileBuffers | Open-handle `WinError 32` and token-read/pathname-unlink TOCTOU | **Partial local CI**, not fully certified |
| Windows Server SMB | Server-mediated namespace operations and SMB locks | Oplocks, leases, reconnect and stable-write policy | Untested |
| Linux Samba SMB | SMB semantics depend on Samba server and clients | Native Linux process bypass, caching, rename/share modes | Untested |
| NFSv3 | Server-side create/rename with configuration caveats | NLM locks, caches, recovery, stable writes | Unsupported |
| NFSv4 | Stateful protocol, locks and delegations | Lease reclaim, failover, visibility and COMMIT durability | Unsupported |
| Generic NAS | Vendor-dependent | Undocumented caching and power-loss handling | Unsupported |

**Do not treat typical protocol features as portable guarantees.** A successful atomic rename does not mean a durable rename after server power loss. File and directory fsync semantics vary across operating systems and network mounts; a read-after-write from one cached client does not prove visibility to another. Advisory locking does not prevent out-of-band CAD or filesystem writes. An exclusive file handle may be lost or retain ambiguous state during reconnection.

## Required certification

Test two or more physical Windows/Linux hosts against exact Windows Server SMB and Samba versions and configurations, then each requested NFS/NAS target independently. Run 1000+ synchronized checkout and commit races. Inject client kill, router disconnect/reconnect, server service restart, power cut, disk full, quota, directory listing staleness, open-handle rename errors, ACL changes, journal truncation, changed share permissions, stale clients and live backup restore. Verify both uniqueness and recoverable durability after each restart. Capture exact client mount flags, SMB dialect, NAS firmware, write-cache settings and OS versions. Reject service-free multiwriter mode until its exact deployment passes certification.

The implemented isolated sample uses SQLite on a **local** disk rather than a filesystem mutex or durable blob-manifest protocol. Its Windows/Linux process tests are about SQLite serialization, not network-share fsync, rename, locking, or durability. The separate Phase 1 application uses POSIX directory fsync and a Windows rename alternative; those have not been certified across real Windows Server/Samba/NFS shares.

## Verified Windows-specific findings versus remaining unknowns (issue #10)

**Actual single-host Windows NTFS evidence:** QA ran [focused GitHub Actions run 41387311010](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311010) against Phase 2 `f3c54386` with **five trials of six independent checkout workers (30 attempts)**. Each trial yielded one successful checkout and five conflicts with no orphaned lock, and the injected transient `WinError 32` close failure recovered via bounded retry. The environment was hosted Windows Server 2025, build 10.0.26100, Python 3.13.17. This provides bounded, local Windows lock-release availability evidence, **not** Windows SMB certification.

**Separate actual-source integrity failure:** [research run 41387552435](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387552435) verified against Phase 2 `f3c54386` on Ubuntu that an old writer's `Path.unlink(writer.lock)` can delete a **successor** lock if the admin revokes/moves the original and the successor acquires between the old writer's token read and unlink. The pre-unlink token recheck and retry do not make pathname deletion atomic. This interleaving is not specific to Windows error 32, and no claim is made that it has been physically reproduced on SMB. See issue #10 and `prototype/test_phase2_lock_release_race.py`.

For the approved **Windows-hosted coordinator + SMB** design, the coordinator's *transactional lease generation* must be authoritative; files on the share are immutable content, not lock records that independently authorize publication. Any standalone local lock-file release and administrative override protocol needs its **own** correctness review. Test both the transient sharing violation and the successor-release race on a real Windows machine and on the supported SMB topology, and preserve evidence of both. Never infer the second is fixed because a repeated non-admin six-process race passed.

**Physical two-host SMB certification executed:** **NONE**. No claims are made about native Windows SMB cross-host file identity, durability, signed/unsigned sessions, server cache flush, share ACL enforcement or power-cut retention.
