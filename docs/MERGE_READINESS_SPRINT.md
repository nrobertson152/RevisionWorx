# RevisionWorx — P0 Merge-Readiness Sprint: Integration Candidate and Director Sequence

**Status:** test-only engineering evidence; **no authorization to merge, rebase, retarget, tag, distribute, or release**. Prepared by Integration PR #2 on 2026-11-02 ET. All synthetic merge and frozen installer-like operations run on **disposable CI workspaces**. This document does not change any other agent's branch.

## 1. Exact inputs and scope

| Component | Pinned source for this sprint | Notes |
|---|---|---|
| Current `main` snapshot | `3b2d75ad36790c9308f73d3b5e22c002a9d117fb` | Docs/coordination; no Phase 1 product code merged |
| Phase 1 PR #6 | `412e69ab90bb1d8b74c1a3cf5519f581e4420e63` | Target `main`; **GitHub reports merge conflicts** |
| Phase 2 PR #7, tested | `22a5273d4740b3a7af8e8cb5cc3565e4336b763b` | Target `phase1-foundation`; core native CI 41574652418 green |
| GUI PR #14, tested | `bd52cdf0c89fcd1dbc03d42951ba8e4a7de5da4f` | Target `phase2-documents`; code at `d4e3623`, later head modifies documentation only; GUI CI 41575959230 green |
| Integration PR #2 | `integration/openschematix` | Remains draft, target `main`; tests, read-only adapter and packaging harness kept separate |

**Moving-head warning:** During this sprint Claude A advanced Phase 2 to `a03f1f00ab58b815534c4282b20ffc6503119aea` (new process-crash tests, workflow and documentation, no runtime source files in this commit). The results for `22a5273` are **not** exact-SHA certification of `a03f1f0` or any subsequent code change. Latest QA and Distributed Systems independently decide whether a new frozen source is safe. No auto-repin.

## 2. Test-only candidate: two different methods, do not confuse them

**A. Source-file overlay** (compatibility information only):
[CI 41577258567](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577258567) at workflow `b3f2ed8` checked out both exact inputs, copied the 10 GUI source files and 6 newest GUI regression test files into a disposable core checkout, recorded both input SHAs and source hashes, ran the full native combined pytest suite, packaged a frozen test-probe GUI/CLI, verified two immutable revision SHA-256 exports, refresh, restart, missing-file recovery and Windows path cases. **Ubuntu: 382 PASS / 2 SKIP; Windows: 382 PASS / 2 SKIP; frozen smoke PASS on both**. Ubuntu also passed the Python-free, `--network none` Docker runtime. **Important limit:** copying a GUI tree can overwrite conflicting core GUI changes; this is NOT a Git merge.

**B. Actual three-way Git merge** (stronger merge-readiness candidate):
`.github/workflows/revisionworx-literal-merge.yml` fetches complete histories of both frozen inputs, then `tools/packaging/stage_exact_git_merge.py` performs `git merge --no-ff` in a disposable, detached CI checkout. No branch, PR, ref or remote is changed. On a conflict, the script writes a failure receipt with unmerged filenames and **refuses auto-resolution**. On success it records: exact input commits, real merge base, synthetic commit SHA, precisely two parents, all changed paths and verified unchanged Git blob IDs of core-only `atomicio.py`, `workspace.py`, `authority.py` and `documents.py`. The merged checkout contains the **actual latest GUI regressions** through Git history. It then runs `python -m pytest -q -ra` with `.[dev]` installed and exercises the same frozen Qt/CLI/immutable history/path/offline runtime smoke. An experimental PyInstaller probe is injected by `--alpha-acceptance` and **MUST NEVER SHIP**.

[Literal Git candidate workflow 41577732526](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577732526) successfully constructed exact two-parent *local-only* commit `664e6887cf59b090a09e954554128a57be1ca22d` from the tested input pair, common base `366d4355abcde7543122551fadaab01ac9832e01`. The core-only authority file Git objects were preserved exactly. **[Literal Git merge CI 41577732526](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577732526): BOTH Ubuntu and Windows Python 3.11 SUCCESS.** Each ran the original nine source-staging safety tests, then `python -m pytest -q -ra` against the **actual synthetic Git-merged source**: **382 PASS / 2 SKIP / 0 FAIL on each OS**. Both built frozen test-only PyInstaller GUI+CLI and passed create/import/checkout/checkin, two immutable revisions, full CLI SHA-256 exports, F5 and new-process reopen, missing-file alert/restoration and copied Unicode/spaces path checks. Ubuntu further passed Python-free `--network none` container widget lifecycle, exports and normal GUI launch; Windows further passed 401-character, extended-local, localhost UNC-loopback and locked-file refusal checks. Both logs print the **same synthetic merge SHA `664e6887cf59b090a09e954554128a57be1ca22d`** and exactly the two pinned parent SHAs. No temporary merge was pushed. A green merged-tree test is scoped local functionality, not an approval to merge or release.

