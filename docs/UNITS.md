# RevisionWorx units: serial-numbered units, their identifiers and configuration (ADR-025)

**Status:** design first, then implemented on `feature/unit-register` (local filesystem, single writer and team workspaces through the
same contract as change control). The desktop window has no Units screen yet; use `revisionworx unit` and `revisionworx link units`.
**Package:** `revisionworx/units/` (standard library only). **No core file is changed**: it persists through CORE_API §8, like
`changes/` and `bom/`.

## 1. What it is for

A **unit** is one physical thing someone made, sold or looks after: a hydraulic power unit with serial NH-26-0001, a pump fitted in it,
a pressure gauge used to test it. RevisionWorx already controls the drawings; this records which revisions describe each unit, from the
day it was built through decades of repairs and modifications, without copying a single file.

- **One identity per unit.** A random `unt_` id, given once. Serial numbers and tags are identifiers attached to it, so a unit
  survives re-serialization, re-tagging, a new owner or a new model designation.
- **References, never copies.** A unit points at revision ids. Five hundred units built to the same drawings share one *build
  standard*; a unit stores only where it differs.
- **One history.** Every change to a unit is an event, written once and chained by SHA-256. The unit's current record is computed from
  its events and checked against them. As-built, as-shipped and as-installed are the configuration at those milestones.
- **No silent substitution.** A unit's drawings are what its history says. A newer released revision is shown *beside* it, flagged,
  never in its place.

What it doesn't do yet (each is planned; see `docs/ARCHITECTURE.md` ADR-025): ECO effectivity and per-unit compliance, accepting
FieldWorx records into a unit's history, serial numbering rules, transfer of a unit between workspaces, a desktop screen.

## 2. Words

| Word | Means |
|---|---|
| unit | one physical item with its own record: a machine, a tracked component, an instrument, a tool (`class`) |
| identifier | a serial number, a tag or another code, with the organization that issued it |
| build standard | a named, fixed set of document revisions; many units can be built to one |
| unit difference | a revision this unit uses instead of its standard's, or a document that doesn't apply to it |
| component | something tracked at a *position* in a unit (a reference designator such as `M1`, or a find-number path such as `10/20`) |
| event | one thing that happened to a unit, recorded once |
| basis | how well a fact is known: `verified` (confirmed, with evidence, by someone accountable), `reported` (from a field report, not yet verified), `declared` (stated by the person recording it), `legacy` (taken from older records) |

## 3. Records

Record kinds (CORE_API §8): `unit`, `unitevt`, `unitkey`, `buildstd`. Every record carries `schema: 1`. Readers refuse a record whose
schema they don't know, or whose fields aren't exactly the ones listed (`MalformedUnitRecord`); nothing is used partially.

