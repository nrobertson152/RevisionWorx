# RevisionWorx 1.0 user guide

For installation see the [README](../README.md). For what is and is not verified, read
[Known limitations](KNOWN_LIMITATIONS.md) first.

## Concepts

- **Workspace:** a folder that holds a `.revisionworx` metadata folder. Everything RevisionWorx knows is in
  there, recorded as an ordered journal. Your project files stay where they are.
- **Engineering root:** a named folder your projects live under (for example `Projects`), with an optional
  path for the other operating system when Windows and Linux machines see the same share differently.
- **Project:** a registered folder under a root. New projects are created from a folder template; existing
  ones are **adopted** (registered as they are, after a read-only preview). Nothing outside registered projects
  is ever scanned.
- **Document:** a file RevisionWorx controls. **Register** controls a file where it already is; **Import**
  copies a file into the project first. Each **revision** is an immutable copy with a SHA-256 hash.
- **Checkout:** permission for one session to edit a document's working file. Only that session can check it in. In a workspace set up for a team, it belongs to the person, on whichever computer.
- **Lifecycle state:** each revision moves Work in Progress → In Review → Approved → Released (or Obsolete);
  releasing a newer revision supersedes the older one.
- **Change request / order (ECR / ECO):** the formal record of why something changes, who approved it, and
  which revisions implemented it.

## Desktop window

1. Start `revisionworx-gui`. The first time, choose an empty folder, type a project name (and optionally a
   number), and click **Create project**. Next time it reopens your last workspace (Workspace › Recent).
2. **Projects:** **New project**, **Adopt existing…** (preview first; nothing is changed until you confirm).
3. **Documents tab:** **Import file…** (Ctrl+I) or **Register file…**, then select a document:
   - **Check out**, edit the working file in your CAD tool and save it, then **Check in…** with a description.
   - **Cancel checkout…** either keeps your edits in the file or restores the latest revision.
   - **Change state**, **Export revision…**, **Compare with previous**, **Restore to working file…** (needs your
     checkout; check in afterwards to make it the newest revision).
4. **F5** re-checks the workspace and reloads. The banner under the menu shows health problems; a red banner
   means the workspace is read-only until the problem is resolved (see Troubleshooting).
5. Help › Keyboard shortcuts lists every shortcut.

## Command line

`revisionworx --help` lists every command; each has its own `--help`. Choose the workspace with
`-w FOLDER` or the `REVISIONWORX_WORKSPACE` environment variable. Add `--json` for machine-readable output.
The README has a worked example for projects, documents and changes.

## Several people and computers

- On **one computer**, any number of windows and command-line sessions may be open. One makes a change at a
  time; the others wait briefly or are told the workspace is busy.
- In a **shared network folder**, let only **one computer** make changes at a time (for example by agreement,
  or by giving other users read-only share permissions). Others can browse and search. This is a 1.0
  limitation, not a recommendation for later versions.
- A checkout belongs to the session (computer and user profile) that made it. If that session is lost, an
  administrator can release it: `revisionworx doc checkout-info DOC_ID` shows its token, then
  `revisionworx doc release-checkout DOC_ID --token TOKEN --reason "..."` (audited). The edited working file is
  not touched.

## Backups

Back up each workspace folder **including its hidden `.revisionworx` folder**, and the project folders, with
your normal backup tool. For a consistent copy, take it while nobody is making changes:
`revisionworx status` shows `writer: none` when no session holds the writer lock.

To restore: put the whole folder back, then run `revisionworx -w FOLDER doctor`. It reads everything, changes
nothing, and reports any problem. Delete the local search cache if search results look stale; it is rebuilt
from the journal (`revisionworx index rebuild`).

Local, per-user files (safe to delete; they are rebuilt or only hold preferences):

| | Windows | Linux |
|---|---|---|
| Preferences, recent workspaces | `%APPDATA%\RevisionWorx` | `~/.config/revisionworx` |
| Search cache | `%LOCALAPPDATA%\RevisionWorx\cache` | `~/.cache/revisionworx` |
| Checkout tokens | `%LOCALAPPDATA%\RevisionWorx\state` | `~/.local/state/revisionworx` |
| Team workspace: this computer's copy of the journal, your login, an administrator's computer secret | `%LOCALAPPDATA%\RevisionWorx\state\shared` | `~/.local/state/revisionworx/shared` |

