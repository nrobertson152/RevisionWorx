# RevisionWorx
A FOSS PDM software that integrates seamlessly with OpenSchematix.

RevisionWorx is an offline, accountless, telemetry-free engineering Product Data Management tool for
small and medium engineering departments, for Windows and Linux. Licensed GPL-3.0-or-later.

> **Status: pre-alpha (Phase 1 foundation).** Workspaces, engineering roots, folder templates,
> project creation, opt-in adoption of existing project folders and a local search index work and are
> tested on Linux. Revisions, check-in/check-out, parts, BOMs, Where Used, Before You Revise and CAD
> reference handling are **not implemented yet**. Multi-user writes on network shares are **not yet
> verified**: use one writer at a time. See `docs/DEVELOPMENT_PROGRESS.md`.

## What works today
- A shared `.revisionworx` metadata folder with a crash-safe ordered journal (your project folders are
  never reorganised and nothing is copied into them).
- Engineering roots with separate Windows/Linux paths for the same share.
- New projects from configurable folder templates (import/export as JSON).
- **Adopt Existing Project**: read-only preview, then registration with a SHA-256 baseline inventory.
  Never modifies the folder, never follows links, never invents history. Old projects stay untouched
  until you adopt them; nothing scans your whole archive.
- Fast local search of registered projects and their inventoried files (SQLite FTS5).
- Recognition of Inventor, SolidWorks, STEP/IGES, DXF/DWG, PDF and OpenSchematix files; OpenSchematix
  files are verified by content.

## Running from source
Python 3.10+ with no other runtime dependencies. The GUI needs Tkinter
(Debian/Ubuntu: `sudo apt install python3-tk`).

```bash
python -m revisionworx --help                       # command line
python -m revisionworx.gui.app [WORKSPACE_FOLDER]   # desktop window
```

Example:
```bash
export REVISIONWORX_WORKSPACE=/mnt/eng            # folder that will hold .revisionworx
python -m revisionworx init /mnt/eng --name "Engineering"
python -m revisionworx root add Projects /mnt/eng/Projects --other-platform-path 'P:\'
python -m revisionworx project new --name "Pump skid" --number J-2001 --customer Acme
python -m revisionworx project preview "/mnt/eng/Projects/Acme/2019/J-1100"
python -m revisionworx project adopt   "/mnt/eng/Projects/Acme/2019/J-1100" --name "Lift table"
python -m revisionworx project list pump
python -m revisionworx status
```

## Development
```bash
pip install pytest
python -m pytest            # GUI test is skipped automatically when Tkinter/display are missing
```
Read `CLAUDE.md` and `docs/` before contributing: requirements, architecture decisions and the
verified progress log live there.
