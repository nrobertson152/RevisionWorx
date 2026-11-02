# RevisionWorx QA strategy

Target: `phase1-foundation`; branch: `qa/testing-infrastructure`.

## Integrity requirements

Preserve unmanaged files during adoption; refuse conflicting checkout authorities, unpublished revisions, and journal corruption; distinguish authoritative `.revisionworx` journal from disposable local SQLite caches. Never claim unmanaged archives were indexed. Fail closed when write authority is ambiguous.

## Phase 1 verified implementation versus future features

Current Phase 1 uses Python >=3.10, Tkinter, journal-first `Workspace`, single writer, bounded adoption and local SQLite indexing. The developer's report of 95 passed/1 skipped is not independent QA evidence. Revisions, checkouts, parts, dependency graph, BOM, native CAD parsing, backups and production-safe SMB/NFS multi-writer operation are not implemented and remain NOT RUN.

## Testing layers

Unit: path validation, hashing, serialization, later graph and numbering. Integration: adoption, journal replay, local indexes and crashes. Adversarial: ENOSPC, EACCES, missing parents, journal gaps, abandoned temp files, external changes. Multiprocess: barriers and authoritative winner count. Network: real SMB/NFS shares from separate hosts, fault injection, reconnect, server restart, stale cache, fencing. Platform: Linux and Windows, Python 3.10/3.13, GUI launch under display/Xvfb. Benchmark: 10,000 projects, 2 million docs, 500,000 dependencies; separate database generation from real application timing.

## P0 release blockers

Unmanaged file mutation; false revision commit; lost immutable release; conflicting checkout; stale writer after administrative lock recovery; corrupted authoritative metadata silently accepted; inconsistent backup restoration; false completeness claims. All block release until empirically resolved. A missing API is NOT RUN, not PASS.

## Immediate executable tests

Use `tests/qa` for independent synthetic workspaces (customer/year/product layouts), byte/mtime/path snapshots, scoped adoption, startup non-traversal, injected journal publication failure and replay. Run `python -m pytest -q tests/qa` then `python -m pytest -q`. Record OS, commit, Python, filesystem, counts and skips.

## Network qualification

Synchronize 2, 3 and 10 clients on actual SMB 3.x and NFSv4 shares: one exclusive checkout winner. Pause old writer, break its lock, resume it: fencing must prevent publication. Repeat race trials at least 1,000 times per supported configuration; test server reboot, lost notifications and disconnect during check-in. Local process races never prove network safety. [QA coordination issue](https://github.com/nrobertson152/RevisionWorx/issues/1) documents missing transaction/fencing interfaces.

## Dependency verification

Create directed/cyclic, direct/indirect, revision-specific, unverified and cross-project edges. Where-Used must show exact known downstream relationships and unmanaged coverage warnings. Before You Revise must not classify all affected assemblies as incompatible. Native CAD filenames alone never prove reference extraction. OpenSchematix current fixtures prove format recognition only.

No test, feature, platform, performance result or release may be declared verified without an actual execution record.