Text is NFC-normalized and stripped; single-line fields refuse control characters, multi-line ones (`notes`, `reason`, a note's `text`)
allow newline and tab. Times are UTC ISO 8601 as the core writes them. Dates (`effective`, milestones) are `YYYY-MM-DD`, or
`YYYY-MM-DDTHH:MM[:SS]Z`, or `""` for unknown.

### 3.1 `unit` (`unt_` + 32 hex): the current record, computed from the events

| Field | Value |
|---|---|
| `schema`, `id` | `1`, the unit's id |
| `class` | `product`, `component`, `instrument` or `tool` |
| `name` | short description, ≤ 200 |
| `model` | `{"part": "" or prt_…, "number": ≤ 100}`: the design it is an instance of. `number` is enough; `part` names a catalog part when there is one |
| `identifiers` | `[{"scheme", "issuer", "value", "active", "since"}]` in the order they were added; `since` is the event that added it |
| `status` | `planned`, `in-build`, `built`, `in-service`, `out-of-service`, `retired`, `scrapped` |
| `project` | `""` or a `prj_` id |
| `customer`, `site`, `location` | text, ≤ 200 each: who has it, where, and where on the site |
| `parent` | `null` or `{"unit": unt_…, "position"}`: the unit this one is fitted in |
| `standard` | `null` or `{"id": bst_…, "event", "basis"}` |
| `documents` | unit differences: `{doc_id: {"revision": rev_… or "" (doesn't apply), "event", "basis"}}` |
| `components` | `{position key: {"position", "part", "number", "unit", "serial", "lot", "since", "event", "basis"}}`; the key is the position casefolded |
| `milestones` | `{"built" \| "shipped" \| "installed" \| "commissioned" \| "decommissioned": {"date", "event"}}` |
| `notes` | ≤ 4000 |
| `events` | `{"count", "head"}`: how many events, and the digest of the last |
| `created`, `updated` | the recorded times of the first and last events |

### 3.2 `unitevt` (`uev_` + first 32 hex of `SHA-256("revisionworx/units/event-id/v1\0" + unit id + "\0" + n)`)

| Field | Value |
|---|---|
| `schema`, `id`, `unit`, `n` | `1`, the derived id, the unit, 1, 2, 3 … with no gaps |
| `type` | section 4 |
| `effective` | when it happened, as far as anyone knows (`""` = unknown) |
| `time`, `actor` | when it was recorded, and by whom (`user`, `host`, and in a team workspace `account` and `name`) |
| `basis` | section 2 |
| `evidence` | up to 50 `{"kind", "ref", "label"}`; `kind` is lowercase letters, digits and `-` (`fieldworx-event`, `inspection`, `work-order`, `document` …) |
| `reason` | why, ≤ 2000 (required to void, remove an identifier or change status back from retired) |
| `change` | `""` or the `eco_` id of the change order it carries out |
| `txid`, `input` | the action's transaction id and the digest of what it asked for (`""` without a txid), so a retry is recognised |
| `body` | depends on `type` |
| `prev`, `digest` | the digest of event *n − 1* (`""` for the first), and `SHA-256("revisionworx/units/event/v1\0" + canonical JSON of every other field)` |

Events are written with `expect_rev=0` (must not exist), so event *n* can be written once and two writers can't both write it.

### 3.3 `unitkey` (`uky_` + first 32 hex of `SHA-256("revisionworx/units/key/v1\0" + scheme + "\0" + norm(issuer) + "\0" + norm(value))`)

`{"schema", "scheme", "issuer", "value", "owner"}`: a claim that `owner` (a `unt_` or `bst_` id) holds this identifier. It is created
with `expect_rev=0` in the same entry as the event that adds the identifier, so two units can't both hold one. `norm` is NFKC,
case-folded, with runs of white space made one space and the ends stripped: `nh-26-0001` and `NH-26-0001` are the same serial.

| Scheme | Is | Claim when retired |
|---|---|---|
| `serial` | the maker's serial number; `issuer` is the maker (`""` = this organization) | kept: a serial names the machine for life |
| `tag` | an owner's tag or asset number; `issuer` is the owner | released: a plant tag is a position and moves to the replacement |
| `other` | any other code (a previous owner's number) | released |
| `fieldworx` | a FieldWorx 0.1 asset id (`ast_` + 32 hex), kept so old field records still attach | kept |
| `standard` | a build standard's number (owner `bst_…`) | never retired |

Removing an identifier entered by mistake releases its claim whatever the scheme; the history still shows it.

### 3.4 `buildstd` (`bst_` + 32 hex): written once, never changed

`{"schema", "id", "number", "title", "model", "documents": {doc_id: rev_id}, "based_on", "change", "notes", "created", "created_by",
"digest"}`. `number` is unique (a `standard` claim). Every revision must belong to its document. Revisions that aren't released or
superseded need an explicit choice (`allow_unreleased`), as an unreleased BOM does in OpenMeridian. `digest` covers every other field
(`revisionworx/units/standard/v1`), and `verify` recomputes it. A changed configuration is a new standard (`based_on` the old one).

## 4. Events

| `type` | `body` | Effect on the unit record |
|---|---|---|
| `unit.registered` | `class, name, model, identifiers: [{scheme, issuer, value}], status, project, customer, site, location, notes` | the starting record (event 1 only) |
| `unit.updated` | `{"fields": {...}}`: changed fields among `class, name, model, project, customer, site, location, notes` | those fields |
| `identifier.added` / `.retired` / `.removed` | `scheme, issuer, value` | added (or made active again), made inactive, taken out |
| `status.changed` | `from, to` | `status`. Nothing leaves `scrapped`; leaving `retired` needs a reason |
| `milestone` | `milestone` | `milestones[milestone] = {date: effective, event}` |
| `standard.set` | `standard` (`""` = none), `cleared: [doc ids]` | `standard`; the listed unit differences are dropped |
| `document.set` | `document, revision` (`""` = doesn't apply to this unit) | `documents[document]` |
| `document.reset` | `document` | the unit difference is dropped; the standard applies again |
| `component.installed` | `position, part, number, unit, serial, lot` | `components[position]`. The position must be empty |
| `component.removed` | `position, disposition, unit` (`disposition`: `scrapped`, `returned`, `repair`, `stock`, `reused`, `unknown`) | the position is emptied |
| `parent.set` | `parent, position` | written on a component unit in the same entry as its `component.*` event: `parent` |
| `note` | `text` | none: it is history |
| `void` | `events: [n, …]` | the listed events are ignored from then on; the reason is required |

**Voiding** corrects a record, it doesn't undo a physical change. It applies to `unit.updated`, `milestone`, `standard.set`,
`document.*`, `note` and `component.*`. Voiding a component event also voids the matching `parent.set` on the component unit in the same
entry, and is refused if a later event on either unit touches that position or that component (record the correction by hand then).
Identifiers are corrected with `identifier.removed`; a status is corrected by setting it again. A void can't be voided.

**A replacement** is `component.removed` and `component.installed` in one entry. A component that is a unit in its own right gets
`parent.set` in the same entry, written at the revision the writer read, so one component can't be fitted in two places, and a unit
can't be fitted inside itself or inside something fitted in it.

## 5. Documents that apply to a unit

For each document named by the unit's standard or by a unit difference:

1. a unit difference wins: its revision, or "doesn't apply";
2. otherwise the standard's revision;
3. the document's released revision is reported beside it, never instead of it.

Each row carries `source` (`unit` or `standard`), the event that set it, its basis and flags:

| Flag | When |
|---|---|
| `newer-released` | the document's released revision isn't this one and is later |
| `not-released` | this revision's state is neither released nor superseded |
| `obsolete` | this revision is in the lifecycle's obsolete state |
| `missing` | the document or revision can't be found (a damaged reference; nothing is shown in its place) |

A unit with no standard and no differences says its configuration isn't recorded.

**Reconstruction.** `after(n)` is the record as it stood after event *n* (voids recorded later don't apply). `on(date)` is the
record as known now for a calendar date: events whose effective date (or, when unknown, recorded date) is on or before it, with every
void applied. If an event recorded later says it happened earlier than one recorded before it, the answer lists it under `late`, because
their order is then a guess.

## 6. Actions and their guarantees

Every action is **one** `Workspace.commit`: the events, the unit records (each at the revision the action read, so a concurrent change
is a `ConflictError` and nothing is written), identifier claims (`expect_rev=0`, or a delete at their revision), and `RecordCheck`s on
records the decision depended on. Consequently every action is journaled, fenced, signed in a team workspace, replayed after a crash and
checked by `revisionworx verify`, exactly as the core guarantees for any entry.

| Action | Refused (nothing written) when |
|---|---|
| `register`, `register_many` (import) | an identifier is held by another unit or repeated in the batch; a project, standard, document or revision is unknown; a revision isn't of its document; a field is invalid |
| identifier add / retire / remove | it is held by another unit; the unit doesn't have it; a removal or a `standard` scheme without a reason |
| `set_status` | the status is unchanged; the unit is scrapped; leaving retired without a reason |
| `create_standard` | the number is taken; a revision isn't of its document; a revision isn't released or superseded without `allow_unreleased`; no documents |
| `set_standard` (one or many units) | the standard is unknown; a unit changed since it was read |
| `set_document`, `reset_document` | the revision isn't of the document; there is no unit difference to reset |
| `install` | the position is occupied (use `replace`); the component unit is fitted elsewhere, is this unit, or contains it |
| `remove` | the position is empty |
| `void` | an event can't be voided, is already void or doesn't exist; a later event depends on it (section 4) |

**Retries.** Every action takes a `txid` (32 lowercase hex, one per user action). If the outcome of an earlier attempt is unknown, the
same call with the same txid returns the result without writing again; a txid used for different input raises `ReplayRefused`, and so
does one whose attempt was fenced (start again with a new txid). New units take ids derived from the txid, so a retry finds them.

## 7. Integrity checks (`revisionworx unit verify`)

| Code | Finding |
|---|---|
| `missing-event`, `altered-event`, `misplaced-event`, `broken-link`, `head-mismatch`, `unrecorded-event` | the unit's history isn't intact |
| `malformed` | a unit, event, claim or standard doesn't match its schema |
| `record-mismatch` | the unit record isn't what its events compute |
| `claim-missing`, `claim-mismatch`, `claim-orphan` | an identifier without its claim, a claim naming another owner, a claim nobody holds |
| `parent-mismatch` | a component unit and the unit it is fitted in disagree |
| `dangling-reference` | a project, standard, document, revision or unit that doesn't exist |
| `standard-altered` | a build standard's digest doesn't match |

Problems are reported, never repaired: the journal holds every entry, and a person decides.

## 8. For other programs

`revisionworx link units [--project P] [--text T] [--detail]` and `revisionworx link unit REF`, documented in `docs/INTEGRATION.md`.
`REF` is a unit id, a FieldWorx `ast_` id, or an identifier's value. FieldWorx packs units from these; nothing reads unit records
directly.

## 9. Failure modes

| What happens | What follows |
|---|---|
| Two people register the same serial at once | One entry wins the claim; the other gets `DuplicateIdentifier` naming the unit that has it |
| Two people change one unit at once | The second commit's unit `expect_rev` fails: `ConflictError`, nothing written; reload and retry |
| One pump fitted in two machines at once | The pump unit's record is written at the revision each writer read: one conflicts |
| Crash before the commit point | Nothing written; retry with the same txid |
| Crash after it | `CommittedNotApplied`: it **is** saved; the next writable open completes it |
| Writer's lock broken by an administrator | `LockLostError`; nothing took effect (core fencing) |
| A revision a unit uses is released again, superseded or obsoleted | The unit still uses it; the flags say what changed |
| A document is moved or renamed | Nothing changes: units hold ids |
| An older RevisionWorx opens the workspace | It reads and replays these entries like any others, and can't write these kinds (CORE_API §8) |
| A modified program in a team workspace overwrites a unit event | The journal rules accept a signed put of any subsystem record; `unit verify` reports `altered-event`. A journal rule making these kinds write-once would need a new workspace format (ADR-024) and is planned |
| A unit record file altered on disk | Reads come from the journal (ADR-022); `verify` reports the file |

## 10. Not done, and costs

- Queries read every unit (`ws.list`): fine for thousands, no index yet. The local search index covers documents only.
- Batch actions are one journal entry each; an import of 1,000 units is one large entry.
- No desktop screen; ECO effectivity, field-record acceptance, numbering rules and transfers are later phases (ADR-025).
- Nothing here is verified between real computers, which RevisionWorx as a whole hasn't been (`docs/KNOWN_LIMITATIONS.md`).
