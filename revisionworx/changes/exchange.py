# SPDX-License-Identifier: GPL-3.0-or-later
"""Local import/export for change control.

* :func:`export_package` - a self-verifying JSON snapshot of changes with their complete audit chains
  (archive, hand-over, independent QA). :func:`verify_package` re-checks every chain and the package
  digest offline.
* :func:`import_requests_csv` - bulk-create draft change requests from a spreadsheet. Every row is
  validated first; any error means nothing is created (one commit for all rows).
* :func:`write_export` - writes to disk without ever leaving a partial file: content goes to a temporary
  file and is moved into place; an existing file is kept unless ``overwrite`` is set.
"""
from __future__ import annotations

import csv
import io
import json
import os
import uuid
from pathlib import Path
from typing import Callable

from .audit import verify_chain
from .model import (AffectedItem, ChangeError, DependencyError, DuplicateNumber, ItemType, MalformedRecord,
                    ValidationError, digest_of, load_change, number_key)
from .service import ChangeService

PACKAGE_FORMAT = "revisionworx.change-package"
PACKAGE_VERSION = 1
_PACKAGE_DOMAIN = "revisionworx/changes/package/v1"
CSV_COLUMNS = ("number", "title", "reason", "description", "category", "priority", "documents", "items")
MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 20 * 1024 * 1024


class ImportRejected(ChangeError):
    """The import file has errors; nothing was created. ``errors`` lists (row, message)."""

    def __init__(self, errors: list[tuple[int, str]]):
        self.errors = errors
        shown = "; ".join(f"row {r}: {m}" for r, m in errors[:10])
        more = f" (and {len(errors) - 10} more)" if len(errors) > 10 else ""
        super().__init__(f"{len(errors)} problem(s), nothing was imported: {shown}{more}")


# ---------------------------------------------------------------------------- package
def export_package(svc: ChangeService, change_ids: list[str] | None = None) -> dict:
    if change_ids is None:
        changes, problems = svc.scan()
        if problems:
            raise MalformedRecord(f"{len(problems)} change record(s) are unreadable; export them by id or "
                                  "repair the workspace first")
    else:
        changes = [svc.get(c) for c in dict.fromkeys(change_ids)]
    body = {"format": PACKAGE_FORMAT, "version": PACKAGE_VERSION, "workspace_id": svc.store.workspace_id,
            "exported_at": svc.clock(), "exported_by": svc.actor(),
            "changes": [c.to_dict() for c in sorted(changes, key=lambda c: (c.kind.value, c.number))],
            "events": {c.id: svc.history(c.id) for c in changes}}
    for c in changes:
        problems = svc.verify_history(c.id)
        if problems:
            raise MalformedRecord(f"{c.number}: audit history is damaged ({problems[0]['problem']}); not exported")
    body["digest"] = digest_of(_PACKAGE_DOMAIN, body)
    return body


def verify_package(data: object) -> list[dict]:
    """Problems found in an exported package; empty means intact (schema, every chain, package digest)."""
    if not isinstance(data, dict) or data.get("format") != PACKAGE_FORMAT:
        return [{"problem": "not-a-change-package"}]
    if data.get("version") != PACKAGE_VERSION:
        return [{"problem": "unsupported-version", "version": data.get("version")}]
    problems: list[dict] = []
    body = {k: v for k, v in data.items() if k != "digest"}
    try:
        if data.get("digest") != digest_of(_PACKAGE_DOMAIN, body):
            problems.append({"problem": "package-digest-mismatch"})
    except (TypeError, ValueError):
        problems.append({"problem": "package-not-canonical"})
    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    for raw in data.get("changes") if isinstance(data.get("changes"), list) else []:
        try:
            change = load_change(raw)
        except MalformedRecord as exc:
            problems.append({"problem": "malformed-change", "detail": str(exc)})
            continue
        chain = events.get(change.id)
        if not isinstance(chain, list):
            problems.append({"problem": "missing-history", "change": change.number})
            continue
        by_n = {e.get("n"): e for e in chain if isinstance(e, dict)}
        for p in verify_chain(change.id, change.event_count, change.event_head, by_n.get):
            problems.append({**p, "change": change.number})
    return problems


def load_package(path: Path) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValidationError("file is too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MalformedRecord(f"not a JSON change package: {exc}") from None


# ---------------------------------------------------------------------------- files
def write_export(path: Path, text: str | bytes, overwrite: bool = False) -> Path:
    """Write atomically: a crash leaves either the old file or the complete new one, never a mix."""
    path = Path(path)
    data = text.encode("utf-8") if isinstance(text, str) else text
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            try:
                _publish_new(tmp, path)
            except FileExistsError:
                raise FileExistsError(f"{path} already exists; choose another name or allow overwrite") from None
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    return path


def _publish_new(tmp: Path, path: Path) -> None:
    """Move a finished temp file to a name that must not exist yet. Hard link where possible (no window);
    on filesystems without hard links (FAT/exFAT drives, some shares) reserve the name exclusively and
    replace the empty placeholder, so the worst crash outcome is an empty file, never a partial one."""
    if os.name == "nt":
        os.rename(tmp, path)                     # refuses an existing destination
        return
    try:
        os.link(tmp, path)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)
    os.replace(tmp, path)


def package_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n"


