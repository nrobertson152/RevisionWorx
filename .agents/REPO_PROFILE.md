# Repository profile — RevisionWorx

Agents read this before changing code. Checked against the repository on 2026-11-02 (main at `e31efe1`, `phase2-documents` at `366d435`). If you find it wrong, open an issue labelled `rules-change`; don't work around it.

## Identity

- Repository: `nrobertson152/RevisionWorx` (public)
- Director: `nrobertson152`
- Default branch: `main`. **`main` currently contains documentation only.** The code is on draft PR branches that have not merged:
  - `phase1-foundation` (PR #6 → main)
  - `phase2-documents` (PR #7 → `phase1-foundation`)
  - `qa/testing-infrastructure` (PR #3), `research/shared-filesystem-protocol` (PR #4), `integration/openschematix` (PR #2)
- **Consequence for agents:** ARF branches start from `main`, so until the Director merges the foundation, dispatched code work has no code to work on. Until then, the coordinator should dispatch only documentation and design issues and mark code issues `blocked` on PR #6.
- Version: pre-alpha
- Supported platforms: Windows and Linux; Python ≥ 3.10 (CI uses 3.13)
- Licence and product constraints:
  - GPL-3.0-or-later; never change the licence. PySide6 (LGPL) is GUI-only.
  - Fully offline: no accounts, cloud, subscriptions, telemetry or mandatory network services.
  - Core and CLI use the standard library only.

## System map (as on `phase2-documents`)

- **Purpose.** An offline engineering PDM (product data management) application. It manages projects, documents, immutable revisions, check-in/check-out, lifecycle, parts/BOMs, Where-Used and "Before You Revise", and integrates with OpenSchematix and native Inventor/SolidWorks files. Authoritative specification: `docs/REQUIREMENTS.md`; architecture, ADRs and open design gates: `docs/ARCHITECTURE.md`.
- **Authority and storage:**
  - `authority.py`: `CommitAuthority`, the file journal, writer locks, fencing.
  - `atomicio.py`, `paths.py`, `content.py`.
  - `workspace.py` (`Workspace`), `projects.py`, `documents.py`, `lifecycle.py`, `revlabels.py`, `filetypes.py`, `scan.py`, `clientstate.py`.
- **Index:** `index.py` (`LocalIndex`): the local SQLite search cache, which is disposable.
- **Interfaces:** `cli.py`. GUI: `gui/` (controller and view models), plus `gui/qt/` (PySide6).
- **Shared data rule:** the shared `.revisionworx` metadata is authoritative; local SQLite is only a cache.

## Invariants that must never break (release blockers)

- **Data integrity.** Revisions are immutable once published, and crash recovery never loses or corrupts authoritative data.
- **Concurrent checkouts are safe.** Two contenders can never both hold authoritative ownership.
- **Administrative lock override stays, and is fenced** (`docs/ADMINISTRATIVE_LOCK_FENCING_DIRECTIVE.md`). A revoked writer has no path to publish; checking authority is atomic with publication. Recovery never resurrects revoked authority. Every override is audited.
- **No destructive migration.** Existing company folder structures and native Inventor/SolidWorks references are preserved. Unmanaged archives are never scanned automatically: new projects register, old projects opt in.
- **Fail closed** when authoritative fencing can't be established. Never claim production multi-user safety on SMB/NFS without verified cross-host tests. The approved optional LAN coordinator (ADR-014) is **not** production-certified.
- **OpenSchematix.** Verify its actual formats and APIs before claiming interoperability. Never modify the OpenSchematix repository from here.
- **Branding.** Use only the Director-supplied logo (`docs/REVISIONWORX_BRANDING_DIRECTIVE.md`).

## Commands (repository root, once code is on the branch)

- Install: `python -m pip install -e ".[dev]"` (installs pytest and PySide6-Essentials)
- Tests: `QT_QPA_PLATFORM=offscreen python -m pytest -q -ra`. Tests marked `qt` skip without PySide6.
- QA integrity suite (on `qa/testing-infrastructure`): `python -m pytest -q -ra tests/qa/test_integrity.py`. Windows stress: `python tests/qa/windows_checkout_stress.py --trials 5 --workers 6 --timeout 100`.
- CI: `main` has no project CI yet. The QA and research branches carry their own workflows (Ubuntu + Windows). Until project CI lands on `main`, agent evidence is limited to what the workflow agents run on Ubuntu. Windows behaviour (for example `WinError 32` lock release, issue #10) is **not verified** unless a Windows run on the exact SHA shows it.
- Cost: hosted Actions are free on this public repository, but avoid redundant matrix runs.

## Change controls

- **Risk floors** are enforced by `.agents/risk-floors.json`. Authority, journal, locking, fencing, atomic I/O, workspace, documents, lifecycle, index and recovery are `critical`. Everything else under `revisionworx/` is `high`.
- **One issue at a time** may change `revisionworx/authority.py` or `revisionworx/workspace.py`.
- **Strict expected failures (xfails) are open requirements**, not fixes. A green run containing them doesn't close the requirement.
- **Record what you ran.** Report local multiprocess tests and real SMB/NFS cross-host tests separately, and label unexecuted ones.
- **The Director decides** merges, releases, multi-user production claims, licence, new runtime dependencies, and changes to the authority protocol or ADRs.
- **Known open defects (live state is on GitHub):**
  - #8: lock-break versus stale-writer publication race.
  - #9: `LocalIndex` watermark inconsistency.
  - #10: intermittent Windows `WinError 32` on writer-lock release.

## Current work

Not here. It lives on issues, PRs and the "ARF: Director briefing" issue.
