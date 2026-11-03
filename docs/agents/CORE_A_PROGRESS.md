# ChatGPT Core Engineering A — progress

## 2026-11-03 — Independent journal schema regression gate

- **Owner/assignment:** issue #29, test-only Core role branch `core/independent-journal-schema-regressions`.
- **Pinned source:** `phase2-documents` `f01b37d345c5208ab0fa520588a2abe8c66c894a` (last production runtime change `96f9c368`).
- **Test file:** `tests/test_core_independent_journal_shape.py` at commit `e36a6406a27f8826e177b670c90a2a4da3128b37`.
- **Seven strict acceptance cases:** six damaged journal variants (boolean seq, string delete flag, list put data, nonstring blob, unhashable txid, unhashable writer token), and one untouched positive control. Damage is injected only into a CLOSED disposable workspace. Legacy digestless format is used to isolate schema validation rather than digest mismatch.
- **Expected on old source:** six desired-behavior tests fail because `read_entry` currently validates only `records` list and presence of `kind`/`id`, or leaks non-typed exceptions downstream; control expected pass. This expectation is from source inspection, **not executed test evidence**.
- **Execution limitation:** this automation container cannot resolve github.com for git clone; GitHub connector supports file commits, but does not execute pytest or GitHub Actions. Native Ubuntu/Windows/Python 3.11/3.13 results are **NOT YET AVAILABLE**. No test count is claimed as observed. Request CI or a connected checkout runner before acceptance.
- **No production changes, no merge, no release.** No Claude A branch touched.

## Next milestone

Claude A: correct `Workspace.read_entry` shape validation and ensure `health()` reports typed corruption rather than raising raw exceptions or returning healthy. QA: run `python -m pytest -q tests/test_core_independent_journal_shape.py` on the pinned old source to establish red controls, then rerun unchanged against Claude A's corrected exact SHA on Ubuntu/Windows Python 3.11/3.13. Keep the CAD source provenance defect as an independent merge blocker. Core Engineer A next: ADR-017 canonical RecordCheck identity and replay-after-restart regression or nonduplicative crash recovery gate, after reviewing newer QA work.

## 2026-11-03 — ADR-017/021 ordered RecordCheck restart gates

- **Assignment:** issue #29; simple comment checkout before writing, no atomic-ref lock.
- **Pinned production baseline:** `f01b37d345c5208ab0fa520588a2abe8c66c894a` (runtime `96f9c368`). Reconfirm before any corrected-source acceptance.
- **Test-only source commit:** `60b1c4a66697507b9e58b2cdba45970cdeeb202a` on `core/independent-journal-schema-regressions`; new file `tests/test_core_adr017_ordered_checks_restart.py`.
- **Three deterministic cases:** (1) identical txid/ordered RecordChecks replay idempotently after restart while reordered checks are rejected; (2) changed RecordCheck expectation after restart raises `TxidReuseError` without advancing journal; (3) identical retry after a checked record changes returns the earlier committed outcome, without rechecking stale preconditions or appending a journal entry.
- **Interfaces:** existing `Workspace.commit`, `transaction_outcome`, `head_seq`, `RecordCheck`, `RecordPut`, `TxidReuseError`. No public API change, production source edit, or branch merge.
- **Execution:** tests NOT EXECUTED; this GitHub connector can commit files but cannot run pytest or Actions. No OS/Python pass/fail count or CI URL. Prior local clone DNS failure is not treated as proof that this test passes. QA should run `python -m pytest -q tests/test_core_adr017_ordered_checks_restart.py` on pinned old source and latest corrected exact SHA across native Ubuntu/Windows Python 3.11/3.13, and inspect all strict outcomes.
- **Next:** examine nonoverlapping orphan recovery/disk-full crash fixture or read-only integrity-doctor fixture after QA confirms overlap and exact source. Core malformed-journal and CAD source-provenance production fixes remain Claude A-owned P0 merge blockers.

