# SPDX-License-Identifier: GPL-3.0-or-later
"""The change-control service: every ECR/ECO operation, each as exactly ONE atomic store commit.

Guarantees (tested in tests/test_changes_*.py):

* **All or nothing.** An operation writes its change record(s), its audit event(s), number reservations and
  counters in one commit. A failure before the commit point writes nothing; after it, the core finishes the
  commit on the next writable open (``CommittedNotApplied`` means *saved*).
* **No lost updates.** Every write states the ``_rev`` it was based on. Pass ``expected_rev`` (the ``rev``
  the user was shown) and a change made by someone else in between raises ``ConflictError``.
* **Validated transitions.** Status changes only through the steps in :mod:`.workflow`; anything else raises
  :class:`TransitionError` before writing.
* **No silent rewriting.** Edits are allowed only in editable states and are recorded with old/new values;
  events can be written once and never replaced (see :mod:`.audit`).
* **Idempotent retries.** With a ``txid`` (32 hex, one per user action), retrying after an ambiguous failure
  returns the current state of the change instead of applying the action twice. Creations derive their ids
  from the txid so the retry finds the original record.
* **Never write what cannot be read back.** Every record is round-tripped through the strict loader first.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from ..workspace import ConflictError, current_actor, new_id, now_iso
from . import audit, workflow
from .catalog import ItemCatalog, ItemState
from .model import (ActionStatus, AffectedItem, Change, ChangeKind, ChangeOrder, ChangeRequest, Decision,
                    Dependency, DependencyError, DependencyType, DuplicateNumber, ECOStatus, ECRStatus,
                    Implementation, ImplementationStatus, ItemType, MalformedRecord, NotFound, OrderAction,
                    Priority, ProposedAction, ReplayRefused, SignoffError, Signoff, SignoffStage, StaleBaseline,
                    TransitionError, ValidationError, actor_key, actor_label, check_txid, clean_text,
                    field_diff, load_change, number_key)
from .store import Check, ChangeStore, Commit, Put
from .workflow import Policy

# Actions on a document that must end in a recorded implementation before an order can be implemented.
NEEDS_IMPLEMENTATION = frozenset({ProposedAction.REVISE, ProposedAction.CREATE, ProposedAction.OBSOLETE})
_EDITABLE_ECR_FIELDS = ("title", "reason", "description", "category", "priority", "project_ids",
                        "affected", "dependencies")
_EDITABLE_ECO_FIELDS = ("title", "description", "category", "priority")


def _derived_id(prefix: str, txid: str, salt: str) -> str:
    return f"{prefix}_{hashlib.sha256(f'revisionworx/changes/{salt}/v1:{txid}'.encode()).hexdigest()[:32]}"


_ACTION_WEIGHT = {ProposedAction.REFERENCE: 0, ProposedAction.REVIEW: 1}


def _merge_item(prior: AffectedItem | None, item: AffectedItem) -> AffectedItem:
    """Combine one item listed by several requests on one order without losing an obligation: a revise,
    create or obsolete wins over review/reference; two different obligations are a conflict."""
    if prior is None:
        return item
    if (prior.baseline_rev_id, number_key(prior.baseline_label)) != \
            (item.baseline_rev_id, number_key(item.baseline_label)):
        raise ValidationError(f"{item.label} has different baselines in the selected requests; "
                              "re-baseline them first")
    if prior.action is item.action:
        return prior
    if prior.action in NEEDS_IMPLEMENTATION and item.action in NEEDS_IMPLEMENTATION:
        raise ValidationError(f"{item.label} has conflicting actions in the selected requests "
                              f"({prior.action.value} and {item.action.value}); put them on separate orders "
                              "or edit the requests")
    rank = lambda a: 2 if a in NEEDS_IMPLEMENTATION else _ACTION_WEIGHT[a]  # noqa: E731
    return item if rank(item.action) > rank(prior.action) else prior


def reservation_id(number: str) -> str:
    return f"cnum_{hashlib.sha256(number_key(number).encode('utf-8')).hexdigest()[:32]}"


@dataclass
class _Pending:
    old: Change | None
    new: Change
    expect: int


class _Tx:
    """Collects one operation's writes. Changes touched twice keep their first expectation."""

    def __init__(self, svc: "ChangeService", op: str, txid: str | None):
        self.svc, self.op, self.txid = svc, op, txid
        self.changes: dict[str, _Pending] = {}
        self.order: list[str] = []
        self.extra: list[Put] = []
        self.checks: dict[tuple[str, str], Check] = {}

    def current(self, change: Change) -> Change:
        p = self.changes.get(change.id)
        return p.new if p else change

    def record(self, old: Change | None, new: Change, event: str, *, comment: str = "",
               details: dict | None = None, expected_rev: int | None = None) -> Change:
        svc = self.svc
        base = self.current(old) if old is not None else None
        n = new.event_count + 1
        ev = audit.make_event(new.id, new.kind.value, n, new.event_head, event, svc.actor(), svc.clock(),
                              from_status=base.status.value if base else None, to_status=new.status.value,
                              comment=comment, details={**(details or {}),
                                                        **({"txid": self.txid} if self.txid else {})})
        final = replace(new, event_count=n, event_head=ev["digest"], modified=ev["time"])
        data = final.to_dict()
        try:
            load_change(data)                            # never write what cannot be read back
        except MalformedRecord as exc:
            raise ValidationError(f"refusing to save an invalid record: {exc}") from exc
        if final.id in self.changes:
            self.changes[final.id].new = final
        else:
            expect = 0 if old is None else (old.rev if expected_rev is None else expected_rev)
            self.changes[final.id] = _Pending(old, final, expect)
            self.order.append(final.id)
        self.extra.append(Put("event", ev["id"], ev, expect_rev=0))
        return final

    def check(self, kind: str, rid: str, rev: int) -> None:
        self.checks.setdefault((kind, rid), Check(kind, rid, rev))

    def build(self) -> Commit:
        puts = [Put(p.new.kind.value, cid, p.new.to_dict(), expect_rev=p.expect)
                for cid, p in ((c, self.changes[c]) for c in self.order)]
        return Commit(self.op, puts + self.extra, list(self.checks.values()))