Reproduction is fully specified in the two workflows, with exact-checkout refusal on unexpected SHAs, recorded bundle provenance and no real engineering files.

## 3. Verified dependency graph and conflicts

| PR | Current target | Required dependency and conflict assessment |
|---|---|---|
| #6 Phase 1 | `main` | **BLOCKED:** main vs `phase1-foundation` Git `merge-tree` status 1, modify/delete conflict `docs/DEVELOPMENT_PROGRESS.md` (deleted from `main`, modified on Phase 1). |
| #7 Phase 2 | `phase1-foundation` | GitHub reports clean relative to this base. Must land on Phase 1 **before #6 goes to main** to prevent shipping the older unsafe Phase 1 alone. |
| #14 GUI | `phase2-documents` | Based on older Phase 2 ancestry; five overlapping PR-changed paths with #7: `revisionworx/gui/controller.py`, `revisionworx/gui/qt/documents.py`, `revisionworx/gui/qt/mainwindow.py`, `revisionworx/gui/viewmodels.py`, `tests/test_qt_gui.py`. A real Git merge for the frozen candidate pair has been demonstrated, but must be repeated at final SHA. |
| #3 QA | `phase1-foundation` | Mostly standalone `tests/qa` and QA workflows, but some require Phase 2 and the latest GUI. Merge/test only after the accepted application source is present, with any stale expectation or strict-XFAIL reviewed by QA. |
| #4 Distributed | `main` | Research/prototypes and protocol documents only; no app writer-authority production changes approved. Director must choose whether to merge research artifacts or leave the branch independent. |
| #2 Integration | `main` | Three fixture paths overlap #6: `tests/fixtures/openschematix/README.md`, `demo_project.schematix`, `start_stop_latch.osch`. Reconcile source bytes and provenance before final merge. Test-only workflows cannot be presented as release build. |

The Linux nonmutating `git merge-tree --write-tree origin/main origin/phase1-foundation` output in literal-merger CI is **precisely**: `CONFLICT (modify/delete): docs/DEVELOPMENT_PROGRESS.md deleted in origin/main and modified in origin/phase1-foundation`. The core architect's `docs/INTEGRATION_PLAN.md` proposes **retaining** the detailed progress document. That is a **Director decision**, not an automatic conflict resolution. Adjust `AGENTS.md` and `CLAUDE.md` references when main begins containing code.

## 4. Proposed exact Director-controlled merge sequence

This order follows the core architect's safety rationale: **#7 → #6 → #14**, followed by separately tested supporting contributions. No step below has been executed by Integration.

1. **Freeze the source and safety gate:** Claude A declares exact Phase 2 candidate SHA; Claude B declares GUI SHA. Core CI must pass Linux/Windows, and QA PR #3 plus Distributed PR #4 must independently accept **that exact core SHA**, especially recent `atomicio.py` changes, recovery, journal-effective writer fencing, and Windows sharing behavior. The earlier scoped rev.4 acceptance of `fe8a179` does **not** transfer to `22a5273` or `a03f1f0`. Director decides the pending integration-plan questions, including retiring the obsolete Phase 1 backport.
2. **Director merges PR #7 into `phase1-foundation`**, not into `main`. Confirm ancestry/commit SHAs and rerun core plus independent negative and recovery tests. This ensures Phase 1 branch contains corrected Phase 2 before exposure through PR #6.
3. **Resolve PR #6's `main` documentation modify/delete conflict** in an authorized merge commit on its owner branch; Director decides whether `docs/DEVELOPMENT_PROGRESS.md` is retained. Reconcile `AGENTS.md` / `CLAUDE.md` location and the now-obsolete sentence saying main has docs only. Verify normal Git merge without rebase, review final diff, rerun CI. **Director merges PR #6 into `main`** only when its contents include the accepted Phase 2 fixes from step 2. Do not publish an intermediate Phase 1-only build.
4. **Claude B integrates the now-accepted core into `feature/desktop-alpha` using a reviewable merge commit**, not a history-rewriting rebase. Run current GUI CI, all latest GUI regression tests, QA desktop tests and Integration's **real Git merge and frozen Windows/Linux matrix** again on the actual new SHAs. Confirm no core-only authority file changed and GUI contract remains valid. **Director retargets PR #14 to `main` and merges** after explicit review of the resulting GUI-only diff.
5. **QA PR #3:** Against final core + GUI on `main`, independently rerun QA acceptance on Ubuntu and Windows Python 3.11/3.13. Review strict-XFAILs and obsolete warning/error expectations. Director may retarget/reconcile and merge the independent QA test/workflow suite; do not merge stale failing tests or treat historical unsafe reproducers' green runs as safety passes.
6. **Distributed PR #4:** Director decides whether the research-only `prototype/` and protocol documentation are to be merged to `main` or retained independently. If merged, do it after the validated core/GUI stack, run applicable research CI, and do not turn speculative coordinator prototypes into supported SMB functionality.
7. **Integration PR #2:** With application and tests stabilized on `main`, Director authorizes any retarget/reconciliation. Compare OpenSchematix fixture object hashes against #6, preserve verified native adapter/BOM oracles, rerun Windows/Linux adapter and frozen packaging suites on the **actual final merged tree**. Keep developer-only `--alpha-acceptance` probe excluded from any shipped program. Director decides whether to merge the isolated adapter and packaging infrastructure; a green candidate workflow does not automatically change the production packaging source pin.
8. **Release is a separate decision:** Require user-operated Windows/Linux GUI walkthrough, actual installation/upgrades and rollback, independent QA signoff, dependency/license audit and necessary operational fault trials. The existing tests do not qualify real two-host SMB/NFS, physical power cuts, external tampering, or a clean disconnected Windows installation VM.

