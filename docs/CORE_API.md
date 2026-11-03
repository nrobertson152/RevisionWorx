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
| `WorkspaceCorruptError` | Writes refused for safety. Includes "orphaned lock-release file … `lock clear-orphan`" and journal gaps. | **Recovery-blocked state.** Show the message verbatim and offer read-only. Never retry in a loop. Resolution is an administrator action (CLI). |
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

| Exception | Meaning | UI action |
|---|---|---|
| `CheckoutConflict(checkout)` | Already checked out; `.checkout["owner"]` has user/host. | "Checked out by …". |
| `NotCheckedOut`, `TokenMismatch` | No checkout, or someone else's. | Refresh the view; offer read-only. |
| `StaleCheckout` | A newer revision exists; nothing was written. | Tell the user to save a copy, check out again and merge. |
| `NoChanges` | File identical to the latest revision. | Offer cancel checkout. |
| `DocumentError` | Other validation (missing file, link, archived document). | Show the message. |
| `ConflictError` | Someone changed the record since you read it; nothing written. | Reload and let the user retry. |
| `LockLostError` | This writable handle lost authority; nothing took effect. | Close the handle, reopen and retry the action. |
| `CommittedNotApplied(seq, txid)` | **The change IS saved.** Only derived files lag. | Report success with a warning. **Do not retry as a failure.** |
| `TxidReuseError` / `IdentityUnverifiable` | The txid was used for a different request / a pre-ADR-021 entry. | Programming error / ask `ws.transaction_outcome(txid)`. |

`ws.transaction_outcome(txid) -> {"state": "committed" | "fenced" | "not-found", "seq"?}` answers
"did my action happen?" after a crash. Only `committed` means it took effect.

## 5. Health (Provisional)

`ws.verify() -> {"problems": [...]}`: show problems read-only. Repair is an administrator action.

## 6. Administrator-only operations (CLI, not for the alpha UI)

`revisionworx lock show | break | clear-orphan` (ADR-015/019). These are deliberately absent from
the UI contract. The alpha shows the recovery-blocked message from §1 and stops there.

## Change log
- 2026-11-02: first published (core `phase2-documents`, ADR-019 rev. 3). Nothing above changed
  between `366d435` and this commit. `OrphanClearConflict` is new, but admin-only (CLI).
