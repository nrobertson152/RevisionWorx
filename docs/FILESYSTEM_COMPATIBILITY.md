# Filesystem Compatibility and Deployment Gate

| Target | Expected features, subject to configuration | Known concern | Status |
|---|---|---|---|
| Local ext4/XFS | Atomic directory create, same-filesystem rename; POSIX fsync | Hardware and mount durability caveats | Local test only |
| Windows NTFS | Exclusive creation, share mode locking, FlushFileBuffers | Rename while open and flush behavior differ | Untested |
| Windows Server SMB | Server-mediated namespace operations and SMB locks | Oplocks, leases, reconnect and stable-write policy | Untested |
| Linux Samba SMB | SMB semantics depend on Samba server and clients | Native Linux process bypass, caching, rename/share modes | Untested |
| NFSv3 | Server-side create/rename with configuration caveats | NLM locks, caches, recovery, stable writes | Unsupported |
| NFSv4 | Stateful protocol, locks and delegations | Lease reclaim, failover, visibility and COMMIT durability | Unsupported |
| Generic NAS | Vendor-dependent | Undocumented caching and power-loss handling | Unsupported |

**Do not treat typical protocol features as portable guarantees.** A successful atomic rename does not mean a durable rename after server power loss. File and directory fsync semantics vary across operating systems and network mounts; a read-after-write from one cached client does not prove visibility to another. Advisory locking does not prevent out-of-band CAD or filesystem writes. An exclusive file handle may be lost or retain ambiguous state during reconnection.

## Required certification

Test two or more physical Windows/Linux hosts against exact Windows Server SMB and Samba versions and configurations, then each requested NFS/NAS target independently. Run 1000+ synchronized checkout and commit races. Inject client kill, router disconnect/reconnect, server service restart, power cut, disk full, quota, directory listing staleness, open-handle rename errors, ACL changes, journal truncation, changed share permissions, stale clients and live backup restore. Verify both uniqueness and recoverable durability after each restart. Capture exact client mount flags, SMB dialect, NAS firmware, write-cache settings and OS versions. Reject service-free multiwriter mode until its exact deployment passes certification.

The implemented isolated sample uses SQLite on a **local** disk rather than a filesystem mutex or durable blob-manifest protocol. Its Windows/Linux process tests are about SQLite serialization, not network-share fsync, rename, locking, or durability. The separate Phase 1 application uses POSIX directory fsync and a Windows rename alternative; those have not been certified across real Windows Server/Samba/NFS shares.
