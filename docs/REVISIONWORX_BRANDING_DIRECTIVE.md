# Director Directive — RevisionWorx Official Application Icon and Splash Screen

**Owner:** Lead Architect (Claude). **Status:** Approved design requirement, implementation pending.

The Project Director supplied the official RevisionWorx logo: a white capital **R** on a rounded blue square with subtle gradient/shading. Use the **user-provided artwork**, not a newly generated or approximated design.

## Implementation requirements
1. Add the supplied logo asset to the repository in an appropriate `assets/branding/` location, preserving a high-resolution original and deriving square app-icon assets from the central blue rounded square, **not the surrounding dark presentation background**.
2. Set it as the application/window/taskbar/dock icon in the actual GUI toolkit used by the current development branch (Qt/PySide6 if migrated; otherwise inspect current implementation). Handle Linux desktop entry/AppImage and Windows executable/installer icons.
3. Display the same logo prominently on the startup splash screen, alongside application name and actual version. The splash must appear during real startup and not be a blocking decorative delay.
4. Ensure icon clarity at 16, 24, 32, 48, 64, 128 and 256 px; create an appropriate multi-resolution `.ico` for Windows and PNGs for Linux/Qt. Keep proportions, rounded corners and original colors; do not distort.
5. Ensure resource paths work in source execution and packaged releases. Add automated resource existence/loading checks and a manual GUI smoke checklist for Linux and Windows. Never claim GUI launch verification unless executed.
6. Commit the actual binary logo files and GUI/packaging changes to the Lead Architect's feature branch, then update development progress and report links. Do not merge or release without Director approval.

**Asset handoff:** The original reference was supplied as a conversation image, not committed to GitHub. The Project Director can provide the original image or a prepared square PNG/ICO to the Lead Architect for binary commit. Do not silently substitute an unrelated logo.