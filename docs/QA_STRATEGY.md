# RevisionWorx QA strategy

QA development branch: `qa/testing-infrastructure` (draft PR #3 currently based on `phase1-foundation`).\nReview targets: `phase1-foundation` (PR #6) and stacked `phase2-documents` (PR #7).\nThe Lead Architect owns integration and protocol choices; QA independently evaluates both.

## Integrity requirements

Preserve unmanaged files during adoption; refuse conflicting checkout authorities, unpublished revisions, and journal corruption; distinguish authoritative `.revisionworx` journal from disposable local SQLite caches. Never claim unmanaged archives were indexed. Fail closed when write authority is ambiguous.

## Implemented code and independent execution status

Phase 1 (PR #6) now uses Python >=3.10, a PySide6 GUI, journal-first `Workspace`, single-writer lock checks, transaction outcome IDs, journal checksums, verify/repair, adoption, and disposable SQLite. Phase 2 (PR #7) adds document registry, immutable content snapshots and revision records, lifecycle, checkout/check-in, CLI and a Qt Documents tab. Parts, dependency graph, BOM, native CAD parsing, backups and production-safe SMB/NFS multi-writer operation remain unimplemented or unverified.

Claude reports 118 passed / 1 Qt skipped for Phase 1 and 151 passed / 1 Qt skipped for Phase 2, both Linux. Claude also ran the earlier QA suite: 16/16 passed against its original Phase 1 base and 15/16 against Phase 2, with one expected contract mismatch. These are **Lead Architect-reported runs, not independent QA execution**. The QA PR updates the contract-mismatch test but has not independently executed it. On Phase 1 the new Phase 2-specific tests skip, and skipped tests do not certify the feature. The Qt widgets have not run, including offscreen; Windows and real network shares are unverified.

## Testing layers

Unit: path validation, hashing, serialization, later graph and numbering. Integration: adoption, journal replay, local indexes and crashes. Adversarial: ENOSPC, EACCES, missing parents, journal gaps, abandoned temp files, external changes. Multiprocess: barriers and authoritative winner count. Network: real SMB/NFS shares from separate hosts, fault injection, reconnect, server restart, stale cache, fencing. Platform: Linux and Windows, Python 3.10/3.13, PySide6 offscreen GUI launch plus Windows desktop smoke; do not treat skipped Qt tests as coverage. Benchmark: 10,000 projects, 2 million docs, 500,000 dependencies; separate database generation from real application timing.

## P0 release blockers

Unmanaged file mutation; false revision commit; lost immutable release; conflicting checkout; stale writer after administrative lock recovery; corrupted authoritative metadata silently accepted; inconsistent backup restoration; false completeness claims. All block release until empirically resolved. A missing API is NOT RUN, not PASS.

## Immediate executable tests

Use `tests/qa` for independent synthetic workspaces (customer/year/product layouts), byte/mtime/path snapshots, scoped adoption, startup non-traversal, injected journal publication failure and replay. Run `python -m pytest -q tests/qa` then `python -m pytest -q`. Record OS, commit, Python, filesystem, counts and skips.

## Specific observed safety gap and proof obligation\n\nADR-008 R2 only rechecks the writer lock immediately before publication; administrative revocation between check and publish is not atomic. A deterministic known-failure QA test pauses the writer at that boundary, breaks the lock through the official audited recovery method, and demands a refused commit and unchanged journal head after the break. It is marked `xfail(strict=True, raises=AssertionError)` until a revised atomic authority mechanism is approved and implemented. Unexpected errors must not be swallowed by this marker. This test is a local reproducer, **not** evidence of SMB/NFS safety. Issue #5 holds the unresolved coordinator-versus-share architecture decision; QA will not implement either option.\n\n## Network qualification

Synchronize 2, 3 and 10 clients on actual SMB 3.x and NFSv4 shares: one exclusive checkout winner. Pause old writer, break its lock, resume it: fencing must prevent publication. Repeat race trials at least 1,000 times per supported configuration; test server reboot, lost notifications and disconnect during check-in. Local process races never prove network safety. [QA coordination issue](https://github.com/nrobertson152/RevisionWorx/issues/1) documents missing transaction/fencing interfaces.

## Dependency verification

Create directed/cyclic, direct/indirect, revision-specific, unverified and cross-project edges. Where-Used must show exact known downstream relationships and unmanaged coverage warnings. Before You Revise must not classify all affected assemblies as incompatible. Native CAD filenames alone never prove reference extraction. OpenSchematix current fixtures prove format recognition only.

No test, feature, platform, performance result or release may be declared verified without an actual execution record.
