# SPDX-License-Identifier: GPL-3.0-or-later
"""Engineering change control: change requests (ECR), change orders (ECO), signoffs, dependencies,
impact reports and an immutable audit trail. Design and GUI contract: docs/CHANGE_CONTROL.md.

Typical use from a GUI controller (same pattern as ``documents``)::

    with Workspace.open(location, writable=True) as ws:
        svc = changes.service_for(ws)
        ecr = svc.create_request("Thicken bracket", "Cracks in fatigue test", affected=[...])

Read-only handles work for every query; writes need a writable handle.
"""
from __future__ import annotations

from .catalog import ItemCatalog, ItemState, MemoryCatalog, RevInfo, WorkspaceCatalog
from .model import (ActionStatus, AffectedItem, Change, ChangeError, ChangeKind, ChangeOrder, ChangeRequest,
                    Decision, Dependency, DependencyError, DependencyType, DuplicateNumber, ECOStatus,
                    ECRStatus, Implementation, ImplementationStatus, ItemType, MalformedRecord, NotFound,
                    OrderAction, Priority, ProposedAction, ReplayRefused, SignoffError, Signoff, SignoffStage,
                    StaleBaseline, TransitionError, ValidationError)
from .service import ChangeService
from .store import ChangeStore, MemoryStore, WorkspaceStore
from .workflow import Policy


def service_for(ws, **kwargs) -> ChangeService:
    """A :class:`ChangeService` on an open workspace handle, verifying documents and projects."""
    kwargs.setdefault("actor", ws.actor)
    return ChangeService(WorkspaceStore(ws), WorkspaceCatalog(ws), **kwargs)


__all__ = ["ActionStatus", "AffectedItem", "Change", "ChangeError", "ChangeKind", "ChangeOrder", "ChangeRequest",
           "ChangeService", "ChangeStore", "Decision", "Dependency", "DependencyError", "DependencyType",
           "DuplicateNumber", "ECOStatus", "ECRStatus", "Implementation", "ImplementationStatus", "ItemCatalog",
           "ItemState", "ItemType", "MalformedRecord", "MemoryCatalog", "MemoryStore", "NotFound", "OrderAction",
           "Policy", "Priority", "ProposedAction", "ReplayRefused", "RevInfo", "SignoffError", "Signoff",
           "SignoffStage", "StaleBaseline", "TransitionError", "ValidationError", "WorkspaceCatalog",
           "WorkspaceStore", "service_for"]
