# RevisionWorx QA strategy

QA development branch: `qa/testing-infrastructure` (draft PR #3 currently based on `phase1-foundation`).
Review targets: `phase1-foundation` (PR #6) and stacked `phase2-documents` (PR #7).\nThe Lead Architect owns integration and protocol choices; QA independently evaluates both.

## Integrity requirements

Preserve unmanaged files during adoption; refuse conflicting checkout authorities, unpublished revisions, and journal corruption; distinguish authoritative `.revisionworx` journal from disposable local SQLite caches. Never claim unmanaged archives were indexed. Fail closed when write authority is ambiguous.

## Implemented code and independent execution status

Phase 1 (PR #6) now uses Python >=3.10, a PySide6 GUI, journal-first `Workspace`, single-writer lock checks, transaction outcome IDs, journal checksums, verify/repair, adoption, and disposable SQLite. Phase 2 (PR #7) adds document registry, immutable content snapshots and revision records, lifecycle, checkout/check-in, CLI and a Qt Documents tab. Parts, dependency graph, BOM, native CAD parsing, backups and production-safe SMB/NFS multi-writer operation remain unimplemented or unverified.

**Current independently executed application evidence supersedes the early architect-only baselines below.** GitHub Actions [run 41387311002](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311002) exercised *the same exact source revisions* (Phase 2 `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6`, QA `454391dfd6bd66059b97631701f6e5722b5e862e`) on Linux and Windows with Python 3.11/3.13. All four complete Phase 2 suites returned **211 passed, 1 skipped, 1 strict xfailed**. That xfail is specifically the *physical* late-publication oracle; logical journal-order fencing is independently passing. The QA-only commands returned Linux **37 passed / 1 skipped / 1 strict xfailed** and Windows **38 passed / 1 strict xfailed** on each Python version.

Focused [run 41387311010](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311010) exercised the three Claude fixes: #8 effective journal-first administrative revocation, #9 cache cursor snapshot consistency for insert/delete, and #10 bounded Windows writer-lock retry. **All acceptance invariants for the candidate implementation passed on Linux/Windows Python 3.13**, except the deliberately stronger physical-no-file xfail. Windows Server 2025 Python 3.13.17 also executed **five real six-process checkout trials (30 worker attempts)** with exactly 1 successful checkout and 5 conflicts in every trial, no worker errors, orphan locks or journal-verification problems. These are local runner filesystem outcomes, **not multi-host SMB certification**.

QA-only Phase 1/PR integration runs also exercise actual PySide6 widgets offscreen, including the Documents tab on the disposable Phase 2 checkout. No Qt *stub* is substituted. Installed Windows/Linux desktop packaging, actual CAD application interoperability, power-loss recovery and physical SMB/NFS remain unqualified. Synthetic dataset generation is not a query benchmark.

The CI workflow was corrected to avoid duplicate push/PR matrix work and to resolve a **single immutable Phase 2 SHA** before executing its four OS/Python matrix jobs. [Run 41387618838](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387618838) independently verified this pinned matrix on Phase 2 `f3c54386` and QA `b0bafe57`; all four Phase 2 full suites returned **211 passed/1 skipped/1 strict xfailed**. Documentation-only pushes no longer launch a second Phase 2 matrix.

For current bug-level acceptance paths, owners, exact SHAs and release prerequisites see [PR_MERGE_READINESS.md](PR_MERGE_READINESS.md) and [TEST_MATRIX.md](TEST_MATRIX.md). Historical failures and early 'unexecuted' notes in `docs/agents/QA_PROGRESS.md` remain as chronology, **not current status**.

## Testing layers

Unit: path validation, hashing, serialization, later graph and numbering. Integration: adoption, journal replay, local indexes and crashes. Adversarial: ENOSPC, EACCES, missing parents, journal gaps, abandoned temp files, external changes. Multiprocess: barriers and authoritative winner count. Network: real SMB/NFS shares from separate hosts, fault injection, reconnect, server restart, stale cache, fencing. Platform: Linux and Windows, Python 3.11/3.13, PySide6 offscreen GUI launch plus Windows desktop smoke; do not treat skipped Qt tests as coverage. Benchmark: 10,000 projects, 2 million docs, 500,000 dependencies; separate database generation from real application timing.

## P0 release blockers

Unmanaged file mutation; false revision commit; lost immutable release; conflicting checkout; stale writer after administrative lock recovery; corrupted authoritative metadata silently accepted; inconsistent backup restoration; false completeness claims. All block release until empirically resolved. A missing API is NOT RUN, not PASS.

## Immediate executable tests

Use `tests/qa` for independent synthetic workspaces (customer/year/product layouts), byte/mtime/path snapshots, scoped adoption, startup non-traversal, injected journal publication failure and replay. Run `python -m pytest -q tests/qa` then `python -m pytest -q`. Record OS, commit, Python, filesystem, counts and skips.

## Specific observed safety gap and proof obligation

ADR-008 R2 only rechecks the writer lock immediately before publication; administrative revocation between check and publish is not atomic. A deterministic known-failure QA test pauses the writer at that boundary, breaks the lock through the official audited recovery method, and demands a refused commit and unchanged journal head after the break. It is marked `xfail(strict=True, raises=AssertionError)` until a revised atomic authority mechanism is approved and implemented. Unexpected errors must not be swallowed by this marker. This test is a local reproducer, **not** evidence of SMB/NFS safety. ADR-014 has **approved** the optional offline LAN coordinator as the supported multi-user architecture, initially Windows-hosted with SMB. Implementation and physical qualification remain pending; QA does not implement Claude's production authority seam or the distributed specialist's coordinator. ADR-015 now filters stale entries **after the lock-break audit** from readers, cache, replay and transaction outcomes; an independent positive regression covers that. Nevertheless, audited break still renames the lock *before* recording its audit, leaving a separate exploitable pre-audit ordering window (issue #8). A strict independent xfail protects that unresolved behavior. LocalIndex rebuild also mixes current records with an earlier cursor (issue #9), under an independent strict xfail. All expected failures must remain visible and never be counted as passing safety requirements.\n\n## Network qualification

Synchronize 2, 3 and 10 clients on actual SMB 3.x and NFSv4 shares: one exclusive checkout winner. Pause old writer, break its lock, resume it: fencing must prevent publication. Repeat race trials at least 1,000 times per supported configuration; test server reboot, lost notifications and disconnect during check-in. Local process races never prove network safety. [QA coordination issue](https://github.com/nrobertson152/RevisionWorx/issues/1) documents missing transaction/fencing interfaces.

## Dependency verification

Create directed/cyclic, direct/indirect, revision-specific, unverified and cross-project edges. Where-Used must show exact known downstream relationships and unmanaged coverage warnings. Before You Revise must not classify all affected assemblies as incompatible. Native CAD filenames alone never prove reference extraction. OpenSchematix current fixtures prove format recognition only.

No test, feature, platform, performance result or release may be declared verified without an actual execution record.

## Independent CI execution record (2026-11-01)

Original QA branch at `304500f`: [GitHub Actions run 41371072088](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371072088), SUCCESS on Ubuntu and Windows with Python 3.11/3.13; QA-only 16 passed/2 skipped and full suite 111 passed/3 skipped in each runner. Both skips in QA cover version-gated tests, not passing Phase 2 or fencing.

GitHub PR merge check of QA atop updated Phase 1, [run 41371072451](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371072451): Windows Python 3.13 QA 16 passed/1 skipped/1 xfailed, full suite 136 passed/2 skipped/1 xfailed. Linux 3.11/3.13 QA 16 passed/1 skipped/1 xfailed, but full suite aborted during Qt import due absent `libEGL.so.1`. Qt runner packages were added in `d3a4378`; rerun results not yet verified. Phase 2-including QA integration execution (eight tests) is queued and must not be counted as passing until its job logs are reviewed.

## Verified Phase 2 stacked test execution (2026-11-01)

The disposable CI integration job on [run 41371698368](https://github.com/nrobertson152/RevisionWorx/actions/runs/41371698368) checked out Claude's Phase 2 commit `77419e34a35a27d29dd9eab3bce858a1ad93871b` and QA commit `2905632fe9b84e97bf121edf94a45f2bb44f5aa3` independently, then copied only `tests/qa` into the temporary Phase 2 test checkout. Linux Python 3.13 results: QA **24 passed, 1 strict xfailed**, full **179 passed, 1 strict xfailed**; no skips. All eight Phase 2-specific integrity tests passed. The known stale-writer TOCTOU remains a real safety defect independently corroborated by the Distributed Systems Engineer in issue #8. The newly added two Controller-backed view-model regressions were committed after that run and await their own CI results. The latest QA/Phase 1 PR run `41371702767` succeeded across Ubuntu/Windows and Python 3.11/3.13 with Qt enabled; that is not a Phase 2-on-Windows test or a production multi-host certification.

## Current QA verification and defect tracking

- The latest verified complete Linux/Windows Phase 2 overlay before the most recent negative-path cases is [run 41381884699](https://github.com/nrobertson152/RevisionWorx/actions/runs/41381884699), Phase 2 SHA `47d81658764cd6bde60e391ac9aac24e722317e8`, QA SHA `d72b7ba25ccbf5162e4db6df13914fbf5faa373e`: Linux QA 32 passed/2 xfailed and full 189 passed/2 xfailed; Windows QA 32 passed/2 xfailed and full 188 passed/1 skipped/2 xfailed. The two failures are strict xfails for physical late publication (#8) and inconsistent index watermark (#9). New test commits require fresh CI evidence.
- Actual offscreen Qt widget tests exercise the Documents tab under PySide6 on both Linux and Windows, including registration/filtering, external-edit warning and blank check-in protection. Additional conflict-dialog regression is added and awaiting exact-current-run verification. This is **real Qt**, but not a packaged Windows application or a live human desktop session.
- `tests/qa/test_integrity.py::test_revoked_writer_publication_is_logically_fenced_on_replay` verifies ADR-015 logical fencing of a post-audit stale entry and local SQLite indexing/replay. It does not imply that a previously renamed lock cannot be exploited before the audit (issue #8), or that SMB enforces any local filesystem operation semantics.
- [Issue #9](https://github.com/nrobertson152/RevisionWorx/issues/9) separately tracks the deterministic LocalIndex snapshot/watermark mismatch. Claude owns the corrective production change; QA owns the acceptance regressions.
