# Integrating other programs with RevisionWorx

Caldera, Ledgerbook, OpenSchematix Viewer and TraceMarx use this interface, and so can any other program. It is
`revisionworx link <command>`. Every command prints **exactly one JSON object** on stdout with `"ok": true` or
`"ok": false`, and exits 0 or 2. Your program needs only the `revisionworx` executable (a pip install or the
standalone build) and a JSON parser. It needs no Python import of RevisionWorx, no Qt, and no network.

The reference client is [`contrib/rwx_client.py`](../contrib/rwx_client.py): one file, standard library only,
Python 3.9+. Copy it into your program. It finds the executable, runs the commands, and raises `RwxError(message,
kind)` with a message written for your user.

**Version:** `API_VERSION = 1` (`revisionworx link version`). It increments only on incompatible changes. New
fields may be added to answers at any time, so ignore fields you do not know.

## Safety

`link` commands go through the same operations as the desktop window: one writer at a time, fail closed on
damaged metadata, check-in only through a real checkout, and imports that never overwrite a file. A refusal
changes nothing. Checkouts made through `link` belong to the same user session as the desktop window and command
line on that computer.

In a workspace set up for a team (`docs/ARCHITECTURE.md`, ADR-024), reading needs nothing, and changing something needs a login kept on the
computer: `revisionworx login NAME`, or **Stay logged in** in the desktop window. What's published is in that person's name. Without one,
`publish` answers `"ok": false` with a message telling the user to log in. If someone else's save is taking a while, the `kind` is `busy`.

## Which workspace

`-w FOLDER` (before `link`) or `REVISIONWORX_WORKSPACE`. Otherwise, for commands about a file, the nearest
folder above it that holds `.revisionworx`, then the workspaces recently opened in the desktop window.

## Commands

| Command | Does | Answer (besides `ok`) |
|---|---|---|
| `version` | integration API and program version | `api`, `version` |
| `workspaces` | known workspaces | `workspaces: [{id, name, location}]` |
| `status FILE` | is FILE a document? how does it compare with the latest revision? | `controlled`, `workspace`, `document`, `file: {path, state, matches_latest}` |
| `projects` | projects in the workspace | `projects: [{id, name, number, customer, mode, archived, folder}]` |
| `documents [--project P] [--text T]` | documents, optionally filtered | `documents: [document]` |
| `document REF [--project P]` | one document with its history | `document` (+ `history: [revision]`) |
| `fetch REF --to DIR [--revision L \| --released]` | copy a revision's verified content to DIR, named `name (rev L).ext` | `path`, `revision` |
| `publish FILE [--project P] [--document REF] [--number N] [--title T] [--folder F] [-m MSG]` | put FILE under revision control (below) | `result`, `action`, `document`, `revision` |
| `open [--document REF \| --file FILE]` | show it in the desktop window | `launched`, `command` |

`REF` is a document id or document number. `P` is a project id, number or name.

**`publish`:**
- **New revision:** with `--document`, or when `--number` matches a document in `--project`, or when FILE already
  is a document's working file. RevisionWorx checks the document out to this session (refused with
  `kind: "checked-out"` if someone else has it), copies FILE onto the working file and checks in with `-m`.
  Identical content records nothing (`result: "unchanged"`), and any checkout this call made is released.
- **New document in `--project`:** registered in place if FILE is inside the project folder, otherwise copied
  into it (`--folder` is a subfolder). An existing file is never overwritten.

**`document`** objects: `id, number, title, status, type, project {id, name, number}, path` (the working file),
`revisions, latest, released, checkout {user, host, since, mine} | null`.

**`revision`** objects: `id, label, state, state_name, sha256, size, created, author, description`.

**`file.state`:**
- `unchanged`: the file is identical to the latest revision.
- `modified`: edited while checked out.
- `modified-externally`: edited without a checkout.
- `missing`: the working file is not on disk.
- `unrevisioned`: the document has no revision yet.
- `unmanaged`: not a document.

**Error `kind`:**
- `not-found`, `ambiguous`, `checked-out`, `busy` (another session is writing; retry), `no-workspace`,
  `bad-input`, `error`.
- The client adds `unavailable` (RevisionWorx not installed).

## Example

```python
from rwx_client import RevisionWorx, RwxError, describe

rwx = RevisionWorx()
if rwx.available():
    try:
        result = rwx.publish("pump-sizing.pdf", project="J-2001", number="CALC-12",
                             title="HPU pump sizing", message="Initial calculation")
        print(result["document"]["number"], "rev", result["document"]["latest"]["label"])
        print(describe(rwx.status(result["document"]["path"])))   # "CALC-12 rev A · Work in Progress"
    except RwxError as exc:
        show_message(str(exc))      # e.g. "DWG-1 is checked out by alex on SHOP-PC; ask them to check it in first"
```

Programs that use this: Caldera (publishes calculation reports), DraftWorx (a drawing's revision status in its window,
check-in, adding drawings to projects, and parts lists as BOMs next to them; it checks out with `revisionworx --json doc checkout`),
Ledgerbook (RevisionWorx document fields), OpenSchematix Viewer (revision status and opening controlled revisions), TraceMarx (reads
the schematic, publishes wire lists and labels).
