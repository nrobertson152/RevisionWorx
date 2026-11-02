# Concurrency and failure injection qualification

## Environments

Use disposable workspaces on Linux ext4/XFS and Windows NTFS, then independent client hosts against specifically versioned SMB 3.x (Windows Server or Samba) and NFSv4 shares. Record mount options, server/client caches, durability and fsync semantics. No tests against company data.

## Deterministic races

1. Barrier-synchronize 2, 3 and 10 clients requesting the same exclusive document checkout. Exactly one may succeed. Confirm against authoritative server journal, not local caches.
2. Pause owner A before commit; break its lock with audit evidence; authorize B; resume A. Fencing must reject A before publication.
3. Crash while checkout held. Admin recovery must preserve evidence and reconcile stale local working copies.
4. Disconnect before and during journal publication. Query durable transaction ID outcome on reconnect. Never acknowledge an uncommitted revision.
5. Drop or duplicate notifications and reconnect stale clients. Rebuild or replay journal sequences and verify identical state.
6. Induce server restart, permission revocation and disk exhaustion. All writes must fail closed and recover to one consistent state.

Run at least 1,000 repeat races per supported real share configuration. Local process tests are necessary but not sufficient.

## Faults at each transaction phase

At prepare, temp creation, write, fsync, publish, record application and checkpoint, inject ENOSPC, EACCES, ENOENT, partial write, SIGKILL and network failure with explicit deterministic barriers. After restart, inspect durable journal, blobs, digests, orphan staging and externally visible status. Assert either committed-and-recoverable or uncommitted-and-not-visible; never a false success.

## Required contracts

Claude must specify share support, transaction IDs and durable outcomes, publication failpoints, fencing tokens, checkout authority and snapshot backup consistency. The coordination request is [issue #1](https://github.com/nrobertson152/RevisionWorx/issues/1). Until the protocol is verified, network multi-writer operation remains blocked.