Do not delete the checkout tokens while you have documents checked out: without them this session can no
longer check those documents in (an administrator can still release the checkouts). In a team workspace, the
copy of the journal is safe to delete, but a `device-*.json` file is not: it's part of an administrator's login.

## Working as a team, from a shared folder

Several people can work from one network folder, each with their own login. There's no server: everyone's copy of RevisionWorx works directly with the folder, and they check each other's work. Read [Known limitations](KNOWN_LIMITATIONS.md) first: this hasn't been verified between real computers yet.

**Find out whether your share is up to it.** Run this on two or more computers, at the same time:

```
revisionworx share test "Z:\Engineering" --seconds 60
revisionworx share report "Z:\Engineering"
```

It races for files the way RevisionWorx does, and tells you if anything went wrong. Doing that once is the only way to know. Delete the `.revisionworx-share-test` folder afterwards.

**Setting it up (the boss, once):**
1. Everyone else closes RevisionWorx. Make a backup: this can't be undone, and older versions can't open the workspace afterwards.
2. Open the workspace on your computer, and choose **Team › Set this workspace up for a team…**. Choose a user name and a long password. From the command line: `revisionworx -w FOLDER team enable --admin NAME`.
3. **Write down the recovery code.** It's shown once. It's the only way to get an administrator account back if your computer is lost or you forget your password. Keep it away from the shared folder.
4. **Team › Accounts… › Add…** for each person, with a starting password. Command line: `revisionworx user add NAME`. Tell them the password yourself. It isn't stored anywhere.

Your administrator login works on your computer only. To use another one, use the recovery code there, which stops the old one working, or make a second administrator account.

**Everyone else:** open the workspace from the shared folder, and log in. The first time, you choose your own password. Without logging in you can look, but not change anything. Command line: `revisionworx login NAME`, which stays logged in for 12 hours; `revisionworx logout` ends it. Other programs that publish to RevisionWorx, such as OpenSchematix, use that login too. In the window, tick **Stay logged in** for that.

**Working:** just as on your own. Check a document out, edit its file in the shared folder, and check it in. Everyone sees who has what, and the window updates itself within a few seconds when someone else makes a change. Saves take turns, so one might wait for someone else's to finish. A checkout belongs to you, not to a computer, so you can check in from another desk.

