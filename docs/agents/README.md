# RevisionWorx — Engineering Assignment Orders

These are the current standing orders issued by the Project Director for the three independent ChatGPT specialists. **Assignment names replace all alphabetic agent labels.** Claude remains Lead Architect.

| Assignment | Orders | Development branch | Progress file |
| --- | --- | --- | --- |
| QA Engineer | [QA_ORDERS.md](QA_ORDERS.md) | `qa/testing-infrastructure` | `QA_PROGRESS.md` |
| OpenSchematix Integration Engineer | [OPENSchematix_INTEGRATION_ORDERS.md](OPENSchematix_INTEGRATION_ORDERS.md) | `integration/openschematix` | `OPENSchematix_INTEGRATION_PROGRESS.md` |
| Distributed Systems Engineer | [DISTRIBUTED_SYSTEMS_ORDERS.md](DISTRIBUTED_SYSTEMS_ORDERS.md) | `research/shared-filesystem-protocol` | `DISTRIBUTED_SYSTEMS_PROGRESS.md` |

**Instructions:** Read your assignment's orders from `main` at the beginning of every session, even if your working branch does not yet contain these new files. Preserve and rename old progress logs on your existing branch. Do not replace your current branch with `main` or overwrite ongoing work just to read orders.

These standing orders supplement `CLAUDE.md` and repository requirements. The Project Director approves merges and releases. Agents must report material conflicts to the Lead Architect.

The existing Phase 1 implementation is on `phase1-foundation`; inspect it when evaluating actual behavior. Do not assume that Phase 1 is merged to `main`.

## Current assignment execution and evidence discipline

Each role's **Active Director priorities** section is the current task queue; historical results in PR descriptions and progress logs are background, not a substitute for checking the newest branch head and CI. Before adding work, inspect active workflows and cancel/supersede only in accordance with existing workflow policy; avoid successive documentation-only commits that repeatedly cancel unverified test runs. Prefer a cohesive code/test batch, then wait for CI results before issuing a definitive verification report. A queued, running, cancelled, or failed run is not a pass. Record both the tested code SHA and the test/CI SHA when using disposable cross-branch overlays.

When reporting a known defect, distinguish a **passing reproduction of unsafe behavior** from a **passing safety acceptance test**; a strict xfail marks an open requirement, not a resolved defect. Report independent evidence once in the appropriate existing issue/PR rather than duplicating comments. Claude owns production integration; agents supply verified tests, isolated prototypes, and documented interface proposals. No specialist may merge, release, retarget another owner's PR, or independently approve multi-user safety.
