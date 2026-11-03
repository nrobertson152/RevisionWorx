# RevisionWorx UI redesign proposal

**Status: PAUSED by the Director on 2026-11-02 so the GUI work can focus on integration conflicts and alpha acceptance. Do not resume without the Director's go-ahead.**

The Director liked the mockup ("that's awesome, great job"), which approved the direction but not a merge or release. Work so far is on `feature/ui-redesign`, stacked on `feature/desktop-alpha`, and is not part of the alpha. It is unfinished: see the status of the last commit on that branch. D1, D3, D4 and D6 below were not answered individually, so the build followed the recommendations. D5, the logo, is still needed.

The Director reviewed the working GUI and said it looks cluttered and outdated, and asked for a very intuitive design. Everything up to `d4e3623` fixed behaviour and made the screens fit. None of it was visual design.

## Where the mockup is
- **Clickable canvas:** the private claude.ai artifact named "RevisionWorx main screen redesign". The Director can open it; a session on the same account finds it with the Artifact tool's `list` action.
- **Source copy:** [`design/redesign/mockup/`](design/redesign/mockup/). `Main.dc.html` holds all the markup and logic; the other files only select a state of it. They run inside the canvas, not on their own.
- **Screenshots:** [`design/redesign/screens/`](design/redesign/screens/).
- **Sample data is invented.** The projects, documents, dates and hashes are made up, and the logo is a labelled placeholder.

## Screens
| Screenshot | Shows |
|---|---|
| `main.png` | Main screen. In the canvas it is clickable: check out, edit, check in, history, state changes, filters, new project, add files |
| `dark.png` | Dark mode |
| `checkin.png` | The check-in dialog |
| `attention.png` | A missing file |
| `readonly.png` | Changes paused for safety (recovery-blocked), with details open |
| `empty.png` | A new project with no files |
| `welcome.png` | First run |
| `one_at_a_time.png` | Alternative layout: one document at a time |

## Principles
1. **One obvious next step.** One filled button, which changes with the state: Check out, Check in or Restore file. Everything else is secondary or under More.
2. **Say what is going on in a sentence.** One status block with what is true and what to do, not three lines of status text.
3. **Show details when asked.** Hashes, sizes and compare live inside a history entry. Project details live behind a menu.
4. **Plain words.** No "managed", "wip" or raw timestamps. Check out and Check in stay, because PDM users know them. Revision labels and part numbers are monospaced so they read as data.
5. **One way to add files.** Import and Register become one Add files action, and the app works out whether to copy the file in or register it in place. Originals are never moved or changed.
6. **Safety does not get softer.** The alpha notice stays permanent. Recovery-blocked still disables every write and still shows the core's message verbatim, one click away. Saved with a warning is still never shown as a failure.