## 2026-11-03 — Pinned four-platform workflow added

- **Checkout:** issue #29 simple GitHub comment checkout; role-owned branch only.
- **Workflow commit:** `328b03849b66946c9682b1c19477edff066adfef`, `.github/workflows/core-a-pinned-regressions.yml`.
- **Pinned historical production:** `f01b37d345c5208ab0fa520588a2abe8c66c894a` checked out separately from role-owned tests; runtime code is NOT changed.
- **Matrix:** native `ubuntu-latest` and `windows-latest`, CPython 3.11 and 3.13; `pytest` executes `tests/test_core_independent_journal_shape.py` and `tests/test_core_adr017_ordered_checks_restart.py` after copying the exact test files into a disposable production checkout. Prints both production and test commit SHAs.
- **Important:** This is a *historical negative-control* gate. Strict malformed-journal tests are expected to fail on the old source; a red job is useful only when logs confirm the expected specific assertions. No CI run ID or observed pass/fail/skip counts were available at commit time; no green acceptance is claimed. A future corrected-source workflow must use the same tests against a pinned new Claude A SHA, not silently update this historical baseline.
- **Next:** inspect actual Actions logs for four native jobs and distinguish setup failures from intended strict corruption failures. Ask QA to rerun unchanged tests on corrected exact core once available; separately pursue source-provenance acceptance.

## 2026-11-03 — Split historical negative/positive CI gates

- **Issue/branch:** #29 / `core/independent-journal-schema-regressions`; simple GitHub comment checkout.
- **Workflow code commit:** `56ddfe4c3037cecdce0e675573434698eb0e594c`; only `.github/workflows/core-a-pinned-regressions.yml` changed. No production code or test assertions altered.
- **Change:** added `pull_request` trigger alongside existing branch-scoped `push` and manual dispatch; split historical pinned matrix into two separate 4-runner jobs: strict malformed-journal **negative-control** (expected RED on historical source) and ADR-017/021 txid/restart **positive** gate (expected GREEN if supported). Both independently print immutable production `f01b37d345c5208ab0fa520588a2abe8c66c894a` and role-test source SHAs. Prevents expected journal failures from obscuring txid acceptance results.
- **Evidence:** workflow source updated through GitHub connector; no Actions execution observed in this run. **0 observed PASS/FAIL/SKIP/XFAIL**, no verified CI URL, no claim of OS/Python success. Previous commit-run lookup only includes PR-triggered first-page results; do not infer absence of push runs.
- **Next:** QA/Coordinator inspect Actions by workflow name for PR or push runs and classify each native Ubuntu/Windows Python 3.11/3.13 job. Strict journal failures must be specific expected schema assertions, not checkout/collection errors; txid positive tests must pass independently. Then rerun identical tests against Claude A's corrected exact SHA. Production journal and CAD source-provenance fixes remain Claude A-owned.

## 2026-11-03 — Read-only crash-corruption fixture (new independently scoped milestone)

- **Checkout:** issue #29 simple comment protocol, no atomic-ref lock.
- **Role-owned test commit:** `28c7ff76fab3ad1c1510677f6e1a19dc3c86e46d`, `tests/test_core_crash_integrity_readonly.py`.
- **Role-owned CI commit:** `37f0a145992388a909415befb8118d02482c92cf`, `.github/workflows/core-a-crash-integrity.yml`.
- **Immutable historical production:** `f01b37d345c5208ab0fa520588a2abe8c66c894a`. New suite has 5 cases: truncated JSON, valid JSON null/array, missing-first-entry gap, and healthy control. It hashes all files before/after read-only open/health to assert no mutation. All fixtures damage only CLOSED disposable workspaces; no lock manipulation or production edits.
- **Native verification:** NOT EXECUTED/NOT YET OBSERVED for these new tests; Ubuntu/Windows Python 3.11/3.13 workflow added, 0 confirmed pass/fail/skip/xfail and no new CI URL. The historical `null` and array cases are strict typed-error negatives, likely red until Claude A fixes schema validation; do not call red acceptance.
- **Previous independent evidence remains:** CI 41607204337 historical malformed journal 6 strict FAIL + 1 control PASS per OS/Python, ADR-017 3 PASS per OS/Python. Not repeated here.
- **Next:** inspect new Actions run and exact SHA, distinguish typed failure from setup errors; QA re-run unchanged five cases against corrected production SHA; consider read-only doctor diagnostics for missing content refs after schema gate clears.

