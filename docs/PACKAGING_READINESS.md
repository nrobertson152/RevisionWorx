# RevisionWorx — experimental Windows/Linux packaging readiness

**Owner:** OpenSchematix Integration Engineer (isolated draft PR #2).  
**Status:** PRE-ALPHA EXPERIMENT. Frozen Windows/Linux CLI and Qt-offscreen startup smoke **PASS** at Phase 2 SHA ffcfb463, [CI run 41388877999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388877999). No installer, upgrade mechanism, signed package or release is created.

## Source and build contract

The inspected Phase 2 source has Python >=3.10, a standard-library CLI, optional PySide6-Essentials GUI, and separate GUI/CLI entry points. Its declared version is **0.1.0.dev0**. This builder intentionally narrows validation to **Python 3.11**.

The first test target is the exact Phase 2 commit **ffcfb463bc050ff460279fd92f6758e0215f73e3**. The build will fail if the checkout SHA differs. Build tools are pinned at top level (PyInstaller 6.13.0, PySide6-Essentials 6.8.3, setuptools 75.8.2, wheel 0.45.1). The output captures a full pip freeze, source SHA, version, platform, Python runtime and binary SHA-256 hashes in provenance.json and dependencies.txt. These do **not** constitute a fully hash-locked or bit-for-bit reproducible build; that is a separate release gate.

The builder produces two **one-directory** executables inside a newly created output directory:

- dist/RevisionWorx/RevisionWorx (or RevisionWorx.exe) — GUI, PySide6 bundled
- dist/RevisionWorxCLI/RevisionWorxCLI (or RevisionWorxCLI.exe) — command line

The builder refuses to write inside the source checkout or overwrite any existing output. No shared workspace or managed CAD document is touched. These directories are **portable proof-of-concept bundles**, not native Windows installers, Linux AppImages or distribution packages.

## Repeatable build commands

First check out RevisionWorx at the pinned SHA into a **separate path** outside the integration checkout. Keep output outside that source checkout.

On Linux, with Python 3.11:

    python3.11 tools/packaging/test_builder.py
    bash tools/packaging/build-linux.sh --source /tmp/revisionworx-phase2 --output /tmp/revisionworx-bundle-test --expected-sha ffcfb463bc050ff460279fd92f6758e0215f73e3

On Windows PowerShell, with Python 3.11 available through python:

    python tools\packaging\test_builder.py
    .\tools\packaging\build-windows.ps1 --source C:\Temp\revisionworx-phase2 --output C:\Temp\revisionworx-bundle-test --expected-sha ffcfb463bc050ff460279fd92f6758e0215f73e3

On Ubuntu-family Linux systems, the PySide6 6.8.3 runtime requires system graphics libraries. The verified GitHub Actions environment installed **libegl1, libgl1, libopengl0, libxkbcommon-x11-0** and the relevant libxcb libraries (cursor, image, keysyms, render-util, xkb, icccm and shape). These are OS packages and are **not** guaranteed to be embedded by PyInstaller. Missing libEGL.so.1 caused the first Linux GUI smoke to exit, so native dependency resolution is an explicit deployment requirement.

Both wrappers invoke the same tools/packaging/build.py and create an isolated build virtual environment. Build dependencies must be downloaded, but bundles are not designed to require cloud services. To build without internet, provide a separately vetted offline wheelhouse and dependency lock; offline build itself is NOT TESTED.

## Scope and interpretation of the smoke test

The build script invokes tools/packaging/smoke_installed.py against the **bundled binaries**. CLI '--help' must succeed. The Qt GUI is started in a clean temporary user profile with QT_QPA_PLATFORM=offscreen, must stay alive for 2.5 seconds, then is terminated. The smoke clears REVISIONWORX_WORKSPACE and isolates HOME, APPDATA, LOCALAPPDATA and XDG paths.

This checks bundled executable **startup**, not an actual installed desktop UI, visual rendering, navigation, file dialogs, shortcuts, DPI, signing, an installer, or native OS integration. Qt-offscreen success cannot be substituted for interactive installed GUI acceptance. The workflow does not upload binaries or publish releases.

## Independently observed packaging execution

- [Run 41388621198](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388621198), source SHA **ffcfb463**: Windows Python 3.11 bundling/CLI and Qt-offscreen startup succeeded; Linux bundled GUI exited 3 from a masked PySide6 import failure. The CLI build itself completed.
- [Run 41388766343](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388766343): explicit top-level QtWidgets import in the experimental GUI entry point exposed the Linux dependency error **libEGL.so.1 not found**; failure was correctly retained rather than accepted as a skipped GUI test.
- **[Run 41388877999](https://github.com/nrobertson152/RevisionWorx/actions/runs/41388877999)**, integration packaging SHA **17cc1cce846df63b4ddab0dc137f495e3f3101d9**, pinned Phase 2 SHA **ffcfb463bc050ff460279fd92f6758e0215f73e3**: both Ubuntu and Windows Python 3.11 jobs **SUCCESS**. Each executed **2 packaging safety unittests passed**, built separate frozen GUI and CLI one-dir directories, passed frozen CLI help, passed frozen Qt GUI startup survival in a disposable Qt-offscreen profile, and emitted provenance with application version **0.1.0.dev0** and binary SHA-256. **No user-visible interactive GUI, native installer, migration or existing-data acceptance was performed.**

This confirms the isolated bundle *startup* gate only. The initial Linux failure is an environmental prerequisite discovered and corrected in the experimental CI environment, not a production-code change.

## Non-destructive upgrade, migration and path/permissions matrix

**NOT EXECUTED** is an explicit unverified release gate, not a successful test. All future data tests must use a disposable **copy**, never a live customer workspace.

| Gate | Required scenario and acceptance | Status | Severity / owner |
| --- | --- | --- | --- |
| OS-specific bundled GUI/CLI startup | Windows/Linux CI, frozen CLI help and offscreen GUI process | PASS (Windows/Linux Python 3.11 bundled CLI+GUI, run 41388877999); not interactive | P1 / Integration |
| GUI interaction in native desktop | Visible windows, menus, file dialogs, icons, keyboard/mouse, DPI, Wayland/X11 and Windows desktop | NOT EXECUTED | P1 / QA |
| Signed native installer/uninstaller | Windows MSI/wizard, Linux AppImage/deb, shortcuts, publisher identity, clean rollback | NOT IMPLEMENTED | P1 / Director |
| Existing workspace non-destruction | Copy workspace, compare journal/records/content SHA before/after packaged read | NOT EXECUTED | P0 / Claude+QA |
| Versioned migration or downgrade | Old snapshot, version gate, dry run, restore/rollback without rewriting original | NOT IMPLEMENTED | P0 / Claude |
| Upgrade crash/power interruption | Interrupted installation and first app launch against cloned workspace, no data loss | NOT EXECUTED | P0 / Claude+QA |
| Cross-platform CAD paths | UNC, spaces, Unicode, long paths, case collisions, POSIX mounts, intact native CAD references | NOT EXECUTED | P0 / QA |
| Permissions/read-only app dir | Windows Program Files UAC, Linux read-only install, user cache/config/state, denied ACL | NOT EXECUTED | P0/P1 / QA |
| Revision checkout/check-in/restore | Verified digest, immutable history, lock cleanup and interrupted release on Windows/Linux | NOT EXECUTED for installed build | P0 / Claude+QA |
| Concurrent Windows writer lock | Repeated six-process races; no WinError 32, lost token or stale mutation (#8/#10) | Independent acceptance pending | P0 / Claude+QA |
| Local index consistency | Insert/delete watermark and restart (#9) | Independent acceptance pending | P0 / Claude+QA |
| GC/txid/history correctness | Phase 2 newer GC, idempotency, revision-chain corruption and recovery | Independent acceptance pending | P0 / Claude+QA |
| Physical two-host SMB certification | Disconnect/restart/fencing, crash consistency, authenticated coordinator for multiuser mode | NOT EXECUTED | P0 / Distributed+QA |
| Qt/licensing dependencies | GPL/LGPL license text, notices, Qt plugin audit, PyInstaller redistribution obligations | NOT EXECUTED | P1 / Director |
| Offline operation and install | No network/account/telemetry, offline wheelhouse and runtime, audit dependency calls | NOT EXECUTED | P1 / QA |
| Bundle integrity/reproducibility | Full transitive hashed locks, signatures and artifact manifests, multi-run bitwise comparison | NOT EXECUTED | P1 / Packaging |

**Data-preservation rule:** never install, upgrade, or uninstall application files inside a .revisionworx authoritative workspace or a managed CAD directory. Uninstall must never delete journal records, immutable revisions, checkout tokens, engineering files, or user backups. There is no implemented migration/updater here.

## Merge/release coordination

The safe proposed sequence remains director review of independent QA for #8/#9/#10 and newer Phase 2 reliability changes; authorized Phase 1 (#6) integration, authorized Phase 2 (#7) integration, then approval of PR #2's read-only adapter and packaging prototype. QA PR #3 and Distributed Systems PR #4 have separate review gates. No agent may automatically merge or release.

The isolated focused packaging workflow is .github/workflows/revisionworx-packaging-readiness.yml. It runs on packaging-file changes or manual dispatch only, not on ordinary documentation updates. It does not modify Claude's core modules or QA's workflows, and it never publishes an artifact. All future production packaging workflows require Claude/QA review and Director approval.

**Release status:** NOT READY. A passing bundle smoke will be evidence only for the two subprocess checks above.