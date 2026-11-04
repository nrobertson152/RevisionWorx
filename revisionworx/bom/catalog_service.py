# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parts catalog and single-level BOM editing over a :class:`~revisionworx.bom.store.CatalogStore`.

Every public mutation is one store commit, so it is all-or-nothing and, with a ``txid``, safe to retry
after a crash or lost connection (same txid + same request = the original result, never a duplicate).

Rules enforced before anything is written (and re-checked by the store through ``expect_rev``):

* part numbers are unique case-insensitively; automatic numbers come from a :class:`NumberingRule`;
* a part referenced by any BOM line cannot be deleted; obsolete parts cannot gain new usages;
* a BOM edit is refused if it would add a validation error - cycle, unknown part, unit of another
  dimension, invalid quantity, duplicate find number or reference designator. Problems that already
  existed (for example in imported legacy data) do not block unrelated edits;
* a part's unit cannot change to another dimension while lines use it.

:class:`EditRejected` carries the validation issues that caused a refusal.
"""
from __future__ import annotations

import getpass
import socket
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .catalog import InMemoryCatalog, number_key
from .errors import BomError, StructureError
from .model import BomLine, Provenance, Structure
from .parts import NumberingRule, Part, PartKind, PartStatus, new_line_id, new_part_id
from .snapshot import BomSnapshot, SourceRef
from .store import STRUCT_ID, CatalogStore, Check, Commit, Delete, Put, StoreConflict, partnum_id
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, UnitRegistry, parse_quantity, to_quantity
from .validate import Issue, validate


class CatalogError(BomError):
    """A catalog operation was refused (unknown part, duplicate number, part in use...). Nothing written."""


class EditRejected(CatalogError):
    """A BOM edit would introduce validation errors. ``issues`` lists them. Nothing written."""

    def __init__(self, issues: Iterable[Issue]):
        self.issues = tuple(issues)
        super().__init__("BOM change refused: " + "; ".join(i.message for i in self.issues[:5]))


def _default_actor() -> dict:
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        user = "unknown"
    return {"user": user, "host": socket.gethostname()}


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def line_to_record(ln: BomLine) -> dict[str, Any]:
    return {"id": ln.line_id, "parent_id": ln.parent_id, "child_id": ln.child_id, "quantity": ln.quantity_text,
            "unit": ln.unit, "find_number": ln.find_number, "reference_designators": list(ln.reference_designators),
            "child_revision": ln.child_revision, "provenance": ln.provenance, "note": ln.note,
            "attributes": dict(ln.attributes)}


def line_from_record(rec: dict[str, Any]) -> BomLine:
    try:
        return BomLine(rec["parent_id"], rec["child_id"], parse_quantity(rec["quantity"]), unit=rec.get("unit", "EA"),
                       find_number=rec.get("find_number", ""),
                       reference_designators=tuple(rec.get("reference_designators", [])),
                       child_revision=rec.get("child_revision", ""), line_id=rec["id"],
                       provenance=rec.get("provenance", "declared"), note=rec.get("note", ""),
                       attributes=tuple(rec.get("attributes", {}).items()))
    except (KeyError, TypeError, AttributeError) as exc:
        raise StructureError(f"malformed BOM line record {rec.get('id', '?')}: {exc}") from None


class PartsCatalogService:
    def __init__(self, store: CatalogStore, *, actor: Callable[[], dict] = _default_actor,
                 clock: Callable[[], str] = _default_clock, units: UnitRegistry = DEFAULT_UNITS,
                 limits: Limits = DEFAULT_LIMITS):
        self.store = store
        self.actor = actor
        self.clock = clock
        self.units = units
        self.limits = limits

    # ================================================================== reads
    def _part_records(self) -> dict[str, dict]:
        return self.store.list("part")

    def parts(self) -> list[Part]:
        return sorted((Part.from_record(r) for r in self._part_records().values()), key=lambda p: (p.key, p.part_id))

    def part(self, part_id: str) -> Part | None:
        rec = self.store.get("part", part_id)
        return Part.from_record(rec) if rec else None

    def part_rev(self, part_id: str) -> int:
        rec = self.store.get("part", part_id)
        return rec["_rev"] if rec else 0

    def require_part(self, part_id: str) -> tuple[Part, int]:
        rec = self.store.get("part", part_id)
        if not rec:
            raise CatalogError(f"no part with id {part_id}")
        return Part.from_record(rec), rec["_rev"]

    def find_by_number(self, number: str) -> Part | None:
        claim = self.store.get("partnum", partnum_id(number))
        if claim:
            return self.part(claim["part_id"])
        return None

    def catalog(self) -> InMemoryCatalog:
        """A read-only :class:`CatalogReader` snapshot of the catalog."""
        return InMemoryCatalog(p.info() for p in self.parts())

    def line_records(self) -> dict[str, dict]:
        return self.store.list("bomline")

    def line_rev(self, line_id: str) -> int:
        rec = self.store.get("bomline", line_id)
        return rec["_rev"] if rec else 0

    def structure(self) -> Structure:
        lines = [line_from_record(r) for r in self.line_records().values()]
        return Structure.build(lines, [p.info() for p in self.parts()])

    def bom(self, parent_id: str) -> tuple[BomLine, ...]:
        return self.structure().children(parent_id)

    def snapshot(self, root_id: str, label: str = "") -> BomSnapshot:
        st = self.structure().restricted_to(root_id)
        if root_id not in st.item_ids:
            st = st.with_parts(self.require_part(root_id)[0].info())
        part = self.part(root_id)
        return BomSnapshot(st, root_id, label or (part.revision if part else ""),
                           SourceRef(kind="catalog", revision_label=label or (part.revision if part else "")))

    def rules(self) -> list[NumberingRule]:
        return sorted((NumberingRule.from_record(r) for r in self.store.list("partrule").values()),
                      key=lambda r: r.rule_id)

    def structure_version(self) -> int:
        rec = self.store.get("bomstruct", STRUCT_ID)
        return rec["_rev"] if rec else 0

    # ================================================================== helpers
    def _stamp(self, data: dict, created: dict | None = None) -> dict:
        who, when = self.actor(), self.clock()
        out = dict(data)
        if created:
            out["created"], out["created_by"] = created.get("created", when), created.get("created_by", who)
        else:
            out["created"], out["created_by"] = when, who
        out["modified"], out["modified_by"] = when, who
        return out

    def _struct_bump(self, version: int | None = None) -> Put:
        """Bump the structure version. Pass the version read *before* the structure that was validated, so a
        structure change committed in between makes this commit fail instead of slipping through."""
        rev = self.structure_version() if version is None else version
        return Put("bomstruct", STRUCT_ID, {"id": STRUCT_ID, "version": rev + 1}, expect_rev=rev)

    def _state(self) -> tuple[int, Structure, dict[str, Part], dict[str, int]]:
        """``(structure version, structure, parts by id, part revisions)`` for validating an edit.

        The version is read first: if anything changes the structure afterwards, the commit that pins this
        version is refused (a stale but safe conflict), never accepted against data it did not validate."""
        version = self.structure_version()
        recs = self._part_records()
        parts = {pid: Part.from_record(r) for pid, r in recs.items()}
        revs = {pid: r["_rev"] for pid, r in recs.items()}
        lines = [line_from_record(r) for r in self.line_records().values()]
        return version, Structure.build(lines, [p.info() for p in parts.values()]), parts, revs

    def _matches_rule(self, number: str) -> bool:
        return any(r.matches(number) for r in self.rules())

    def _check_unit(self, unit: str) -> str:
        if not self.units.known(unit):
            raise CatalogError(f"unknown unit {unit!r}; known units: {', '.join(self.units.codes())}")
        return self.units.canonical(unit)

    # ================================================================== numbering
    def save_rule(self, rule: NumberingRule, *, txid: str | None = None) -> NumberingRule:
        current = self.store.get("partrule", rule.rule_id)
        self.store.commit(Commit("bom.rule.save", [Put("partrule", rule.rule_id, rule.to_record(),
                                                       expect_rev=current["_rev"] if current else 0)]), txid)
        return rule

    def _rule(self, rule_id: str) -> NumberingRule:
        rec = self.store.get("partrule", rule_id)
        if not rec:
            raise CatalogError(f"no numbering rule {rule_id!r}")
        return NumberingRule.from_record(rec)

    def preview_number(self, rule_id: str) -> str:
        """The number :meth:`create_part` would issue next with this rule (not reserved)."""
        rule = self._rule(rule_id)
        seq = self.store.get("partseq", rule_id)
        return rule.next_free(seq["last"] if seq else rule.start - 1, {p.key for p in self.parts()})[1]

    # ================================================================== parts
    def create_part(self, number: str = "", *, rule_id: str = "", description: str = "", unit: str = "EA",
                    kind: str = PartKind.PART.value, revision: str = "", manufacturers: Iterable = (),
                    vendors: Iterable = (), documents: Iterable = (), attributes: Any = (),
                    part_id: str = "", txid: str | None = None) -> Part:
        """Create a part. Give ``number`` (manual or legacy) or ``rule_id`` (automatic), not both."""
        if bool(number) == bool(rule_id):
            raise CatalogError("give either a part number or a numbering rule")
        ops: list = []
        manual = False
        if rule_id:
            rule = self._rule(rule_id)
            seq = self.store.get("partseq", rule_id)
            last = seq["last"] if seq else rule.start - 1
            n, number = rule.next_free(last, {p.key for p in self.parts()})
            ops.append(Put("partseq", rule_id, {"id": rule_id, "last": n}, expect_rev=seq["_rev"] if seq else 0))
        else:
            manual = not self._matches_rule(number) if self.rules() else False
        part = Part(part_id or new_part_id(), number, description=description, unit=self._check_unit(unit), kind=kind,
                    revision=revision, manufacturers=tuple(manufacturers), vendors=tuple(vendors),
                    documents=tuple(documents), attributes=attributes, manual_number=manual)
        existing = self.find_by_number(part.number)
        if existing is not None:
            raise CatalogError(f"part number {part.number} is already used by {existing.part_id}")
        ops += [Put("part", part.part_id, self._stamp(part.to_record()), expect_rev=0),
                Put("partnum", partnum_id(part.number), {"id": partnum_id(part.number), "number": part.number,
                                                          "part_id": part.part_id}, expect_rev=0)]
        self.store.commit(Commit("bom.part.create", ops), txid)
        return part

    def update_part(self, part_id: str, expect_rev: int, *, txid: str | None = None, **changes: Any) -> Part:
        """Change part fields. Renumbering re-claims the number atomically; the id never changes."""
        version, st, parts, revs = self._state()
        if part_id not in parts:
            raise CatalogError(f"no part with id {part_id}")
        old, rev = parts[part_id], revs[part_id]
        if rev != expect_rev:
            raise StoreConflict("part", part_id, expect_rev, rev)
        if "unit" in changes:
            changes["unit"] = self._check_unit(changes["unit"])
        for name in ("manufacturers", "vendors", "documents"):
            if name in changes:
                changes[name] = tuple(changes[name])
        new = old.with_changes(**changes)
        ops: list = [Put("part", part_id, self._stamp(new.to_record(), created=self.store.get("part", part_id)),
                         expect_rev=rev)]
        if new.key != old.key:
            other = self.find_by_number(new.number)
            if other is not None and other.part_id != part_id:
                raise CatalogError(f"part number {new.number} is already used by {other.part_id}")
            old_claim = self.store.get("partnum", partnum_id(old.number))
            ops.append(Delete("partnum", partnum_id(old.number), old_claim["_rev"] if old_claim else None))
            ops.append(Put("partnum", partnum_id(new.number), {"id": partnum_id(new.number), "number": new.number,
                                                                "part_id": part_id}, expect_rev=0))
            if self.rules() and "manual_number" not in changes:
                new = new.with_changes(manual_number=not self._matches_rule(new.number))
                ops[0] = Put("part", part_id, self._stamp(new.to_record(), created=self.store.get("part", part_id)),
                             expect_rev=rev)
        elif new.number != old.number:   # case-only change: same claim, update the stored spelling
            claim = self.store.get("partnum", partnum_id(new.number))
            ops.append(Put("partnum", partnum_id(new.number), {"id": partnum_id(new.number), "number": new.number,
                                                                "part_id": part_id},
                           expect_rev=claim["_rev"] if claim else 0))
        if new.unit != old.unit:
            if self.units.get(new.unit).dimension != self.units.get(old.unit).dimension and (
                    st.usages(part_id) or st.children(part_id)):
                raise CatalogError(f"{old.number} is used in BOMs; its unit cannot change from {old.unit} "
                                   f"({self.units.get(old.unit).dimension}) to {new.unit} "
                                   f"({self.units.get(new.unit).dimension})")
            ops.append(self._struct_bump(version))  # quantities elsewhere are interpreted in this unit
        self.store.commit(Commit("bom.part.update", ops), txid)
        return new

    def set_status(self, part_id: str, status: str, expect_rev: int, *, txid: str | None = None) -> Part:
        return self.update_part(part_id, expect_rev, status=PartStatus(status).value, txid=txid)

    def delete_part(self, part_id: str, expect_rev: int, *, txid: str | None = None) -> None:
        version, st, parts, _ = self._state()
        if part_id not in parts:
            raise CatalogError(f"no part with id {part_id}")
        part = parts[part_id]
        users = st.usages(part_id)
        if users or st.children(part_id):
            where = ", ".join(sorted({st.info(ln.parent_id).display_number for ln in users})) or "its own BOM"
            raise CatalogError(f"{part.number} is used in BOMs ({where}); remove those lines first or mark it "
                               "obsolete")
        claim = self.store.get("partnum", partnum_id(part.number))
        self.store.commit(Commit("bom.part.delete", [
            Delete("part", part_id, expect_rev), Delete("partnum", partnum_id(part.number),
                                                        claim["_rev"] if claim else None),
            self._struct_bump(version)]), txid)

    # ================================================================== single-level BOM editing
    def _new_errors(self, before: Structure, after: Structure) -> list[Issue]:
        old = {(i.code, i.parent_id, i.child_id, i.message) for i in validate(before, units=self.units,
                                                                               limits=self.limits).errors}
        return [i for i in validate(after, units=self.units, limits=self.limits).errors
                if (i.code, i.parent_id, i.child_id, i.message) not in old]

    def _prepare_line(self, parts: dict[str, Part], parent_id: str, child_id: str, quantity: Any, unit: str | None,
                      **kw: Any) -> BomLine:
        for pid in (parent_id, child_id):
            if pid not in parts:
                raise CatalogError(f"no part with id {pid}")
        child = parts[child_id]
        if child.obsolete:
            raise CatalogError(f"{child.number} is obsolete and cannot be added to a BOM")
        q = to_quantity(quantity)
        return BomLine(parent_id, child_id, q, unit=self._check_unit(unit or child.unit), **kw)

    def _commit_lines(self, op: str, before: Structure, after: Structure, line_ops: list, part_ids: Iterable[str],
                      txid: str | None, version: int, revs: dict[str, int]) -> None:
        errors = self._new_errors(before, after)
        if errors:
            raise EditRejected(errors)
        checks = [Check("part", pid, revs[pid]) for pid in sorted(set(part_ids))]
        self.store.commit(Commit(op, line_ops + checks + [self._struct_bump(version)]), txid)

    def add_line(self, parent_id: str, child_id: str, quantity: Any, unit: str | None = None, *, find_number: str = "",
                 reference_designators: Any = (), child_revision: str = "", note: str = "", attributes: Any = (),
                 provenance: str = Provenance.DECLARED.value, txid: str | None = None) -> BomLine:
        version, before, parts, revs = self._state()
        ln = self._prepare_line(parts, parent_id, child_id, quantity, unit, find_number=find_number,
                                reference_designators=reference_designators, child_revision=child_revision,
                                note=note, attributes=attributes, provenance=provenance, line_id=new_line_id())
        after = before.with_lines(ln)
        self._commit_lines("bom.line.add", before, after, [Put("bomline", ln.line_id, line_to_record(ln), 0)],
                           (parent_id, child_id), txid, version, revs)
        return ln

    def update_line(self, line_id: str, expect_rev: int, *, txid: str | None = None, **changes: Any) -> BomLine:
        rec = self.store.get("bomline", line_id)
        if not rec:
            raise CatalogError(f"no BOM line {line_id}")
        old = line_from_record(rec)
        if {"parent_id", "child_id", "line_id"} & set(changes):
            raise CatalogError("a line's parent, child and id cannot change; remove it and add a new line")
        if "quantity" in changes:
            changes["quantity"] = to_quantity(changes["quantity"])
        if "unit" in changes:
            changes["unit"] = self._check_unit(changes["unit"])
        new = old.with_changes(**changes)
        version, before, _, revs = self._state()
        if old not in before.lines:
            raise StoreConflict("bomline", line_id, expect_rev, self.line_rev(line_id))
        after = before.replacing_line(old, new)
        self._commit_lines("bom.line.update", before, after,
                           [Put("bomline", line_id, line_to_record(new), expect_rev)], (old.parent_id, old.child_id),
                           txid, version, revs)
        return new

    def remove_line(self, line_id: str, expect_rev: int, *, txid: str | None = None) -> None:
        rec = self.store.get("bomline", line_id)
        if not rec:
            raise CatalogError(f"no BOM line {line_id}")
        self.store.commit(Commit("bom.line.remove", [Delete("bomline", line_id, expect_rev), self._struct_bump()]),
                          txid)

    def replace_bom(self, parent_id: str, lines: Iterable[BomLine], *, expect_version: int | None = None,
                    txid: str | None = None) -> tuple[BomLine, ...]:
        """Replace the whole single-level BOM of ``parent_id`` (e.g. from a CAD export) in one commit.
        Lines without a ``bln_`` id get one. ``expect_version`` pins the structure version read by the caller."""
        version, before, parts, revs = self._state()
        if expect_version is not None and expect_version != version:
            raise StoreConflict("bomstruct", STRUCT_ID, expect_version, version)
        if parent_id not in parts:
            raise CatalogError(f"no part with id {parent_id}")
        new = []
        for ln in lines:
            if ln.parent_id != parent_id:
                raise CatalogError(f"line for {ln.child_id} belongs to {ln.parent_id}, not {parent_id}")
            child = parts.get(ln.child_id)
            if child is None:
                raise CatalogError(f"no part with id {ln.child_id}")
            if child.obsolete and not any(o.child_id == ln.child_id for o in before.children(parent_id)):
                raise CatalogError(f"{child.number} is obsolete and cannot be added to a BOM")
            new.append(ln if ln.line_id.startswith("bln_") else ln.with_changes(line_id=new_line_id()))
        after = before.with_single_level(parent_id, new)
        old_recs = {r["id"]: r for r in self.line_records().values() if r["parent_id"] == parent_id}
        ops: list = [Delete("bomline", lid, r["_rev"]) for lid, r in sorted(old_recs.items()) if
                     lid not in {ln.line_id for ln in new}]
        for ln in new:
            ops.append(Put("bomline", ln.line_id, line_to_record(ln),
                           old_recs[ln.line_id]["_rev"] if ln.line_id in old_recs else 0))
        self._commit_lines("bom.line.replace", before, after, ops, [parent_id] + [ln.child_id for ln in new], txid,
                           version, revs)
        return tuple(sorted(new, key=BomLine.sort_key))

    # ================================================================== import
    def import_part_rows(self, rows: Iterable[tuple[int, Part | None, dict]], *, update_existing: bool = False,
                         txid: str | None = None) -> dict[str, int]:
        """Create or update many parts in **one** commit (see :func:`revisionworx.bom.parts_csv.import_parts`).
        Rows are ``(row number, Part with id or None, fields)``. Returns counts."""
        version, st, existing, revs = self._state()
        by_number = {p.key: p for p in existing.values()}
        ops: list = []
        problems: list[str] = []
        created = updated = unchanged = 0
        claimed: set[str] = set()
        ids_seen: dict[str, int] = {}
        for row, part, fields in rows:
            if part is not None:
                if part.part_id in ids_seen:
                    problems.append(f"row {row}: part id {part.part_id} repeats row {ids_seen[part.part_id]}")
                    continue
                ids_seen[part.part_id] = row
            fields = dict(fields)
            try:
                fields["unit"] = self._check_unit(fields.get("unit", "EA"))
            except CatalogError as exc:
                problems.append(f"row {row}: {exc}")
                continue
            if part is not None:
                part = part.with_changes(unit=fields["unit"])
            target = existing.get(part.part_id) if part is not None else None
            if target is None and part is None:
                target = by_number.get(number_key(fields["number"]))
            if target is None:
                new = part if part is not None else Part(new_part_id(), **fields)
                if new.key in by_number or new.key in claimed:
                    problems.append(f"row {row}: part number {new.number} is already used")
                    continue
                claimed.add(new.key)
                ops += [Put("part", new.part_id, self._stamp(new.to_record()), 0),
                        Put("partnum", partnum_id(new.number), {"id": partnum_id(new.number), "number": new.number,
                                                                "part_id": new.part_id}, 0)]
                created += 1
                continue
            new = target.with_changes(**fields)
            if new == target:
                unchanged += 1
                continue
            if not update_existing:
                problems.append(f"row {row}: {target.number} already exists with different data "
                                "(allow updates to change it)")
                continue
            if new.key != target.key:
                problems.append(f"row {row}: renumbering {target.number} to {new.number} is not done by import")
                continue
            if new.unit != target.unit and (st.usages(target.part_id) or st.children(target.part_id)) and \
                    self.units.known(target.unit) and \
                    self.units.get(new.unit).dimension != self.units.get(target.unit).dimension:
                problems.append(f"row {row}: {target.number} is used in BOMs; its unit dimension cannot change")
                continue
            rec = self.store.get("part", target.part_id)
            ops.append(Put("part", target.part_id, self._stamp(new.to_record(), created=rec), revs[target.part_id]))
            updated += 1
        if problems:
            raise CatalogError("parts import refused, nothing written: " + "; ".join(problems[:10]) +
                               (f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""))
        if ops:
            self.store.commit(Commit("bom.part.import", ops + [self._struct_bump(version)]), txid)
        return {"created": created, "updated": updated, "unchanged": unchanged}

    def import_structure(self, structure: Structure, *, replace_existing: bool = False, create_missing: bool = True,
                         txid: str | None = None) -> dict[str, str]:
        """Bring an imported structure (for example from CSV, with unresolved ``pn:`` identities) into the
        catalog in **one** commit. Items are matched to catalog parts by id, then by number; missing parts
        are created (``create_missing``) as manual-number parts. Assemblies that already have a BOM in the
        catalog are refused unless ``replace_existing``. Returns the mapping import id -> catalog part id."""
        version, current, existing, revs = self._state()
        by_number = {p.key: p for p in existing.values()}
        mapping: dict[str, str] = {}
        new_parts: list[Part] = []
        for pid in structure.item_ids:
            info = structure.info(pid)
            if pid in existing:
                mapping[pid] = pid
            elif info.number and number_key(info.number) in by_number:
                mapping[pid] = by_number[number_key(info.number)].part_id
            elif create_missing:
                if not info.number:
                    raise CatalogError(f"item {pid} has no part number; cannot create it")
                unit = info.unit if self.units.known(info.unit) else "EA"
                part = Part(new_part_id(), info.number, description=info.description, unit=self.units.canonical(unit),
                            kind=info.kind if info.kind in {k.value for k in PartKind} else (
                                PartKind.ASSEMBLY.value if structure.is_assembly(pid) else PartKind.PART.value),
                            revision=info.revision, manual_number=bool(self.rules()) and not self._matches_rule(
                                info.number))
                new_parts.append(part)
                by_number[part.key] = part
                mapping[pid] = part.part_id
            else:
                raise CatalogError(f"{info.display_number} is not in the catalog")
        parents = sorted({mapping[ln.parent_id] for ln in structure.lines})
        clash = [existing[p].number for p in parents if p in existing and current.children(p)]
        if clash and not replace_existing:
            raise CatalogError(f"these assemblies already have a BOM in the catalog: {', '.join(clash)}; "
                               "use replace_existing to overwrite them")
        new_lines = [ln.with_changes(parent_id=mapping[ln.parent_id], child_id=mapping[ln.child_id],
                                     line_id=new_line_id()) for ln in structure.lines]
        obsolete = sorted({existing[ln.child_id].number for ln in new_lines
                           if ln.child_id in existing and existing[ln.child_id].obsolete
                           and not any(o.child_id == ln.child_id for o in current.children(ln.parent_id))})
        if obsolete:
            raise CatalogError(f"obsolete parts cannot gain new BOM usages: {', '.join(obsolete)}")
        before = current.with_parts(*(p.info() for p in new_parts))
        after = before
        for parent in parents:
            after = after.with_single_level(parent, [ln for ln in new_lines if ln.parent_id == parent])
        errors = self._new_errors(before, after)
        if errors:
            raise EditRejected(errors)
        ops: list = []
        for p in new_parts:
            ops += [Put("part", p.part_id, self._stamp(p.to_record()), 0),
                    Put("partnum", partnum_id(p.number), {"id": partnum_id(p.number), "number": p.number,
                                                          "part_id": p.part_id}, 0)]
        for r in self.line_records().values():
            if r["parent_id"] in parents:
                ops.append(Delete("bomline", r["id"], r["_rev"]))
        ops += [Put("bomline", ln.line_id, line_to_record(ln), 0) for ln in new_lines]
        ops += [Check("part", pid, revs[pid]) for pid in sorted(set(mapping.values()) & set(existing))]
        ops.append(self._struct_bump(version))
        self.store.commit(Commit("bom.import", ops), txid)
        return mapping

