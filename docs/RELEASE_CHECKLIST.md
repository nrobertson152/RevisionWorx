# RevisionWorx 1.0.0 release checklist (Director)

Releases, tags and licensing are the Director's alone (AGENTS.md). Everything up to the decision has been
prepared on `main`; this page lists what is done, what needs you, and the exact steps.

## Release candidate

The release candidate is the head of `main` at the time you decide. Use the commit whose **CI** and **Package**
runs are both green (Actions tab, filter by branch `main`). Record that SHA below.

| | |
|---|---|
| Candidate commit | `__________` |
| CI run (all jobs green) | `__________` |
| Package run (all jobs green) | `__________` |

## Done (automated, every commit on main)

- [x] Full suite on Linux (Python 3.10, 3.13) and Windows (3.11, 3.13), including Qt widget tests offscreen.
- [x] Windows real-contention and checkout-race tests, repeated 5× per job.
- [x] Built wheel installed into a clean environment and launched from outside the source (Windows, Linux).
- [x] Keyboard-driven walkthrough of the window on a real X display, including kill -9 and restart.
- [x] Standalone Windows x64 and Linux x86-64 builds: `--version`, init/status/doctor/change list, and
      `revisionworx-gui --self-test` all pass before the build is kept.
- [x] Independent adversarial QA suite (`tests/qa`) in every run; its 3 strict xfails are documented
      limitations, not open defects.
- [x] README, CHANGELOG, user guide, known limitations, licences and third-party notices in the packages.

## Needs you

- [ ] **Human GUI acceptance** (about 30 minutes): follow `docs/GUI_ACCEPTANCE.md` on the packaged build of
      the candidate commit, on Windows (and Linux if you use it). Record pass/fail per step on the PR or an
      issue. Nobody has done this yet.
- [ ] **Accept the 1.0 scope** in `docs/KNOWN_LIMITATIONS.md`, in particular: several computers writing one
      network folder is **not verified** (warned in the product, one writer at a time).
- [ ] **Optional: the official logo.** Supply the original logo file (PNG, ideally 1024×1024 or larger) to add
      as the application icon (`docs/REVISIONWORX_BRANDING_DIRECTIVE.md`). Without it the builds use the default
      icon. Adding it means a new candidate commit.

## Release steps (after the boxes above)

1. In `CHANGELOG.md`, replace "(unreleased: awaiting the Director's release decision)" with the release date
   (or ask Claude to). That is a new commit on `main`; wait for its CI and Package runs to go green and use it as
   the candidate.
2. Tag the candidate and push the tag. The tag triggers a **Package** run that builds the release files from
   exactly that commit:
   ```bash
   git tag -a v1.0.0 <candidate-sha> -m "RevisionWorx 1.0.0"
   git push origin v1.0.0
   ```
3. When that Package run is green, download its artifacts: `standalone-windows-x64`,
   `standalone-linux-x86_64`, `python-packages`.
4. Create the GitHub Release for `v1.0.0` (Releases › Draft a new release). Title "RevisionWorx 1.0.0"; notes:
   the 1.0.0 section of `CHANGELOG.md` plus a link to `docs/KNOWN_LIMITATIONS.md`. Attach:
   `RevisionWorx-1.0.0-windows-x64.zip` and `.sha256`, `RevisionWorx-1.0.0-linux-x86_64.tar.gz` and `.sha256`,
   `revisionworx-1.0.0-py3-none-any.whl`, `revisionworx-1.0.0.tar.gz`, `SHA256SUMS`.
   With the GitHub CLI, the same in one step:
   ```bash
   gh release create v1.0.0 --title "RevisionWorx 1.0.0" --notes-file <notes.md> \
     RevisionWorx-1.0.0-windows-x64.zip* RevisionWorx-1.0.0-linux-x86_64.tar.gz* \
     revisionworx-1.0.0-py3-none-any.whl revisionworx-1.0.0.tar.gz SHA256SUMS
   ```
5. Optional: publish the wheel to PyPI (`twine upload revisionworx-1.0.0*`). The name `revisionworx` must be
   available to you there.

## After the release

- Start `1.0.1.dev0` / `1.1.0.dev0` on `main` (Claude can do this).
- Post-1.0 backlog: issue #28 (status table) and #36 (roadmap); multi-computer verification: #5.
