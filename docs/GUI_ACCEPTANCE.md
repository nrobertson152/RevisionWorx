# RevisionWorx 0.1.0 Alpha: GUI acceptance

**Status: not yet performed.** No person has run this yet. Everything automated is listed in [What automation already covers](#what-automation-already-covers), and it does not replace these steps.

**Who runs it:** the Director, or someone the Director names. **Time:** about 30 minutes.

**Scope:** alpha Tier 1 only, meaning one user, a local disk and disposable test data. Passing this does not make the GUI suitable for real engineering data or shared folders. It also does not approve a merge or a release. Those are the Director's separate decisions.

**The visual redesign is paused.** This checks the current screens for correct behaviour. Judge whether things work and are understandable, not how modern they look. The redesign work is kept on `feature/ui-redesign`.

## Which commit to test

An acceptance applies only to the exact commit tested.

- **Now (optional):** test the head of `feature/desktop-alpha` to catch usability problems early. This branch does not contain the final core yet.
- **For PR #14 (required):** repeat on the merge commit that brings in the core candidate accepted by QA. That commit does not exist yet.

## Set up

Use a copy of a file you can lose. Don't use a real project folder.

**Windows (PowerShell).** Needs Python 3.11 ([python.org](https://www.python.org/downloads/)) and Git.

```powershell
git clone --branch feature/desktop-alpha https://github.com/nrobertson152/RevisionWorx.git C:\RwxTest\src
cd C:\RwxTest\src
git rev-parse HEAD                       # record this in the results
py -3.11 -m venv C:\RwxTest\venv
C:\RwxTest\venv\Scripts\python -m pip install ".[gui]"
C:\RwxTest\venv\Scripts\revisionworx-gui
```

**Linux.** Needs Python 3.11 and Git. Some systems also need Qt's runtime libraries; on Ubuntu those are `libegl1 libgl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-xinerama0`.

```bash
git clone --branch feature/desktop-alpha https://github.com/nrobertson152/RevisionWorx.git ~/rwxtest/src
cd ~/rwxtest/src
git rev-parse HEAD                       # record this in the results
python3.11 -m venv ~/rwxtest/venv
~/rwxtest/venv/bin/python -m pip install ".[gui]"
~/rwxtest/venv/bin/revisionworx-gui
```

**Starting from nothing:** if you have run RevisionWorx before, it reopens your last workspace. To see the first-run screen, rename the preferences folder first: `%APPDATA%\RevisionWorx` on Windows or `~/.config/revisionworx` on Linux.

In the steps below, `F` is the folder you choose in step 2, and `revisionworx` means the tool in the venv: `C:\RwxTest\venv\Scripts\revisionworx` or `~/rwxtest/venv/bin/revisionworx`.

## Steps

Mark each step Pass or Fail. If a step fails, note what you saw, keep going if you can, and attach a screenshot.

### Workflow 1: start offline, create a project

| # | Do | Expect |
|---|---|---|
| 1 | Turn off Wi-Fi or unplug the network, then start the app. | It opens with no account or sign-in prompt. The amber notice says single user, local disk only, not suitable for irreplaceable data. The window title says (Alpha). |
| 2 | On the start page, choose a new empty folder as `F`. Enter name `Test Bracket` and number `T-1`. Click **Create project**. | The project opens. `F\Projects\T-1 Test Bracket` exists on disk. |

### Workflow 2: import and browse

| # | Do | Expect |
|---|---|---|
| 3 | Put a test file outside `F`. Click **Import file…**, choose it and click Import. | The document is listed with its first revision and status. The title defaults to the file name. |
| 4 | Check the original test file. | It is still there and unchanged. |
| 5 | Import a second file, then type part of its name in the filter box. Clear the filter afterwards. | The list narrows to the match, then comes back. |

### Workflow 3: check out and check in

| # | Do | Expect |
|---|---|---|
| 6 | Select a document and click **Check out**. | The status says it is checked out to you. **Check in…** becomes available. |
| 7 | Click **Open file**, change the file and save it. Use your CAD program if the test file is a CAD file. | The file opens in its normal program. After F5, the status says the working file has been edited. |
| 8 | Click **Check in…** and try to submit with no description. | It is refused and asks for a description. Nothing is saved. |
| 9 | Enter a description and submit. | "Checked in revision…" appears, and the history gains a revision showing your description. |

### Workflow 4: history and restart

| # | Do | Expect |
|---|---|---|
| 10 | Select the older revision in the history, then the newer one. With the newer one selected, click **Compare with previous**. | The revision details show author, size and SHA-256. Compare says whether the content differs and how. |
| 11 | Select the older revision and click **Export revision…**, saving outside `F`. | The status says where it went. The exported file has the older content. |
| 12 | Close the app and start it again. | The same workspace reopens, with both revisions. |
| 13 | Check out, select the older revision and click **Restore to working file…**. Then click **Cancel checkout…** and choose **Discard edits**. | The working file temporarily has the old content. After Discard, it has the latest content again and the history is unchanged. |

### Workflow 5: errors you can act on

| # | Do | Expect |
|---|---|---|
| 14 | With no checkout, edit the working file directly and press F5. | An amber warning says it was changed WITHOUT a checkout. Undo your edit. |
| 15 | Check out, delete the working file, press F5. | A red status says the working file is missing. |
| 16 | Click **Check in…** and submit. | A "Missing file" message says what to do. No revision is added. |
| 17 | Follow it: **Cancel checkout…** then **Discard edits**. | The file comes back with the latest content. |
| 18 | Deny access to a new source file, then try to import it. Windows: `icacls <file> /deny "%USERNAME%:R"`. Linux: `chmod 000 <file>`. Undo afterwards with `icacls <file> /remove:d "%USERNAME%"` or `chmod 644 <file>`. | A message says permission was denied and to check your access rights. Nothing is imported. |
| 19 | Close the app. Create an empty text file named `.releasing-deadbeef` containing only `{}` in `F\.revisionworx\locks`. Start the app and click **Check out**. | An error explains the block. A red banner says RECOVERY BLOCKED and READ-ONLY. Every write action is disabled, but you can still browse history and export. |
| 20 | Run `revisionworx -w "F" lock clear-orphan .releasing-deadbeef --reason acceptance`, then press F5 in the app. | The banner clears and Check out works again. |

### Workflow 6: alpha marking

| # | Do | Expect |
|---|---|---|
| 21 | Move around the screens: start page, project, documents and a dialog. | The alpha notice stays visible on the main window. Nothing suggests shared folders or production use are supported. |

### Worth judging while you go

These aren't pass or fail, but they are what automation can't tell us.

- Was the next step obvious at each point?
- Did any message confuse you or leave you unsure whether your work was saved?
- Could you complete the journey using only the keyboard? Tab moves between controls, and the underlined letters work with Alt.
- On a high-DPI screen or at Windows display scaling above 100%, was anything cut off?

## Report the result

Post this as a comment on issue #13.

```text
GUI acceptance
Commit (git rev-parse HEAD):
OS and version:
Python version:
Display scaling:
CAD program used, if any:
Steps passed:
Steps failed (number, what you saw):
Steps not run, and why:
Other comments:
Verdict: ACCEPT / ACCEPT WITH ISSUES / REJECT
```

Report steps you did not run as not run, not as passed.

## What automation already covers

Results come from exact-commit CI recorded in [`DESKTOP_ALPHA_PROGRESS.md`](DESKTOP_ALPHA_PROGRESS.md). Those results apply only to the commit named there.

| Check | Where | How it runs |
|---|---|---|
| Workflows 1 to 6 through real widgets, with the core on real files | Linux and Windows | Offscreen |
| Window size with real fonts | Windows | Native platform, sizes only |
| Clean install, entry points, opening a project | Linux and Windows | Linux offscreen, Windows native platform |
| Keyboard-only journey, including `kill -9`, recovery block and crash lock | Linux only | Real X display, real keystrokes |
| A real crash between commit and apply, then restart and recovery | Linux and Windows | Child process, offscreen window |
| GUI merged with the last accepted core | Linux and Windows | Offscreen |

## What nobody has checked

- A person using the installed app, on any OS.
- Native Windows file dialogs, and opening files in a real CAD program.
- A real keyboard-only journey on Windows.
- Screen readers. Controls have names, but no screen reader has been used.
- Display scaling above 100%, or a real high-DPI monitor.
- This GUI on the final core, because that core does not exist yet.
- Installers or packaged builds. These steps use a Python install from source.
- Step 18 on Windows. The `icacls` commands and the resulting message have not been tried there.

## Known limitations

- A session that crashes while writing leaves a lock. The GUI shows the exact `lock break` command, but you have to run it yourself. Whether this should be recovered automatically when the process is gone is an open question for the Director.
- Reading a workspace file on Windows can be refused for a moment by another program or a concurrent write. The branch's current core does not retry this. A saved change is still reported as saved, and F5 retries. Core's retry arrives with the core merge.
- Shared network folders are not supported.
- No byte-level progress for very large files.
