# RevisionWorx — working with AI agents

RevisionWorx is an offline, cross-platform, GPL-3.0 engineering PDM (product data management) application. The specification is `docs/REQUIREMENTS.md`; architecture, ADRs and open design gates are in `docs/ARCHITECTURE.md`. Read both before changing code.

## Roles

- **Director (`nrobertson152`)** decides what to build, merges pull requests and approves releases. Only the Director merges, releases, tags, deletes branches or makes multi-user production claims.
- **Claude** is the primary developer and architect. It implements issues, owns the architecture decisions, and opens pull requests.
- **ChatGPT / Codex** does independent review and testing. Codex reviews every pull request automatically. Comment `@codex <request>` on a PR to ask it for a fix.
- **GitHub** holds the record: issues, pull requests and CI.

## How work flows

1. The Director opens an issue.
2. One branch and one focused PR per issue. The PR description says `Closes #<n>`.
3. Tests and Codex review run on the PR. Address the findings, or explain on the PR why a finding is wrong.
4. The Director merges.

Report results, blockers and questions on the issue or PR, not only in chat. For every test claim, give the exact command, the OS and Python version, the commit, and the pass/fail/skip/xfail counts. "Not run" is not "passed". A strict expected failure (xfail) is an open requirement, not a fix. Report local multiprocess tests and real SMB/NFS cross-host tests separately.

**Where the code is:** `main` holds documentation only. The code is on draft PRs #6 (`phase1-foundation`) and #7 (`phase2-documents`, stacked on #6). Until those merge, base code work on the right branch and say which one in the PR.

## Engineering rules

- Build working software incrementally; don't stop at architectural prose.
- Preserve existing company folder structures and native Inventor/SolidWorks references. No destructive migration.
- Don't scan unmanaged archives automatically. New projects register; old projects opt in.
- **Release blockers:** data integrity, safe concurrent checkouts, immutable revisions and crash recovery.
- **Authority:** the shared `.revisionworx` metadata is authoritative; local SQLite is a disposable search cache. Don't assume shared network filesystem operations are safe until verified.
- **Administrative lock override stays, and is fenced** (`docs/ADMINISTRATIVE_LOCK_FENCING_DIRECTIVE.md`). A revoked writer must have no path to publish.
- **Fail closed** when authority can't be established.
- **Offline only:** no accounts, cloud dependency, subscriptions, telemetry or mandatory network services. Core and CLI use the standard library only.
- **OpenSchematix:** verify its actual formats and APIs before claiming interoperability. Never modify the OpenSchematix repository from here.
- Design before editing shared-data protocols, and document assumptions and failure modes.
- Add regression tests for every behaviour; reproduce a defect before claiming it's fixed.
- Keep `docs/ARCHITECTURE.md` in step with actual design decisions, and mark unimplemented ideas as proposed.
- Branding: use only the Director-supplied logo (`docs/REVISIONWORX_BRANDING_DIRECTIVE.md`).

## Commands (on a branch with code)

```sh
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q -ra
```

Hosted Actions are free on this public repository, but avoid redundant matrix runs.

## Code Review Rules

Reviewers (Codex reads this section) should treat these as blocking:

- Any path where a revoked or stale writer can publish, or where two contenders can both hold authority.
- Changes to revisions after publication, or crash and restart paths that lose data or resurrect revoked authority.
- Treating the SQLite index as authoritative, or index results that disagree with the journal.
- Destructive migration, automatic scanning of unmanaged archives, or broken native CAD references.
- Claims of SMB/NFS or multi-user safety without real cross-host evidence.
- Behaviour changes without a regression test, or tests that would pass on the old buggy code.
- New network access, telemetry or runtime dependencies in the core.
