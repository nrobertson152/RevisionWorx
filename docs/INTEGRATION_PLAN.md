# Integration plan: Phase 1 → Phase 2 → desktop GUI

**Status: proposed, for the Director's decision.** Nothing here has been done. No branch was rebased,
retargeted, merged or changed to produce it: every merge result below came from `git merge-tree`, which
writes nothing to a branch. Written 2026-11-02 against the SHAs in the table.

## Recommendation

Land the stack in order with ordinary merge commits, never rebases:
**#7 into `phase1-foundation`, then #6 into `main`, then #14 into `main`.** Do this only after the gates
below pass. Drop the separate Phase 1 backport.

Why this order:
- **No SHA changes.** QA and Distributed Systems pin exact SHAs. A rebase would throw away that evidence.
- **#7 fast-forwards.** `phase2-documents` is 0 behind `phase1-foundation`.
- **Phase 1 alone must not reach `main`.** `phase1-foundation` at `412e69a` still has the defects from #8, #9
  and #10 and the lock races fixed in ADR-019.
- **The backport has no audience.** Phase 1 was never released, so nobody needs a fixed Phase 1 on its own.
  The existing candidate `36d1957` was rejected by QA and Distributed Systems, and redoing it would mean
  qualifying a second copy of the most delicate code.

## Where things stand

| Branch | SHA | PR | Relation | State |
|---|---|---|---|---|
| `main` | `f040da9` | | docs only | |
| `phase1-foundation` | `412e69a` | #6 draft → `main` | 6 ahead, 29 behind `main`\* | GitHub reports conflicts |
| `phase2-documents` | `3cb2764` (code) | #7 draft → `phase1-foundation` | 0 behind its base | clean |
| `feature/desktop-alpha` | `9205d82` | #14 draft → `phase2-documents` | based on `366d435` | clean |
| `integration/openschematix` | `94ad487` | #2 draft → `main` | test harness and docs | separate |
| `qa/testing-infrastructure` | `baa1e4b` | #3 draft → `phase1-foundation` | QA tests | stays separate |
| `research/shared-filesystem-protocol` | `946795b` | #4 draft → `main` | research | stays separate |
| `perf-journal-index` | `47b90f4` | none | parked | conflicts |
| `phase1-backport-8-9-10` | `36d1957` | none | rejected | drop |

\* From the GitHub compare API at the time of writing; `main` and the branches share history at `0c9cc6b`.
Later commits on any branch change these numbers.

## Trial merges

| Merge | Result | Tests on the merged tree |
|---|---|---|
| `3cb2764` + `feature/desktop-alpha` `9205d82` | no conflicts | 354 passed, 1 skipped, 0 failed (Linux, Python 3.13.18, PySide6 offscreen) |
| `main` + `3cb2764` | 1 conflict: `docs/DEVELOPMENT_PROGRESS.md` | 271 passed, 1 skipped, 0 failed (Linux, Python 3.13.18, no Qt) |
| `3cb2764` + `perf-journal-index` | conflicts in `workspace.py` and two docs | not run |

These ran on Linux only, in a scratch copy. They are compatibility checks, not acceptance.

An earlier trial was useful: testing the core against the GUI branch caught a regression before review, where
a damaged journal took away read access. That is now fixed and tested in core (ADR-022). **A trial merge with
the GUI should stay part of every core change until #14 lands.**

Separately, one QA test, `tests/qa/test_desktop_alpha_integrity.py::test_real_postjournal_checkin_failure_survives_restart_once`,
fails on the merged tree with both `fe8a179` and `3cb2764` as the core. It expects an error that the newer GUI
now reports as a success with a warning. It looks like a QA test that needs updating, which is QA's call.

## Conflicts and how to resolve them

**`docs/DEVELOPMENT_PROGRESS.md`:** `main` deleted it in #12; the code branches keep it updated. Keep the
branch's file, since it is the only full record of what was done and tested.

