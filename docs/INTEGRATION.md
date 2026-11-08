# Integrating other programs with RevisionWorx

Caldera, Ledgerbook, OpenSchematix Viewer and TraceMarx use this interface, and so can any other program. It is
`revisionworx link <command>`. Every command prints **exactly one JSON object** on stdout with `"ok": true` or
`"ok": false`, and exits 0 or 2. Your program needs only the `revisionworx` executable (a pip install or the
standalone build) and a JSON parser. It needs no Python import of RevisionWorx, no Qt, and no network.

The reference client is [`contrib/rwx_client.py`](../contrib/rwx_client.py): one file, standard library only,
Python 3.9+. Copy it into your program. It finds the executable, runs the commands, and raises `RwxError(message,
kind)` with a message written for your user.

**Version:** `API_VERSION = 1` (`revisionworx link version`). It increments only on incompatible changes. New
fields may be added to answers at any time, so ignore fields you do not know. Commands added since API 1 are named in
`version`'s `features` (today `["units", "unit-changes", "pdf-derivatives", "unit-records", "unit-deviations",
"field-signatures"]`): check for one before using it.

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
| `fetch REF --to DIR [--revision L \| --released] [--pdf]` | copy a revision's verified content to DIR, named `name (rev L).ext`; with `--pdf` (feature `pdf-derivatives`) the PDF tied to that revision instead, `not-found` if it has none | `path`, `revision` |
| `publish FILE [--project P] [--document REF] [--number N] [--title T] [--folder F] [-m MSG]` | put FILE under revision control (below) | `result`, `action`, `document`, `revision` |
| `open [--document REF \| --file FILE]` | show it in the desktop window | `launched`, `command` |
| `units [--project P] [--text T] [--detail]` | the unit register (feature `units`): summaries, or with `--detail` each `unit` object below | `units: [summary \| unit]` |
| `unit REF [--at N \| --on DATE]` | one unit, now, after its event N, or as known now on a date | `unit` |
| `where-used (--revision DOC@REV \| --document D \| [--part-number PN] [--serial S] [--lot L] [--component REF]) [--history]` | units that use a revision or document, or hold what is fitted (feature `unit-where-used`, below) | `rows`, `coverage` |
| `unit-register`, `unit-fit`, `unit-remove`, `unit-note`, `unit-milestone`, `unit-change-install` | record a fact on units (feature `unit-records`, below) | `result`, `unit` and `events`, or `units` |
| `devices` | phones enrolled to accounts (feature `field-signatures`, below) | `team`, `devices: [device]` |
| `device-enrol --account A --device D --key K --fingerprint F [--name N]` | enrol a phone to an account (below) | `result`, `device` |

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

**`revision`** objects: `id, label, state, state_name, sha256, size, created, author, description, pdf`. `pdf` (feature
`pdf-derivatives`, ADR-026) is `null` or `{sha256, size, tool}`: the PDF attached to *this* revision. A newer revision without
one has `null`; another revision's PDF is never offered in its place.

**`unit` objects** (docs/UNITS.md; `REF` is a unit id, a FieldWorx `ast_` id, or an identifier such as a serial or tag):
`id, label, class, name, model {part, number}, identifiers [{scheme, issuer, value, active, since}], status,
project {id, number, name} | null, customer, site, location, parent {unit, label, position} | null,
standard {id, number, title, event, basis} | null, configuration_recorded, documents, components, factory_lots, milestones,
notes, aliases, events {count, head}, created, updated, rev, history`, and `as_of` when `--at`/`--on` was given. Each of `documents` is
`{document {id, number, title}, revision {id, label, number, state, state_name, sha256} | null, applies, source ("standard" |
"unit"), event, basis, released {id, label} | null, flags}`; `flags` among `newer-released`, `not-released`, `obsolete`, `missing`.
The revision is always the one recorded for this unit; a newer released one is only ever named in `released`. Each of
`components` is `{position, part, number, unit, label, serial, lot, since, event, basis}`; each of `factory_lots` (feature
`unit-lots`, below) is `{number, lot, quantity, units, since, event, basis}`; each of `history` is
`{n, type, summary, effective, time, actor, basis, evidence, reason, change, voided}`. A unit answered for now (not `--at` or
`--on`) also has `changes` (feature `unit-changes`): each engineering change that reaches it (docs/UNITS.md section 10), outstanding ones first, as
`{change, number, title, kind, required, state, outstanding, since, documents: [{document, number, from, from_label, to,
to_label}]}`, and `deviations` (feature `unit-deviations`): each deviation or concession that covers it (docs/UNITS.md section 12),
in effect first, as `{deviation, number, kind ("deviation" | "concession"), title, departure, temporary, until, state ("in-effect" |
"cleared" | "not-used"), in_effect, overdue, since, documents: [{document, number, revision, label}]}`. A unit that differs from its
drawings where one in effect allows it is not a mistake in the record. A summary (`units` without `--detail`) is
`{id, label, class, name, model, status, project, customer, site, location, identifiers, standard, parent, events, rev}`.

**Merged units** (feature `unit-merges`, docs/UNITS.md section 15). Every `unit` object has `merged_into` (`{unit, label, event, time}` or `null`) and
`merged_from` (`[{unit, label, event, time}]`, oldest first). `aliases` is sorted: the unit's FieldWorx `ast_` ids and the `unt_`
ids of the units merged into it, the earlier ids a field record or label may name. `units` doesn't list a unit merged into
another. `unit REF`, given a merged unit's id, answers the unit it was merged into and adds `named: {unit, merged_into}`; an action
recorded through `link` against it is recorded on that unit, with evidence `{kind: "merged-unit", ref: <the id named>}`.

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
- Recording on units adds `duplicate` (a serial or tag another unit holds; the message names it), `changed` (the unit changed
  since you read it: `--if-events`), `txid-used` (that txid was used for something else) and `conflict` (someone changed a
  record at the same moment; nothing was written, try again with the same txid).
- `--field-signature` adds `signature`: the signature can't be trusted (below); nothing was written. `device-enrol` answers
  `refused` when the phone can't be enrolled that way.
- The client adds `unavailable` (RevisionWorx not installed).

## Where used (feature `unit-where-used`)

`where-used` answers what `revisionworx unit where-used` answers, read-only, for a program putting together a recall or
a where-used report. Give one of:

- `--revision DOC@REV`: units whose configuration uses that revision, through their build standard or their own
  difference. Each row has `unit`, `source`, `event` and `basis`.
- `--document D`: units whose configuration names that document, and which revision each uses. Each row has `unit`,
  `revision`, `applies`, `source` and `flags`.
- What is fitted: `--part-number`, `--serial`, `--lot` and `--component` in any combination, all of which must hold. A lot
  belongs to a part, so `--lot` needs `--part-number`. Each row has `unit`, `position`, `fitted` (the event), `removed`,
  `current`, `serial` and `lot`. With `--history`, it is wherever that has ever been fitted, and a removed row adds
  `fitted_on`, `removed_on` and `disposition`. A unit built from that lot of that part (its `factory_lots`, feature
  `unit-lots`) is a row too, with `position` `""` and `built_from: {units, quantity}`: how many units were built with the lot,
  and how much of it that work used. Nothing records a factory lot leaving a unit, so these rows are always `current`; with
  `units` more than 1, the unit holds some of that lot, not necessarily any particular part of it.

Matching ignores case. Every `unit` is the unit's summary, the same as `units` returns: label, status, customer, site,
location, identifiers and project. So the caller knows where each one is without asking again. `coverage` says what was
searched: the units registered in this workspace. A unit recorded nowhere else can't be found, and a lot fitted without
being recorded on a unit isn't there to find.

## Recording on units (feature `unit-records`)

Other programs record facts on units through these commands, never by writing files: OpenMeridian registering the units its own
work orders built, FieldWorx's office bridge recording what someone in the office accepted from the field. Each is the same action
as the `revisionworx unit` command of the same name (docs/UNITS.md section 6), with the same refusals, and is **one journal entry
or nothing**. In a team workspace it is in the name of the person logged in on this computer, and refused without a login.

| Command | Records |
|---|---|
| `unit-register (--serial S ... [--issuer I] \| --serial-from NUMBERING [--count N] \| --tag T [--tag-issuer I] \| --other X) [--name] [--model] [--class] [--status] [--project P] [--customer] [--site] [--location] [--standard STD] [--doc DOC@REV ...] [--built\|--shipped\|--installed\|--commissioned\|--decommissioned DATE] [--notes] [--lots JSON \| --lots-file FILE]` | new units, all or none: one per `--serial`, or `--count` units (1 to 1000) each issued the next serial of a numbering, or one unit known by a tag or other code. With `--lots` (feature `unit-lots`), the lots the work that built them used: below. Answer: `units` |
| `unit-fit REF POSITION (--part-number PN [--serial S] [--lot L] \| --component REF) [--replace [--disposition D]]` | something fitted; an occupied position is refused without `--replace` |
| `unit-remove REF POSITION [--disposition D]` | something taken out; an empty position is refused |
| `unit-note REF TEXT` | a note in the unit's history |
| `unit-milestone REF built\|shipped\|installed\|commissioned\|decommissioned [--on DATE]` | a milestone (no date when it isn't known) |
| `unit-change-install REF ECO` | an engineering change made on the unit: its drawings move to the change's new revisions in the same entry, `reported` unless `--basis verified` (docs/UNITS.md section 10.2). Also answers `compliance` |

Every one also takes:

- **`--txid HEX`, required:** 32 lowercase hex characters that you choose for this action, keep, and send again with every attempt
  at it. If an earlier attempt was recorded (its answer was lost, say), the same command answers `"result": "already-recorded"` and
  writes nothing; the same txid with different input is refused (`txid-used`). Derive it from your own record so it survives your
  program restarting: for example the first 32 hex digits of SHA-256 of `"yourprogram/accept/v1\0" + your record's id`.
