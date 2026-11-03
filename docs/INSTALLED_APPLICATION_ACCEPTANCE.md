# RevisionWorx — installed application and upgrade acceptance checklist

**Status:** QA/Director manual acceptance procedure; not evidence of a finished installer.
**Assignment:** Integration & Release Readiness Engineer. Production source remains Claude-owned.

## 1. Test subject and provenance

Record the exact full commit SHA for (a) the packaging harness on `integration/openschematix`, (b) the independent Phase 2 source build, and (c) the Phase 1 parent of Phase 2. Record OS build, architecture, Python builder version, PyInstaller version, PySide6 version, actual runtime Qt plugin dependencies, executable SHA-256 and any code signing identity. Do not treat `0.1.0.dev0` as a released or stable file-format version.

The acceptance matrix must distinguish four levels:

| Level | Verification method | Claim allowed |
| --- | --- | --- |
| A | Source CLI or PySide6 offscreen unit test | Source import or widget action, not packaging |
| B | Frozen one-directory CLI subprocess and Qt-offscreen startup | Bundled executables start on this runner with installed OS dependencies |
| C | Actual desktop install or copied bundle launched interactively on a real display | Tested UI, keyboard, dialogs and desktop integration on that OS |
| D | Real installation/update/uninstallation against disposable copies of old engineering workspaces | Compatibility, rollback and data preservation for the *specific versions tested* |

