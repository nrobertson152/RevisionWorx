# RevisionWorx — Claude Engineering Instructions

You are the principal software architect and implementation engineer for RevisionWorx, an offline, cross-platform, GPL-3.0 engineering PDM application.

## Mandatory reading order
1. `docs/REQUIREMENTS.md` — authoritative product specification.
2. `docs/ARCHITECTURE.md` — architectural constraints, provisional designs, and unresolved decisions.
3. `docs/DEVELOPMENT_PROGRESS.md` — verified implementation status and next tasks.
4. Existing source code, tests, README and license before changing them.

## Working rules
- Build working software incrementally. Do not stop at architectural prose.
- Preserve existing company folder structures and native Inventor/SolidWorks references. No destructive migration.
- Do not scan unmanaged archives automatically. New projects are registered, old projects opt in.
- Treat data integrity, safe concurrent checkouts, immutable revisions and crash recovery as release blockers.
- Shared `.revisionworx` metadata is authoritative; local SQLite is a disposable search cache. Do not assume shared network filesystem operations are safe until verified.
- Work completely offline: no accounts, cloud dependency, subscriptions, telemetry or mandatory network services.
- Verify actual OpenSchematix formats and APIs before claiming interoperability. Do not modify OpenSchematix without explicit authorization.
- Never fabricate support, benchmarks, tests or releases.
- Design before editing shared-data protocols; document assumptions and failure modes.
- Add regression tests for every behavior and reproduce defects before claiming fixes.
- For each meaningful change: implement, test, document results, and update `docs/DEVELOPMENT_PROGRESS.md`.
- Keep `docs/ARCHITECTURE.md` synchronized with actual design decisions. Mark unimplemented ideas as proposed.
- Do not merge, tag or publish releases without the project director's approval.

## Next session — Director handoff (2026-11-01)

**Do not restart Phase 1.** Read [`docs/agents/CLAUDE_RESUME_HANDOFF.md`](docs/agents/CLAUDE_RESUME_HANDOFF.md) on latest `main` and your existing `docs/DEVELOPMENT_PROGRESS.md` on `phase2-documents`. Inspect latest PRs #2/#3/#4/#6/#7, issues #5/#8/#9/#10 and exact CI status before implementing.

Primary corrective code ownership: (1) Issue #8 effective pre-audit lock-break revocation race, (2) Issue #9 LocalIndex watermark inconsistency, (3) Issue #10 intermittent Windows writer-lock release `WinError 32`; prioritize critical integrity and choose a safe incremental change that can be independently QA tested. Production authority integration remains Claude-owned; the optional LAN coordinator is approved as a design but **not** production-certified.

QA, Integration and Distributed Systems agents have completed significant tests and handoffs; use them rather than duplicating their work. Standard hosted Actions are generally free on this public repo, but avoid unnecessary matrix churn and do not trigger billable private-repo work. No merges, releases, or production multiuser claims without Director approval.