- `--effective DATE` (when it happened, if not today; `unit-milestone` uses `--on`), `--basis verified|reported|declared|legacy`
  (how well it is known: `declared` by default, `reported` for `unit-change-install`; a field report is `reported`), `--reason`,
  `--change ECO` (the change it carries out) and `--evidence KIND:REF[=LABEL]` (repeatable): what the fact rests on, kept in the
  history, such as `work-order:WO-00012` or `fieldworx-event:<event id>=replaced on site by J. Smith`. KIND is lowercase letters,
  digits and `-`.
- **`--if-events N`** (not `unit-register`): act only if the unit still has N events, its `events.count` when your program or its
  user decided. If anything was recorded on it since, the answer is `changed` and nothing is written: look again. It is compared
  when the action reads the unit, at the revision its entry insists on. A retry of an attempt already recorded isn't checked.
- **`--field-signature DEVICE:EVENT:SIG`** (feature `field-signatures`): the field record was signed on a phone enrolled to an
  account (below). Give it with `--basis verified` and `--evidence fieldworx-event:EVENT` for the same record.

**Answer:** `result` (`recorded` or `already-recorded`), `unit` (as `unit` answers it, after the action) and `events` (the numbers
of the events this attempt wrote on it); `unit-register` answers `units` instead. `warning` when it was saved but finishing was
delayed (the workspace completes it when next opened for writing): it is recorded, so don't record it again.