## 2026-11-03 — Read-only derived-record integrity doctor fixtures

- **Scope:** issue #29 / PR #34, distinct from already-verified historical journal-shape negatives and QA's production validation. Test-only role branch; no production source changes.
- **Source baseline:** immutable Phase 2 `f01b37d345c5208ab0fa520588a2abe8c66c894a` (runtime `96f9c368`).
- **Tests:** `571a16e1a3c7a622b848f12714d2efe1c5e211b7`, `tests/test_core_readonly_derived_integrity.py`. Three fixtures: healthy derived-record verify; modified materialized record must report `mismatch`; unjournaled derived record must report `not-in-journal`. All assert `verify(repair=False)` preserves every workspace file byte-for-byte and never claims repair. Only closed disposable workspaces are modified.
- **Native workflow:** `48f729831f391feacdbba2d1c0f6c9a2696bba8c`, `.github/workflows/core-a-derived-integrity.yml`, Ubuntu/Windows Python 3.11/3.13, pinned source with role-owned test overlay. No execution observed yet, therefore 0 confirmed PASS/FAIL/SKIP/XFAIL and no CI acceptance claim.
- **Previously verified separate fixture:** crash-integrity CI [41623821729](https://github.com/nrobertson152/RevisionWorx/actions/runs/41623821729), four native jobs each 2 strict historical FAIL/3 PASS; raw AttributeError on `null`/`[]` remains Claude A production blocker. This result is not repeated or counted as a pass for the new suite.
- **Next:** inspect new workflow logs and exact test source SHA; fix fixture defects on this role branch if any. Preserve immutable old-source negative evidence. When Claude A publishes corrected SHA, rerun unchanged journal tests against that exact source on both OS/Python versions and hand off to QA/Distributed for independent acceptance. No merge/release authority.


## 2026-11-03 — Corrected-source exact-SHA positive acceptance workflow

- **Role/checkout:** Core Engineering A, issue #29; independent branch `core/independent-journal-schema-regressions`; no production edits.
- **Deliverable:** `.github/workflows/core-a-corrected-source-requalification.yml` at `1e2a6eee1a90ac069a04423d95c83326e9cefdce`. Separate manual-dispatch workflow accepts a full 40-character production commit SHA, rejects malformed SHAs and known historical defective `f01b37d345c5208ab0fa520588a2abe8c66c894a`, checks out the requested SHA, compares actual checkout HEAD, and prints exact role-test and production SHAs. Only the four existing independent test files are copied into disposable production checkout; assertions remain unchanged.
- **Matrix:** native Ubuntu/Windows with Python 3.11/3.13, strict journal (7), crash integrity (5), ADR-017/021 (3), derived-record doctor (3): **18 tests per job, 72 planned assertions across four jobs**. This is an expected collection count, NOT an observed test count.
- **Execution:** not dispatched, 0 observed PASS/FAIL/SKIP/XFAIL, no new CI URL. The workflow may require registration on the default branch before GitHub allows manual dispatch; do not merge without Director approval. A corrected Claude A production SHA must be identified and reviewed before dispatch. Even green native jobs would not establish physical power-loss or SMB/NFS multi-host safety.
- **Next:** QA #31 review the exact-source overlay and immutable assertions; Claude A #7 publish corrected production SHA for journal/CAD fixes; once dispatchable and approved, run native matrix and inspect job logs, then investigate remaining distinct source-provenance fixture from #28.

## 2026-11-03 — Independent CAD reference and immutable revision-chain restart fixture

- **Checkout:** issue #29, simple comment protocol; reserve backlog #28 large-CAD reference/revision integrity; no overlapping live checkout identified.
- **Production baseline:** immutable Phase 2 `f01b37d345c5208ab0fa520588a2abe8c66c894a`; not a corrected Claude A SHA.
- **Test commits:** `212600eb190629df8c76c56df60cb57d42759719` and fixture path correction `f88c14fc09e6b60f2d19e781e319b0e55dcb370f`, `tests/test_core_revision_chain_cad_refs.py`.
- **Two independent cases:** (1) native relative assembly reference bytes remain unchanged while a referenced part is registered, checked out, revised and reopened read-only; both immutable content objects and the ordered revision chain survive restart; (2) three revisions retain exact SHA-256 bytes, linear previous-revision pointers and journal-derived history after restart.
- **Native CI workflow:** `56a13d8d5cf61696cd905a8fa09a629bdd053953`, `.github/workflows/core-a-cad-revision-chain.yml`; Ubuntu/Windows Python 3.11/3.13, two cases per job (8 planned). CI workflow pins production SHA, reports both checkout SHAs, overlays only the new test file.
- **Execution:** NOT EXECUTED in this session; 0 observed PASS/FAIL/SKIP/XFAIL. No CI URL or production acceptance claimed. No production source or shared-data protocol changes. Existing historical malformed-journal negatives remain open.
- **Next:** inspect new native CI job logs and fix any fixture defects on role branch. QA #31 independently verify exact test SHA; Claude A retains source-provenance race and journal schema fixes. When corrected Phase 2 SHA exists, run unchanged positive requalification on both OS/Python versions. No SMB/NFS or physical-crash safety claim.


## 2026-11-03 — Unicode nested CAD provenance and unmanaged sibling regression

- **Checkout:** #29 trust-based protocol; independent #28 reserve item 13; Claude A currently owns production journal/CAD source-capture corrections, so no production files modified.
- **Test commit:** `0ed627d9d49ca52b373fc2c72c4d79e1a15bb37f`; `tests/test_core_unicode_cad_provenance.py`. Two cases: (1) nested non-ASCII part and assembly names preserve exact original spelling, embedded relative-reference bytes, revision hashes and latest revision after read-only restart; (2) unmanaged nested vendor CAD sibling files preserve bytes and nanosecond mtime across managed revision and restart. Synthetic payloads, **not** actual Inventor/SolidWorks reference parsing.
- **Workflow commit:** `6505d262c89721bebe802d87ca5f91b26f353123`; `.github/workflows/core-a-unicode-cad-provenance.yml`. Pinned Phase 2 production `f01b37d345c5208ab0fa520588a2abe8c66c894a`, separate test checkout, Ubuntu/Windows Python 3.11/3.13, `python -m pytest -q -ra tests/test_core_unicode_cad_provenance.py`, two tests per job (8 planned).
- **Verification:** no tests executed or native job logs observed during authoring: **0 observed PASS/FAIL/SKIP/XFAIL**. CI is not accepted until per-job logs and exact test checkout SHA are inspected. No claim of CAD parser, filesystem race, or multi-host safety.
- **Next:** inspect Actions native jobs, correct fixture assumptions if necessary; independent QA review. After Claude A checks in corrected SHA, run unchanged strict malformed-journal tests and CAD source-race acceptance separately.

## 2026-11-03 — Frozen fixture provenance for corrected-source CI

- **QA finding addressed:** independent test checkout previously floated with PR synthetic merge SHA, limiting exact fixture reproducibility (QA assertion audit `cab0da118698f391d99beaedba765180eb91feaa`).
- **Workflow commit:** `e39e5b5439e8e0392d9560e7c595933013781e82`; corrected-source workflow now checks out independent fixtures at immutable `ba51686f0f0db77373c771ab17121668a8648e3b` and asserts `git rev-parse HEAD` equals that SHA before overlay. Production remains fixed at `3cc42e13c442d0b8ce7ad9e9d8ee85b4615af194`, separately asserted. No test assertion or production changes.
- **Previously verified positive matrix:** Actions 41689355302, Ubuntu/Windows Python 3.11/3.13 each 22 passed, 0 failed/skipped/xfailed; this evidence predates the fixture-pin workflow update. **New workflow CI not yet verified**; no new pass count claimed.
- **Next:** inspect new native CI logs for both immutable checkout identities and 22 passes/job. QA to review fail-closed precommit and external rename adversarial tests; Windows `.releasing-*` and cross-host acceptance remain open.


## 2026-11-03 — Frozen fixture corrected-source native CI independently verified

- **Workflow:** `.github/workflows/core-a-corrected-pinned-ci.yml`, commit `e39e5b5439e8e0392d9560e7c595933013781e82`, branch head before this documentation update `8a3bf41940f238829a17118ba4165da327514cea`.
- **Actions run:** https://github.com/nrobertson152/RevisionWorx/actions/runs/41707814032, success. Independently read four native raw job logs, not merely summary status.
- **Exact production checkout:** `3cc42e13c442d0b8ce7ad9e9d8ee85b4615af194`; **exact independent fixture checkout:** `ba51686f0f0db77373c771ab17121668a8648e3b`. Both identities were printed and asserted in each job before overlaying six frozen suites.
- **Command:** `python -m pytest -q -ra tests/test_core_independent_journal_shape.py tests/test_core_crash_integrity_readonly.py tests/test_core_adr017_ordered_checks_restart.py tests/test_core_readonly_derived_integrity.py tests/test_core_revision_chain_cad_refs.py tests/test_core_unicode_cad_provenance.py`.
- **Native jobs:** Ubuntu Python 3.13 job `126167676731` 22 PASS; Ubuntu Python 3.11 job `126167677370` 22 PASS; Windows Python 3.11 job `126167677113` 22 PASS; Windows Python 3.13 job `126167677581` 22 PASS. **Total 88 PASS, 0 FAIL, 0 SKIP, 0 XFAIL**.
- **Scope:** positive acceptance for these 22 assertions on the pinned corrected source with immutable fixture identity. This closes the prior QA fixture-identity evidence gap; it does **not** establish malformed-journal precommit abort, external concurrent CAD rename/source replacement, Windows live `.releasing-*` PermissionError recovery, disk/power-loss durability, or multi-host SMB/NFS safety. Historical old-source negative-control workflows may remain red by design.
- **Next:** independent adversarial precommit abort fixture and source-replacement race fixture, coordinated with QA and Claude A; no merge/release authority.


## 2026-11-03 — Bounded verified-content corruption and no-output gates

- **Checkout:** #29 simple GitHub CHECKED OUT/CHECKED IN; role-owned branch only.
- **New fixture commit:** `b93c012d18783c24dde4da5273a53b98aa9cc4f7`; `tests/test_core_bounded_content_integrity.py`.
- **Four deterministic tests:** healthy exact verified read; same-length damaged immutable object must raise typed `WorkspaceCorruptError` on read and export without creating destination; bounded read must raise `ContentTooLarge`; missing object must raise typed corruption. All snapshot every workspace file byte before/after read-only operations; only disposable closed-workspace objects are damaged.
- **Implementation inspected:** `revisionworx/content.py` on `phase2-documents`, with `ContentStore.read_verified`, `export`, `ContentTooLarge`, and `WorkspaceCorruptError`. No production changes or shared format changes.
- **Execution:** NOT RUN, no Linux/Windows/Python counts or new CI link. This commit is test-only; exact `python -m pytest -q -ra tests/test_core_bounded_content_integrity.py` must run on Ubuntu/Windows Python 3.11/3.13 against a pinned corrected production SHA before acceptance. Four test cases per environment planned; 0 observed pass/fail/skip/xfail.
- **Remaining:** arrange immutable-fixture exact-SHA CI, inspect native logs, QA review; separately test concurrent CAD rename and malformed-journal precommit abort; Windows `.releasing-*` and multi-host gates remain open. Prior frozen corrected-source 88-pass run 41707814032 does NOT include these four new tests.


## 2026-11-03 — Bounded content integrity native CI dispatch

- **Checkout:** issue #29 trust-based comment protocol; no atomic-ref lock.
- **Four independent tests:** `tests/test_core_bounded_content_integrity.py` frozen at `b93c012d18783c24dde4da5273a53b98aa9cc4f7` (original test-only implementation).
- **Corrected production:** `3cc42e13c442d0b8ce7ad9e9d8ee85b4615af194`, checked out and asserted by full SHA.
- **New workflow:** `.github/workflows/core-a-bounded-content-integrity.yml` at `49ce403d6799ffbc5b92db996df888041bcf8fe6`. Independent fixture and production checkouts use immutable full SHAs, printed and checked before copying one test file. Matrix: native Ubuntu/Windows, CPython 3.11/3.13. Exact command: `python -m pytest -q -ra tests/test_core_bounded_content_integrity.py`. Four cases/job, 16 planned executions.
- **Observed execution:** no associated PR workflow runs returned immediately after commit; this lookup is PR-only/first-page and does not prove no push workflow. **0 observed PASS/FAIL/SKIP/XFAIL; no positive acceptance claim.**
- **Scope:** verified exact content bytes, same-size object corruption blocks read/export, bounded read fails typed, missing object fails closed, and no read-only workspace mutation. This does not exercise concurrent source replacement, Windows release permission transitions, journal precommit abort or cross-host filesystem behavior.
- **Next:** inspect four native logs and both printed SHA values; classify actual pytest results, hand off to QA #31; if any fixture fails, repair only Core A branch and rerun. Continue adversarial source-replacement and journal-abort fixtures without duplicating Claude A production implementation.


## 2026-11-03 — Native pinned bounded-content CI independently verified

- **Workflow run:** https://github.com/nrobertson152/RevisionWorx/actions/runs/41736330254 (completed, success); role head before documentation `21be80705c4c5b3098226eef5d0e22505e68921f`.
- **Both full SHAs asserted and printed in all four raw job logs:** production `3cc42e13c442d0b8ce7ad9e9d8ee85b4615af194`; independent test fixture `b93c012d18783c24dde4da5273a53b98aa9cc4f7`.
- **Exact command:** `python -m pytest -q -ra tests/test_core_bounded_content_integrity.py`.
- **Ubuntu Python 3.11:** job `126266357795`, 4 PASS, 0 FAIL/SKIP/XFAIL. **Ubuntu Python 3.13:** job `126266357461`, 4 PASS, 0 FAIL/SKIP/XFAIL.
- **Windows Python 3.11:** job `126266357926`, 4 PASS, 0 FAIL/SKIP/XFAIL. **Windows Python 3.13:** job `126266357884`, 4 PASS, 0 FAIL/SKIP/XFAIL.
- **Total:** 16 PASS, 0 FAIL, 0 SKIP, 0 XFAIL, based on independently fetched native job logs, not just green workflow status. This is positive evidence for four bounded-content cases at the pinned corrected SHA, not broader release acceptance.
- **Other workflow caution:** at this same role head, crash-integrity read-only run `41736330311` and historical independent regression evidence run `41736330220` report failure; those must be triaged independently, not counted as green. Phase 1 PR #6 malformed-journal P0 remains HOLD (QA run 41730782278).
- **Next:** QA review of frozen fixtures; investigate failing workflows' exact production SHA and negative-control expectations; adversarial precommit-abort and concurrent source replacement gates; no SMB/NFS or release safety claim.
