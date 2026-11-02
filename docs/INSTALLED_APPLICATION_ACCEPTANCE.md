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
| Pinned onedir Linux/Windows bundled startup | Automated offscreen smoke verified on earlier Phase 2 `ffcfb463`; new `d656884b` rerun is a separate result | Integration |
| Copied existing workspace preservation under frozen CLI and Qt-offscreen GUI | **PASS** (disposable current-schema copy, 5 hashed files, Windows/Linux Python 3.11, 41390661790); not an old-version migration | Integration |
| Fully interactive installed GUI on actual Windows/Linux desktop | **NOT EXECUTED** | QA + Director |
| Native installer and uninstaller, signing and redistribution compliance | **NOT IMPLEMENTED** | Release engineering |
| Actual migration, backward compatibility, power-loss rollback and ACLs | **NOT EXECUTED** | Claude + QA |
| #8 writer fencing, #9 index snapshot, #10 Windows lock correctness | Issues remain open; independent acceptance must be separately cited | Claude + QA |
| GC/reference safety, txid idempotency, revision chain integrity | New Phase 2 changes need independent acceptance | Claude + QA |
| Real two-host authenticated SMB coordinator with failure/recovery | **NOT EXECUTED** | Distributed Systems + QA |

**Director approval is required** before PR retarget, merge, release or publication. PR #2 is a draft research/verification branch. No packaging smoke test authorizes production multi-user claims, migration, or destructive actions.