**Factory lots** (feature `unit-lots`, the Director's decision of 2026-11-08). `unit-register --lots JSON` gives the lots the
work that built the units used, as a JSON list of `{"number": PART-NUMBER, "lot": LOT, "quantity": TEXT}` (1 to 1000; `quantity`
is how much of the lot that work used, as your program writes it, or left out). `--lots-file FILE` reads the same from a UTF-8
file, which has no command-line length limit. Each unit registered records them in the same entry (`lots.used`, docs/UNITS.md
section 4), with `units`: how many units were registered with them, so with several, each holds some of them. A part's lot is given
once; anything else is `bad-input` and nothing is written. The lots are part of what the txid stands for: the same txid with other
lots is `txid-used`. A registration without `--lots` asks for exactly what it asked before, so retrying an older one still works.

## Phones enrolled to accounts (feature `field-signatures`)

In a team workspace, a FieldWorx phone signs each record it makes with its own Ed25519 key (FieldWorx's PROTOCOL.md section 12),
and an administrator enrols the phone to the person's account (ADR-028). A record signed on an enrolled phone can then be accepted as
**verified** in that person's name.

**`device-enrol --account A --device D --key K --fingerprint F [--name N]`**, in the name of the administrator logged in on this
computer. `D` is the phone's device id (`dev_` and 32 hex digits), `K` its public key (64 hex digits), both from the phone's own
files; `F` is the fingerprint the person enrolling typed **from the phone's screen**, and must be `K`'s: the first 8 bytes of
`SHA-256("fieldworx/key-fingerprint/v1\0" + K's 32 bytes)` as 16 uppercase hex digits in four groups joined by `-`. Answer:
`result` (`enrolled`, or `already-enrolled` for the same phone and account again, which writes nothing) and `device`. `refused`
for a fingerprint that isn't the key's, a device or key enrolled before (revoked ones included: a phone that changes hands starts
again as a new device), or a disabled account; `not-found` for no such account.

**`devices`** lists every phone, revoked ones included: `{device, account, account_name, name, fingerprint, key, added, added_by,
revoked, revoked_by, reason, active, problem}`. `active` is whether its signatures count now; `problem` says why not, if it isn't
revoked (`enrolled to more than one account, so it counts for neither`, `the account is disabled`). An administrator revokes a phone
with `revisionworx user device revoke NAME DEVICE --reason R`.

**`--field-signature DEVICE:EVENT:SIG`** on a recording command: `EVENT` is the field record's id (64 hex digits) and `SIG` the
Ed25519 signature (128 hex digits) of the bytes `"fieldworx/event-signature/v1\0"` followed by `EVENT` as ASCII. RevisionWorx
checks that DEVICE is enrolled to exactly one account, isn't revoked, its account is active, and SIG holds with the key enrolled for
it; and that the command has `--basis verified` and cites the same record as `--evidence fieldworx-event:EVENT` (`bad-input`
otherwise). Then it records the fact with two more evidence items, `field-signature` (`DEVICE:EVENT`, labelled with SIG) and
`field-signer` (the account, labelled with the phone's name), in the name of the person logged in here, as always. Anything else is
`signature`, with nothing written: accept the record as `reported` instead if a person decides to. A retry of an acceptance
already recorded answers `already-recorded`, even if the phone has been revoked since. Those two evidence kinds can't be given with
`--evidence`.

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

Programs that use this: Caldera (publishes calculation reports), FieldWorx (the bridge reads projects, documents and units to pack
for phones), DraftWorx (a drawing's revision status in its window,
check-in, adding drawings to projects, and parts lists as BOMs next to them; it checks out with `revisionworx --json doc checkout`),
Ledgerbook (RevisionWorx document fields), OpenSchematix Viewer (revision status and opening controlled revisions), TraceMarx (reads
the schematic, publishes wire lists and labels).