class ChangeService:
    """Engineering change control over a :class:`~.store.ChangeStore`.

    ``catalog`` verifies affected documents/projects (pass ``WorkspaceCatalog(ws)``); without it every item
    is *declared* (unverified). ``clock``, ``ids`` and ``actor`` are injectable for deterministic fixtures."""

    def __init__(self, store: ChangeStore, catalog: ItemCatalog | None = None, *,
                 clock: Callable[[], str] = now_iso, ids: Callable[[str], str] = new_id,
                 actor: Callable[[], dict] = current_actor):
        self.store, self.catalog = store, catalog
        self.clock, self.ids, self.actor = clock, ids, actor
        self.last_replayed = False

    # ================================================================== reading
    def policy(self) -> Policy:
        rec = self.store.get("config", "policy")
        if rec is None:
            return Policy()
        try:
            return Policy.from_dict({k: v for k, v in rec.items() if k not in ("event_count", "event_head")})
        except ValidationError as exc:
            raise MalformedRecord(f"stored change-control policy is invalid: {exc}") from exc

    def get(self, change_id: str) -> Change:
        kind = "ecr" if change_id.startswith("ecr_") else "eco" if change_id.startswith("eco_") else None
        rec = self.store.get(kind, change_id) if kind else None
        if rec is None:
            raise NotFound(f"no change with id {change_id!r}")
        return load_change(rec)

    def request(self, ecr_id: str) -> ChangeRequest:
        c = self.get(ecr_id)
        if not isinstance(c, ChangeRequest):
            raise NotFound(f"{ecr_id} is not a change request")
        return c

    def order(self, eco_id: str) -> ChangeOrder:
        c = self.get(eco_id)
        if not isinstance(c, ChangeOrder):
            raise NotFound(f"{eco_id} is not a change order")
        return c

    def scan(self, kinds: Iterable[str] = ("ecr", "eco")) -> tuple[list[Change], list[dict]]:
        """Every readable change plus a problem entry for each malformed one (never silently dropped)."""
        out: list[Change] = []
        problems: list[dict] = []
        for kind in kinds:
            for rid, rec in sorted(self.store.list(kind).items()):
                try:
                    out.append(load_change(rec))
                except MalformedRecord as exc:
                    problems.append({"problem": "malformed-change", "id": rid, "detail": str(exc)})
        return out, problems

    def find_by_number(self, number: str) -> Change | None:
        res = self.store.get("number", reservation_id(number))
        return self.get(res["change_id"]) if res else None

    def history(self, change_id: str) -> list[dict]:
        change = self.get(change_id)
        out = []
        for n in range(1, change.event_count + 1):
            ev = self.store.get("event", audit.event_id(change_id, n))
            if ev is None:
                raise MalformedRecord(f"{change.number}: audit event {n} is missing; run verify_history")
            out.append({k: v for k, v in ev.items() if k != "_rev"})
        return out

    def verify_history(self, change_id: str) -> list[dict]:
        change = self.get(change_id)

        def fetch(n: int) -> dict | None:
            ev = self.store.get("event", audit.event_id(change_id, n))
            return {k: v for k, v in ev.items() if k != "_rev"} if ev else None
        return audit.verify_chain(change_id, change.event_count, change.event_head, fetch)

    def describe(self, item: AffectedItem) -> ItemState | None:
        return self.catalog.describe(item) if self.catalog else None

    # ================================================================== plumbing
    def _run(self, op: str, txid: str | None, replay_id: str | None,
             build: Callable[[_Tx], Change]) -> Change:
        check_txid(txid)
        self.last_replayed = False
        if txid is not None:
            outcome = self.store.outcome(txid)
            if outcome["state"] == "fenced":
                raise ReplayRefused(f"attempt {txid} never took effect because its session lost the writer "
                                    "lock; nothing changed. Start again with a new txid")
            if outcome["state"] == "committed":
                if replay_id is None:
                    raise ReplayRefused(f"txid {txid} was already used")
                try:
                    change = self.get(replay_id)
                except NotFound:
                    raise ReplayRefused(f"txid {txid} was already used for a different change") from None
                if not any((ev.get("details") or {}).get("txid") == txid for ev in self.history(replay_id)):
                    raise ReplayRefused(f"txid {txid} was already used for a different change")
                self.last_replayed = True
                return change
        tx = _Tx(self, op, txid)
        result = build(tx)
        try:
            seq = self.store.commit(tx.build(), txid)
        except ConflictError as exc:
            if exc.kind == "changenum":
                raise DuplicateNumber("that change number was taken by another change; nothing was saved") \
                    from exc
            raise
        return replace(tx.changes[result.id].new, rev=seq) if result.id in tx.changes else result

    def _new_change_id(self, kind: str, txid: str | None, salt: str = "") -> str:
        return _derived_id(kind, txid, kind + salt) if txid else self.ids(kind)

    def _reserve(self, tx: _Tx, kind: str, number: str | None, change_id: str, policy: Policy,
                 counter: dict) -> str:
        """Allocate or validate the visible number and add its reservation (expect_rev=0) to the tx."""
        if number is not None and str(number).strip():
            num = clean_text(number, "number", required=True)
            if self.store.get("number", reservation_id(num)) is not None:
                raise DuplicateNumber(f"change number {num!r} is already used")
        else:
            n = counter["next"]
            for _ in range(100_000):
                candidate = policy.format_number(kind, n)
                n += 1
                if self.store.get("number", reservation_id(candidate)) is None and \
                        reservation_id(candidate) not in counter["taken"]:
                    break
            else:  # pragma: no cover - would need 100 000 manual numbers in the way
                raise DuplicateNumber("could not allocate a free change number")
            counter["next"] = n
            num = candidate
        rid = reservation_id(num)
        if rid in counter["taken"]:
            raise DuplicateNumber(f"change number {num!r} appears twice in this operation")
        counter["taken"].add(rid)
        tx.extra.append(Put("number", rid, {"number": num, "change_id": change_id, "kind": kind}, expect_rev=0))
        return num

    def _counter(self, kind: str) -> dict:
        rec = self.store.get("config", f"counter-{kind}")
        nxt = rec.get("next") if rec else 1
        if not isinstance(nxt, int) or nxt < 1:
            raise MalformedRecord(f"change number counter for {kind} is invalid")
        return {"next": nxt, "start": nxt, "rev": rec["_rev"] if rec else 0, "taken": set()}

    @staticmethod
    def _save_counter(tx: _Tx, kind: str, counter: dict) -> None:
        if counter["next"] != counter["start"]:
            tx.extra.append(Put("config", f"counter-{kind}", {"next": counter["next"]}, expect_rev=counter["rev"]))

    # ------------------------------------------------------------------ items and dependencies
    def _prepare_items(self, items: Iterable[Any]) -> tuple[AffectedItem, ...]:
        out: list[AffectedItem] = []
        for raw in items:
            item = raw if isinstance(raw, AffectedItem) else AffectedItem.create(**raw) \
                if isinstance(raw, dict) else None
            if item is None:
                raise ValidationError("affected items must be AffectedItem objects or dictionaries")
            state = self.describe(item)
            if state is not None:
                if not state.exists:
                    raise ValidationError(f"{item.item_type.value} {item.item_id or item.label!r} does not exist")
                if item.item_type is ItemType.DOCUMENT:
                    if item.baseline_rev_id is None:
                        item = replace(item, baseline_rev_id=state.latest_rev_id,
                                       baseline_label=state.latest_label)
                    else:
                        info = self.catalog.revision(item.baseline_rev_id)
                        if info is None or info.document_id != item.item_id:
                            raise ValidationError(f"revision {item.baseline_rev_id} is not a revision of "
                                                  f"document {item.label or item.item_id}")
                        item = replace(item, baseline_label=info.label)
                if not item.label:
                    item = replace(item, label=state.label)
            out.append(item)
        keys = [i.key for i in out]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValidationError(f"affected items listed twice: {', '.join(dupes)}")
        return tuple(out)

    def _prepare_deps(self, change_id: str, deps: Iterable[Any]) -> tuple[Dependency, ...]:
        out = []
        for raw in deps:
            if isinstance(raw, Dependency):
                dep = raw
            elif isinstance(raw, str):
                dep = Dependency(raw)
            elif isinstance(raw, dict):
                dep = Dependency.from_dict(raw)
            else:
                raise ValidationError("dependencies must be change ids, Dependency objects or dictionaries")
            if dep.change_id == change_id:
                raise DependencyError("a change cannot depend on itself")
            try:
                self.get(dep.change_id)
            except (NotFound, MalformedRecord) as exc:
                raise DependencyError(f"unknown dependency {dep.change_id}: {exc}") from None
            out.append(dep)
        if len({d.change_id for d in out}) != len(out):
            raise DependencyError("a dependency is listed twice")
        self._check_acyclic(change_id, out)
        return tuple(out)

    def _check_acyclic(self, change_id: str, deps: Iterable[Dependency]) -> None:
        graph: dict[str, list[str]] = {}
        changes, _ = self.scan()
        for c in changes:
            graph[c.id] = c.requires()
        graph[change_id] = [d.change_id for d in deps if d.dep_type is DependencyType.REQUIRES]
        # DFS from each new target back to change_id
        stack, seen = list(graph[change_id]), set()
        while stack:
            node = stack.pop()
            if node == change_id:
                raise DependencyError("that dependency would create a cycle")
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, []))

    def stale_items(self, change: Change, tx: _Tx | None = None) -> list[tuple[AffectedItem, ItemState]]:
        """Document items whose current latest revision is not the recorded baseline."""
        out = []
        for item in change.affected:
            if item.item_type is not ItemType.DOCUMENT or not item.baseline_rev_id:
                continue
            state = self.describe(item)
            if state is None or not state.exists:
                continue
            if tx is not None and state.check_kind and state.check_rev is not None:
                tx.check(state.check_kind, item.item_id, state.check_rev)   # pin the decision atomically
            if state.latest_rev_id != item.baseline_rev_id:
                out.append((item, state))
        return out

    def _require_fresh(self, change: Change, tx: _Tx, policy: Policy, step: str) -> None:
        stale = self.stale_items(change, tx)
        if stale and policy.block_stale_baselines:
            names = [f"{i.label} (baseline {i.baseline_label or i.baseline_rev_id}, now "
                     f"{s.latest_label or s.latest_rev_id})" for i, s in stale]
            raise StaleBaseline(f"cannot {step}: {len(stale)} affected document(s) were revised after "
                                f"{change.number} recorded its baseline: {'; '.join(names)}. Return it to draft "
                                "and re-baseline the items, then review again. Nothing was saved.",
                                [i.key for i, _ in stale])

    def _signoff(self, change: Change, stage: SignoffStage, decision: Decision, role: str, comment: str,
                 policy: Policy) -> Signoff:
        actor = self.actor()
        if decision is Decision.APPROVE and policy.require_distinct_approver and \
                actor_key(actor) == actor_key(change.created_by):
            raise SignoffError(f"policy requires someone other than the requester "
                               f"({actor_label(change.created_by)}) to approve")
        for s in change.signoffs:
            if s.stage is stage and s.round == change.round and actor_key(s.actor) == actor_key(actor) and \
                    s.content_digest == change.content_digest():
                raise SignoffError(f"{actor_label(actor)} has already signed off this "
                                   f"{stage.value.replace('-', ' ')}")
        return Signoff(self.ids("sgn"), stage, decision, actor, self.clock(), change.content_digest(),
                       clean_text(role, "role"), clean_text(comment, "comment", multiline=True,
                                                            required=decision is Decision.REJECT),
                       change.round)

    @staticmethod
    def gate(change: Change, stage: SignoffStage) -> dict:
        """Current approvals/rejections that count for a gate: this round, this exact content."""
        digest = change.content_digest()
        current = [s for s in change.signoffs
                   if s.stage is stage and s.round == change.round and s.content_digest == digest]
        approvers = {actor_key(s.actor) for s in current if s.decision is Decision.APPROVE}
        rejects = [s for s in current if s.decision is Decision.REJECT]
        return {"approvals": len(approvers), "rejections": rejects}

    # ================================================================== policy
    def save_policy(self, settings: dict, expected_rev: int | None = None, comment: str = "") -> Policy:
        new = Policy.from_dict(settings)
        rec = self.store.get("config", "policy")
        old = Policy() if rec is None else self.policy()
        count, head = (rec.get("event_count", 0), rec.get("event_head", "")) if rec else (0, "")
        ev = audit.make_event("policy", "config", count + 1, head, "policy-changed", self.actor(), self.clock(),
                              comment=clean_text(comment, "comment", multiline=True),
                              details={"old": old.to_dict(), "new": new.to_dict()})
        expect = (rec["_rev"] if rec else 0) if expected_rev is None else expected_rev
        self.store.commit(Commit("change.policy.save", [
            Put("config", "policy", {**new.to_dict(), "event_count": count + 1, "event_head": ev["digest"]},
                expect_rev=expect),
            Put("event", ev["id"], ev, expect_rev=0)]))
        return new

    def policy_history(self) -> list[dict]:
        rec = self.store.get("config", "policy") or {}
        return [self.store.get("event", audit.event_id("policy", n)) for n in range(1, rec.get("event_count", 0) + 1)]

    # ================================================================== change requests
    def _request_spec(self, policy: Policy, change_id: str, spec: dict) -> ChangeRequest:
        unknown = set(spec) - {"title", "reason", "description", "category", "priority", "affected",
                               "project_ids", "dependencies"}
        if unknown:
            raise ValidationError(f"unknown change request fields: {sorted(unknown)}")
        try:
            priority = Priority(spec.get("priority") or "normal")
        except ValueError:
            raise ValidationError(f"priority must be one of {', '.join(p.value for p in Priority)}") from None
        pids = tuple(spec.get("project_ids") or ())
        for pid in pids:
            st = self.describe(AffectedItem(ItemType.PROJECT, "", pid))
            if not isinstance(pid, str) or (st is not None and not st.exists):
                raise ValidationError(f"unknown project {pid!r}")
        now = self.clock()
        return ChangeRequest(
            id=change_id, number="?", title=clean_text(spec.get("title"), "title", required=True),
            status=ECRStatus.DRAFT, created=now, created_by=self.actor(), modified=now,
            description=clean_text(spec.get("description", ""), "description", multiline=True),
            priority=priority, category=policy.category(spec.get("category") or "design"), project_ids=pids,
            affected=self._prepare_items(spec.get("affected") or ()),
            dependencies=self._prepare_deps(change_id, spec.get("dependencies") or ()),
            reason=clean_text(spec.get("reason"), "reason", required=True, multiline=True))

    def create_request(self, title: str, reason: str, *, description: str = "", category: str = "design",
                       priority: str = "normal", affected: Iterable[Any] = (), project_ids: Iterable[str] = (),
                       dependencies: Iterable[Any] = (), number: str | None = None,
                       txid: str | None = None) -> ChangeRequest:
        """New ECR in Draft. ``reason`` (why the change is needed) is required. Document items without a
        baseline get the document's current latest revision as their baseline."""
        check_txid(txid)
        cid = self._new_change_id("ecr", txid)
        spec = dict(title=title, reason=reason, description=description, category=category, priority=priority,
                    affected=list(affected), project_ids=list(project_ids), dependencies=list(dependencies))

        def build(tx: _Tx) -> Change:
            policy = self.policy()
            counter = self._counter("ecr")
            ecr = self._request_spec(policy, cid, spec)
            ecr = replace(ecr, number=self._reserve(tx, "ecr", number, cid, policy, counter))
            self._save_counter(tx, "ecr", counter)
            return tx.record(None, ecr, "created", details={"number": ecr.number})
        return self._run("change.ecr.create", txid, cid, build)

    def create_requests(self, specs: list[dict], txid: str | None = None) -> list[ChangeRequest]:
        """Create several draft ECRs in ONE commit (bulk import): all are created or none."""
        check_txid(txid)
        if not specs:
            raise ValidationError("nothing to create")
        ids = [self._new_change_id("ecr", txid, f":{i}") for i in range(len(specs))]
        if txid is not None and self.store.outcome(txid)["state"] != "not-found":
            return [self._run("change.ecr.bulk", txid, ids[0], lambda tx: None)] + [self.get(i) for i in ids[1:]]
        tx = _Tx(self, "change.ecr.bulk", txid)
        policy, counter = self.policy(), self._counter("ecr")
        made = []
        for i, (cid, spec) in enumerate(zip(ids, specs)):
            spec = dict(spec)
            number = spec.pop("number", None)
            try:
                ecr = self._request_spec(policy, cid, spec)
                ecr = replace(ecr, number=self._reserve(tx, "ecr", number, cid, policy, counter))
            except (ValidationError, DuplicateNumber, DependencyError) as exc:
                raise type(exc)(f"item {i + 1}: {exc}") from exc
            made.append(tx.record(None, ecr, "created", details={"number": ecr.number, "bulk": True}))
        self._save_counter(tx, "ecr", counter)
        try:
            seq = self.store.commit(tx.build(), txid)
        except ConflictError as exc:
            if exc.kind == "changenum":
                raise DuplicateNumber("a change number was taken by another change; nothing was saved") from exc
            raise
        return [replace(tx.changes[c.id].new, rev=seq) for c in made]

    def edit_request(self, ecr_id: str, *, expected_rev: int | None = None, comment: str = "",
                     txid: str | None = None, **fields) -> ChangeRequest:
        """Change draft content. Every changed field is recorded with its old and new value."""
        bad = set(fields) - set(_EDITABLE_ECR_FIELDS)
        if bad:
            raise ValidationError(f"cannot edit {sorted(bad)} (editable: {', '.join(_EDITABLE_ECR_FIELDS)})")

        def build(tx: _Tx) -> Change:
            old = self.request(ecr_id)
            if old.status not in workflow.ECR_EDITABLE:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; only drafts can be "
                                      "edited (return it for rework first)")
            policy = self.policy()
            spec = {"title": old.title, "reason": old.reason, "description": old.description,
                    "category": old.category, "priority": old.priority.value,
                    "project_ids": list(old.project_ids), "affected": list(old.affected),
                    "dependencies": list(old.dependencies), **fields}
            fresh = self._request_spec(policy, old.id, spec)
            new = replace(old, title=fresh.title, reason=fresh.reason, description=fresh.description,
                          category=fresh.category, priority=fresh.priority, project_ids=fresh.project_ids,
                          affected=fresh.affected, dependencies=fresh.dependencies)
            diff = field_diff(old, new, _EDITABLE_ECR_FIELDS)
            if not diff:
                raise ValidationError("nothing changed")
            return tx.record(old, new, "edited", comment=clean_text(comment, "comment", multiline=True),
                             details={"changes": diff}, expected_rev=expected_rev)
        return self._run("change.ecr.edit", txid, ecr_id, build)

    def add_item(self, change_id: str, item: Any, *, expected_rev: int | None = None,
                 txid: str | None = None) -> ChangeRequest:
        ecr = self.request(change_id)
        return self.edit_request(change_id, affected=list(ecr.affected) + [item],
                                 expected_rev=ecr.rev if expected_rev is None else expected_rev, txid=txid)

    def remove_item(self, change_id: str, item_key: str, *, expected_rev: int | None = None,
                    txid: str | None = None) -> ChangeRequest:
        ecr = self.request(change_id)
        ecr.item(item_key)
        return self.edit_request(change_id, affected=[i for i in ecr.affected if i.key != item_key],
                                 expected_rev=ecr.rev if expected_rev is None else expected_rev, txid=txid)

    def rebaseline(self, change_id: str, item_key: str | None = None, *, expected_rev: int | None = None,
                   txid: str | None = None) -> ChangeRequest:
        """Move document baselines (one, or every stale one) to the document's current latest revision."""
        ecr = self.request(change_id)
        stale = {i.key for i, _ in self.stale_items(ecr)}
        targets = {item_key} if item_key else stale
        if item_key:
            ecr.item(item_key)
        if not targets & stale:
            raise ValidationError("no affected document is out of date")
        items = [replace(i, baseline_rev_id=None, baseline_label="") if i.key in targets else i for i in ecr.affected]
        return self.edit_request(change_id, affected=items, comment="re-baselined to current revisions",
                                 expected_rev=ecr.rev if expected_rev is None else expected_rev, txid=txid)

    def _ecr_step(self, step: str, ecr_id: str, expected_rev: int | None, txid: str | None,
                  mutate: Callable[[ChangeRequest, _Tx, Policy], tuple[ChangeRequest, str, dict]]) -> ChangeRequest:
        def build(tx: _Tx) -> Change:
            old = self.request(ecr_id)
            policy = self.policy()
            new, event, details = mutate(old, tx, policy)
            return tx.record(old, new, event, comment=details.pop("_comment", ""), details=details,
                             expected_rev=expected_rev)
        return self._run(f"change.ecr.{step}", txid, ecr_id, build)

    def submit(self, ecr_id: str, comment: str = "", *, expected_rev: int | None = None,
               txid: str | None = None) -> ChangeRequest:
        def mutate(old, tx, policy):
            target = workflow.ecr_target("submit", old.status, old.number)
            if policy.require_affected_items and not old.affected:
                raise ValidationError(f"{old.number} lists no affected items; add at least one before submitting")
            self._require_fresh(old, tx, policy, "submit")
            for dep in old.dependencies:
                self.get(dep.change_id)
            return (replace(old, status=target, round=old.round + 1, disposition=""), "submitted",
                    {"_comment": clean_text(comment, "comment", multiline=True), "round": old.round + 1})
        return self._ecr_step("submit", ecr_id, expected_rev, txid, mutate)

    def begin_review(self, ecr_id: str, comment: str = "", *, expected_rev: int | None = None,
                     txid: str | None = None) -> ChangeRequest:
        def mutate(old, tx, policy):
            target = workflow.ecr_target("begin_review", old.status, old.number)
            return replace(old, status=target), "review-started", {"_comment": clean_text(comment, "comment",
                                                                                           multiline=True)}
        return self._ecr_step("begin_review", ecr_id, expected_rev, txid, mutate)

    def return_for_rework(self, ecr_id: str, reason: str, *, expected_rev: int | None = None,
                          txid: str | None = None) -> ChangeRequest:
        why = clean_text(reason, "reason", required=True, multiline=True)

        def mutate(old, tx, policy):
            target = workflow.ecr_target("return_for_rework", old.status, old.number)
            return replace(old, status=target, disposition=why), "returned", {"_comment": why}
        return self._ecr_step("return_for_rework", ecr_id, expected_rev, txid, mutate)

    def approve(self, ecr_id: str, comment: str = "", *, role: str = "", expected_rev: int | None = None,
                txid: str | None = None) -> ChangeRequest:
        """Record an approval signoff. The request becomes Approved once the policy's number of distinct
        approvals for this review round and this exact content is reached (it stays In review before)."""
        def mutate(old, tx, policy):
            target = workflow.ecr_target("approve", old.status, old.number)
            self._require_fresh(old, tx, policy, "approve")
            so = self._signoff(old, SignoffStage.ECR_REVIEW, Decision.APPROVE, role, comment, policy)
            new = replace(old, signoffs=old.signoffs + (so,))
            got = self.gate(new, SignoffStage.ECR_REVIEW)["approvals"]
            done = got >= policy.ecr_approvals_required
            details = {"_comment": so.comment, "signoff": so.to_dict(), "approvals": got,
                       "required": policy.ecr_approvals_required}
            return (replace(new, status=target) if done else new), ("approved" if done else "signed-off"), details
        return self._ecr_step("approve", ecr_id, expected_rev, txid, mutate)

    def reject(self, ecr_id: str, reason: str, *, role: str = "", expected_rev: int | None = None,
               txid: str | None = None) -> ChangeRequest:
        def mutate(old, tx, policy):
            target = workflow.ecr_target("reject", old.status, old.number)
            so = self._signoff(old, SignoffStage.ECR_REVIEW, Decision.REJECT, role, reason, replace(
                policy, require_distinct_approver=False))
            return (replace(old, status=target, signoffs=old.signoffs + (so,), disposition=so.comment),
                    "rejected", {"_comment": so.comment, "signoff": so.to_dict()})
        return self._ecr_step("reject", ecr_id, expected_rev, txid, mutate)

    def cancel_request(self, ecr_id: str, reason: str, *, expected_rev: int | None = None,
                       txid: str | None = None) -> ChangeRequest:
        why = clean_text(reason, "reason", required=True, multiline=True)

        def mutate(old, tx, policy):
            if old.status is ECRStatus.CONVERTED:
                raise TransitionError(f"{old.number} is on a change order; cancel the change order instead")
            target = workflow.ecr_target("cancel", old.status, old.number)
            return replace(old, status=target, disposition=why), "cancelled", {"_comment": why}
        return self._ecr_step("cancel", ecr_id, expected_rev, txid, mutate)

    def set_dependencies(self, change_id: str, dependencies: Iterable[Any], *, expected_rev: int | None = None,
                         txid: str | None = None) -> Change:
        """Replace a change's dependency list (ECR: draft only; ECO: while work is open)."""
        if change_id.startswith("ecr_"):
            return self.edit_request(change_id, dependencies=list(dependencies), expected_rev=expected_rev,
                                     txid=txid)

        def build(tx: _Tx) -> Change:
            old = self.order(change_id)
            if old.status not in workflow.ECO_WORK_OPEN:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; its dependencies "
                                      "can no longer change")
            new = replace(old, dependencies=self._prepare_deps(old.id, dependencies))
            diff = field_diff(old, new, ("dependencies",))
            if not diff:
                raise ValidationError("nothing changed")
            return tx.record(old, new, "edited", details={"changes": diff}, expected_rev=expected_rev)
        return self._run("change.eco.dependencies", txid, change_id, build)

    # ================================================================== change orders
    def create_order(self, request_ids: Iterable[str], *, title: str = "", description: str = "",
                     actions: Iterable[Any] = (), number: str | None = None,
                     txid: str | None = None) -> ChangeOrder:
        """Turn one or more Approved requests into a change order in Planning. The requests become
        "On change order" in the same commit. Their affected items and requirements carry over."""
        check_txid(txid)
        rids = list(dict.fromkeys(request_ids))
        if not rids:
            raise ValidationError("a change order needs at least one approved change request")
        oid = self._new_change_id("eco", txid)

        def build(tx: _Tx) -> Change:
            policy = self.policy()
            ecrs = [self.request(r) for r in rids]
            items: dict[str, AffectedItem] = {}
            deps: dict[str, Dependency] = {}
            for ecr in ecrs:
                if ecr.status is not ECRStatus.APPROVED:
                    raise TransitionError(f"{ecr.number} is {workflow.STATUS_NAMES[ecr.status]}; only approved "
                                          "requests can go on a change order")
                self._require_fresh(ecr, tx, policy, "create the change order")
                for it in ecr.affected:
                    items[it.key] = _merge_item(items.get(it.key), it)
                for d in ecr.dependencies:
                    if d.change_id not in rids:
                        deps.setdefault(d.change_id, d)
            now = self.clock()
            ttl = title or (ecrs[0].title if len(ecrs) == 1 else
                            "Implements " + ", ".join(e.number for e in ecrs))
            eco = ChangeOrder(id=oid, number="?", title=clean_text(ttl[:200], "title", required=True),
                              status=ECOStatus.PLANNING, created=now, created_by=self.actor(), modified=now,
                              description=clean_text(description, "description", multiline=True),
                              priority=max((e.priority for e in ecrs), key=list(Priority).index),
                              category=ecrs[0].category,
                              project_ids=tuple(dict.fromkeys(p for e in ecrs for p in e.project_ids)),
                              affected=tuple(items.values()), dependencies=tuple(deps.values()),
                              request_ids=tuple(rids))
            eco = replace(eco, actions=tuple(self._make_action(eco, a) for a in actions))
            counter = self._counter("eco")
            eco = replace(eco, number=self._reserve(tx, "eco", number, oid, policy, counter))
            self._save_counter(tx, "eco", counter)
            self._check_acyclic(oid, eco.dependencies)
            for ecr in ecrs:
                tx.record(ecr, replace(ecr, status=workflow.ecr_target("convert", ecr.status), eco_id=oid),
                          "converted", details={"eco_id": oid, "eco_number": eco.number})
            return tx.record(None, eco, "created",
                             details={"number": eco.number, "request_ids": rids,
                                      "requests": [e.number for e in ecrs]})
        return self._run("change.eco.create", txid, oid, build)

    def _make_action(self, eco: ChangeOrder, raw: Any) -> OrderAction:
        spec = {"title": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else None
        if spec is None:
            raise ValidationError("an action must be a title or a dictionary")
        unknown = set(spec) - {"title", "required", "assignee", "item_key", "note"}
        if unknown:
            raise ValidationError(f"unknown action fields: {sorted(unknown)}")
        required = spec.get("required", True)
        if not isinstance(required, bool):
            raise ValidationError("'required' must be true or false")
        key = spec.get("item_key")
        if key is not None:
            eco.item(key)
        return OrderAction(self.ids("act"), clean_text(spec.get("title"), "title", required=True), required,
                           ActionStatus.OPEN, clean_text(spec.get("assignee", ""), "assignee"), key,
                           clean_text(spec.get("note", ""), "note", multiline=True))

    def _eco_step(self, op: str, eco_id: str, expected_rev: int | None, txid: str | None,
                  mutate: Callable[[ChangeOrder, _Tx, Policy], tuple[ChangeOrder, str, dict]]) -> ChangeOrder:
        def build(tx: _Tx) -> Change:
            old = self.order(eco_id)
            policy = self.policy()
            new, event, details = mutate(old, tx, policy)
            return tx.record(old, new, event, comment=details.pop("_comment", ""), details=details,
                             expected_rev=expected_rev)
        return self._run(f"change.eco.{op}", txid, eco_id, build)

    def edit_order(self, eco_id: str, *, expected_rev: int | None = None, comment: str = "",
                   txid: str | None = None, **fields) -> ChangeOrder:
        bad = set(fields) - set(_EDITABLE_ECO_FIELDS)
        if bad:
            raise ValidationError(f"cannot edit {sorted(bad)} (editable: {', '.join(_EDITABLE_ECO_FIELDS)})")

        def mutate(old, tx, policy):
            if old.status not in workflow.ECO_WORK_OPEN:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]} and can no longer "
                                      "be edited")
            changes: dict[str, Any] = {}
            if "title" in fields:
                changes["title"] = clean_text(fields["title"], "title", required=True)
            if "description" in fields:
                changes["description"] = clean_text(fields["description"], "description", multiline=True)
            if "category" in fields:
                changes["category"] = policy.category(fields["category"])
            if "priority" in fields:
                try:
                    changes["priority"] = Priority(fields["priority"])
                except ValueError:
                    raise ValidationError("unknown priority") from None
            new = replace(old, **changes)
            diff = field_diff(old, new, _EDITABLE_ECO_FIELDS)
            if not diff:
                raise ValidationError("nothing changed")
            return new, "edited", {"_comment": clean_text(comment, "comment", multiline=True), "changes": diff}
        return self._eco_step("edit", eco_id, expected_rev, txid, mutate)

    def add_action(self, eco_id: str, title: str, *, required: bool = True, assignee: str = "",
                   item_key: str | None = None, note: str = "", expected_rev: int | None = None,
                   txid: str | None = None) -> ChangeOrder:
        def mutate(old, tx, policy):
            if old.status not in workflow.ECO_WORK_OPEN:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; actions can be "
                                      "added only while planning or in progress")
            act = self._make_action(old, {"title": title, "required": required, "assignee": assignee,
                                          "item_key": item_key, "note": note})
            return replace(old, actions=old.actions + (act,)), "action-added", {"action": act.to_dict()}
        return self._eco_step("action.add", eco_id, expected_rev, txid, mutate)

    def _set_action(self, eco_id: str, action_id: str, status: ActionStatus, note: str, expected_rev, txid,
                    op: str) -> ChangeOrder:
        def mutate(old, tx, policy):
            if old.status is not ECOStatus.IN_PROGRESS:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; actions are "
                                      "completed while the order is in progress")
            act = old.action(action_id)
            if act.status is not ActionStatus.OPEN:
                raise TransitionError(f"action {act.title!r} is already {act.status.value}")
            text = clean_text(note, "note", multiline=True, required=status is ActionStatus.WAIVED)
            done = replace(act, status=status, note=text or act.note, completed_by=self.actor(),
                           completed_at=self.clock())
            new = replace(old, actions=tuple(done if a.id == action_id else a for a in old.actions))
            return new, op, {"_comment": text, "action_id": action_id, "title": act.title}
        return self._eco_step(f"action.{op}", eco_id, expected_rev, txid, mutate)

    def complete_action(self, eco_id: str, action_id: str, note: str = "", *, expected_rev: int | None = None,
                        txid: str | None = None) -> ChangeOrder:
        return self._set_action(eco_id, action_id, ActionStatus.DONE, note, expected_rev, txid, "action-done")

    def waive_action(self, eco_id: str, action_id: str, reason: str, *, expected_rev: int | None = None,
                     txid: str | None = None) -> ChangeOrder:
        return self._set_action(eco_id, action_id, ActionStatus.WAIVED, reason, expected_rev, txid,
                                "action-waived")

    def record_implementation(self, eco_id: str, item_key: str, *, rev_id: str | None = None,
                              rev_label: str = "", note: str = "", acknowledge_intervening: bool = False,
                              expected_rev: int | None = None, txid: str | None = None) -> ChangeOrder:
        """Record which revision implements an affected item.

        Documents: ``rev_id`` must be a revision of that document that descends from the baseline and is not
        the baseline itself. Revisions checked in between by other work are listed and must be acknowledged
        (``acknowledge_intervening=True``) - otherwise :class:`StaleBaseline`. Other item types (declared):
        give ``rev_label`` only; it is recorded as declared, not verified."""
        def mutate(old, tx, policy):
            if old.status is not ECOStatus.IN_PROGRESS:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; implementations "
                                      "are recorded while the order is in progress")
            item = old.item(item_key)
            intervening: tuple[str, ...] = ()
            label = clean_text(rev_label, "revision label", limit="label")
            verified = item.item_type is ItemType.DOCUMENT and self.catalog is not None
            if item.item_type is ItemType.DOCUMENT:
                if not rev_id:
                    raise ValidationError(f"{item.label}: give the id of the revision that implements the change")
                if self.catalog is not None:
                    info = self.catalog.revision(rev_id)
                    if info is None or info.document_id != item.item_id:
                        raise ValidationError(f"{rev_id} is not a revision of {item.label}")
                    if rev_id == item.baseline_rev_id:
                        raise ValidationError(f"{info.label} is the baseline revision itself; check in a new "
                                              "revision, or mark the item as not requiring a change")
                    chain = self.catalog.ancestors(rev_id)
                    if item.baseline_rev_id and item.baseline_rev_id not in chain:
                        raise StaleBaseline(f"revision {info.label} of {item.label} does not descend from the "
                                            f"baseline {item.baseline_label}; nothing was saved", [item.key])
                    if item.baseline_rev_id:
                        intervening = tuple(reversed(chain[1:chain.index(item.baseline_rev_id)]))
                    if intervening and not acknowledge_intervening:
                        labels = [self.catalog.revision(r).label for r in intervening]
                        raise StaleBaseline(f"{len(intervening)} other revision(s) of {item.label} were checked "
                                            f"in after the baseline ({', '.join(labels)}); confirm they are "
                                            "accounted for and record again with acknowledge_intervening",
                                            [item.key])
                    label = info.label
                    state = self.describe(item)
                    if state and state.check_kind and state.check_rev is not None:
                        tx.check(state.check_kind, item.item_id, state.check_rev)
            else:
                if rev_id:
                    raise ValidationError(f"{item.item_type.value} items are declared, not managed: give "
                                          "rev_label, not rev_id")
                if not label:
                    raise ValidationError("rev_label is required")
            imp = Implementation(item.key, ImplementationStatus.IMPLEMENTED, rev_id, label, intervening,
                                 clean_text(note, "note", multiline=True), self.actor(), self.clock())
            others = tuple(i for i in old.implementations if i.item_key != item.key)
            return (replace(old, implementations=others + (imp,)), "implementation-recorded",
                    {"implementation": imp.to_dict(), "verified": verified})
        return self._eco_step("implementation", eco_id, expected_rev, txid, mutate)

    def mark_not_required(self, eco_id: str, item_key: str, reason: str, *, expected_rev: int | None = None,
                          txid: str | None = None) -> ChangeOrder:
        why = clean_text(reason, "reason", required=True, multiline=True)

        def mutate(old, tx, policy):
            if old.status is not ECOStatus.IN_PROGRESS:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; items are "
                                      "dispositioned while the order is in progress")
            item = old.item(item_key)
            imp = Implementation(item.key, ImplementationStatus.NOT_REQUIRED, note=why, recorded_by=self.actor(),
                                 recorded_at=self.clock())
            others = tuple(i for i in old.implementations if i.item_key != item.key)
            return replace(old, implementations=others + (imp,)), "implementation-not-required", \
                {"_comment": why, "item_key": item.key}
        return self._eco_step("not_required", eco_id, expected_rev, txid, mutate)

    def sign_off_order(self, eco_id: str, decision: str, comment: str = "", *, role: str = "",
                       expected_rev: int | None = None, txid: str | None = None) -> ChangeOrder:
        """Release signoff while Planning, closure (verification) signoff while Implemented."""
        try:
            dec = Decision(decision)
        except ValueError:
            raise ValidationError("decision must be 'approve' or 'reject'") from None

        def mutate(old, tx, policy):
            stage = {ECOStatus.PLANNING: SignoffStage.ECO_RELEASE,
                     ECOStatus.IMPLEMENTED: SignoffStage.ECO_CLOSURE}.get(old.status)
            if stage is None:
                raise TransitionError(f"{old.number} is {workflow.STATUS_NAMES[old.status]}; signoffs are taken "
                                      "while planning (release) or when implemented (closure)")
            so = self._signoff(old, stage, dec, role, comment, policy)
            return replace(old, signoffs=old.signoffs + (so,)), "signed-off", \
                {"_comment": so.comment, "signoff": so.to_dict()}
        return self._eco_step("signoff", eco_id, expected_rev, txid, mutate)

    def _gate_ok(self, eco: ChangeOrder, stage: SignoffStage, required: int, what: str) -> None:
        g = self.gate(eco, stage)
        if g["rejections"]:
            r = g["rejections"][-1]
            raise SignoffError(f"cannot {what}: {actor_label(r.actor)} rejected it ({r.comment})")
        if g["approvals"] < required:
            raise SignoffError(f"cannot {what}: {g['approvals']} of {required} required approval(s) recorded")

    def start_work(self, eco_id: str, comment: str = "", *, expected_rev: int | None = None,
                   txid: str | None = None) -> ChangeOrder:
        def mutate(old, tx, policy):
            target = workflow.eco_target("start_work", old.status, old.number)
            self._gate_ok(old, SignoffStage.ECO_RELEASE, policy.eco_release_approvals_required, "start work")
            self._require_fresh(old, tx, policy, "start work")
            return replace(old, status=target), "work-started", {"_comment": clean_text(comment, "comment",
                                                                                        multiline=True)}
        return self._eco_step("start_work", eco_id, expected_rev, txid, mutate)

    def blockers(self, eco: ChangeOrder) -> list[str]:
        """Why the order cannot be marked implemented yet (empty = it can)."""
        out = []
        for a in eco.actions:
            if a.required and a.status is ActionStatus.OPEN:
                out.append(f"required action open: {a.title}")
        for it in eco.affected:
            if it.action in NEEDS_IMPLEMENTATION and \
                    eco.implementation(it.key).status is ImplementationStatus.PENDING:
                out.append(f"no implementation recorded for {it.label}")
        for dep in eco.requires():
            try:
                other = self.get(dep)
            except (NotFound, MalformedRecord):
                out.append(f"dependency {dep} cannot be read")
                continue
            if other.status not in (ECRStatus.CLOSED, ECOStatus.CLOSED):
                out.append(f"depends on {other.number}, which is {workflow.STATUS_NAMES[other.status]}")
        for rid in eco.request_ids:
            ecr = self.request(rid)
            for dep in ecr.requires():
                if dep in eco.request_ids or dep == eco.id or dep in eco.requires():
                    continue
                other = self.get(dep)
                if other.status not in (ECRStatus.CLOSED, ECOStatus.CLOSED):
                    out.append(f"{ecr.number} depends on {other.number}, which is "
                               f"{workflow.STATUS_NAMES[other.status]}")
        return out

    def mark_implemented(self, eco_id: str, comment: str = "", *, expected_rev: int | None = None,
                         txid: str | None = None) -> ChangeOrder:
        def mutate(old, tx, policy):
            target = workflow.eco_target("mark_implemented", old.status, old.number)
            problems = self.blockers(old)
            if problems:
                raise DependencyError(f"{old.number} is not complete: " + "; ".join(problems))
            for dep in old.requires():
                other = self.get(dep)
                tx.check(other.kind.value, other.id, other.rev)
            return replace(old, status=target, round=old.round + 1), "implemented", \
                {"_comment": clean_text(comment, "comment", multiline=True), "round": old.round + 1}
        return self._eco_step("implemented", eco_id, expected_rev, txid, mutate)

    def reopen(self, eco_id: str, reason: str, *, expected_rev: int | None = None,
               txid: str | None = None) -> ChangeOrder:
        why = clean_text(reason, "reason", required=True, multiline=True)

        def mutate(old, tx, policy):
            target = workflow.eco_target("reopen", old.status, old.number)
            return replace(old, status=target), "reopened", {"_comment": why}
        return self._eco_step("reopen", eco_id, expected_rev, txid, mutate)

    def close_order(self, eco_id: str, comment: str = "", *, expected_rev: int | None = None,
                    txid: str | None = None) -> ChangeOrder:
        """Close an Implemented order and, in the same commit, close every request it implements."""
        def build(tx: _Tx) -> Change:
            old = self.order(eco_id)
            policy = self.policy()
            target = workflow.eco_target("close", old.status, old.number)
            self._gate_ok(old, SignoffStage.ECO_CLOSURE, policy.eco_closure_approvals_required, "close")
            problems = self.blockers(old)
            if problems:
                raise DependencyError(f"{old.number} cannot close: " + "; ".join(problems))
            self._check_revision_states(old, policy)
            for rid in old.request_ids:
                ecr = self.request(rid)
                tx.record(ecr, replace(ecr, status=workflow.ecr_target("close", ecr.status, ecr.number)),
                          "closed", details={"eco_id": old.id, "eco_number": old.number})
            return tx.record(old, replace(old, status=target), "closed",
                             comment=clean_text(comment, "comment", multiline=True),
                             details={"requests_closed": list(old.request_ids)}, expected_rev=expected_rev)
        return self._run("change.eco.close", txid, eco_id, build)

    def _check_revision_states(self, eco: ChangeOrder, policy: Policy) -> None:
        role = policy.close_requires_revision_state
        if not role:
            return
        if self.catalog is None:
            raise TransitionError("policy requires released revisions, but revision states cannot be checked here")
        wanted = self.catalog.lifecycle_state(role)
        ok = {wanted, self.catalog.lifecycle_state("superseded") or "superseded"}
        if role == "approved":
            ok.add(self.catalog.lifecycle_state("released"))
        for imp in eco.implementations:
            if imp.status is ImplementationStatus.IMPLEMENTED and imp.rev_id:
                info = self.catalog.revision(imp.rev_id)
                if info is None or info.state not in ok:
                    raise TransitionError(f"policy requires implementing revisions to be {role}; revision "
                                          f"{imp.rev_label} is {info.state if info else 'missing'}")

    def cancel_order(self, eco_id: str, reason: str, *, expected_rev: int | None = None,
                     txid: str | None = None) -> ChangeOrder:
        """Cancel an order. Its requests go back to Approved (they can go on a new order or be cancelled)."""
        why = clean_text(reason, "reason", required=True, multiline=True)

        def build(tx: _Tx) -> Change:
            old = self.order(eco_id)
            target = workflow.eco_target("cancel", old.status, old.number)
            for rid in old.request_ids:
                ecr = self.request(rid)
                if ecr.eco_id == old.id:
                    tx.record(ecr, replace(ecr, status=workflow.ecr_target("unconvert", ecr.status, ecr.number),
                                           eco_id=None),
                              "order-cancelled", comment=why, details={"eco_id": old.id})
            return tx.record(old, replace(old, status=target, disposition=why), "cancelled", comment=why,
                             expected_rev=expected_rev)
        return self._run("change.eco.cancel", txid, eco_id, build)
