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
