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

## 7. Current-head 96f9c36 + 0773bde exact real-merge compatibility and new independent P1 HOLD

**Source pair:** Phase 2 `96f9c368a11fd43c0440d404c7258c7dfeacabf6`
and Desktop GUI `0773bde599f39aee88a8a536c28673bc4a1e13f5`,
both exact PR heads at staging. No GUI runtime changes after the earlier
accepted UI; newest GUI commit changed docs/mockups only. Core adds
new recovery, journal-shape validation, Windows working-file sharing,
SQLite, checkout retry, health and GC logic, so prior QA acceptance
cannot be inherited.

**Reproducible test-only real Git merge:** The separate
`.github/workflows/revisionworx-live-head-merge-readiness.yml`
at Integration commit `7ac4f9a6b288055598b25ef9655070a2836c5193`
does a complete-history source checkout, checks both commit SHAs and
clean-tree state, invokes `tools/packaging/stage_exact_git_merge.py`,
records the exact two-parent synthetic Git merge
`d2e4842b48566741daff4ab7202a403a37478995`
(never pushed), checks exact unchanged blob OIDs of
`revisionworx/atomicio.py`, `authority.py`, `documents.py` and
`workspace.py`, runs the **actual Git-merged tree's native full
`python -m pytest -q -ra` suite** with Qt, builds test-only
PyInstaller GUI and CLI and executes the full immutable revision
lifecycle in relocated frozen directories.

**[CI 41579981999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41579981999):
BOTH Windows and Ubuntu Python 3.11 jobs COMPLETE SUCCESS.**
Ubuntu full merged suite **501 PASS, 2 SKIP, 0 FAIL**; Windows full merged
suite **500 PASS, 3 SKIP, 0 FAIL**. Both jobs passed 9 synthetic source
overlay safeguards, genuine two-parent Git merge, frozen GUI create,
external import, checkout, edited-file checkin, two immutable historical
revision SHA-256 exports via separate frozen CLI, F5 retained selection,
separate GUI process restart, temporarily missing working-file detection
and restoration, preserved original hashes, and copied Unicode/space
paths. Ubuntu also passed Python/pip-absent nonroot/read-only-root
Docker **`--network none`** and the normal GUI launch.
Windows separately passed local 401-character path, extended-length
path, localhost UNC-loopback read and byte-range locked-file read
rejection. This is **automated offscreen widget acceptance**, not a
physical Windows offline VM or human-operated GUI.

**Independent Git-merger staging safeguard testing:** New
`tools/packaging/test_stage_exact_git_merge.py` at `bdd166f`
tests divergent repositories with successful true merge and exactly
two source parents, mismatched source SHAs, dirty input, unresolvable
conflict, refused unapproved core-authority changes, existing
receipts, unsafe receipt location and source alias refusal.
The first four-platform run
[41580498901](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580498901)
was RED on Windows due to Git's CRLF checkout conversion creating
fabricated changes on cloned fixture files (Linux passes). A first
fixture correction at `214cb42` used `-c core.autocrlf=false`
during clone and added an `.gitattributes` LF rule, but accidentally
encoded embedded literal backslash-n in a Python source line,
giving an IndentationError across all platforms
[41580643954](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580643954).
This was corrected at exact test source
`013c3a5eaa901db29ce6a4d691e9afcc7bdefc99`,
with all earlier failed CI preserved.
[**CI 41580746150**](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580746150)
**4/4 native Windows/Ubuntu Python 3.11/3.13 SUCCESS**:
each ran nine real Git fixture tests and nine source-overlay staging
tests, all **18 PASS per job, zero fail**. These are safeguard
tests for the test-only staging tool, **not production protocol approval**.

**Independent safety disposition: `96f9c36` REJECT/HOLD, regardless of the
green functional Integration CI.**
- Independent QA
[PR #3 malformed journal finding](https://github.com/nrobertson152/RevisionWorx/pull/3#issuecomment-6595764358)
reproduced `Workspace.read_entry` unhandled `AttributeError` for
valid JSON top-level non-mapping payloads, plus false-positive
`health().ok=True` and raw `TypeError` on corrupt nested record
types. Four-platform frozen negative controls
[41580089612](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580089612)
and [41580332225](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580332225)
fail strict positive requirements. Some behavior predated
`96f9c36`; the new advertised recovery/health contract remains
unmet. Fix requires schema validation and positive QA acceptance,
not a test expectation change or a blanket catch.
- Independent Distributed Systems
[PR #4 CAD snapshot finding](https://github.com/nrobertson152/RevisionWorx/pull/4#issuecomment-6595779332)
used real OS two-stage CAD save-by-rename, preserving the source
path's size/mtime but replacing the opened inode/file identity while
`ContentStore.store_file()` reads. It can accept old bytes even though
the final pathname points to the replacement. The content hash/journal
are self-consistent, but source-to-revision provenance is wrong.
[Four-platform 41580498940](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580498940)
demonstrates the unsafe case with a strict expected positive failure
on each job and validates `stat/fstat/stat` `(st_dev,st_ino)`
identity check as minimum correction on actual Windows and Linux.
Require `SourceChangedError` and **normal PASS** of the independently
pinned corrected-source oracle before merge.

**P0 Director gates remain unchanged:** Claude A must correct the P1s,
freeze an exact new core SHA, green all four native core CI jobs
and obtain explicit independent QA/Distributed **exact SHA** positive
verdicts, then rerun this exact real-Git Windows/Linux integration
matrix on the corrected paired source. PR #7 must enter the Phase 1
branch before PR #6 can reach main; the `docs/DEVELOPMENT_PROGRESS.md`
modify/delete conflict and Director-owned decision still block #6.
GUI #14 follows, then independently validated QA #3,
optional research #4, and adapter/packaging #2.
No real merges, rebases, releases, retargets, production repins
or changes to other agent branches occurred here.

**Additional limits:** no installed interactive human GUI,
native signed installer/upgrade/rollback, physical power-loss,
two-host SMB/NFS, actual hostile lock namespace, socket-attempt audit
or disconnected clean Windows VM. Test-only `--alpha-acceptance`
wrapper MUST NOT SHIP.
