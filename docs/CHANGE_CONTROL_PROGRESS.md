# Change control (issue #19) — progress log

**Owner:** Claude D. **Branch:** `feature/change-control` → draft PR #20 (base `phase2-documents`).
**Rule:** every claim gives the commit, command, OS/Python and counts. "Not run" is not "passed".

## Session 1 — 2026-11-02/03

**Base:** `phase2-documents` `a03f1f0`. Baseline there (Linux, Python 3.13.18, root, no PySide6):
`python -m pytest -q -ra` → 343 passed, 3 skipped.

| Commit | What |
|---|---|
| `dd6f8b1` | `revisionworx/changes/`: model, workflow + policy, store adapter, audit chain, catalog, service, impact, query, exchange, presentation, fixtures |
| `081cc21` | 198 tests, `docs/CHANGE_CONTROL.md`, ARCHITECTURE pointer, branch CI workflow; fix: number prefixes keep meaningful spaces (found by a test) |
| `0aad7f7` | `python -m revisionworx.changes` headless commands for QA (list/show/impact/verify/export/import-csv/verify-package) + tests; this log |

### Evidence

| Where | Commit | Command | Result |
|---|---|---|---|
| Sandbox Linux, Py 3.13.18, root | `081cc21` | `python -m pytest -q tests/test_changes_*.py` | 198 passed |
| same | `081cc21` | `python -m pytest -q -ra` | 541 passed, 3 skipped |
| Sandbox Linux, Py 3.13.18, root | `0aad7f7` | `python -m pytest -q tests/test_changes_*.py` | 200 passed |
| [CI 41580249473](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580249473) ubuntu-latest, CPython 3.11.17 | `0aad7f7` | change-control step (5 files) / `python -m pytest -q -ra` | 198 passed / 543 passed, 3 skipped (Qt module; 2 Windows-only share-mode tests) |
| same run, windows-latest, CPython 3.13.17 | `0aad7f7` | same | 198 passed / 544 passed, 2 skipped (Qt module; case-insensitive FS test) |

Run 41580084986 on `081cc21` was cancelled by the newer push (concurrency group); it is not evidence.

Killed-process tests (`test_changes_workspace.py::test_killed_process_then_recovery_in_another_process`)
are separate local processes on one machine. No SMB/NFS, multi-host or power-loss evidence exists.

## Session 1b — Codex review fixes (2026-11-03)

| Commit | What |
|---|---|
| `e171810` | exports work without hard links (FAT/exFAT, some shares); actions never crash on an unreadable dependency |
| `cb68433` | Codex P1: merged order no longer drops a `revise` obligation behind `reference`; conflicting obligations/baselines refused. Codex P2: CSV import retry with same txid returns the original requests |
| `3038c9b` | Codex P2: Approved (not yet ordered) ECRs can be returned for rework when stale; new round voids old approvals. Codex P2: ECO approved content (digest v2) includes dependencies. Contract test for PR #21 where-used shape |

Every fix has a regression test that fails on the previous commit.

| Where | Commit | Command | Result |
|---|---|---|---|
| Sandbox Linux, Py 3.13.18, root | `3038c9b` | `python -m pytest -q -ra` | 553 passed, 3 skipped |
| [CI 41583059300](https://github.com/nrobertson152/RevisionWorx/actions/runs/41583059300) ubuntu-latest, CPython 3.11.17 | `3038c9b` | change-control step / full suite | 208 passed / 553 passed, 3 skipped |
| same run, windows-latest, CPython 3.13.17 | `3038c9b` | same | 208 passed / 554 passed, 2 skipped |

Independent QA has not yet confirmed any of these fixes.

### Decisions
- Persist only through the subsystem interface `documents.py` uses; confined to `store.py`. Awaiting
  Claude A's confirmation (PR #7 / #20).
- Fixed state machines + small policy instead of user-defined workflows: every workspace means the same
  thing by "approved"; ceremony is configurable.
- Signoffs bind to content digest and review round; they are local evidence, not signatures.
- Stale baselines block by default (fail closed); policy can relax to a warning.
- Parts/assemblies/BOMs are declared (unverified) until #15/#18 register resolvers.

### Open
- Claude A: subsystem persistence interface confirmation.
- Claude B: Changes view wiring against `docs/CHANGE_CONTROL.md` §7.
- #15/#18: part resolvers and a where-used provider.
