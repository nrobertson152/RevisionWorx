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
