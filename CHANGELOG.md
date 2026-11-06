# Changelog

All notable changes to RevisionWorx. Versions follow [Semantic Versioning](https://semver.org/).

## 1.0.0 (unreleased: awaiting the Director's release decision)

The first release. Everything below is new.

### Working as a team
- A workspace can be set up for a team working from one shared network folder, with no server. An administrator account is created on
  the boss's computer, the administrator creates regular accounts, and people log in and check documents out. Every change is signed,
  and every computer checks every change, so nobody can make themselves an administrator or act in someone else's name. Other programs can
  use a login kept on the computer. It's opt-in, can't be undone, and hasn't been verified between real computers:
  `revisionworx share test` is how to find out about your share. See [Known limitations](docs/KNOWN_LIMITATIONS.md).

### FluidWorx
- `.fluidworx` simulation studies are recognised by content and registered under a new built-in "Simulation study"
  document type with alpha revisions. They are versioned byte for byte, like any other file.

### Supported use
- One computer's local disk, Windows or Linux. Several RevisionWorx windows or command-line sessions on that
  computer are safe together. Shared network folders: one computer making changes at a time (a warning is
  shown), or set up for a team, which isn't verified between real computers yet; see
  [Known limitations](docs/KNOWN_LIMITATIONS.md).

### Core
- `.revisionworx` workspace with an ordered, crash-safe journal; every change is one atomic, checked commit.
  An interrupted change is completed when the workspace is next opened for writing.
- Single writer lock with fencing: a writer whose lock was broken or revoked cannot publish (ADR-015/019).
  Administrative lock break and orphan clearing are audited.
- Windows sharing violations (virus scanners, backup tools) are retried within a bound, never reported as
  damage; a busy lock release asks you to retry.
- Fail closed: damaged or unreadable metadata makes the workspace read-only with a plain explanation, never a
  guess.
- `revisionworx doctor` (read-only whole-workspace integrity check) and `revisionworx verify [--repair]`.
- Disposable local SQLite search index, rebuilt from the journal; never treated as authoritative.

### Projects and documents
- Engineering roots with per-platform paths; folder templates; new projects; opt-in **Adopt existing project**
  with a read-only preview and SHA-256 inventory. No automatic scanning of unmanaged archives.
- Document registration and import; immutable SHA-256 revisions; configurable revision labels.
- Check-out / check-in in place, stale-edit detection, cancel with or without restoring the file, restore an
  earlier revision's content, administrative checkout release.
- Lifecycle states (Work in Progress, In Review, Approved, Released, Superseded, Obsolete) with audited
  transitions; drawing register report (text/CSV).
- Recognition of Inventor, SolidWorks, STEP/IGES, DXF/DWG, PDF and OpenSchematix files.

### Desktop window (PySide6)
- The official RevisionWorx logo (supplied by the Director): window, taskbar and dock icon, a start-up splash
  with the name and version that stays only while the window and the last workspace load, the About dialog and
  the start page. Windows executables carry it as their icon; the Linux build adds `install-desktop-entry.sh`
  for the application menu.
- Start page with one-step project creation, recent workspaces, projects list and details, adopt dialog,
  documents tab (import, register, check out/in, cancel, open, show in folder, change state, export, compare,
  restore), workspace health banner, F5 refresh, keyboard shortcuts and accessible names.
- A saved change is never reported as failed because a later step (such as releasing the lock) failed.
- `revisionworx-gui --self-test REPORT` checks that an installed or packaged copy works.

### Engineering change control
- Change requests (ECR) and change orders (ECO) with validated state machines, signoffs bound to the reviewed
  content, actions, dependencies, implementing revisions verified against document history, impact reports,
  hash-chained audit per change, self-verifying export package and all-or-nothing CSV import.
- `revisionworx change ...` drives the whole lifecycle from the command line.
- A reused transaction id with different input is refused instead of being treated as a retry.

### BOM
- Parts catalog and BOM model; validation (cycles, quantities, units), explode, roll-up, compare, change impact,
  purchasing list, CSV/XLSX/JSON import and export: `revisionworx bom ...`.

### Packaging
- Standalone builds for Windows x64 (zip) and Linux x86-64 (tar.gz) with SHA-256 checksums; wheel and sdist.
  Every build is self-tested in CI before it is kept.
