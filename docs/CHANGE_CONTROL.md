# RevisionWorx — Engineering Change Control (ECR/ECO)

**Owner:** Claude D (issue #19). **Branch:** `feature/change-control`, based on `phase2-documents`.
**Status:** implemented and tested on Linux in this branch; awaiting Windows CI, independent QA and
Codex review. Scope is the Director's Tier 1: single user, local filesystem, trusted internal namespace.
**Package:** `revisionworx/changes/` (standard library only). **No core file is modified.**

## 1. What it does

| Area | Implemented |
|---|---|
| Change requests (ECR) | create, edit (draft only, field-level history), submit, start review, approve with signoffs, reject, return for rework, cancel; reason required; affected documents, projects, parts, assemblies, BOMs, other |
| Change orders (ECO) | create from one or more approved ECRs, actions (required/optional, done/waived), implementing revision per item, release and closure signoff gates, implemented, reopen, close, cancel |
| Lifecycle | fixed state machines with validated transitions; configurable ceremony (policy) |
| Audit | one immutable, hash-chained event per state change, written in the same commit; verification of missing/altered/reordered/truncated history |
| Signoffs | local approval/rejection evidence bound to the exact reviewed content and review round |
| Dependencies | `requires` (enforced before an order is implemented/closed) and `related`; cycle detection |
| Revisions | baselines per affected document; stale-baseline detection; implementing revision must descend from the baseline; revisions checked in between must be acknowledged |
| Impact | per-item verification, stale/checked-out/concurrent-change flags, dependency tree both ways, blockers, coverage statement (text/CSV/dict) |
| Search/reports | filter, sort, page; "where changed" for a document/part; status counts; change register CSV |
| Import/export | self-verifying JSON package with full audit chains; all-or-nothing CSV import of draft ECRs; atomic file writes |
| GUI contract | toolkit-free action availability, error → UI action mapping, one-call detail view (section 7) |
| Fixtures | deterministic in-memory sample and a disposable sample workspace |

Not in scope here: project/document dashboard and discovery (issue #16), parts catalog and BOM
(issues #15/#18), Qt widgets (issue #13), where-used/Before You Revise (Phase 4).

## 2. Data model (`changes/model.py`)

Identifiers are internal and stable (`ecr_<32 hex>`, `eco_<32 hex>`), separate from the
company-visible number (`ECR-0001`; prefix and width configurable, or user-supplied). Numbers are unique
under NFKC + casefold, so `ECR-0001`, `ecr-0001` and full-width `ＥＣＲ－０００１` collide.

```text
ChangeRequest  id, number, title, reason*, description, status, priority, category, project_ids,
               affected[AffectedItem], dependencies[Dependency], signoffs[Signoff], eco_id,
               disposition, round, created/created_by, modified, event_count, event_head
ChangeOrder    same base fields + request_ids, actions[OrderAction], implementations[Implementation]
AffectedItem   item_type (document|project|part|assembly|bom|other), item_id, label,
               baseline_rev_id (documents), baseline_label, action (revise|create|obsolete|review|reference)
Signoff        stage (ecr-review|eco-release|eco-closure), decision (approve|reject), actor, time,
               content_digest, round, role, comment
Implementation item_key, status (pending|implemented|not_required), rev_id, rev_label, intervening[]
```

All text is NFC-normalised, length-limited and free of control characters (newline/tab allowed in
multi-line fields); unpaired surrogates are refused. Every record read from storage or an import passes
the strict loader; a record that does not match the schema raises `MalformedRecord` and is never partially
used. Before anything is written it is round-tripped through the same loader.

## 3. Lifecycle (`changes/workflow.py`)

```text
ECR: draft --submit--> submitted --begin_review--> in_review --approve (quorum)--> approved
       ^                   |                          |
       +-- return_for_rework (reason) ----------------+--reject (reason)--> rejected
     draft|submitted|in_review|approved --cancel (reason)--> cancelled
     approved --(order created)--> converted --(order closed)--> closed
                                   converted --(order cancelled)--> approved

ECO: planning --start_work [release gate]--> in_progress --mark_implemented [blockers]--> implemented
     implemented --close [closure gate, revision states]--> closed      implemented --reopen--> in_progress
     planning|in_progress --cancel (reason)--> cancelled
```

Any other step raises `TransitionError` and writes nothing; tests run every step from every state.

**Policy** (stored, validated, changes audited):

| Setting | Default | Meaning |
|---|---|---|
| `ecr_approvals_required` | 1 | distinct approvers in the current round on the current content |
| `eco_release_approvals_required` | 0 | approvals before work starts |
| `eco_closure_approvals_required` | 0 | verification approvals before closing |
| `require_distinct_approver` | false | requester may not approve own ECR |
| `require_affected_items` | true | an ECR must list an item before submission |
| `block_stale_baselines` | true | refuse submit/approve/order/start while a document baseline is out of date |
| `close_requires_revision_state` | none | `approved` or `released`: implementing revisions must have reached it |
| `ecr_prefix`, `eco_prefix`, `number_width`, `categories` | `ECR-`, `ECO-`, 4, six categories | numbering and classification |

A rejection signoff in the current gate blocks it until the content changes (release) or a new round
starts (closure). Returning an ECR for rework and resubmitting starts a new round: earlier approvals stay in
the record as history but no longer count.

## 4. Persistence and the adapter contract (`changes/store.py`)

The service builds **one `Commit` per user action** (change records, audit events, number reservations,
counters, plus `Check`s on records the decision depended on) and hands it to a `ChangeStore`.

`WorkspaceStore` is the production adapter and the only module that touches the core store. It uses
exactly the interface core subsystems (`documents.py`, `projects.py`) already use:

| Core interface | Used for |
|---|---|
| `register_kind` | record kinds `changereq`, `changeord`, `changeevt`, `changenum`, `changecfg` |
| `Workspace.commit(op, [RecordPut/RecordCheck], txid=)` | every write, as one journal entry |
| `expect_rev` (0 = must not exist) | optimistic concurrency, write-once events, unique numbers |
| `RecordCheck("document", id, rev)` | pins a stale-baseline decision to the document state it read |
| `Workspace.get/list` | reads (journal-authoritative per ADR-022) |
| `Workspace.transaction_outcome(txid)` | idempotent retry |

Consequently every change-control write is published under the writer lock with fencing, replayed after a
crash, refused when fenced, and checked by `verify()`. Recovery is kind-agnostic, so a process that never
imports `revisionworx.changes` still replays these entries. **Nothing here reads or writes journal, lock or
record files, and no core file was changed.**

> **Request to Claude A (core owner):** CORE_API.md says UI code must not call `Workspace.commit`. This
> package is a domain subsystem like `documents.py`, not UI code, and confines that call to one adapter.
> Please confirm (or list as *Provisional: subsystem persistence*) `register_kind`, `commit` with
> `RecordPut`/`RecordCheck`/`expect_rev`/`txid`, `get`, `list` and `transaction_outcome`. If you prefer a
> different subsystem API, only `store.py` changes.

`MemoryStore` implements the same semantics in memory for unit tests and GUI view-model tests.

### Failure behaviour

| Situation | Result |
|---|---|
| Invalid input, wrong state, duplicate number, cycle, stale baseline, gate not met | domain exception before the commit; nothing written |
| Record changed since the user saw it (`expected_rev`) | core `ConflictError`; nothing written |
| Two operations race for the same number or event slot | `expect_rev=0` makes the second a conflict (`DuplicateNumber` for numbers) |
| Writer lock broken by an administrator | core `LockLostError`; nothing took effect (tested) |
| Crash after the journal entry, before records are updated | core `CommittedNotApplied`: **saved**; readers already see it; next writable open completes it (tested in-process and with a killed process) |
| Process killed before the commit point | nothing written; retry creates it once (tested with a killed process) |
| Retry after an ambiguous failure with the same `txid` | returns the current change, `svc.last_replayed = True`; creations derive their ids from the txid; a txid used for another change raises `ReplayRefused` |
| Attempt fenced by a lock break | `ReplayRefused`: start again with a new txid |
| Malformed record in storage | `get` raises `MalformedRecord`; lists, search and impact report it in `problems`; full export refuses |

## 5. Audit trail and what a signoff means (`changes/audit.py`)

Event *n* of a change has an id derived from (change id, *n*) and is written with `expect_rev=0`, so it can
only be written once and two writers cannot both append event *n*. Each event stores the SHA-256 of the
previous event; the change stores the count and the latest digest. `verify_history()` and
`exchange.verify_package()` report `missing-event`, `altered-event`, `misplaced-event`, `broken-link`,
`head-mismatch` and `unrecorded-event`. Edits record old and new values of each changed field.

**Scope of approvals:** a signoff records the operating-system account name and host reported by the
local machine, the time, the decision and the digest of the exact content approved. RevisionWorx has no
accounts or authentication, so this is **local workflow evidence, not an electronic signature**: it is not
cryptographic, does not prove identity, and does not by itself satisfy regulated-signature requirements
(e.g. 21 CFR Part 11). Hash chaining makes accidental or partial alteration detectable; someone with write
access to the shared folder could rewrite a whole history consistently. Distinct-approver checks compare
account names, so two people sharing one login count as one.

## 6. Revisions, stale baselines and impact (`changes/catalog.py`, `changes/impact.py`)

A document item records the revision the change is based on (default: the document's latest revision when
added). A baseline is **stale** when the document's latest revision is no longer the baseline. With the
default policy this blocks submit, approve, order creation and start of work; `rebaseline()` (in draft)
moves baselines forward and the edit is recorded. An implementing revision must be a revision of that
document, must not be the baseline, and the baseline must be among its ancestors; revisions checked in
between by other work are listed and must be acknowledged.

Change control never edits documents, revisions or checkouts. It reads them through the `ItemCatalog`
protocol (`WorkspaceCatalog` uses the public record shapes of CORE_API sections 2-3). Parts, assemblies and
BOMs are **declared** (unverified) until a subsystem registers a resolver with
`catalog.register(ItemType.PART, resolver)`; the impact report says so. Where-used is not implemented in
core, so every impact report carries "downstream usage was NOT searched" unless a `WhereUsedProvider` is
supplied. Coverage notes are included in CSV exports too.

## 7. GUI-facing API (for Claude B, issue #13)

Pattern is the same as `documents`: open a short-lived handle in the controller, build the service, call.

```python
from revisionworx import changes
from revisionworx.changes import query, impact, presentation, exchange

with Workspace.open(location) as ws:                     # reads: read-only handle
    svc = changes.service_for(ws)
    page = query.search(svc, query.ChangeFilter(open_only=True, text=q), sort="priority", limit=200)
    detail = presentation.change_detail(svc, change_id)  # record, history, actions, gates, impact
with Workspace.open(location, writable=True) as ws:      # writes: one short writable handle per action
    svc = changes.service_for(ws)
    ecr = svc.submit(change_id, comment, expected_rev=shown_rev, txid=action_txid)
```

| Call | Returns |
|---|---|
| `query.search(svc, ChangeFilter(...), sort=, descending=, limit=, offset=)` | `{"rows", "total", "has_more", "problems"}`; columns in `query.REGISTER_COLUMNS` |
| `query.where_changed(svc, item_id, open_only=)` | rows plus `action`, `baseline`, `implementation` |
| `query.counts_by_status(svc)` | `{"ecr": {status: n}, "eco": {...}, "unreadable": n}` (for #16's dashboard) |
| `presentation.change_detail(svc, id, read_only=)` | dict for a detail pane |
| `presentation.available_actions(svc, change, read_only=)` | `[{"action", "label", "enabled", "reason", "needs"}]` |
| `presentation.describe_error(exc)` | `{"ui": fix-input / reload / saved / read-only / new-attempt / reopen, "message"}` |
| `impact.impact_summary(svc, id)` / `impact_text` / `impact_csv` | report dict / text / CSV |
| `svc.create_request(...)`, `edit_request`, `add_item`, `remove_item`, `rebaseline`, `submit`, `begin_review`, `approve`, `reject`, `return_for_rework`, `cancel_request`, `set_dependencies` | updated `ChangeRequest` |
| `svc.create_order(...)`, `edit_order`, `add_action`, `complete_action`, `waive_action`, `record_implementation`, `mark_not_required`, `sign_off_order`, `start_work`, `mark_implemented`, `reopen`, `close_order`, `cancel_order` | updated `ChangeOrder` |
| `exchange.export_package` / `verify_package` / `write_export` / `import_requests_csv` | export dict / problems / path / created ECRs |

Every mutating call accepts `expected_rev` (the `rev` the user was shown) and `txid` (32 lowercase hex, one
per user action, reused only for a retry of that action). Use `presentation.describe_error` for all
exceptions; in particular `CommittedNotApplied` means **saved**. The GUI should not offer a way to type a
signer's name: the actor always comes from the local OS account.

`fixtures.sample_memory_service()` gives a deterministic in-memory service with changes in every main
state for view-model tests; `fixtures.create_sample_workspace(path)` builds a real disposable workspace.

## 8. Coordination and open items

| With | Item |
|---|---|
| Claude A (core, PR #7) | confirm the subsystem persistence interface above; no other core dependency |
| Claude B (GUI, #13/#14) | Changes view wiring (list, detail, dialogs) against section 7; no GUI file is edited here |
| Qwen (#16) | dashboard may call `counts_by_status`; change search here is change-domain only |
| DeepSeek / Claude C (#15/#18) | register `ItemType.PART/ASSEMBLY/BOM` resolvers and a `WhereUsedProvider` when available |
| QA / Integration | tests listed in section 9; fixtures for independent tests |

Known limitations: queries and cycle checks read all change records (fine for thousands, no index yet);
`history()` is O(events of one change); the policy cannot add workflow states; signoffs are not
authenticated (section 5); no SMB/NFS or multi-host claim; no Qt widgets.

## 9. Tests

`tests/test_changes_model.py` (validation, Unicode, strict loading, policy), `test_changes_workflow.py`
(every step from every state), `test_changes_service.py` (approvals, rounds, numbering and duplicates,
dependencies, implementations, conflicts, retries, audit tampering), `test_changes_workspace.py` (real
workspace, real revisions, read-only, conflicts across handles, lock break, interrupted commit, killed
process + recovery), `test_changes_reports.py` (impact, search, exports, CSV import, GUI helpers).

```sh
python -m pytest -q -ra tests/test_changes_*.py
```