**Current evidence reaches level B only.** Linux/Windows [run 41390661790](https://github.com/nrobertson152/RevisionWorx/actions/runs/41390661790) also demonstrates **copied-workspace preservation for five files** and Unicode/spaces paths in a disposable Qt-offscreen test; it does **not** reach level C interactive desktop or level D actual installer/migration. The packaging jobs are experimental one-directory bundles, not MSI, AppImage, .deb or system installers.

### Latest qualified scope (not level C or D)

[Run 41392572210](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210) (packaging `ccb5520b31209eeb4e03943afc89b8b981474fc1`,
Phase 2 `d656884b39401134149273dd0121ef620b76eb8e`) demonstrated Ubuntu frozen binaries starting inside a
network-disabled container without Python/pip. The Windows frozen CLI
qualified a **401-character local path**, a Windows extended-length path, an
existing localhost administrative UNC share and rejection of a byte-range
locked workspace manifest, without mutating the sample workspace.
Both platforms passed injected journal failures and a disposable
backup/recovery rehearsal; these are **not actual installer migration,
real power-loss, physical ENOSPC or production ACL denial**. A clean Windows
VM with Python uninstalled and outbound network blocked is still required.

## 2. Explicitly safe test-data preparation

- [ ] Create a new dedicated VM or test user; record operating-system build and filesystem (NTFS, ext4, etc.).
- [ ] Make at least three **separate disposable copies** of a representative known-good workspace: untouched baseline, upgrade candidate and deliberate-failure candidate. Preserve the original sample outside the installer and application folders.
- [ ] Preserve and verify SHA-256 of *all* `.revisionworx` journal, state, records and content files, and a CAD project sample. Record file counts, journal sequence, revision IDs and document records. Verify backup restores before any destructive procedure.
- [ ] Include a real `.schematix`, legacy `.osch`, unrelated CAD file, Unicode filename and filename with spaces. Record behavior of symbolic links and filesystem case collisions separately.
- [ ] Ensure the product is not configured to open a real company workspace via command arguments, `REVISIONWORX_WORKSPACE` or recent-workspace preferences.
- [ ] Use a local standalone test first; no SMB/NFS writes until the multi-host authority qualification gate is independently approved.

## 3. Windows native application validation

- [ ] Test both fresh user and existing user profile; launch frozen `RevisionWorx.exe` on a real Windows desktop **without** `QT_QPA_PLATFORM=offscreen`.
- [ ] Verify a visible primary window, actual projects table, Documents tab, menus, keyboard focus, shortcuts, contextual commands, resize, scaling at 100%/150%/200%, multiple monitors, accessibility and high-contrast colors.
- [ ] Open only the disposable copy, navigate existing documents/revisions, verify no title or revision history was silently rewritten. Repeat clean launch after a reboot.
- [ ] Exercise `RevisionWorxCLI.exe --help`, `--version`, and `--workspace COPY --json status`; verify exit codes and that the copied workspace's metadata hashes and journal sequence are unchanged after read-only operations.
- [ ] Test a read-only application directory, a standard non-admin user, long paths and Unicode, restricted directory ACL, spaces in Program Files, and Windows Defender/SmartScreen warnings. Record `WinError 32` if observed, including lock ownership.
- [ ] If an MSI/EXE installer is later introduced, verify install/repair/uninstall, shortcuts, Uninstall registry entry, unprivileged behavior, rollback and no deletion of user CAD or PDM data. **There is currently no installer to execute.**
- [ ] Test an interrupted install or upgrade in a VM snapshot, not on a live share; verify immutable revision data after rollback.

## 4. Linux native application validation

- [ ] On the target distribution and supported minimum glibc baseline, launch the onedir `RevisionWorx` on an actual **X11 and Wayland** desktop (separate runs, no offscreen override).
- [ ] Verify correct Qt platform plugin resolution, visible window, SVG/icon/font resources, keyboard focus, file chooser behavior, accessibility, HiDPI and system appearance.
- [ ] Verify system libraries such as `libegl1`, `libgl1`, `libopengl0`, `libxkbcommon-x11-0`, `libxcb-cursor0`, `libxcb-image0`, `libxcb-keysyms1`, `libxcb-render-util0`, `libxcb-xkb1`, `libxcb-icccm4` and `libxcb-shape0` on the actual target. These may not be bundled. The initial Ubuntu CI failure was `libEGL.so.1`; revised test-runner prerequisites resolved it.
- [ ] Run under an ordinary non-root user with a read-only application directory, writable per-user XDG config/cache, and a cloned read-only workspace. Check file permissions, blocked symlinks, POSIX mount points, long and Unicode paths.
- [ ] If a .deb/AppImage is introduced, test offline install, launcher/icon registration, upgrade and uninstall on at least two supported distributions; do not conflate an onedir binary with an AppImage.

## 5. Upgrade, migration and rollback acceptance

- [ ] Snapshot old-version workspace and compare manifest schema/version, journal hashes, references, content-store SHA-256, project paths, checkout state and actor history against the new version before launching.
- [ ] Open a **cloned** old-version workspace with the candidate packaged app, first as a read-only user. No writes are permitted during read-only navigation or `status`.
- [ ] If a migration is required, require an explicit documented migration plan, a verified backup, rollback and a deterministic error on unsupported future format. The current prototype **does not implement migration**.
- [ ] Test controlled interruption at installation, first launch, first document revision read, checkout, check-in, recovery, GC dry-run and rollback. Verify no source CAD file is modified by indexing.
- [ ] Verify that uninstall/reinstall leaves the complete workspace, immutable revisions, content objects and backups intact.
- [ ] Record expected and actual SHA-256, missing content objects, stale caches, deterministic error states and exact recovery commands. Never infer safety from a green offscreen smoke.

## 6. Final release gates and authorization

| Gate | Current status | Owner |
| --- | --- | --- |
| Pinned onedir Linux/Windows bundled startup | **PASS** on exact Phase 2 `d656884b`: frozen offline/failure readiness [run 41392572210](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210) | Integration |
| Copied existing workspace preservation under frozen CLI and Qt-offscreen GUI | **PASS** (disposable current-schema copy, 5 hashed files, Windows/Linux Python 3.11, 41390661790); not an old-version migration | Integration |
| Fully interactive installed GUI on actual Windows/Linux desktop | **NOT EXECUTED** | QA + Director |
| Native installer and uninstaller, signing and redistribution compliance | **NOT IMPLEMENTED** | Release engineering |
| Actual migration, backward compatibility, power-loss rollback and ACLs | **PARTIAL** disposable corrupt-copy backup restoration and injected I/O; **NOT EXECUTED** real old-version migration, power interruption or ACL denial | Claude + QA |
| #8 writer fencing, #9 index snapshot, #10 Windows lock correctness | Issues remain open; independent acceptance must be separately cited | Claude + QA |
| GC/reference safety, txid idempotency, revision chain integrity | New Phase 2 changes need independent acceptance | Claude + QA |
| Real two-host authenticated SMB coordinator with failure/recovery | **NOT EXECUTED** (localhost admin UNC read is not two-host certification) | Distributed Systems + QA |

**Director approval is required** before PR retarget, merge, release or publication. PR #2 is a draft research/verification branch. No packaging smoke test authorizes production multi-user claims, migration, or destructive actions.

## Offline frozen CLI lifecycle — additional automated Linux gate

`tools/packaging/offline_lifecycle.sh ABSOLUTE_CLI` creates its own disposable
workspace and engineering root, with spaces and Unicode. It accepts no workspace
argument. Run as a non-root user. `jq`, `sha256sum` and ordinary shell tools are
**test-harness** prerequisites, not product dependencies. No Python is used.
The existing `smoke_offline_container.sh` now calls this gate before GUI startup;
the CI container remains network-disabled, read-only with only `/tmp` writable,
and mounts only frozen bundles and the harness. `jq` is installed at image-build
time, never downloaded during acceptance.

The gate asserts project creation, file registration with initial revision digest,
checkout, editing and check-in from a new process using persisted local tokens,
two-entry history on reopen, verified revision blobs, and historical revision
export matching the original bytes. It removes write permissions from the
engineering root and requires project creation to fail, without a created folder
or a registered failed project; existing revisions must still verify. Cleanup
restores test-directory permissions even when an assertion fails.

Passing means frozen **CLI** workflow and normal process-restart persistence.
It does not mean GUI-driven document import, unexpected-kill recovery, native
installer acceptance, Windows ACL behavior, or suitability of the pinned source
for production. A no-network container establishes unavailable connectivity,
not absence of attempted socket calls. Local Linux runtime evidence must not
be substituted for the Windows job or actual interactive GUI QA.

### Failure and deployment evidence capture (disposable VM only)

For each manual scenario below record old/new executable hashes and full source
SHAs, OS/filesystem, baseline workspace file hashes and revision IDs, the exact
fault boundary, exit codes, operator-visible error, and hashes after recovery.
Use a separate immutable baseline, candidate and restore destination. Never point
these procedures at production data or an engineering share.

| Scenario | Procedure and pass condition |
| --- | --- |
| Termination during operation | On a VM snapshot, terminate the candidate at separately recorded document import, checkout and check-in boundaries. Restart with documented recovery; verify every previously committed revision by digest and reconcile any in-flight outcome before retry. Process survival alone is not a pass. |
| Disk full / interrupted write | Use a dedicated size-limited test volume, verify it is actually full, then run each write. Require an actionable error and verify committed content after freeing space/restart. Injected ENOSPC tests remain separate evidence. Do not fill the system volume. |
| Read-only / permissions | Run as a standard user against a disposable read-only project and metadata copy; record the denied operation, absence of false success and unchanged committed revision hashes. Test Windows ACLs independently of Unix mode bits. |
| Interrupted upgrade | Snapshot both application directory and workspace before each supported version pair. Interrupt installation and first launch separately; require either the old or new complete application, explicit unsupported-format errors, and verified preserved content. No native installer currently exists, so this gate is unexecuted. |
| Backup / restore | Stop all writers, copy the complete authority and blobs to a separate backup, restore into a new destination, verify all revision digests and workspace identity with the installed application. Do not restore over a working original. |
| Rollback / compatibility | Keep both binaries and pristine old-version data; upgrade only a copy. Reopen with old and new versions only where the format policy permits. A format refusal is recorded as a limitation, never bypassed by editing a manifest version. Current-schema copies are not historical migration evidence. |

Linux desktop launchers, executable permissions and XDG locations must additionally
be tested on real X11/Wayland desktops; Windows long-path and localhost UNC checks
remain single-host path checks, not authenticated multi-host share reliability.


## Frozen 0.1.0 Alpha: interactive acceptance still required

The **automated** frozen GUI test in
[FROZEN_DESKTOP_ALPHA_ACCEPTANCE.md](FROZEN_DESKTOP_ALPHA_ACCEPTANCE.md)
passed Ubuntu and Windows at GUI source f00aa31b in run 41566749753.
It drives real Qt widgets in offscreen mode, not a human desktop session.
The separate test-only wrapper accepts an alpha probe command; it must NOT
be distributed as a normal application.

A future manual sign-off for a standard GUI build must capture:

1. Clean OS installation, normal unprivileged user, installed executable
   checksum, source SHA, OS build, filesystem, and whether Python and
   networking are actually absent. Test Windows and Linux X11/Wayland.
2. Launch regular GUI **without the test probe**; confirm visible alpha
   banner says single-user/local-only and unsuitable for valuable data.
   Use only new disposable test files and a VM snapshot.
3. Use mouse, native file picker and keyboard to create/open a project,
   import a distinct source, check out, edit, check in with a meaningful
   description, browse both revisions, export each to separate destinations,
   close GUI and restart a fresh process.
4. Independently verify original source, both revision SHA-256 exports,
   journal/history, correct checkout ownership and user-visible status.
   A read-only reopen must not mutate authority data.
5. Exercise missing working file, denied file or directory, conflicting
   checkout, recovery-blocked workspace and ambiguous commit outcomes.
   Require an explicit refusal or saved-with-warning according to durable
   journal evidence; no false success or destructive auto-repair.
6. Verify keyboard shortcuts, focus order, scroll/table selection,
   screen scaling, unicode+spaces installation path, and exception dialogs.
   Capture screenshots/screens recording and fail/pass with deviations.
7. With no network and no Python in a separate **clean Windows VM**, repeat
   the same workflow. A proxy-disabled CI child is not equivalent.
8. Upgrade and rollback only cloned test workspaces after installer and
   compatibility policies are specified; keep pristine pre-upgrade backups.

None of these human-operated steps has been executed or signed off.
The trusted lock/journal namespace and single-user local filesystem are
alpha scope constraints, not permission to ignore cooperative-writer safety.
