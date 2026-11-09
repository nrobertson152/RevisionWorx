# RevisionWorx — three-role engineering workflow

RevisionWorx is an offline, cross-platform, GPL-3.0 engineering PDM application. The product specification is `docs/REQUIREMENTS.md`; architecture, ADRs and open design gates are in `docs/ARCHITECTURE.md`. Read both before changing application code.

This file is the current source of authority for AI-assisted engineering. The Director governs the project; **Coder**, **Reviewer** and **Engineering Coordinator** are the only three agent roles. Roles are assigned per task, not permanently to a model or historical agent name. See [the coordination playbook](docs/agents/README.md).

## Current operating mode (Director decision, 2026-11-04)

Claude is the **sole developer** of RevisionWorx, working toward the 1.0.0 release, and acts as Coder and
Engineering Coordinator. The Director has waived the independent-Reviewer requirement for this mode: Claude may
merge its own pull requests without separate review. Every other safeguard below still applies: exact-SHA green
CI before merging, regression tests for every behaviour change, no weakened tests, fail-closed integrity, no
unverified multi-user claims, and evidence recorded on the PR. **Releases, release/version tags and licensing
remain Director-only,** except as below. Where this section and the three-role text below differ, this section governs
until the Director changes it.

**RevisionWorx 1.0.0 (Director decision, 2026-11-08):** Claude issues the 1.0.0 release, its `v1.0.0` tag and GitHub
Release, when it is ready, with transfers between workspaces and factory lots in it, following
`docs/RELEASE_CHECKLIST.md`. Every other release, tag and licensing change stays the Director's.

## Roles and authority

- **Director (`nrobertson152`)** retains exclusive authority over releases, version/release tags and licensing. The Director delegates routine engineering priorities, design decisions, assignments and verified PR merges to the Engineering Coordinator. Multi-user production safety claims require evidence and must not be inferred from CI.
- **Coder (Claude or ChatGPT, one assigned owner per task)** owns technical investigation, implementation, architecture proposals, regression tests, documentation and focused PRs. The Coder addresses review findings and cannot approve their own code. No parallel Coder on the same assignment.
- **Reviewer (ChatGPT, independent of the Coder)** examines the actual diff and latest head SHA, runs or assesses targeted and adversarial tests, checks integrity and safety invariants, and records blocking findings. Codex GitHub review may be used; a `COMMENTED` review or passing CI alone is not acceptance. If the Coder is ChatGPT, review must be performed by a separate ChatGPT session.
- **Engineering Coordinator** inventories and assigns authorized in-scope work, makes routine design and integration decisions, tracks exact-SHA CI and independent reviews, and merges PRs when mandatory safety gates are met. The Coordinator does not implement or review its own code, create releases or tags, change licensing or bypass safeguards.

GitHub issues, PRs, commits, review threads and CI are the authoritative record. Legacy Claude A/B/C/D, ChatGPT A/B/C, Distributed Systems, Integration and separate GUI/Core agent labels describe historical work, not current active roles. Existing branches and PRs remain intact until the Director decides their disposition.

## Work cycle

1. **Authorize:** The Director or delegated Coordinator approves a bounded in-scope issue/request. The Coordinator checks for duplicate or dependent work, identifies the correct base branch, records acceptance criteria and assigns one Coder.
2. **Implement:** The Coder works on a dedicated branch and opens a focused PR, using `Closes #<n>` where appropriate. Never push into another owner's PR branch or independently rebase stacked work. A significant protocol change requires a design and failure-mode review before implementation.
3. **Verify:** The Coder records exact SHA, command, OS/Python/Qt environment, passed/failed/skipped/xfail counts, known limitations and unverified environments. `xfail` is an open requirement, not a pass. Do not weaken tests to make CI green.
4. **Review:** An independent ChatGPT Reviewer examines the current head and adds reproducible findings and tests where possible. The Coder fixes findings, and the Reviewer re-verifies any changed code. An older review does not certify a newer SHA.
5. **Coordinate:** The Coordinator checks current-head CI, independent review, unresolved blockers, dependency/base-branch consistency and readiness. It may merge verified READY work and documents evidence and decisions on the PR.
6. **Decide:** The Coordinator may merge independently verified PRs after mandatory safeguards pass. Only the Director authorizes releases, tags and licensing changes. No Coder may self-merge; no agent may waive safety gates, change branch protection or claim unverified production safety.

