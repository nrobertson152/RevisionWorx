# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only adapter from the core's immutable document revisions to BOM snapshots.

A BOM file (parent-child or indented CSV, or RevisionWorx BOM JSON) registered as an ordinary document is
revisioned by the core like any other file. This adapter turns any of its revisions into a
:class:`BomSnapshot` whose :class:`SourceRef` names the document, revision id, label, number, lifecycle
state and SHA-256, so two revisions of a BOM can be compared.

Core calls used, all read-only:

* ``documents.history(ws, doc_id)`` - docs/CORE_API.md section 3 (Stable);
* ``ws.get("document", doc_id)`` / ``documents.list_documents`` - section 3 (Stable);
* ``documents.read_revision_bytes(ws, rev_id, max_bytes)`` - ADR-018, the verified bounded read the
  architecture designates for integrations. It is not yet listed in CORE_API.md; a request to add it is
  on PR #7.

The adapter never opens a writable handle, never reads the mutable working file and never writes to the
workspace. A damaged content object raises the core's ``WorkspaceCorruptError`` unchanged (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .catalog import CatalogReader
from .csvio import structure_from_csv, structure_from_indented_csv
from .diff import BomDiff, compare
from .errors import BomError, BomSourceError
from .snapshot import BomSnapshot, SourceRef, load_any

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
BOM_SUFFIXES = (".csv", ".json")


@dataclass(frozen=True)
class BomRevision:
    """One revision of a BOM document, as listed by :meth:`WorkspaceBomReader.revisions`."""

    revision_id: str
    number: int
    label: str
    state: str
    sha256: str
    created: str
    author: str
    description: str


class WorkspaceBomReader:
    """Builds BOM snapshots from revisions of documents in an open workspace (read-only handle is enough)."""

    def __init__(self, ws: Any, *, catalog: CatalogReader | None = None, max_bytes: int = DEFAULT_MAX_BYTES):
        self.ws = ws
        self.catalog = catalog
        self.max_bytes = max_bytes

    def _documents(self):
        from revisionworx import documents
        return documents

    def bom_documents(self, project_id: str | None = None) -> list[dict]:
        """Registered documents whose file name ends in .csv or .json (candidates; content decides)."""
        docs = self._documents().list_documents(self.ws, project_id)
        return [d for d in docs if PurePosixPath(d["rel_path"]).suffix.casefold() in BOM_SUFFIXES]

    def revisions(self, document_id: str) -> list[BomRevision]:
        out = []
        for r in self._documents().history(self.ws, document_id):
            out.append(BomRevision(r["id"], r["number"], r["label"], r["state"], r["sha256"], r["created"],
                                   (r.get("author") or {}).get("user", ""), r["description"]))
        return out

    def snapshot(self, revision_id: str, *, root_number: str = "", root_id: str = "",
                 layout: str = "auto") -> BomSnapshot:
        """The BOM held by one revision. ``layout`` is ``auto``, ``parent-child``, ``indented`` or ``json``.

        For parent-child CSVs with more than one top-level item, name the root."""
        documents = self._documents()
        try:
            rev, data = documents.read_revision_bytes(self.ws, revision_id, self.max_bytes)
        except ValueError as exc:  # content.ContentTooLarge subclasses ValueError
            if type(exc).__name__ == "ContentTooLarge":
                raise BomSourceError(f"revision {revision_id} is too large to read as a BOM: {exc}") from None
            raise
        suffix = PurePosixPath(rev.get("source_rel_path", "")).suffix.casefold()
        if layout == "auto":
            layout = "json" if suffix == ".json" else sniff_layout(data)
        source = SourceRef(kind="workspace-revision", document_id=rev["document_id"], revision_id=rev["id"],
                           revision_label=rev["label"], revision_number=int(rev["number"]), sha256=rev["sha256"],
                           path=rev.get("source_rel_path", ""), state=rev.get("state", ""))
        if layout == "json":
            loaded = load_any(data)
            if isinstance(loaded, BomSnapshot):
                return BomSnapshot(loaded.structure, loaded.root_id, loaded.label or rev["label"], source)
            structure = loaded
            root = root_id or single_root(structure, root_number)
        elif layout == "indented":
            structure, root = structure_from_indented_csv(data, root_number=root_number, root_id=root_id,
                                                          catalog=self.catalog)
        elif layout == "parent-child":
            structure = structure_from_csv(data, catalog=self.catalog)
            root = root_id or single_root(structure, root_number)
        else:
            raise BomError(f"unknown BOM layout {layout!r}")
        return BomSnapshot(structure, root, rev["label"], source)

    def compare_revisions(self, revision_a: str, revision_b: str, **kw) -> tuple[BomSnapshot, BomSnapshot, BomDiff]:
        a, b = self.snapshot(revision_a, **kw), self.snapshot(revision_b, **kw)
        if a.source.document_id != b.source.document_id:
            raise BomError("the revisions belong to different documents")
        return a, b, compare(a.structure, b.structure)

    def latest_snapshot(self, document_id: str, **kw) -> BomSnapshot:
        revs = self.revisions(document_id)
        if not revs:
            raise BomSourceError(f"document {document_id} has no revisions")
        return self.snapshot(revs[-1].revision_id, **kw)


def sniff_layout(data: bytes) -> str:
    """``indented`` when the header has a level column or starts with an item column, else ``parent-child``."""
    head = data[:4096].decode("utf-8-sig", errors="replace").splitlines()[:1]
    header = head[0].casefold() if head else ""
    if "level" in header or header.startswith("item"):
        return "indented"
    return "parent-child"


def single_root(structure, root_number: str = "") -> str:
    """The item numbered ``root_number``, or the only top-level item. Raises :class:`BomError` otherwise."""
    from .catalog import number_key
    if root_number:
        matches = [p.part_id for p in structure.parts if number_key(p.number) == number_key(root_number)]
        if len(matches) != 1:
            raise BomError(f"root {root_number!r} matches {len(matches)} items in the BOM")
        return matches[0]
    tops = structure.top_level_ids
    if len(tops) != 1:
        raise BomError(f"the BOM has {len(tops)} top-level items ({', '.join(tops[:5])}); name the root")
    return tops[0]