## Layout
- **Sidebar:** workspace switcher, search, projects with a small count of your checkouts, help, alpha notice. Parts, BOM and search sections (spec section 12, issues #15 and #16) get added here when they exist. It does not advertise anything that is not built.
- **Header:** project name and number, Add files, project menu. The menu holds open folder, project details and archive.
- **Filters:** All, Checked out by me, Needs attention, plus filter text.
- **Document list:** type icon, title with a problem or checkout tag, file name, number, revision, state. This replaces the separate Released, Checked out by and Working file columns.
- **Document panel:** title, status block, main button, Open file, More, latest and released revision, state menu with verbs (Send for review, Approve, Release), history timeline.

### What moved
| Now | Proposed |
|---|---|
| 17 buttons on screen at once | One main button and a few secondary ones |
| Project fields above the tabs | Project menu > Project details |
| Import file and Register file | Add files |
| Show in folder, Export, Cancel checkout | More menu |
| Compare, Restore | Inside the selected history entry |
| Change state combo plus button | The state chip opens a menu of verbs |
| Status bar text and page buttons | Removed; long lists scroll |
| Menu bar | Decision D4 below |
| Search box above the project table | Sidebar |

## Design tokens
**Type:** IBM Plex Sans 400/500/600 and IBM Plex Mono 400/500, both under the SIL Open Font License. Base size 14 px, document title 18 px, project title 22 px, secondary text 12.5 px. Check the licence text and bundle size before shipping them.

**Shape:** radius 8 px for controls, 10 px for status blocks, 12 px for cards. Flat surfaces with 1 px lines, no gradients.

| Token | Light | Dark |
|---|---|---|
| ground (sidebar) | `#F2F4F7` | `#12161B` |
| surface | `#FFFFFF` | `#191E25` |
| tint (selection) | `#E6EFFC` | `#1E2C42` |
| line / strong line | `#E1E5EA` / `#C6CCD5` | `#2A313A` / `#404955` |
| ink / secondary text | `#14181F` / `#56606C` | `#E9ECF0` / `#A2ACB8` |
| accent / text on tint | `#1D5FC9` / `#174FA8` | `#2F6FDB` / `#8DB8F7` |
| neutral chip | `#EEF1F5` on `#3D4651` text | `#262C34` on `#C3CAD3` text |
| ok | `#E2F4E9` / `#14613A` | `#16352A` / `#7AD6A2` |
| warning | `#FFF3D6` / `#6F4700` | `#3A2E12` / `#EDC36B` |
| danger | `#FDECEA` / `#A31F17` | `#3E1F1D` / `#F39A90` |

The accent is a placeholder until it can be matched to the real logo.

## How it was checked
The mockup was run locally in Chromium with the canvas's own runtime and the real fonts, and the scripted flows passed: check in, cancel with discard, restore, state changes, filters, new project, read-only, first run, the alternative layout. There was no horizontal overflow at 390 and 900 px. All 22 text and background colour pairs checked are at least 4.5:1 in both themes. The tools are in [`design/redesign/tools/`](design/redesign/tools/).

**Not checked:** it has not been seen on the canvas page itself, which this environment cannot open. It is a web mockup, so the real Qt build will differ in detail. No person has tried it, and "intuitive" can only really be judged that way.

## Decisions for the Director
- **D1 Layout:** side by side (document list and panel, `main.png`), or one document at a time (`one_at_a_time.png`). Side by side is the recommendation: you can scan and act without losing your place, at the cost of more on screen.
- **D2 Look:** is this direction right, including the typeface?
- **D3 Dark mode:** follow the system setting and allow an override? That is the recommendation.
- **D4 Menu bar:** remove it, moving its items to the sidebar, project menu and Help? The mockup assumes yes. Shortcuts stay.
- **D5 Logo:** the file is needed. `docs/REVISIONWORX_BRANDING_DIRECTIVE.md` on `main` says it was only shared as a conversation image, and it must not be approximated.
- **D6 Add files:** merge Import and Register into one action?

## Implementation plan, after review
Stay on PySide6 Widgets, styled with QSS and custom-painted item delegates. No web view, no new runtime dependency in the core, still fully offline.

1. Theme module (tokens, light and dark, bundled fonts, icons) and the application icon.
2. Shell: sidebar and header, replacing the current layout.
3. Document list with a painted delegate.
4. Document panel: status block, state-driven main button, More menu, state menu.
5. History timeline.
6. Dialogs, empty states, first run, drag-and-drop.
7. Update `tests/e2e_x11_walkthrough.py` and the offscreen tests in the same change as each step. Keep the review screenshots current.

### Things to watch
- **Launch contract:** Integration and QA drive widgets by name. Keep attributes and `observable_state()` stable where possible. Otherwise bump the contract version and announce it on PR #14 and issue #13 first.
- **Mnemonics and keys:** the walkthrough presses real keys, and the current Alt keys will change.
- **Narrow windows:** the current minimum is 842x666 on Windows. Three panes need a rule below roughly 1100 px, such as collapsing the sidebar.
- **Scrolling:** in the app, the list and panel scroll independently. The mockup scrolls as a page.
- **Windows:** the offscreen font on Windows CI is about 1.9x wider than real text, so size checks need the native step.

### Core requests this would add (Claude A)
- **A9:** the read-only banner offers a copyable fix command. Building it needs structured data on `WorkspaceCorruptError`, such as the file name and command. Without that, the GUI can only copy the core's message.
- **Restore file:** for a missing working file, the GUI could compose checkout, `restore_to_working` and cancel. A single public call would be safer. Check how core behaves with a missing file first.
- **A1:** a read-only `Workspace.health()` stays the main request. See `docs/DESKTOP_ALPHA_PROGRESS.md`.
