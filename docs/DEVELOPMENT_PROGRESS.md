# RevisionWorx — Development Progress

**Updated:** 2026-11-01
**Status:** Specification and architecture scaffolding only. No application code or tests have been verified.

## Existing repository baseline
- GitHub repository: https://github.com/nrobertson152/RevisionWorx
- Existing files before this documentation change: `README.md`, `LICENSE`.
- This change adds `CLAUDE.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, and this progress file.
- No runtime language/framework has been selected or benchmarked.
- No RevisionWorx functional features, tests, installers, networking behavior or interoperability have been verified.

## Phase tracking
| Phase | Status |
| --- | --- |
| 1. Foundation, workspace configuration, registration, index | Not started |
| 2. Drawing/revisions/checkouts | Not started |
| 3. Verified multi-user collaboration | Not started |
| 4. Parts, dependencies, Where Used, Before You Revise | Not started |
| 5. OpenSchematix and BOM integration | Not started |
| 6. Native CAD integrations | Not started |
| 7. Backup, change management and reporting | Not started |
| 8. Benchmarks, regression and packaging | Not started |

## Immediate next actions
1. Read `CLAUDE.md`, requirements and proposed architecture in full.
2. Inspect the repo and select a maintainable Windows/Linux desktop stack.
3. Document the initial domain model and architectural decision records.
4. Add a minimal runnable application with automated tests and a repeatable local development workflow.
5. Implement opt-in workspace configuration and a project registry **without any full-share traversal**.
6. Introduce local SQLite indexing behind a stable repository interface.
7. Prove critical shared-filesystem concurrency semantics before allowing authoritative multiuser writes.
8. Update this file with actual commits, executed commands, tests, failures and next work.

## Progress update format
For each milestone record:
- Date and commit SHA
- Features implemented and tested (not aspirational)
- Tests actually executed and outcomes
- Known limitations / data integrity risks
- Next concrete tasks

## Persistent warnings
- Never conflate a local cache with authoritative lock ownership.
- Do not claim old unmanaged projects have been indexed or included in where-used coverage.
- Do not claim Inventor/SolidWorks integration or OpenSchematix compatibility without real parser/API tests.
- Never invent success or mark a phase completed based solely on documentation.

## Phase 5 integration research checkpoint (integration/openschematix; 2026-11-01)
- Inspected OpenSchematix v1.1.0 source and native/legacy serialization; confirmed ZIP `.schematix` container version 1, sheet schemas 2/3 and legacy JSON `.osch` schema 1.
- Added isolated stdlib-only read-only index prototype at `revisionworx_integrations/openschematix.py` and contract tests in `tests/test_openschematix_adapter.py`.
- Added `docs/OPENSchematix_INTEGRATION.md` and `docs/OPENSchematix_DATA_MODEL.md`; no bindings to unfinished storage/GUI APIs.
- Prototype emits candidate manufacturer/part references with unknown BOM quantity; no confirmed PDM associations or full native BOM implementation.
- Verification: GitHub branch writes/readbacks succeeded. Local git clone failed due to unavailable DNS/network; tests have **not** been executed yet. Preserve prototype status until CI or executable tests confirm it.
- Next: run tests in CI or local checkout; compare known fixtures against native BOM; verify single-snapshot read semantics and complete schema validation before production adoption.
