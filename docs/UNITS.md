# RevisionWorx units: serial-numbered units, their identifiers and configuration (ADR-025)

**Status:** implemented and tested (`tests/test_units_*.py`) on local disks, single-writer and team workspaces, through the same
contract as change control. The desktop window shows units read-only (View → Units…); they are recorded with `revisionworx unit`,
and other programs read and record through `revisionworx link` (`docs/INTEGRATION.md`).
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

What it doesn't do yet (planned; see `docs/ARCHITECTURE.md` ADR-025): transfer of a unit between workspaces, and merging two
records that turn out to be one machine.

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

Section 10 adds two kinds for engineering changes: `uniteff` (an order's effectivity decision) and `unitcmp` (one unit's compliance
with one order). Section 11 adds `unitseq`, a numbering that issues serials. Section 12 adds `unitdev` (a deviation or concession)
and `unitdvu` (whether it is in effect on one unit).

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
| `verified` | `events: [n, …]` | the facts those events recorded (documents, components, the standard) now have basis `verified`; written when a reported change is confirmed (section 10), so nothing moves twice |
| `void` | `events: [n, …]` | the listed events are ignored from then on; the reason is required |

**Voiding** corrects a record, it doesn't undo a physical change. It applies to `unit.updated`, `milestone`, `standard.set`,
`document.*`, `note`, `verified` and `component.*`. Voiding a component event also voids the matching `parent.set` on the component unit in the same
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
| `signature-invalid` | an event's field signature (section 8) is malformed, isn't one signature and one signer, has a basis other than `verified`, cites another field record, or doesn't hold with the key the signer's account enrolled for that phone |
| `signature-reused` | one signed field record is cited by more than one action (txid) |

Problems are reported, never repaired: the journal holds every entry, and a person decides.

## 8. For other programs

`revisionworx link units [--project P] [--text T] [--detail]` and `revisionworx link unit REF`, documented in `docs/INTEGRATION.md`.
`REF` is a unit id, a FieldWorx `ast_` id, or an identifier's value. FieldWorx packs units from these; nothing reads unit records
directly.

Other programs **record** through `link unit-register`, `unit-fit`, `unit-remove`, `unit-note`, `unit-milestone` and
`unit-change-install` (feature `unit-records`): the actions of section 6 and 10.2, one entry each, through the same writer as the
desktop window, so in a team workspace they are signed by the person logged in on that computer. Nothing writes unit records
directly. Three rules make a program's write safe to repeat and safe to decide on:

- **A txid is required.** The program derives it from its own record (a work order, a field event) and sends it with every
  attempt, so a lost answer is retried without recording twice (section 6, *Retries*).
- **Evidence and basis say where a fact came from.** A field report is `reported`, with `fieldworx-event:<event id>` as evidence;
  a unit a work order built is `declared`, with `work-order:<number>`. The actor is the person who recorded it here, never the
  device or the technician: who did the work is in the evidence's label.
- **`--if-events N` acts only on what was decided on.** When a person accepts a field record against the unit as they saw it,
  the action is refused (`Changed`, nothing written) if anything was recorded on the unit since. The count is compared when the
  action reads the unit, at the revision its commit insists on (`UnitService.expecting`).

**Signed field records** (feature `field-signatures`, ADR-028). A field record signed on a phone an administrator enrolled to an
account is accepted with `--field-signature DEVICE:EVENT:SIG`, `--basis verified` and `--evidence fieldworx-event:EVENT`. RevisionWorx
checks it first (the phone enrolled to exactly one active account, not revoked, the signature holding over the record with the key
enrolled for it), then the event carries two more evidence items:

| `kind` | `ref` | `label` |
|---|---|---|
| `field-signature` | `DEVICE:EVENT`: the phone and the record it signed | the signature, 128 hex digits |
| `field-signer` | the account the phone is enrolled to | the phone's name |

They are reserved: nothing records them except `link` after checking, so typing one as `--evidence`, here or through `link`, is
refused. Revoking the phone stops its signatures being accepted on anything new; `unit verify` still checks those accepted before.

The Director decided (2026-11-07) that OpenMeridian may register the units its own work orders build, and nothing else; FieldWorx
records only what someone in the office accepts. Proposals from phones are never applied by themselves.

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
| A phone that signed records is lost, or enrolled to the wrong person | An administrator revokes it (`revisionworx user device revoke`); its signatures are refused from then on, and what was accepted stands |
| Signature evidence written by a program that skips the checks | `verify` reports `signature-invalid` |

## 10. Engineering changes reaching units (phase 2)

