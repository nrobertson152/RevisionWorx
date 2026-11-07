# Changelog

All notable changes to RevisionWorx. Versions follow [Semantic Versioning](https://semver.org/).

## 1.0.0 (unreleased: awaiting the Director's release decision)

The first release. Everything below is new.

### Units: what was built, and what's in it
- A register of serial-numbered units (machines, tracked components, instruments, tools), each with one permanent identity,
  its serial numbers and tags, and one history that can't be edited, only added to. Units built to the same drawings share a
  **build standard**; a unit records only where it differs. A unit's drawings are what its history says: a newer released
  revision is shown beside them, never instead. Components are fitted at positions; a tracked component can be in one place at
  a time, and its own history says where it has been. `revisionworx unit ...`; `revisionworx link units` and `link unit` for
  other programs; existing registers and FieldWorx asset registers import all at once or not at all. See
  [docs/UNITS.md](docs/UNITS.md).
- **PDFs tied to revisions.** `revisionworx doc pdf FRM-200@B export.pdf` attaches the PDF of a CAD drawing to exactly that
  revision; a newer revision has no PDF until one is attached, never the older one's. `doc pdfs --missing` lists released
  revisions without one. Other programs see each revision's PDF and fetch it with `link fetch --pdf`.
- **Serial numbering.** `revisionworx unit numbering new NH --pattern "NH-{yy}-{seq:4}"`, then `unit new --serial-from NH`:
  the next serial of the unit's year (or month) is issued and claimed in the same step as the registration, so two people never
  get the same one, and a serial already in use is skipped.
- **Engineering changes reach units.** Beside an implemented ECO, record once which units it reaches: future production only,
  a required or optional retrofit, one unit's own modification, or a documentation correction. Candidates are the units that
  still have the revision it replaces; a scope picks them by customer, serial range, build standard or by name. Each unit in a
  retrofit has its own compliance record (pending, scheduled, deferred, installed, verified, not applicable, reversed...), and
  its drawings move only when its own history records the installation, flagged *reported* until verified. `revisionworx unit
  change ...`, `unit compliance` for every unit still missing a required change, and `changes` on each unit for other programs.

### Working as a team
- A workspace can be set up for a team working from one shared network folder, with no server. An administrator account is created on
  the boss's computer, the administrator creates regular accounts, and people log in and check documents out. Every change is signed,
  and every computer checks every change, so nobody can make themselves an administrator or act in someone else's name. Other programs can
  use a login kept on the computer. It's opt-in, can't be undone, and hasn't been verified between real computers:
  `revisionworx share test` is how to find out about your share. See [Known limitations](docs/KNOWN_LIMITATIONS.md).

### FluidWorx
- `.fluidworx` simulation studies are recognised by content and registered under a new built-in "Simulation study"
  document type with alpha revisions. They are versioned byte for byte, like any other file.

### DraftWorx
- `.draftworx` drawings are recognised by content and registered as engineering drawings, with lettered revisions, so they're in a
  project's drawing register. DraftWorx shows a drawing's revision status and checks it in through `revisionworx link`.

### Other programs' files
- Files from KiCad, Altium, Eagle, QElectroTech, LibrePCB, EPLAN, AutoCAD Electrical, FreeCAD, Solid Edge, CATIA, Creo, NX,
  Fusion, Rhino and SketchUp, and Gerber and drill files, are recognised and named for what they are. KiCad's files are checked
  by content. New documents get the matching type: schematics are *Schematic*, drawings *Engineering drawing*, models *CAD part*
  or *CAD assembly*, and boards a new built-in *PCB layout* type, all with lettered revisions. A Creo save number
  (`bracket.prt.3`) isn't taken for the type. Existing documents keep their types.

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
