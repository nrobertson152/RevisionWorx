# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only view of the artefacts a change can affect.

Change control never edits documents, revisions or projects. It only needs to ask: does this item exist,
what is its current revision, which revision descends from which, and is it checked out. That question is
the :class:`ItemCatalog` protocol. :class:`WorkspaceCatalog` answers it for documents and projects from
the public core records (CORE_API.md sections 2-3). Item types the core does not manage yet (parts,
assemblies, BOMs: issues #15/#18) are answered by resolvers registered with :meth:`register`; until one
exists they are reported as *declared*, i.e. not verified - never as verified or absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .model import AffectedItem, ItemType


@dataclass(frozen=True)
class RevInfo:
    id: str
    document_id: str
    label: str
    number: int
    state: str
    previous_rev_id: str | None


@dataclass(frozen=True)
class ItemState:
    """What the catalog knows about an affected item right now."""
    exists: bool
    label: str = ""
    project_id: str | None = None
    latest_rev_id: str | None = None
    latest_label: str = ""
    released_rev_id: str | None = None
    status: str = ""
    checked_out_by: dict | None = None
    check_kind: str | None = None       # store Check kind ("core:document") to pin the decision atomically
    check_rev: int | None = None


Resolver = Callable[[AffectedItem], ItemState | None]


class ItemCatalog(Protocol):
    def describe(self, item: AffectedItem) -> ItemState | None:
        """None means "this catalog cannot verify that item type" (declared, unverified)."""
    def revision(self, rev_id: str) -> RevInfo | None: ...
    def lifecycle_state(self, role: str) -> str | None: ...
    def register(self, item_type: ItemType, resolver: Resolver) -> None: ...


class _Base:
    MAX_CHAIN = 100_000

    def __init__(self):
        self._resolvers: dict[ItemType, Resolver] = {}

    def register(self, item_type: ItemType, resolver: Resolver) -> None:
        """Let another subsystem (e.g. a parts registry) verify its own item type."""
        self._resolvers[ItemType(item_type)] = resolver

    def describe(self, item: AffectedItem) -> ItemState | None:
        fn = self._resolvers.get(item.item_type)
        return fn(item) if fn else None

    def ancestors(self, rev_id: str) -> list[str]:
        """``rev_id`` and every earlier revision of its document, newest first."""
        out, rid = [], rev_id
        while rid and len(out) < self.MAX_CHAIN:
            if rid in out:
                break
            info = self.revision(rid)
            if info is None:
                break
            out.append(rid)
            rid = info.previous_rev_id
        return out

    def revision(self, rev_id: str) -> RevInfo | None:  # pragma: no cover - overridden
        raise NotImplementedError


def _rev_info(rec: dict | None) -> RevInfo | None:
    if not rec or not isinstance(rec.get("document_id"), str):
        return None
    return RevInfo(rec["id"], rec["document_id"], str(rec.get("label", "")), int(rec.get("number", 0)),
                   str(rec.get("state", "")), rec.get("previous_rev_id"))


class WorkspaceCatalog(_Base):
    """Documents and projects from an open workspace handle (public record shapes only)."""

    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self._resolvers[ItemType.DOCUMENT] = self._document
        self._resolvers[ItemType.PROJECT] = self._project

    def _document(self, item: AffectedItem) -> ItemState:
        doc = self.ws.get("document", item.item_id) if item.item_id else None
        if doc is None:
            return ItemState(exists=False)
        latest = self.ws.get("revision", doc["latest_rev_id"]) if doc.get("latest_rev_id") else None
        co = self.ws.get("checkout", doc["id"])
        label = " ".join(x for x in (doc.get("number", ""), doc.get("title", "")) if x) or doc.get("rel_path", "")
        return ItemState(True, label, doc.get("project_id"), doc.get("latest_rev_id"),
                         latest.get("label", "") if latest else "", doc.get("released_rev_id"),
                         doc.get("status", ""), (co or {}).get("owner"), "core:document", doc.get("_rev"))

    def _project(self, item: AffectedItem) -> ItemState:
        rec = self.ws.get("project", item.item_id) if item.item_id else None
        if rec is None:
            return ItemState(exists=False)
        label = " ".join(x for x in (rec.get("number", ""), rec.get("name", "")) if x)
        return ItemState(True, label, rec["id"], status=rec.get("status", ""), check_kind="core:project",
                         check_rev=rec.get("_rev"))

    def revision(self, rev_id: str) -> RevInfo | None:
        try:
            return _rev_info(self.ws.get("revision", rev_id))
        except ValueError:
            return None

    def lifecycle_state(self, role: str) -> str | None:
        from .. import documents
        return documents.get_lifecycle(self.ws).get(role)


class MemoryCatalog(_Base):
    """Fake documents/revisions for tests and GUI fixtures without a workspace."""

    def __init__(self):
        super().__init__()
        self.documents: dict[str, dict] = {}
        self.revisions: dict[str, dict] = {}
        self.roles = {"approved": "approved", "released": "released"}
        self._resolvers[ItemType.DOCUMENT] = self._document

    def add_document(self, doc_id: str, label: str, project_id: str = "prj_memory") -> None:
        self.documents[doc_id] = {"id": doc_id, "label": label, "project_id": project_id,
                                  "latest_rev_id": None, "released_rev_id": None, "checked_out_by": None}

    def add_revision(self, doc_id: str, rev_id: str, label: str, state: str = "wip") -> None:
        doc = self.documents[doc_id]
        prev = doc["latest_rev_id"]
        number = self.revisions[prev]["number"] + 1 if prev else 1
        self.revisions[rev_id] = {"id": rev_id, "document_id": doc_id, "label": label, "number": number,
                                  "state": state, "previous_rev_id": prev}
        doc["latest_rev_id"] = rev_id

    def _document(self, item: AffectedItem) -> ItemState:
        doc = self.documents.get(item.item_id or "")
        if doc is None:
            return ItemState(exists=False)
        latest = self.revisions.get(doc["latest_rev_id"] or "")
        return ItemState(True, doc["label"], doc["project_id"], doc["latest_rev_id"],
                         latest["label"] if latest else "", doc["released_rev_id"], "active",
                         doc["checked_out_by"])

    def revision(self, rev_id: str) -> RevInfo | None:
        return _rev_info(self.revisions.get(rev_id))

    def lifecycle_state(self, role: str) -> str | None:
        return self.roles.get(role)