The ECR/ECO workflow (`docs/CHANGE_CONTROL.md`) stays the only change process, and change orders are unchanged. Beside an order, the
unit register records an **effectivity** decision: which units the change reaches, and how. Each unit it reaches gets a **compliance**
record. A unit's documents move only through events in its own history that cite the change.

### 10.1 `uniteff` (`uef_` + first 32 hex of `SHA-256("revisionworx/units/effectivity/v1\0" + change id)`): written once

`{"schema", "id", "change", "kind", "standard", "documents": [{"document", "from", "to"}], "scope": {"rule", "value"}, "units": [ids],
"reason", "decided", "decided_by", "txid", "input", "digest"}`. Its id comes from the change order's, so an order has one decision, and
two people deciding at once can't both succeed.

| `kind` | Means | What deciding writes, in one entry |
|---|---|---|
| `production` | future units only | the decision, naming the build standard new units are built to; existing units get no record |
| `retrofit-required` | every unit in scope must be changed | a compliance record per unit, `pending` |
| `retrofit-optional` | offered to every unit in scope | a compliance record per unit, `offered` |
| `unit` | one unit's own modification | one compliance record, `pending` |
| `documentation` | drawings corrected, machines unchanged | `document.set` events moving each unit in scope from `from` to `to`, citing the change, "documentation correction, no physical change"; no compliance records |

`documents` is copied from the change order when it is decided: each document item implemented by a revision gives a row, `from` its
baseline and `to` its implementing revision. Deciding anything but `production` needs the order `implemented` or `closed`, so those
revisions are final, and at least one row. `production` needs a build standard. A cancelled order can't be decided. The rows cover
documents only: parts a retrofit fits or removes are recorded with `unit fit` and `unit remove` citing the change. `digest` covers every
other field (`revisionworx/units/effectivity/v1`), and `verify` recomputes it.

**Scope.** The *candidates* are units, not scrapped, whose applicable revision of a row's document is the row's `from`, or that have one
of the order's affected parts fitted. Rules: `candidates` (all of them), `standard` (candidates conforming to one standard),
`serial-range` (candidates with a serial from A to B, compared as people sort them, so `NH-9` comes before `NH-10`), `customer`
(candidates of one customer), and `units` (units named one by one, which needn't be candidates: a unit whose configuration isn't recorded
may still need the change). The rule and its value are kept for audit; `units` is the list it chose. A unit found later joins with
`add`, which writes its compliance record and leaves the decision as it was.

### 10.2 `unitcmp` (`ucp_` + first 32 hex of `SHA-256("revisionworx/units/compliance/v1\0" + change id + "\0" + unit id)`)

`{"schema", "id", "change", "unit", "required", "state", "steps": [{"state", "time", "effective", "actor", "basis", "reason", "events",
"txid", "input"}], "created", "updated"}`. One record per change and unit, created with `expect_rev=0`. Steps are only ever appended;
`state` is the last step's; `events` are the unit events the step wrote.

| From | To |
|---|---|
| `pending` (required) | `scheduled`, `installed`, `verified`, `deferred`, `not-applicable` |
| `offered` (optional) | `accepted`, `declined`, `installed`, `verified`, `not-applicable` |
| `accepted` | `scheduled`, `installed`, `verified`, `declined` |
| `scheduled` | `installed`, `verified`, `deferred`, `not-applicable`, and back to `pending` (required) or `accepted` (optional) |
| `deferred` | `pending`, `scheduled`, `installed`, `verified`, `not-applicable` |
| `declined` | `accepted` |
| `installed` | `verified`, `reversed` |
| `verified` | `reversed` |
| `not-applicable`, `reversed` | nothing: they are ends |

`deferred`, `declined`, `not-applicable` and `reversed` need a reason. `not-applicable` is an engineering decision that the change
doesn't concern this unit.

**Installing** (`installed`, or `verified` when the basis is `verified`) writes, in the same entry, a `document.set` for each row whose
`from` the unit has, citing the change, with the basis given (`reported` by default). Rows the unit already has at `to` are skipped. It
is refused if the unit has another revision of a row's document (someone records that by hand), and if no row applies (mark it not
applicable). Until it is verified the unit shows the new revisions flagged *reported*: showing the old drawing of a changed machine is
the more dangerous error.

**Verifying** an installed change appends a `verified` event to the unit naming the install's events, which makes their facts
`verified`. The documents don't move a second time.

**Reversing** writes `document.set` events back to `from` for rows the unit has at `to`, citing the change, and the state `reversed`.
Nothing is voided, because both changes happened.