**What an administrator can do that others can't:** manage accounts, release someone else's checkout (it's recorded, with your reason), change the lifecycle, document types, templates and engineering roots, and remove unused stored content.

**Disabling an account** stops that person's changes being accepted, by every computer, from then on. What they did before stands.

| You see | What it means | What to do |
|---|---|---|
| "Alice is saving a change…" | It's her turn, and it's taking a while. | Try again in a moment. If her computer has stopped, this clears itself after a minute, and it's recorded. |
| "Log in to make changes" | You aren't logged in. | **Team › Log in**. |
| "This login is out of date" | Your password was changed, or reset. | Log out and in again. |
| "Someone else changed the workspace at the same moment" | Your change wasn't made. Nothing is lost. | Try again. |
| "This administrator account only works on the computer it was set up on" | You're on another computer. | Use **Use recovery code…** in the login window, or a second administrator account. |
| "journal entry N is gone from the shared folder" / "isn't the one this computer read" | Files in `.revisionworx` have been lost, altered or restored. Changes are refused. | Lost or altered: on a computer that has read them, `revisionworx team repair`. Restored from a backup on purpose: on each computer, `revisionworx team forget-local`. Then `revisionworx doctor`. |

## PDFs of CAD drawings

A phone or a customer can't open an Inventor or SolidWorks drawing, so attach the PDF you export with each revision:
`revisionworx doc pdf FRM-200@B "FRM-200 rev B.pdf" --tool "Inventor 2025 export"`. The PDF is tied to revision B only: when rev
C is checked in, it has no PDF until you attach one, and rev B's is never shown in its place. A wrong PDF is replaced with
`--replace --reason "..."`; the earlier one stays on record. `revisionworx doc pdfs --missing` lists released revisions that have
no PDF yet. `revisionworx doc verify` checks the stored PDFs as well as the revisions.

## Units: what you built, and what's fitted in it

A **unit** is one physical thing with a serial number: a machine you built, a pump fitted in it, a gauge you test it with.
RevisionWorx records which revisions describe each unit from the day it was built, without copying a single drawing
(`docs/UNITS.md` has the details). In the window, **View → Units…** (Ctrl+Shift+U) finds a unit by serial, tag, model,
customer or site and shows what it is built to, what's due on it and its history; it only reads. Units are recorded from
the command line.

**Build standards.** Most units are built to the same drawings. Name that set once:
`revisionworx unit standard new HPU-75-A --title "HPU-75 standard A" --from-project J-2001` takes the released revision of every
document in the project (`--doc FRM-200@A` names them one by one). A build standard never changes; a new set is a new standard.

**Registering units.** `revisionworx unit new --serial NH-26-0001 --model HPU-75 --standard HPU-75-A --project J-2001 --built 2026-03-14 --status built`.
To have RevisionWorx issue serials, make a numbering once, `revisionworx unit numbering new NH --pattern "NH-{yy}-{seq:4}"`, and
register with `--serial-from NH` instead of `--serial`: the next number of the unit's year is taken in the same step, so two people
never get the same one. `{seq:4}` is the number padded to 4 digits; `{yyyy}`, `{yy}` and `{mm}` come from the built date (else
today), and numbers start again each year (or month) the pattern shows. `unit numbering next NH` shows the next serial without
taking it.
A serial from another maker gets `--issuer Parker`; a customer's tag is `--tag P-101A --tag-issuer "Acme Refinery"`. A serial can
belong to one unit only, and so can a tag, until it's retired (`unit id retire`) and moves to the machine that replaces it.
An existing register imports in one go: `revisionworx unit import register.csv --dry-run` lists every problem and writes
nothing; without `--dry-run` it registers every row or none. FieldWorx asset registers (`*.fwassets.csv`) import as they are,
and keep their FieldWorx ids, so inspections already recorded still find their unit.

**What a unit is built to.** `revisionworx unit show NH-26-0001` lists each document with the revision that applies to *this*
unit and where that came from. When engineering releases a newer revision, the unit keeps its own and says
"a later revision is released, not applied to this unit". When a unit is modified: `unit doc NH-26-0001 FRM-200@B --change ECO-0042
--basis verified`. When many are: `unit conform HPU-75-B NH-26-0001 NH-26-0002 ... --clear`, one entry for all of them.

**Components.** `unit fit NH-26-0001 P1 --part-number PMP-10 --serial 12345`. A component important enough to follow on its own
(repaired, refitted elsewhere) is registered as a unit with `--class component` and fitted with `--component SERIAL`; it can be in
one place at a time, and `unit where-used --component SERIAL --history` says everywhere it has been. A replacement is
`unit fit ... --replace --disposition repair`.

**From other programs.** Programs record on units through `revisionworx link` (`docs/INTEGRATION.md`): the units a work order
built, or what someone in the office accepted from the field. Each fact says where it came from (a work order, a field record)
and how well it's known (*reported* until someone verifies it), and in a team workspace it's in the name of whoever is logged in.

**Mistakes** are never edited away: `unit void NH-26-0001 7 --reason "recorded on the wrong unit"` keeps the event in the history
and computes the unit without it. `unit history` shows everything; `unit show --on 2027-05-01` shows the unit as known now on
that date; `unit verify` checks every history and reference, and changes nothing.

**Which units an engineering change reaches.** Once an ECO is implemented, decide which units it reaches. This is written once,
beside the ECO, which itself doesn't change:

- `revisionworx unit change candidates ECO-0042` lists the units that still have the revision the ECO replaces (or one of its
  affected parts), with why.
- `unit change decide ECO-0042 --kind retrofit-required --reason "cracks found in service"` puts every candidate in scope.
  `--scope customer --value Acme`, `--scope serial-range --value NH-26-0001..NH-26-0040`, `--scope standard --value HPU-75-A`
  or `--scope units --unit NH-26-0007 ...` choose fewer. Other kinds: `retrofit-optional` (offered; each customer accepts or
  declines), `unit` (one unit's own modification), `production` (new units only: `--standard HPU-75-B`), and `documentation`
  (a drawing corrected, machines unchanged: every unit in scope moves to the new revision at once, marked as a correction).
- Each unit in scope then goes its own way: `unit change set NH-26-0007 ECO-0042 scheduled`, `... deferred --reason ...`,
  `... not-applicable --reason ...`. `unit change install NH-26-0007 ECO-0042` records that it was done: the unit's drawings
  move to the new revisions, flagged *reported* until `unit change verify NH-26-0007 ECO-0042` confirms it (or install with
  `--basis verified` when someone saw it). `unit change reverse ... --reason ...` records it being taken out again.
- `unit change status ECO-0042` shows every unit in scope and where it stands. `unit compliance` lists every unit still
  missing a required change (and exits 1 if there is one, for scripts); `unit compliance NH-26-0007` lists one unit's changes.
  A unit found later joins with `unit change add ECO-0042 NH-26-0099`.

**Deviations and concessions.** When named units are to be built or repaired differently from their drawings, without changing the
drawings, record the approval once:

- `revisionworx unit deviation new --kind deviation --title "PMP-11 for PMP-10 at P1" --departure "P1 is a PMP-11 pump, not the
  PMP-10 the BOM calls for" --reason "PMP-10 out of stock; same flow and mounting" --unit NH-26-0007 --unit NH-26-0008
  --doc BOM-75@B --temporary --until 2027-03-31` gets the next number, DEV-0001. A **concession** (`--kind concession`, numbered
  CON-0001 …) accepts units already made that differ: a weld out of tolerance that passed its load test. Without `--temporary`
  it is permanent.
- What was actually fitted is recorded as usual, citing it: `unit fit NH-26-0007 P1 --part-number PMP-11 --replace
  --evidence deviation:DEV-0001`. When the right part goes back in, `unit deviation clear NH-26-0007 DEV-0001 --event 12`
  (the event that fitted it). A unit that didn't need it after all: `unit deviation not-used NH-26-0008 DEV-0001 --reason ...`.
- `unit deviation show DEV-0001` lists each unit and where it stands; `unit deviation list` lists them all; `unit deviation
  overdue` lists units with a temporary one past its end date (and exits 1 if there is one). One more unit:
  `unit deviation add DEV-0001 NH-26-0009 --reason ...`. `unit show` and the Units window list a unit's deviations.

## Troubleshooting

| You see | What it means | What to do |
|---|---|---|
| "The workspace is busy" / waiting for the writer lock | Another session is making a change. | Wait and retry. `revisionworx lock show` shows who holds the lock. |
| The lock is held by a session that crashed or a computer that is off | Its lock was left behind. | Confirm that session really is stopped, then `revisionworx lock break --token TOKEN --reason "..."` (token from `lock show`). Audited; the old session can no longer write. |
| "Nothing was written ... a virus scanner or backup tool has the file open" (Windows) | A file was briefly locked by another program. | Retry. If it keeps happening, exclude the `.revisionworx` folder from real-time scanning. |
| Red banner: RECOVERY BLOCKED / read-only | RevisionWorx found metadata it cannot trust and refuses to change anything. | Copy the workspace folder somewhere safe, then run `revisionworx doctor` and follow the instruction it prints. `revisionworx verify --repair` rebuilds records from the journal. Never edit files inside `.revisionworx` by hand. |
| "unreadable orphaned lock-release file" | A release was interrupted and its file cannot be read. | After confirming no other session is running: `revisionworx lock clear-orphan NAME --reason "..."`. |
| "Saved, but ... will be completed when the workspace is next opened" | Your change is recorded in the journal; a final step was delayed. | Nothing is lost. Open the workspace again (or press F5); do not repeat the change. |
| Search results missing or out of date | The local index is a cache. | `revisionworx index rebuild`. |