**Stop immediately and repin/retest** if any source branch moves, review finds a new P0 issue, actual merge produces unreviewed conflicts, native suite is red, QA/Distributed fail to accept the frozen core, or there is a mismatch between source/receipt/build SHA. Only the Director authorizes GitHub merges and releases.

## 5. Reproducible workflows and boundaries

- **Source-overlay diagnostic:** `.github/workflows/revisionworx-merge-readiness.yml` (not authoritative merge).
- **Literal Git two-parent integration:** `.github/workflows/revisionworx-literal-merge.yml`, `tools/packaging/stage_exact_git_merge.py` (authoritative for synthetic merge feasibility, NOT approval to push it).
- **Previously accepted isolated test baseline:** `.github/workflows/revisionworx-corrected-core-compatibility.yml` (older `fe8a179` and GUI `17651af` pins retained separately).
- **Original frozen packaging baseline:** `.github/workflows/revisionworx-packaging-readiness.yml` remains independently pinned to older `d656884`; never silently repin it.
- The full native suite executes using Python 3.11 with PySide6/Qt in `offscreen` mode. Ubuntu Python-free, network-none Docker runtime is automated; Windows frozen runner removes Python from child PATH but is **not** a physically disconnected Python-free VM. Source GUI keyboard X11 CI is automated, not a human-operated interactive desktop acceptance.

**Disposition:** reviewable P0 test-only integration candidate and proposed merge plan; PR #2 stays DRAFT. No merge, force-push, retarget, rebase, release or source modification to other engineers' branches.


## 6. Moving-head checkpoint after the verified matrix

**This is a deliberately pinned historical candidate, not a claim that every
current PR head has been tested.** After the exact-source
[41577732526](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577732526)
two-platform SUCCESS:

- PR #7 advanced beyond `22a5273` to
  `a03f1f00ab58b815534c4282b20ffc6503119aea` (new separate-process
  crash tests/workflow/docs) and subsequently
  `77ce2cb1b5bc742909639c502e1af29050f625da` (new
  `revisionworx/index.py` SQLite busy/locking logic and regression tests).
  The [`a03f1f0` Core CI 41577700770](https://github.com/nrobertson152/RevisionWorx/actions/runs/41577700770)
  FAILED on Windows Python 3.13, while Windows Python 3.11 and both
  Ubuntu versions succeeded. Claude A's later `77ce2cb` commit claims
  correction for the WAL-init/open race; its fresh
  [Core CI 41578429442](https://github.com/nrobertson152/RevisionWorx/actions/runs/41578429442)
  was queued at this checkpoint. **No automatic acceptance transfer.**
- PR #14 advanced to `0773bde599f39aee88a8a536c28673bc4a1e13f5`
  by **one documentation/mockup-only commit**, verified via GitHub compare
  from the tested `bd52cdf`. No runtime GUI files in that single follow-on.
  Nonetheless the tested input SHA remains `bd52cdf`, not the newer head.
- QA #3 and Distributed #4 research branches also advanced; their
  own exact-source acceptance reports must control any release gate.

**Required before the Director executes step 1:** freeze the corrected new
core at a SHA with green native CI (including Windows Python 3.13),
QA and Distributed independently recheck the changed implementation, and
re-run this real-merge Windows/Linux full-suite and frozen matrix against
that frozen core plus the chosen GUI SHA. The commit
`664e6887cf59b090a09e954554128a57be1ca22d` exists ONLY in
the CI temporary Git store, not in any GitHub branch or pull request.
