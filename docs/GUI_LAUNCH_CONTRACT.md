# RevisionWorx desktop alpha — GUI launch and test contract (contract version 1)

**Owner:** Claude B (desktop GUI, issue #13). **Consumers:** Integration (PR #2) frozen-app smoke,
QA. **Scope:** RevisionWorx 0.1.0 Alpha, Director tier 1 only: a single user on a local
filesystem with disposable test data. Shared network folders are not supported and must not be
advertised.

Anything listed here stays stable on `feature/desktop-alpha` until it is announced on PR #14 and
issue #13 with migration notes. Anything not listed may change without notice.

## 1. Launch

| Entry | Behaviour |
|---|---|
| `revisionworx-gui` (gui-script) / `python -m revisionworx.gui.app` | Opens the main window. |
| `revisionworx-gui <workspace-folder>` | Opens that workspace. On failure it shows the start page with the reason and a modal error. |
| no argument | Reopens the most recent workspace if its folder exists. Otherwise, or if that fails, it shows the **start page**, with the reason in `start_error_label` (no modal). |

- Exit code **3** when PySide6 is missing; a message goes to stderr. Otherwise the code is Qt's exit code (0 on normal quit).
- No network access, account or telemetry. Offscreen works with `QT_QPA_PLATFORM=offscreen`.
- Per-user locations. All three are overridable in tests through `Controller(cfg_dir=, cache_dir=, state_dir=)`:
  - **Preferences** (`preferences.json`: recent workspaces): `%APPDATA%\RevisionWorx` or `$XDG_CONFIG_HOME/revisionworx` (default `~/.config/revisionworx`).
  - **Checkout tokens** (`checkouts.json`): `%LOCALAPPDATA%\RevisionWorx\state` or `$XDG_STATE_HOME/revisionworx`. Deleting this file loses the ability to check in, and recovering needs an administrator.
  - **Search cache**: the core default (`revisionworx.index.default_cache_dir()`). It is disposable.
  - **Journal checkpoints** (`views/`): in the state folder, beside the checkout tokens (ADR-029). Disposable: deleting one only means the next read-only handle reads the whole journal once.
- **Quick start layout.** "Create project" in folder `F` creates the workspace at `F/.revisionworx`, the engineering root `F/Projects`, and the project folder `F/Projects/<number> <name>`. If `F` already holds a workspace, it is reused.

## 2. Stable widget surface

You can reach widgets in two ways:
- **Attributes**, which the PR #2 probe already uses; or
- **`findChild(QWidget, objectName)`**, using the names below.

**`MainWindow(ctl: Controller)`**
- `alpha_banner`: object name `alphaBanner`.
- `health_banner`: object name `healthBanner`.
- `stack`, `start_page`, `workspace_page`.
- `table`, `table_model`: projects.
- `documents_tab`.
- `open_location(path, quiet=False, select_project=None) -> bool`.
- `select_project(id)`, `selected_project_id()`, `refresh_all()` (F5), `close_ws()`.
- `observable_state()`: see §3.
- Object names: `main_search`, `main_new_btn`, `main_adopt_btn`, `main_table`, `main_archive_btn`, `main_open_btn`, `main_busy_label`, `projectsNotice`, `projectHeading`.
- `tabs`: Documents (index 0, shown by default), Baseline files, Project details.
- While a background job runs, `close()`, `close_ws()` and `open_location()` are refused (`open_location` returns `False`). Wait for `busy` to be false first.

**`StartPage`**
- `folder_edit`, `name_edit`, `number_edit`, `customer_edit`, `preview`, `error_label`, `create_btn`, `open_btn`, `recent`. Object names are `start_<attribute>`.
- `last_created_id`: the project created by the last successful `create_btn` click.

**`DocumentsTab`**
- Buttons: `import_btn`, `register_btn`, `checkout_btn`, `checkin_btn`, `cancel_btn`, `open_file_btn`, `open_dir_btn`, `transition_btn`, `export_btn`, `compare_btn`, `restore_btn`.
- Other widgets: `filter`, `notice`, `table`, `history`, `revision_detail`. Object names are `documents_<attribute>`.
- Also `status` (`documentStatus`), `ownership` (`ownershipLabel`), `table_model`, `history_model`, `list_vm.project_id`, `panel.working_path`, `panel.history` (newest first), `selected_doc_id()`, `select_document(id)`, and `dialogs` (open Import dialogs; the newest is last).

**`ImportDialog`**
- `source`, `folder`, `dest`, `number`, `title`, `description`, `import_btn`, `message`.
- `flow.state` (`idle`, `importing`, `done` or `error`), `flow.message`, `flow.result` (`(doc, rev)`; `None` when saved with a warning).

**`CheckinDialog`**
- Modal from `checkin_btn`. Fields: `description` (plain text), `label`, `unchanged`, `error`. `submit()` validates and accepts.

## 3. Observable state (`MainWindow.observable_state()`, contract 1)

The method is read-only. It returns a JSON-serialisable dict and performs no I/O. Keys:

- **Window:** `contract`, `page` (`start` or `workspace`), `title`, `alpha_banner`, `health_banner` (`""` when hidden), `read_only`, `busy`, `workspace`, `start_error`.
- **Projects:** `projects_notice`, `projects` (`id`, `name`, `number`), `selected_project`.
- **Documents:** `documents_notice`, `documents` (`id`, `rel_path`, `rev`, `file_state`, `checked_out_by`).
- **Selected document:** opening a project selects its first document, if it has any. `selected_document`, `document_status`, `message_level` (`ok`, `warning` or `error`), `ownership`, `history` (newest first: `id`, `label`, `sha256`, `description`).
- **Actions:** `actions` holds an enabled flag for each of `new_project`, `adopt`, `archive`, `import`, `register`, `checkout`, `checkin`, `cancel_checkout`, `change_state`, `open_file`, `restore` and `compare`. (`adopt`, `archive`, `register` and `change_state` were added at `062baa3`; existing keys are unchanged, so this is still contract 1.)

`file_state` is one of `unchanged`, `modified`, `modified-externally`, `missing`, `unrevisioned` or `unavailable`.

## 4. Outcome semantics the smoke must assert (no false success)

- **Success:** `message_level == "ok"` and the history grows.
- **Saved with a warning** (core `CommittedNotApplied`, or a txid confirmed `committed` after an unexpected error): `message_level == "warning"`, the history grows, and the status starts with "Checked in." or "Imported (saved)". It is never shown as a failure.
- **Saved, but the document could not be re-read straight afterwards** (a Windows sharing denial, for example): the status starts with the same saved text and continues "⚠ This document could not be re-read: … Press F5 to try again". `message_level == "warning"`, even for a write with no other caveat. `selected_document` is `None` and no document actions are enabled, because what the panel knows is out of date. F5 retries the same document and restores the plain outcome. Never asserted as a failure.
- **Failure:** `message_level == "error"`. The history is unchanged, and the status never contains "Checked in".
- **Unknown outcome** (an unexpected error, and the journal could not be read): `message_level == "error"` and the status says it could not confirm. Check the history; do not assume either outcome.
- **Refused while writing:** a write refused for safety gives the same recovery-blocked state as below, after the next `MainWindow.poll()`. F5 re-checks.
- **Export:** runs in the background. `document_status` says where the file went, or `message_level == "error"`. A registered working file and anything inside `.revisionworx` are refused, whatever the save dialog confirmed.
- **Document unreadable:** `selected_document` is `None`, `message_level == "error"`, and the status starts with "⚠ Cannot read this document". No document actions are enabled. F5 retries.
- **Recovery-blocked:** `read_only` is true, `health_banner` contains "RECOVERY BLOCKED" and "READ-ONLY", and every write action is disabled, including the menu items. Browsing, history and export still work. F5 clears this only after an administrator has repaired the workspace.
- **Workspace unavailable** (folder moved, drive disconnected, reads refused): `read_only` is true, `health_banner` says it cannot be read, and every write action is disabled. F5 clears it once the workspace can be read. It is not recovery-blocked and needs no administrator.
- **Opening a second workspace fails:** `open_location` returns `False` and the current workspace stays on screen and open, with a modal error. The start page appears only when nothing is open.
- **Switching or closing a workspace** closes its Import and Adopt windows. A flow still held after that refuses to start and changes nothing.
- **Damaged or unwritable preferences:** never stop the application. A damaged file reads as an empty recent list. If the list cannot be saved, the workspace still opens and `ctl.preferences_warning` holds the reason.
- **Background jobs:** while `busy` is true, every write action is disabled. Poll `ctl.drain()` together with `app.processEvents()` until `busy` is false. **Changed after `2a6a7bd`: `busy` stays true until the result is delivered**, and `drain()` is what ends it, so a loop that waits on `busy` without draining (or without a running `MainWindow`) never finishes. When `busy` turns false, history, messages and actions have already settled; no further wait is needed.

## 5. What automated runs do NOT show

Offscreen runs do not show human-visible rendering, native file dialogs, OS integration (opening files and folders), installer behaviour, accessibility tools, or real SMB/NFS behaviour. Report offscreen and interactive results separately.
