# RevisionWorx — Corrected Phase 2 Core + Desktop Alpha

## Strictly experimental combined-source frozen compatibility (2026-11-02)

**Status: PASS on Ubuntu and Windows for the exact pinned test-only composite, NOT a releasable program, production authority repin, Phase 1 backport, or merge permission.**

### Why this exists

QA and Distributed Systems independently scoped-ACCEPTED the three historically reproducible local-cooperator ADR-019 writer-lock P1 fixes at corrected Phase 2 source commit `fe8a179f0203ef08151dc8459c84609dc639c4fe`. Meanwhile, the standalone desktop GUI code at `17651af68128572c7c83429513044a5948dd07bd` passed its own Windows/Linux CI and frozen Integration matrix. Neither alone proves that **the newer core and earlier GUI code function together**. This experiment checks that precise integration boundary on disposable local project data.

### Reproducible inputs and provenance

| Source | Exact pinned SHA | Scope |
|---|---|---|
| Core | `fe8a179f0203ef08151dc8459c84609dc639c4fe` | Phase 2 corrected candidate, UNMERGED |
| GUI | `17651af68128572c7c83429513044a5948dd07bd` | Claude B desktop alpha GUI, UNMERGED |
| Integration harness | `393485e96385beded0f74693bb26644c129851eb` | Contains test-only composition code and separate workflow |

The workflow `.github/workflows/revisionworx-corrected-core-compatibility.yml` checks out both immutable commits into separate directories. `tools/packaging/stage_corrected_core_gui.py` verifies exact `git rev-parse HEAD` for each checkout, rejects already-modified inputs, and copies **only** `revisionworx/gui/` into a disposable corrected-core checkout. That checkout is intentionally **dirty**; it does not represent an actual Git commit. The staging receipt lists both exact SHAs and SHA-256 of every overlaid GUI file (10 regular files), and is printed in the CI logs. The builder's ordinary `source_sha` names the core checkout HEAD only; **the separate GUI receipt is essential** to establish the true two-source composition.

Source staging is done only on GitHub Actions' disposable runner; neither PR's branch, any authoritative project workspace, nor the user-facing production packaging source is modified. `--alpha-acceptance` injects a **test-only** deterministic frozen GUI probe wrapper; this wrapper **MUST NOT SHIP**.

### Independently verified exact-source CI

**[GitHub Actions run 41572137880](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572137880), workflow code SHA `393485e96385beded0f74693bb26644c129851eb`: both Ubuntu and Windows Python 3.11 jobs COMPLETED SUCCESS.** Each job prints `TEST_ONLY_COMPOSITE_SOURCE_RECEIPT` with the exact source SHAs, `composite_tree_is_dirty_by_design=true`, 10 overlaid GUI files, and success-side `FROZEN_ALPHA_GUI_EVIDENCE`.

Each platform independently built a frozen PyInstaller GUI and CLI from this test composite and ran:

1. Five packaging safety unit tests and original copied-workspace readonly/hash consistency checks.
2. Copied Unicode-and-spaces installation directory, actual offscreen Qt widget create/open project, import an outside fixture, checkout, edit and check-in with description, two journal-backed immutable revisions.
3. `MainWindow.observable_state()` version 1 checks for selected project/document, checkout and check-in action availability and two accessible revision records; F5 must keep selection, and fresh GUI-process restart must reopen two revisions.
4. Negative case: temporarily remove an ordinary working file from the disposable project, F5, assert `file_state=missing`, no check-in and preserved history; restore the same file in `finally`, F5 and require `file_state=unchanged`.
5. Independently launch the frozen CLI to export and SHA-256-verify **both** historic revision content blobs, confirm the source fixture unchanged and no journal/authority mutation from read-only GUI reopening.

**Ubuntu additional:** copied frozen binaries passed the full Qt widget and CLI historical-export workflow inside a nonroot, read-only-root Docker container with **`--network none` and no installed Python/pip**, plus normal (non-probe) frozen GUI launch. Evidence `OFFLINE_ALPHA_GUI_EVIDENCE python=absent network=none copied_bundle=pass qt_create_import_checkout_checkin=pass qt_reopen=pass cli_sha_exports=pass normal_gui_start=pass`.

**Windows additional:** copied frozen binaries completed the full Qt widget and CLI workflow; 401-character local and extended-length paths, localhost administrative UNC read, locked-file read rejection, unchanged original hash map passed. The Windows runner still provides Python to build the program; the frozen child process has it removed from `PATH`, **not** a genuine Python-free disconnected Windows VM.

### Safety and product-scope exclusions

These green builds verify only the listed deterministic scenarios. They do **not** establish:

- Release readiness, production core pin upgrade, permission to backport Phase 1, successful signed installer or upgrade/rollback.
- Human-driven on-screen GUI and native file dialogs, X11/Wayland interactions, accessibility, or operating-system visual behavior.
- Actual power loss, disk exhaustion, real Windows share-mode ACL failures, arbitrary thread schedules, malware/direct manipulation of internal lock directories.
- An attempted-socket/telemetry audit, actual disconnected Windows VM, any two-host SMB/NFS contention, or network-folder collaboration.

The Director-approved near-term product tier is **trusted single-user offline local filesystem** and disposable alpha test data. Network folders are a future product tier. The two independently scoped safety acceptances for corrected ADR-019 rev.4 apply only to the exact core SHA and demonstrated cooperative local-writer interleavings; they are not broad guarantees. Director's authorization and remaining acceptance work are prerequisites to shipping.

### Separation from the original production packaging matrix

`.github/workflows/revisionworx-packaging-readiness.yml` remains unchanged and pins older source `d656884b39401134149273dd0121ef620b76eb8e` independently. The original Linux/Windows acceptance at [41571637265](https://github.com/nrobertson152/RevisionWorx/actions/runs/41571637265) is a separate green CI and cannot be silently transferred to the corrected-core composite. The standalone frozen GUI also stays separately pinned to `17651af` in `.github/workflows/revisionworx-alpha-packaging.yml`.

**Final engineering disposition: TEST-ONLY combined-source compatibility PASS for exact source pair. PR #2 remains draft. No merge/backport/release/authority repin.**

## 2026-11-02 — hardened staging test suite and asynchronous GUI completion

**New test-only stage safeguards:** commit `516db2dec53df3158dd01e40dc395509ac76199b`
adds nine actual local Git fixture tests for the stage operation:
successful exact-SHA two-source overlay and source/receipt hash,
mismatched core SHA, mismatched GUI SHA, preexisting dirty core, dirty
GUI, existing receipt, receipt inside source, identical source folders,
and wrong GUI layout. The composite workflow now executes this suite
on Ubuntu and Windows *before* source staging; workflow change at
`d1daadf09cdc254035cbad8cb4c61b21b1240053`.

**Observed intermittent Windows failure:** [run 41572552095](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572552095)
at `d1daadf` had Ubuntu SUCCESS but Windows FAILURE. On Windows the
GUI had already drawn two revision history rows, but the **immediate**
post-checkin observable state did not yet show the expected enabled
checkout controls. The old test returned when history row count became
two, before waiting for the final asynchronous controller and visible
action state. Log details: `AssertionError('GUI observable lost
committed revisions or checkout controls')`. That result is recorded as
a failure; it is **not** overwritten by the earlier green run. The
observed failure does not independently establish whether the core
commit and journal were incorrect; it exposed an unverified UI
completion boundary in the probe.

**Harness correction:** commit `c18f4ac3d49e4d54d7c77e834ffcdc3a5ee0e6f6`
requires all of: history has two rows, `ctl.busy` false, enabled GUI
checkout action, disabled GUI checkin action. It polls a bounded time
while dispatching actual Qt events; failure now includes the full
`MainWindow.observable_state()` diagnostic. It does **not** weaken
or skip any expected final visible-state/integrity assertion.
Workflow change `955a5423bbdab3b4dd8e0b4a880077886753acab`
makes the independent corrected-core composite matrix rerun when the
probe itself changes.

**[Corrected final CI 41572811571](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572811571),
source pair unchanged**: Ubuntu and Windows Python 3.11 **BOTH
SUCCESS**. Each passed all nine stage-safety regression tests, exact
checkout provenance receipt (core `fe8a179`, GUI `17651af`, 10
overlaid source files), frozen GUI/CLI build, true offscreen Qt widget
two revision lifecycle and visible post-checkin state, refresh/reopen,
temporary missing working-file error/recovery, independently verified
historical SHA-256 exports and unchanged authority snapshot.
Ubuntu additionally passed a full Python/pip-free, network-none
nonroot/read-only-root frozen GUI/CLI workflow plus normal GUI launch;
Windows passed long/extended local paths, localhost UNC and sharing
refusal. Frozen-source test-only wrapper remains **not shippable**.

**Independent baseline unaffected:** older pinned production-source
packaging matrix [41572788046](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572788046)
and standalone GUI source alpha [41572788094](https://github.com/nrobertson152/RevisionWorx/actions/runs/41572788094)
also completed SUCCESS on Ubuntu and Windows at updated probe commit
`c18f4ac`. No production core pin, actual core branch, GUI branch,
PR target, merge, release, tag or backport changed.

Acceptance remains only tested exact composite source functionality
for disposable trusted local workspaces; not a signed installer,
human-operated desktop GUI, real Windows disconnected Python-free VM,
power-loss/cross-host SMB, or release permission.
