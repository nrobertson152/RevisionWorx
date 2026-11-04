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
- **Checkout:** permission for one session to edit a document's working file. Only that session can check it in.
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

Do not delete the checkout tokens while you have documents checked out: without them this session can no
longer check those documents in (an administrator can still release the checkouts).

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
