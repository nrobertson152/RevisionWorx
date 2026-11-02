# RevisionWorx
A FOSS PDM software that integrates seamlessly with OpenSchematix.

RevisionWorx is an offline, accountless, telemetry-free engineering Product Data Management tool for
small and medium engineering departments, for Windows and Linux. Licensed GPL-3.0-or-later.

> **Status: pre-alpha.**
> - **Tested on Linux:** workspaces, engineering roots, folder templates, project creation, opt-in
>   adoption, a local search index and (on the `phase2-documents` branch) document registration,
>   immutable SHA-256 revisions, lifecycle states, and check-out/check-in.
> - **Not implemented yet:** parts, BOMs, Where Used, Before You Revise and native CAD reference
>   extraction.
> - **Desktop window:** written for PySide6 but not yet run. Multi-user writes on network shares are **not yet
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
Python 3.10+. The core and command line have no other runtime dependencies. The desktop window
needs PySide6 (Qt 6, LGPL-3.0): `pip install "PySide6-Essentials>=6.5"`.

```bash
python -m revisionworx --help                       # command line
python -m revisionworx.gui.app [WORKSPACE_FOLDER]   # desktop window (PySide6)
python -m revisionworx verify                       # check records against the journal
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
python -m revisionworx doc register PROJECT_ID "/mnt/eng/Projects/Acme/J-2001 Pump skid/03 Drawings/Native/frame.idw" --number DWG-1
python -m revisionworx doc checkout DOC_ID      # edit the file in place with your CAD tool
python -m revisionworx doc checkin DOC_ID -m "Add weld symbols"
python -m revisionworx rev transition REV_ID in_review
python -m revisionworx doc register-report PROJECT_ID --csv register.csv
```

## Development
```bash
pip install pytest "PySide6-Essentials>=6.5"
QT_QPA_PLATFORM=offscreen python -m pytest   # Qt tests are skipped (not passed) without PySide6
```
Read `CLAUDE.md` and `docs/` before contributing: requirements, architecture decisions and the
verified progress log live there.
