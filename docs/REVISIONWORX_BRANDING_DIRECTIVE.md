# Director Directive — RevisionWorx Official Application Icon and Splash Screen

**Owner:** Lead Architect (Claude). **Status:** Implemented (2026-11-05). The Director supplied the artwork in conversation on 2026-11-05; it is committed unchanged as `assets/branding/revisionworx-logo-original.png`.

The Project Director supplied the official RevisionWorx logo: a white capital **R** on a rounded blue square with subtle gradient/shading. Use the **user-provided artwork**, not a newly generated or approximated design.

## Implementation requirements
1. Add the supplied logo asset to the repository in an appropriate `assets/branding/` location, preserving a high-resolution original and deriving square app-icon assets from the central blue rounded square, **not the surrounding dark presentation background**.
2. Set it as the application/window/taskbar/dock icon in the actual GUI toolkit used by the current development branch (Qt/PySide6 if migrated; otherwise inspect current implementation). Handle Linux desktop entry/AppImage and Windows executable/installer icons.
3. Display the same logo prominently on the startup splash screen, alongside application name and actual version. The splash must appear during real startup and not be a blocking decorative delay.
4. Ensure icon clarity at 16, 24, 32, 48, 64, 128 and 256 px; create an appropriate multi-resolution `.ico` for Windows and PNGs for Linux/Qt. Keep proportions, rounded corners and original colors; do not distort.
5. Ensure resource paths work in source execution and packaged releases. Add automated resource existence/loading checks and a manual GUI smoke checklist for Linux and Windows. Never claim GUI launch verification unless executed.
6. Commit the actual binary logo files and GUI/packaging changes to the Lead Architect's feature branch, then update development progress and report links. Do not merge or release without Director approval.

**Asset handoff:** The original reference was supplied as a conversation image, not committed to GitHub. The Project Director can provide the original image or a prepared square PNG/ICO to the Lead Architect for binary commit. Do not silently substitute an unrelated logo.
## Implementation (2026-11-05)
- **Artwork:** `assets/branding/revisionworx-logo-original.png` (2560×1429, exactly as supplied) and
  `assets/branding/revisionworx-icon.png` (the blue rounded tile alone, 946 px, transparent outside it).
- **Derivation:** `packaging/make_icons.py` (Pillow, a developer tool). Nothing is redrawn. The tile's outline and its
  anti-aliased edge come from the artwork itself; the dark presentation background and the drop shadow are left
  out. Run it again if the artwork is ever replaced.
- **Run time:** `revisionworx/gui/resources/revisionworx-{16,24,32,48,64,128,256,512}.png` and `revisionworx.ico`
  (16-256 px), loaded by `revisionworx/gui/qt/branding.py`. It provides:
  - the application/window icon, with the desktop file name and the Windows AppUserModelID so the taskbar shows
    RevisionWorx, not Python;
  - the About and start-page logos;
  - the splash, which is shown from the moment Qt starts until the main window is up and the last workspace has
    opened. There is no artificial delay.
- **Packaging:**
  - `packaging/revisionworx.spec` bundles the images and sets the `.ico` on both executables.
  - The Linux build carries `install-desktop-entry.sh` and `revisionworx.desktop.in`.
  - `.github/workflows/package.yml` checks that the images are inside both builds and that the desktop entry
    installs, validates and removes.
  - `--self-test` (run on every packaged build) fails unless the icon sizes and the splash load.
- **Tests:** `tests/test_branding.py` covers the files, sizes, transparent corners, ICO directory and packaging
  metadata, the desktop-entry installer, and, with Qt, the icon, About and start-page logos and the splash
  content and start-up order.
- **Not verified by automation:** how the icon looks on real screens and taskbars; see `docs/GUI_ACCEPTANCE.md`.
  Merging was done under the Director's 2026-11-04 decision that Claude, as sole developer, merges its own PRs
  once CI passes; releases remain the Director's.
