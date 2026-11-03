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
| next | `python -m revisionworx.changes` headless commands for QA (list/show/impact/verify/export/import-csv/verify-package) + tests; this log |

### Evidence

| Where | Commit | Command | Result |
|---|---|---|---|
| Sandbox Linux, Py 3.13.18, root | `081cc21` | `python -m pytest -q tests/test_changes_*.py` | 198 passed |
| same | `081cc21` | `python -m pytest -q -ra` | 541 passed, 3 skipped |
| GitHub Actions, ubuntu-latest Py 3.11 / windows-latest Py 3.13 | `081cc21` | `.github/workflows/change-control.yml` | see PR #20 comment |

Killed-process tests (`test_changes_workspace.py::test_killed_process_then_recovery_in_another_process`)
are separate local processes on one machine. No SMB/NFS, multi-host or power-loss evidence exists.

### Decisions
- Persist only through the subsystem interface `documents.py` uses; confined to `store.py`. Awaiting
  Claude A's confirmation (PR #7 / #20).
- Fixed state machines + small policy instead of user-defined workflows: every workspace means the same
  thing by "approved"; ceremony is configurable.
- Signoffs bind to content digest and review round; they are local evidence, not signatures.
- Stale baselines block by default (fail closed); policy can relax to a warning.
- Parts/assemblies/BOMs are declared (unverified) until #15/#18 register resolvers.

### Open
- Windows CI result (pending at time of writing).
- Claude A: subsystem persistence interface confirmation.
- Claude B: Changes view wiring against `docs/CHANGE_CONTROL.md` §7.
- #15/#18: part resolvers and a where-used provider.