**`CLAUDE.md` and `AGENTS.md`:** these merge cleanly, but the result needs one follow-up edit. `CLAUDE.md`
becomes `main`'s `@AGENTS.md`. The line in `AGENTS.md` saying "`main` holds documentation only" and naming #6
and #7 as the location of the code becomes wrong once they land, and should change in the same step.

## Proposed sequence

Each step needs the Director; the agents only prepare it.

| # | Step | Who | Depends on |
|---|---|---|---|
| 1 | Pin a core SHA and rerun QA and Distributed Systems on it, Linux and Windows | QA, DS | Core CI green |
| 2 | Answer the decisions below | Director | |
| 3 | Add API requests A1-A7 that were accepted, then requalify | Core, QA | Step 1 |
| 4 | Move #14 onto the accepted core with a merge commit on `feature/desktop-alpha`, and rerun GUI CI and Integration's frozen matrix | GUI, Integration | Step 3 |
| 5 | Merge #7 into `phase1-foundation` (fast-forward) | Director | Steps 1-3 |
| 6 | Resolve the docs conflict with a merge commit, rerun the suite, merge #6 into `main` | Core, then Director | Step 5 |
| 7 | Retarget #14 to `main`, confirm its diff is GUI only, merge | Director | Steps 4, 6 |
| 8 | Update `AGENTS.md`, settle #3 and #4 (decision 5), and retire the backport branch | Director | Step 7 |
| 9 | Manual GUI acceptance on an installed build, then any 0.1.0 Alpha release decision | Director, Integration | Step 7 |

## Gates still open

**Must pass before step 5:**
- [ ] Independent QA and Distributed Systems acceptance of the pinned core SHA. The scoped ACCEPT covers
  `fe8a179` and the three rev. 4 fixes only, and does not carry over. ADR-022 and the #10 changes have not been
  reviewed by anyone but their author.
- [ ] Core CI green on Windows and Linux at that SHA.
- [ ] Codex review of that SHA with its findings resolved.

**Must pass before an alpha release:**
- [ ] Manual, interactive GUI acceptance on Windows and Linux. All GUI evidence so far is automated.
- [ ] Installed package smoke test (Integration, #2).

**Explicitly not required for the Tier 1 alpha, and not claimed:** SMB/NFS, multi-host or multi-user safety,
power-loss durability, and protection from outside programs editing `.revisionworx`. Tiers 2 and 3 need their
own qualification.

## Issue status

The Director closes issues. This is the core owner's view at `3cb2764`:

| Issue | Status | Suggested disposition |
|---|---|---|
| #8 stale-writer publication | Fixed in Phase 2; independently accepted | Close when #7 lands |
| #9 index rebuild watermark | Fixed in `325b7a8`, QA accepted; further regressions added | Close when #7 lands |
| #10 Windows checkout race | Earlier fix accepted. Three more gaps fixed here, unreviewed | Close after QA's Windows run at the pinned SHA |
| #5 authority architecture | Tier 1 decided. Network tier open | Keep open for Tier 2 |
| #1 QA integration contract | Standing | QA's call |

## Decisions for the Director

1. **Merge order.** Approve the order above, including no rebase?
2. **Drop the Phase 1 backport?** Recommended. It reverses an earlier request on PR #6 for a new isolated
   backport, so this needs a clear answer.
3. **`perf-journal-index`.** Recommended: don't merge it. ADR-022 now covers its revocation scan, so redo what
   is left (txid index, head probe) on top of `main` after integration, if measurements call for it.
4. **`docs/DEVELOPMENT_PROGRESS.md`.** Keep it on `main`, as recommended above, even though #12 removed it?
5. **QA and research branches.** Keep them as long-lived branches and close #3 and #4 unmerged, or merge the QA
   tests into `tests/qa/` on `main`? Recommended: merge QA's tests once they pass against `main`, so they
   stop depending on a separate branch.