# ---------------------------------------------------------------------------- CSV import
def document_lookup(ws) -> Callable[[str], str | None]:
    """Resolve a document reference from a CSV cell: an internal id or a unique document number."""
    docs = ws.list("document")
    by_number: dict[str, list[str]] = {}
    for d in docs.values():
        if d.get("number"):
            by_number.setdefault(number_key(d["number"]), []).append(d["id"])

    def find(ref: str) -> str | None:
        if ref in docs:
            return ref
        hits = by_number.get(number_key(ref), [])
        if len(hits) > 1:
            raise ValidationError(f"document number {ref!r} is ambiguous ({len(hits)} documents)")
        return hits[0] if hits else None
    return find


def parse_requests_csv(text: str, find_document: Callable[[str], str | None] | None = None) -> list[dict]:
    """Rows -> change request specs. ``documents``: ';'-separated document numbers or ids. ``items``:
    ';'-separated declared items as ``type:label`` or ``type:label@revision`` (parts, assemblies ...)."""
    if len(text.encode("utf-8", "surrogatepass")) > MAX_IMPORT_BYTES:
        raise ValidationError("file is too large")
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [h.strip().casefold() for h in (reader.fieldnames or [])]
    errors: list[tuple[int, str]] = []
    if not header or not {"title", "reason"} <= set(header):
        raise ImportRejected([(1, "header must include at least 'title' and 'reason'")])
    unknown = set(header) - set(CSV_COLUMNS)
    if unknown:
        raise ImportRejected([(1, f"unknown columns: {', '.join(sorted(unknown))}")])
    if len(set(header)) != len(header):
        raise ImportRejected([(1, "a column appears twice")])
    reader.fieldnames = header
    specs: list[dict] = []
    for line_no, raw in enumerate(reader, start=2):
        if None in raw:
            errors.append((line_no, "more cells than columns"))
            continue
        cells = {k: (v or "") for k, v in raw.items()}
        if not any(v.strip() for v in cells.values()):
            continue
        if len(specs) >= MAX_IMPORT_ROWS:
            errors.append((line_no, f"more than {MAX_IMPORT_ROWS} rows"))
            break
        affected: list[AffectedItem] = []
        try:
            for ref in filter(None, (r.strip() for r in cells.get("documents", "").split(";"))):
                if find_document is None:
                    raise ValidationError("document references need a workspace to resolve them")
                doc_id = find_document(ref)
                if doc_id is None:
                    raise ValidationError(f"unknown document {ref!r}")
                affected.append(AffectedItem.create(ItemType.DOCUMENT, "", doc_id))
            for ref in filter(None, (r.strip() for r in cells.get("items", "").split(";"))):
                kind, sep, rest = ref.partition(":")
                if not sep or kind.strip().casefold() == "document":
                    raise ValidationError(f"declared item {ref!r} must look like part:P-100 or part:P-100@B")
                label, _, rev = rest.partition("@")
                affected.append(AffectedItem.create(kind.strip().casefold(), label, baseline_label=rev))
        except ValidationError as exc:
            errors.append((line_no, str(exc)))
            continue
        spec = {"title": cells.get("title", ""), "reason": cells.get("reason", ""),
                "description": cells.get("description", ""), "category": cells.get("category", "") or "design",
                "priority": (cells.get("priority", "") or "normal").strip().casefold(), "affected": affected}
        if cells.get("number", "").strip():
            spec["number"] = cells["number"]
        specs.append((line_no, spec))
    if errors:
        raise ImportRejected(errors)
    if not specs:
        raise ImportRejected([(2, "no rows to import")])
    seen: dict[str, int] = {}
    for line_no, spec in specs:
        if "number" in spec:
            key = number_key(spec["number"].strip())
            if key in seen:
                errors.append((line_no, f"number {spec['number']!r} repeats row {seen[key]}"))
            seen[key] = line_no
    if errors:
        raise ImportRejected(errors)
    return specs


def import_requests_csv(svc: ChangeService, text: str, find_document: Callable[[str], str | None] | None = None,
                        txid: str | None = None) -> list:
    """Create one draft ECR per row, all in one commit. Any invalid row: :class:`ImportRejected`, nothing
    created. Rows are validated individually first so every problem is reported at once."""
    specs = parse_requests_csv(text, find_document)
    if txid is not None and svc.store.outcome(txid)["state"] != "not-found":
        # a retry of an import that may already have happened: the service returns the original requests
        # (or refuses a fenced attempt) - validating again would find this import's own numbers taken
        return svc.create_requests([spec for _, spec in specs], txid=txid)
    errors = []
    probe = ChangeService(svc.store, svc.catalog, clock=svc.clock, ids=svc.ids, actor=svc.actor)
    policy = probe.policy()
    for line_no, spec in specs:
        try:
            probe._request_spec(policy, "ecr_" + "0" * 32, {k: v for k, v in spec.items() if k != "number"})
            if "number" in spec and probe.find_by_number(spec["number"].strip()) is not None:
                raise DuplicateNumber(f"change number {spec['number'].strip()!r} is already used")
        except (ValidationError, DuplicateNumber, DependencyError) as exc:
            errors.append((line_no, str(exc)))
    if errors:
        raise ImportRejected(errors)
    return svc.create_requests([spec for _, spec in specs], txid=txid)


__all__ = ["ImportRejected", "export_package", "verify_package", "load_package", "write_export", "package_json",
           "document_lookup", "parse_requests_csv", "import_requests_csv", "CSV_COLUMNS"]
