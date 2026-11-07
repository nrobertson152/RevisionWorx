<img src="revisionworx/gui/resources/revisionworx-128.png" alt="RevisionWorx logo" width="96" align="right">

# RevisionWorx

Offline engineering Product Data Management (PDM) for small engineering teams, on Windows and
Linux. No cloud, no subscription, no telemetry. Free software under the GNU GPL v3 or later.

RevisionWorx keeps your existing project folders exactly where they are and adds revision control, check-out /
check-in, lifecycle states and engineering change control on top, recorded in a crash-safe journal inside a
`.revisionworx` folder next to your projects.

## What RevisionWorx 1.0 is for

**Supported:** one computer's local disk. Several RevisionWorx windows or command-line sessions on that
computer are safe together: only one can change the workspace at a time, the others wait or are refused, and an
interrupted change is completed or rolled forward the next time the workspace is opened.

**Shared network folders (SMB/NFS):** you may keep a workspace in a network folder, but **let only one computer
make changes at a time**, and RevisionWorx shows a warning when a workspace is in a network folder. Or **set it up for a
team**: each person logs in, an administrator manages the accounts, and everyone works directly from the folder, with no
server. See [Working as a team](docs/USER_GUIDE.md#working-as-a-team-from-a-shared-folder). Between real computers that
**has not been verified yet**: `revisionworx share test` is how to find out about your share. See
[Known limitations](docs/KNOWN_LIMITATIONS.md).

Keep regular backups of your project folders and their `.revisionworx` folder, as with any engineering data.

## Features

- **Workspaces and projects.** Engineering roots (with separate Windows and Linux paths for the same share),
  new projects from configurable folder templates, and **Adopt existing project**: a read-only preview, then
  registration with a SHA-256 inventory. Your folders are never reorganised, and nothing scans your archive
  until you adopt a project.
- **Documents and revisions.** Register or import files; every check-in is an immutable revision with a
  SHA-256 hash, author, time and description. To go back, restore an older revision's content into your
  working file and check it in as a new revision; history is never rewritten.
- **Check-out / check-in.** Edit files in place with your CAD tool. Only the session that checked a document
  out can check it in; conflicting edits are detected and your working file is never overwritten.
  An administrator can release an abandoned checkout (`revisionworx doc release-checkout`, audited).
- **Lifecycle.** Work in Progress, In Review, Approved, Released, Superseded, Obsolete, with audited
  transitions.
- **Units.** A register of what you built: serial numbers and tags, the build standard each unit was built to,
  its own modifications, the components fitted in it, and a history that can only be added to.
- **Engineering change control.** Change requests (ECR) and change orders (ECO): review and approval
  signoffs, actions, the revision that implements each affected document, impact reports, and a tamper-evident
  audit chain per change.
- **BOM tools.** Validate, explode, roll up, compare and convert BOMs from CSV, XLSX or JSON; cycle and
  quantity checks; purchasing lists; change impact.
- **Integrity tools.** `revisionworx doctor` checks the whole workspace read-only; `revisionworx verify`
  checks records against the journal. Damage is reported and writing stops (fail closed) rather than guessing.
- **Search.** A fast local index of projects, files and documents (a disposable cache, rebuilt from the
  journal whenever needed).
- Recognises Inventor, SolidWorks, STEP/IGES, DXF/DWG, PDF, OpenSchematix, FluidWorx and DraftWorx files, and those of
  other programs: KiCad, Altium, Eagle, QElectroTech, LibrePCB, EPLAN and AutoCAD Electrical projects, Gerber and drill
  files, and FreeCAD, Solid Edge, CATIA, Creo, NX, Fusion, Rhino and SketchUp models and drawings. OpenSchematix,
  FluidWorx, DraftWorx and KiCad files are checked by content. A new document gets the type of thing it is (a KiCad
  board is a *PCB layout*, a CATIA drawing an *Engineering drawing*, with lettered revisions). Native CAD files are
  stored and versioned byte-for-byte; RevisionWorx does not rewrite CAD references.

## Install

**Standalone (no Python needed).** Download `RevisionWorx-1.0.0-windows-x64.zip` or
`RevisionWorx-1.0.0-linux-x86_64.tar.gz`, check it against its `.sha256` file, and unpack it anywhere.
Run `revisionworx-gui` (the desktop window) or `revisionworx` (the command line) from the unpacked folder.
On Linux, some systems need Qt's runtime libraries; on Ubuntu:
`sudo apt install libegl1 libgl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-xinerama0`.

**With Python 3.10 or later:**

```bash
python -m pip install "revisionworx-1.0.0-py3-none-any.whl[gui]"   # or, from a source checkout: pip install ".[gui]"
revisionworx-gui            # desktop window
revisionworx --help         # command line (standard library only; works without the [gui] extra)
```

To check that a copy works: `revisionworx-gui --self-test report.txt` creates a project in a temporary folder,
imports a file, reopens it, and writes `self-test PASSED` to `report.txt`.

## Getting started

Desktop: start `revisionworx-gui`, choose an empty folder, enter a project name, and click **Create project**.
Add files with **Import file…** (Ctrl+I) or **Register file…**, then use **Check out** / **Check in…** as you edit them.

Command line:

```bash
export REVISIONWORX_WORKSPACE=/data/eng           # folder that holds .revisionworx (or use -w FOLDER)
revisionworx init /data/eng --name "Engineering"
revisionworx root add Projects /data/eng/Projects
revisionworx project new --name "Pump skid" --number J-2001 --customer Acme
revisionworx project preview "/data/eng/Projects/Acme/2019/J-1100"     # read-only
revisionworx project adopt   "/data/eng/Projects/Acme/2019/J-1100" --name "Lift table"
revisionworx doc register PROJECT_ID "/data/eng/Projects/J-2001 Pump skid/03 Drawings/Native/frame.idw" --number DWG-1
revisionworx doc checkout DOC_ID                  # edit the file in place with your CAD tool
revisionworx doc checkin DOC_ID -m "Add weld symbols"
revisionworx rev transition REV_ID in_review
revisionworx doctor                               # read-only health check
```

Engineering changes:

```bash
revisionworx change new-request "Thicken base plate" --reason "Cracks in fatigue test" --doc DWG-1
revisionworx change submit ECR-0001 && revisionworx change review ECR-0001 && revisionworx change approve ECR-0001
revisionworx change new-order ECR-0001 --action "Update drawing"
revisionworx change show ECO-0001                 # item keys and action ids
revisionworx change signoff ECO-0001 approve && revisionworx change start ECO-0001
#   ... check out, edit and check in DWG-1 as revision B ...
revisionworx change implement ECO-0001 document:DOC_ID --rev B
revisionworx change action-done ECO-0001 ACTION_ID
revisionworx change implemented ECO-0001 && revisionworx change signoff ECO-0001 approve
revisionworx change close ECO-0001
```

Units built, and what's fitted in them (`docs/UNITS.md`):

```bash
revisionworx unit standard new HPU-75-A --title "HPU-75 standard A" --from-project J-2001
revisionworx unit new --serial NH-26-0001 --model HPU-75 --standard HPU-75-A --built 2026-03-14 --status built
revisionworx unit fit NH-26-0001 P1 --part-number PMP-10 --serial 12345
revisionworx unit show NH-26-0001                # the revisions that apply to this unit, its components and history
revisionworx unit where-used --revision FRM-200@A
```

BOM files: `revisionworx bom validate bom.csv`, `revisionworx bom explode bom.xlsx`, `revisionworx bom diff old.csv new.csv`
(`revisionworx bom -h` lists all commands).

More: [User guide](docs/USER_GUIDE.md) · [Known limitations](docs/KNOWN_LIMITATIONS.md) ·
[Changelog](CHANGELOG.md) · [Release checklist](docs/RELEASE_CHECKLIST.md) · [Architecture](docs/ARCHITECTURE.md) · [Core API](docs/CORE_API.md) ·
[Change control](docs/CHANGE_CONTROL.md) · [BOM API](docs/bom/BOM_API.md)

## Development

```bash
python -m pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen python -m pytest -q -ra     # Qt tests are skipped (not passed) without PySide6
```

CI runs the full suite on Linux (Python 3.10, 3.13) and Windows (3.11, 3.13), installs the built wheel in a
clean environment, drives the window with real keystrokes on an X display, and builds and self-tests the
standalone packages. Read [AGENTS.md](AGENTS.md) and `docs/` before contributing.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). The desktop window uses Qt for Python (PySide6), LGPL-3.0.
