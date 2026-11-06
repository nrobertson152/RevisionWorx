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