**Out of compliance:** a unit with a required change (`retrofit-required` or `unit`) whose state is neither `verified` nor
`not-applicable`. A reversed required change counts.

### 10.3 Refusals, retries and checks

Every action is one entry: the decision or step, the unit events, the unit records at the revisions read, and a `RecordCheck` on the
change order. Refused, with nothing written: an unknown change; a change request rather than an order; a cancelled order; a second
decision; `production` without a standard; another kind before the order is implemented, or with no implemented document; a scope that
chooses nothing; a scrapped unit; a step the table doesn't allow; a step without a required reason; an install that doesn't apply. A
`txid` makes a retry write nothing, as for every unit action.

`unit verify` adds `effectivity-altered` (the digest doesn't match), `compliance-invalid` (steps that aren't allowed transitions, a
state that isn't the last step's, step events that aren't on the unit or don't cite the change, a required flag that disagrees with the
decision) and `dangling-reference` (a change, unit or standard that doesn't exist), and reports compliance with no decision.

### 10.4 Commands and other programs

`revisionworx unit change candidates|decide|status|add|set|install|verify|reverse` and `revisionworx unit compliance` (the units
section of `docs/USER_GUIDE.md`). `link unit` and `link units --detail` give each unit's `changes`: change id and number, title, kind, required,
state, the date of the last step, and the rows. FieldWorx shows them on the phone.

## 11. Serial numbering

A **numbering** issues serial numbers for new units, so two people registering units at once never get the same one.

`unitseq` (`usq_` + first 32 hex of `SHA-256("revisionworx/units/numbering/v1\0" + norm(name))`): `{"schema", "id", "name",
"pattern", "issuer", "start", "counters": {period: last number issued}, "created", "created_by"}`. Its id comes from its name, so
two numberings can't share one.

A **pattern** is text with exactly one `{seq}` or `{seq:N}` (the number, zero-padded to N digits, N 1 to 12) and any of `{yyyy}`,
`{yy}` and `{mm}`, filled from the unit's effective date, else its built date, else the day it is registered. Any other `{` or `}` is
refused, and so is a pattern that could give more than 100 characters. Numbers run separately for each *period*: each year when the
pattern has a year, each month when it has a month too, otherwise one run. `NH-{yy}-{seq:4}` gives NH-26-0001, NH-26-0002 … and
NH-27-0001 in 2027.

**Issuing.** A unit registered with `serial_from` gets the next number in its period: the counter moves and the serial is claimed in
the same entry as the registration, and the numbering record is written at the revision read, so two registrations at once can't take
the same number (one conflicts and is retried). A number whose serial is already claimed (a unit imported earlier, say) is skipped.
Numbers are never reused: a unit, once registered, keeps its serial. Changing a pattern is a new numbering.

## 12. Deviations and concessions

A **deviation** is permission, given before the work, to build or repair named units differently from their documents: a PMP-11
pump at P1 while the PMP-10 the BOM calls for is out of stock, say. A **concession** accepts named units already made that differ
from them: a weld undercut found after the load test passed. Neither changes a document. A change for every future unit is an
engineering change (section 10); a deviation is for the units it names.

### 12.1 `unitdev` (`udv_` + first 32 hex of `SHA-256("revisionworx/units/deviation-id/v1\0" + norm(number))`): written once

`{"schema", "id", "number", "kind", "title", "departure", "documents": [{"document", "revision"}], "units": [ids], "temporary",
"until", "reason", "approved", "approved_by", "txid", "input", "digest"}`. Its id comes from its number, so two can't share one.

| Field | Value |
|---|---|
| `number` | ≤ 64; by default the next `DEV-nnnn` (a deviation) or `CON-nnnn` (a concession) |
| `kind` | `deviation` or `concession` |
| `title`, `departure` | what it is (≤ 200), and what differs from the documents, in words (≤ 4000, multi-line) |
| `documents` | the revisions it departs from, each document once (may be empty) |
| `units` | the units it was approved for, each once, none scrapped |
| `temporary`, `until` | `true` when the unit is to conform again (the departure is then cleared on each unit), with an optional end date `YYYY-MM-DD`; a permanent one has no end date |
| `reason` | why it is acceptable (required) |
| `approved`, `approved_by` | when it was recorded and by whom (in a team workspace the signed-in account): recording it is approving it |

`digest` covers every other field (`revisionworx/units/deviation/v1`); `verify` recomputes it, and nothing is carried out from, or
shown from, a deviation whose digest doesn't match.

### 12.2 `unitdvu` (`udu_` + first 32 hex of `SHA-256("revisionworx/units/deviation-unit/v1\0" + deviation id + "\0" + unit id)`)

`{"schema", "id", "deviation", "unit", "state", "steps", "created", "updated"}`, with the same steps as compliance (section 10.2),
only ever appended. Approving writes one per unit, in effect, in the same entry; a unit added later gets its own (with a reason) and
the deviation stays as it was.

| From | To |
|---|---|
| `in-effect` | `cleared` (a temporary one: the unit conforms again; the step can name the unit's events that restored it), `not-used` (the unit was made or repaired without it; needs a reason) |
| `cleared`, `not-used` | nothing: they are ends |

**Past its end date:** a temporary one still in effect on a unit after its `until` date (the date recorded times use, UTC).
Approving or clearing writes no event on the unit: what was fitted is recorded the usual way (`unit fit`), and may cite the
deviation as evidence (`--evidence deviation:DEV-0007`).

### 12.3 Refusals, checks and other programs

Refused, with nothing written: a number already used; no unit, a scrapped or unknown unit; a document named twice or a revision
that doesn't exist; an end date on a permanent one; a missing title, departure or reason; clearing a permanent one; a step the table
doesn't allow; event numbers that aren't on the unit or were voided. A `txid` makes a retry write nothing; a retry of an approval
numbered automatically answers the number the first attempt took. Two approvals of one number at once: one conflicts.

`unit verify` adds `deviation-altered` (the digest or number doesn't match), `deviation-invalid` (a unit record whose steps aren't
allowed, a cleared permanent one, step events that aren't on the unit, a unit approved with no record of it) and
`dangling-reference`.

`revisionworx unit deviation new|list|show|add|clear|not-used|overdue` (`docs/USER_GUIDE.md`). `link unit` and `link units
--detail` give each unit's `deviations` (feature `unit-deviations`; `docs/INTEGRATION.md`), in effect first. The Units window
shows them and says when one is past its end date.

## 13. Handover packs

A **handover pack** is one unit's record as a folder anyone can open, with no RevisionWorx and no network: for a customer at
handover, a service company, or the file of a machine that is sold. `revisionworx unit pack NH-26-0001 FOLDER` (or the Units
window's **Handover pack…**) writes it into a new folder:

| File | What it is |
|---|---|
| `index.html` | The page to read and print: identity, where the unit is, milestones, the documents that apply with their revisions and a link to each PDF, what's fitted, approved departures (deviations and concessions), the engineering changes that reach it, and its history. Plain HTML and CSS, no scripts, nothing fetched from anywhere |
| `documents/<number> rev <label>.pdf` | The PDF of each revision that applies: the revision's own PDF (ADR-026), or the revision itself when it is a PDF. Never another revision's: one without a PDF is listed on the page as not included, with why |
| `unit.json` | The record, as `revisionworx link unit` answers it but without computer names, with `format` `revisionworx-unit-pack`, `version` 1 and the PDFs included or not |
| `SHA256SUMS` | The SHA-256 of every other file, in the format `sha256sum -c SHA256SUMS` reads |

- **Made from what a read-only handle sees.** Nothing is written to the workspace. The pack is put together beside the folder
  and moved into place at the end, so a failure leaves nothing; a folder that exists and isn't empty is refused.
- **`--on DATE`** makes the pack of the unit as known on that date (section 4), so the revisions are the ones that applied then.
- **The computer's name stays behind.** History entries keep who recorded each event (a quality record says who verified it),
  but not the computer they used.
- **`revisionworx unit check-pack FOLDER`** checks a pack anywhere, with no workspace. Every file listed has that SHA-256, nothing
  else is there, and the record is one this version can read. Exit 1 if not.
- **Not signed.** A pack shows that it is whole, not who made it, and its first page says so. Who vouches for a pack (a
  company key, or the issuing person's account) is the Director's decision (gate 4); a signature would be added beside
  `SHA256SUMS`.

## 14. Not done, and costs

- Queries read every unit (`ws.list`): fine for thousands, no index yet. The local search index covers documents only.
- Batch actions are one journal entry each; an import of 1,000 units is one large entry.
- Transfers between workspaces and merged identities are a later phase (ADR-025). Handover packs aren't signed yet (section 13).
- Approval of a deviation is recording it; there is no review workflow for deviations like change control's for ECOs.
- Effectivity rows cover documents only; parts a retrofit changes are recorded by hand, citing the change.
- Candidates read every unit's configuration: fine for thousands of units.
- Nothing here is verified between real computers, which RevisionWorx as a whole hasn't been (`docs/KNOWN_LIMITATIONS.md`).
