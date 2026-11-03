# Quality gates

Every PR is reviewed by a different model at its exact head commit before it can merge. The verdict is recorded as the `arf/cross-model-review` commit status, written by GitHub Actions, and the branch ruleset requires it from that source. A new push creates a new commit with no status, so the review is repeated automatically.

A verdict counts only after `tools/arf.py` has checked it against the contract and the risk. The model's ACCEPT is a claim; the check decides whether the evidence supports it.

## Risk tiers, review depth and cost

Risk is the impact if the change is wrong, not its size. It sets both what the implementer must show and how deep, and how expensive, the review is.

| Risk | Typical work | Implementer provides | Review | What the validator requires for ACCEPT |
| --- | --- | --- | --- | --- |
| Low | Docs, cosmetic, narrow non-critical fix | Focused check | **Light**: diff against contract, focused check, cheaper model if `ARF_*_LIGHT_MODEL` is set, ~25 turns | Every criterion answered; NOT RUN only with a stated reason |
| Medium | Normal feature or behaviour change | Unit and regression tests; affected integration tests | **Standard**: re-run tests, add at least one new case, ~60 turns | As low, plus at least one test actually run |
| High | Persistence, file formats, migrations, security, concurrency, engineering correctness (e.g. CAD connectivity) | Regression test that fails on the base commit; negative and fault tests; compatibility with existing files | **Full**: confirm the test fails on base, adversarial cases, ~120 turns | Numbered criteria in the contract; every criterion PASS; nothing NOT RUN; tests run |
| Critical | Known data loss, corruption, unauthorized write | As high, plus recovery behaviour | **Full** | As high. Never auto-merged |

Before any model is started, the review job runs free checks: the rules version, the PR-description policy, and the review-round cap. A PR that fails them goes back for rework without spending a model call.

The reviewer is chosen from the `arf/provenance` status that the publish job writes on each commit it pushes, never from the PR text. A head commit without trusted provenance isn't reviewed until the Director vouches for it.

The effective risk is the highest of: the PR's `risk:` label, the contract's risk, and the floor in `.agents/risk-floors.json` for any file the PR changes. Lowering a label doesn't lower the bar, and the coordinator can't set risk below a floor or below an issue's existing risk. The coordinator sets risk; the Director can raise it any time.

## Evidence

A test claim without these is not evidence:

- Commit SHA it ran against.
- Environment: OS, runtime and tool versions, plus display/filesystem/network conditions where relevant.
- Exact command, and counts for passed, failed, skipped, xfailed, not run.
- Which acceptance criterion (`AC1`, `AC2`, ...) it proves, and what it does not prove.

Green CI with skips, expected failures or simulated platforms doesn't satisfy a criterion that needs the real thing. Record those as not verified. At high and critical risk, an unverifiable criterion goes to the Director (`needs_director`) rather than being accepted.

## Definition of done

All of these hold:

- The contract's criteria are met.
- A validated cross-model ACCEPT exists at the head SHA.
- Required CI is green.
- The PR description is complete.
- For protected files, the Director's approval run exists at that SHA.

Then the Director merges, or auto-merge does if the Director enabled it for this risk tier (low or medium only). Release is a separate Director decision.
