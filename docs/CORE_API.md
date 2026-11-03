# RevisionWorx core API contract (for UI and integration callers)

**Owner:** Claude A (core). **Consumers:** Claude B (desktop alpha, issue #13) and Integration
(PR #2). **Source of truth:** `phase2-documents`. A change to anything below is announced on
PR #7 and issue #13 with migration notes *before* it lands. Everything not listed here is
internal and may change without notice. In particular, do not call `Workspace.commit`, the
journal or lock helpers, or anything starting with `_`, from UI code.

Stability levels:
- **Stable:** the signature, return keys and exceptions listed here hold for 0.1.0 alpha.
- **Provisional:** usable, but may change with notice.

## 1. Opening a workspace (Stable)

```python
from revisionworx.workspace import Workspace
ws = Workspace.create(location: Path, name: str)        # new workspace; returned open, writable
ws = Workspace.open(location: Path, writable=False)     # read-only by default
ws.close()                                              # or: with Workspace.open(...) as ws:
```

- `location` is the shared folder that contains (or will contain) `.revisionworx/`.
- A UI holds **one** writable handle per workspace, opened on demand for a change and closed
  promptly. A writable handle holds the single writer lock, and every other user is read-only
  while it is held.
- Read-only handles never block anyone.

| Exception (all subclass `WorkspaceError`) | Meaning | UI action |
|---|---|---|
| `WorkspaceLockedError(holder)` | Another session holds the writer lock. `holder` has `user`, `host`, `pid`, `acquired`. | Show "in use by user@host since …"; offer read-only. |
| `WorkspaceCorruptError` | Writes refused for safety. Includes "orphaned lock-release file … `lock clear-orphan`" and journal gaps. | **Recovery-blocked state.** Show the message verbatim and offer read-only. Never retry in a loop. Resolution is an administrator action (CLI). **A journal gap also refuses read-only opens**, so there is no read-only to offer in that one case. |
| `LockReleaseBusyError` (`.name`) | A writable open was refused because a lock-release file could not be read right now (another session releasing, or a program holding it). Nothing was written; not corruption. | Show the message; offer read-only and a manual **Retry**. Do not loop automatically. Not an administrator action unless it persists. |
| `WorkspaceFormatError` | Not a workspace, or an unsupported format version. | "This folder is not a RevisionWorx workspace (or needs a newer version)." |
| `OSError` (`PermissionError`, `FileNotFoundError`) | Filesystem refused. | Show the path and the OS message; do not retry automatically. |

## 2. Projects (Stable)

```python
from revisionworx import projects
projects.add_root(ws, name, path) -> str                                 # root_id (needs a root first)
projects.create_project(ws, projects.NewProject(name=..., number=..., ...)) -> dict  # project record
projects.adopt_project(ws, folder, name, mode="indexed", number="") -> dict          # opt-in existing folder
projects.preview_adoption(ws, folder) -> dict                            # read-only preview, call first
ws.list("project") -> dict[str, dict]                                    # id -> record
projects.project_folder(ws, record) -> Path
```

- A project record has `id`, `name`, `number`, `customer`, `description`, `mode`, `root_id`,
  `rel_path`, `status`, `origin`, `created`, `created_by` and `_rev`.
- Errors: `ProjectError` (validation, duplicate number, no root, path problems).

## 3. Documents (Stable)

```python
from revisionworx import documents as d
d.register_document(ws, project_id, file_path, *, type_id=None, number="", title="",
                    description="", capture=True, revision_description="Initial revision",
                    label=None) -> tuple[doc: dict, rev1: dict | None]
d.list_documents(ws, project_id=None) -> list[dict]       # sorted by project, then path
d.document_status(ws, doc_id, verify_hash=False) -> dict  # state: missing | unrevisioned | unchanged
                                                          #        | modified | modified-externally
d.history(ws, doc_id) -> list[dict]                       # revisions, oldest first
d.working_path(ws, doc) -> Path
d.export_revision(ws, rev_id, dest, overwrite=False) -> Path
```

- "Import" means the file already lives inside the project folder and is registered **in
  place**. The core never moves or edits it. To bring in an outside file, the UI copies it into
  the project folder first, then registers it.
- A document record has `id`, `project_id`, `rel_path`, `number`, `title`, `type_id`, `status`,
  `latest_rev_id`, `rev_count` and `_rev`.
- A revision record has `id`, `document_id`, `number`, `label`, `sha256`, `size`,
  `description`, `state`, `previous_rev_id`, `restored_from`, `created`, `author` and `transitions`.
- Revisions are immutable.

## 4. Checkout and check-in (Stable)

```python
co = d.checkout(ws, doc_id, note="") -> dict          # co["token"] is the session's proof of ownership
d.checkin(ws, doc_id, token, description, label=None, txid=None) -> dict   # new revision record
d.cancel_checkout(ws, doc_id, token, revert=False) -> None
```

- Keep the token across restarts with `revisionworx.clientstate.CheckoutTokens().put(ws.id,
  doc_id, token)` / `.get(...)`. Without it the checkout can only be released by an
  administrator.
- `description` must be non-empty.
- **Retries:** pass a `txid` (32 lowercase hex) that you generate once per user action. On an
  ambiguous failure (crash, lost connection), retry with the same txid. You get the original
  result, never a duplicate revision.
- **A fenced txid is spent.** If the attempt lost the writer lock, the retry raises `LockLostError`
  ("never took effect"). Nothing changed. Reopen and start a new attempt with a **new** txid.

| Exception | Meaning | UI action |
|---|---|---|
| `CheckoutConflict(checkout)` | Already checked out; `.checkout["owner"]` has user/host. | "Checked out by …". |
| `NotCheckedOut`, `TokenMismatch` | No checkout, or someone else's. | Refresh the view; offer read-only. |
| `StaleCheckout` | A newer revision exists; nothing was written. | Tell the user to save a copy, check out again and merge. |
| `NoChanges` | File identical to the latest revision. | Offer cancel checkout. |
| `MissingFileError` (a `DocumentError`) | The working file is not there. | Tell the user to put it back. |
| `DocumentError` | Other validation (link, archived document). | Show the message. |
| `ConflictError` | Someone changed the record since you read it; nothing written. | Reload and let the user retry. |
| `LockLostError` | This writable handle lost authority; nothing took effect. | Close the handle, reopen and retry the action. |
| `CommittedNotApplied(seq, txid)` | **The change IS saved.** Only derived files lag. `.result` holds what `checkout` or `checkin` would have returned, including the checkout token. | Report success with a warning. Keep the token. **Do not retry as a failure.** |
| `TxidReuseError` / `IdentityUnverifiable` | The txid was used for a different request / a pre-ADR-021 entry. | Programming error / ask `ws.transaction_outcome(txid)`. |

`ws.transaction_outcome(txid) -> {"state": "committed" | "fenced" | "not-found", "seq"?}` answers
"did my action happen?" after a crash. Only `committed` means it took effect.

## 5. Health (Provisional)

`ws.health() -> {"ok", "reason", "head_seq", "applied_seq", "pending"}` is the quick check: it never raises for a damaged
journal, `ok` is False when writes would be refused, and `pending` entries are saved and need no action.

`ws.verify() -> {"problems": [...]}` is the full check: show problems read-only. Repair is an administrator action. A problem
with `severity: "info"`, such as `fenced-entry`, needs no action and can never be cleared.

## 6. Administrator-only operations (CLI, not for the alpha UI)

`revisionworx lock show | break | clear-orphan` (ADR-015/019). These are deliberately absent from
the UI contract. The alpha shows the recovery-blocked message from §1 and stops there.

## 7. Local search index (Provisional)

`open_index(ws, cache_dir=None)` syncs and returns a `LocalIndex`. Use its methods, including `project_files(project_id)`, rather
than `idx.db`: only the methods replace a damaged cache and retry. `IndexBusyError` (a `WorkspaceError`) means another
RevisionWorx process is using it: nothing is wrong, so try again. A damaged cache replaces itself. `idx.coverage()`
now includes `baselines_unavailable`, the projects whose file list could not be read.

## Change log
- 2026-11-02: `ws.health()`, `LocalIndex.project_files` and `documents.require_document` are new (Claude B's A1 and A5). All additive.
- 2026-11-02: `MissingFileError` and `CommittedNotApplied.result` are new (Claude B's A4 and A6). `verify` marks `fenced-entry` with
  `severity: info`, and the CLI no longer exits 1 for it alone. `doc verify` also audits the journal for changed revisions.
  `read_writer_lock` reports an unreadable lock as `{"token": "?", "unreadable": True}` instead of raising `ValueError`.
- 2026-11-03 (ADR-019 rev. 5): `LockReleaseBusyError` is new. `Workspace.open(writable=True)` raises it, not `WorkspaceCorruptError`, when a `.releasing-*`/`.fencing-*` file stays access-denied (Windows delete-pending or a held file); if that file has gone, the open now succeeds. No signature changed.
- 2026-11-02 (`feffbcf`): `checkin(txid=...)` retries now return the original revision, as this page always said.
  Before, a retry raised `NotCheckedOut` or `TxidReuseError`. `IndexBusyError` is new. `get`/`list` ignore record files
  the journal never wrote. No signature changed. `CheckoutTokens` now stores one file per checkout; its API is unchanged,
  and tokens in an existing `checkouts.json` still work.
- 2026-11-02: first published (core `phase2-documents`, ADR-019 rev. 3). Nothing above changed
  between `366d435` and this commit. `OrphanClearConflict` is new, but admin-only (CLI).