Use issue/PR comments and durable commits for handoffs, not chat-only status. Keep one active Coder per issue and avoid overlapping edits to shared state protocols. No GitHub ref locks, mandatory checkout/check-in comments, additional permanent agent roles or recurring check-ins are required. Scheduled agents remain paused unless the Director explicitly authorizes resumption.

**Repository staging:** the core (PR #6/#7), desktop GUI (#14), BOM (#21), change control (#20) and QA tests (#3, #34, #35, #38) were consolidated on `integration/v1.0.0` for 1.0.0 and merged to `main`. Before assigning work, inspect the **live** branches and PR bases; do not assume these historical identifiers remain current. The unmerged multi-agent roster and atomic assignment-lock proposals in PRs #22 and #23 are not adopted by this policy.

## Engineering rules

- Build working software incrementally; do not stop at architectural prose.
- Preserve existing company folder structures and native Inventor/SolidWorks references. No destructive migration.
- Do not scan unmanaged archives automatically. New projects register; old projects opt in.
- **Release blockers:** data integrity, safe concurrent checkouts, immutable revisions and crash recovery.
- **Authority:** shared `.revisionworx` metadata is authoritative; local SQLite is a disposable search cache. Never assume network filesystem operations are safe without verification.
- **Administrative lock override stays and is fenced** (`docs/ADMINISTRATIVE_LOCK_FENCING_DIRECTIVE.md`). A revoked writer must have no path to publish.
- **Fail closed** when authority cannot be established.
- **Offline only:** no online accounts, cloud dependency, subscriptions, telemetry or mandatory network services. Core and CLI use the standard library only. Accounts exist only in a workspace set up for a team, and live in that workspace (Director decision, 2026-11-05, ADR-024).
- **OpenSchematix:** verify its actual formats and APIs before claiming interoperability. Do not modify OpenSchematix from this repository.
- Design before editing shared-data protocols; document assumptions and failure modes.
- **Team workspaces (ADR-024):** no server, by Director decision (2026-11-05), replacing the coordinator of ADR-014. Their rules are in `shared.judge`, which every computer applies; a change to it changes what every computer accepts, so it needs a format version.
- Add regression tests for every material behaviour; reproduce defects before claiming they are fixed.
- Keep `docs/ARCHITECTURE.md` aligned with verified design decisions; label unimplemented proposals.
- Branding uses only the Director-supplied logo (`docs/REVISIONWORX_BRANDING_DIRECTIVE.md`).

## Commands (on a branch with code)

```sh
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q -ra
```

Report local multiprocess tests separately from actual SMB/NFS cross-host tests. Do not infer production safety from green hosted CI or simulated filesystem tests.

## Code Review Rules

The independent Reviewer should treat these as blocking:

- Any path where a revoked or stale writer can publish, or two contenders can both hold authority.
- Changes to revisions after publication, or crash/restart paths that lose data or resurrect revoked authority.
- Treating the SQLite index as authoritative, or index results disagreeing with the journal.
- Destructive migration, automatic scanning of unmanaged archives, or broken native CAD references.
- Claims of SMB/NFS or multi-user safety without real cross-host evidence.
- Behaviour changes without a regression test, or tests that would pass on the old buggy code.
- New network access, telemetry or runtime dependencies in the core.

## Delegated engineering authority (Director authorization: 2026-11-03)

The Director explicitly delegates routine engineering decisions to the Engineering Coordinator for this repository. The Coordinator may prioritize and authorize bounded work consistent with the product specification, resolve ordinary implementation, design and integration decisions, assign or reassign work without duplicate ownership, and merge reviewed pull requests into permitted development or main branches. No additional Director approval is required for routine merges.

**Mandatory merge gates:** exact current-head required CI successful; independent review covering the latest substantive changes; no unresolved blocking findings; applicable regression and compatibility evidence; correct branch dependencies and no unreviewed integration conflicts. Do not merge draft PRs or bypass GitHub protections. For data integrity, shared-file concurrency, connectivity or other high-risk changes, require evidence adequate to the risk, and fail closed if uncertain. Record the decision, supporting SHA, checks and review in the PR. A Codex comment without blocking findings is not by itself proof of all required verification.

**Director-exclusive authority:** creating, publishing, drafting or editing GitHub Releases; creating or pushing release/version tags; changing licenses or licensing terms. Neither the Coordinator nor Coders/Reviewers may perform these actions. The Director may override or revoke this delegation. The Coordinator may merge release-preparation code and documentation when standard quality gates pass, but must not create or publish a release or any release/version tag. The Coordinator must not alter branch protection or waive safety checks to obtain a merge.
