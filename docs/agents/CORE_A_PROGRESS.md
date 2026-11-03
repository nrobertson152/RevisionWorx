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
