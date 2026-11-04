# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapters that let other RevisionWorx subsystems use the BOM layer without importing its internals.

:class:`ChangeControlWhereUsed` implements the ``WhereUsedProvider`` protocol of change control
(issue #19, PR #20, ``revisionworx/changes/impact.py``): ``where_used(item) -> {"edges": [...],
"coverage": str}``. It is duck-typed (it reads ``item.item_type`` and ``item.item_id``/``item.label``), so
this package does not import ``revisionworx.changes``.
"""
from __future__ import annotations

from typing import Any, Callable

from .catalog import number_key
from .graph import describe_quantity, where_used
from .model import Structure

PART_TYPES = {"part", "assembly", "bom"}


class ChangeControlWhereUsed:
    """Where-used over the catalog structure. ``structure`` is a callable so each report reads fresh data
    (for example ``PartsCatalogService(...).structure``)."""

    def __init__(self, structure: Callable[[], Structure], *, source: str = "parts catalog"):
        self._structure = structure
        self.source = source

    def where_used(self, item: Any) -> dict:
        itype = getattr(getattr(item, "item_type", None), "value", getattr(item, "item_type", ""))
        if itype not in PART_TYPES:
            return {"edges": [], "coverage": f"Where-used was not searched for {itype or 'this'} items; the BOM "
                                             "layer covers parts, assemblies and BOMs only."}
        st = self._structure()
        pid = getattr(item, "item_id", None) or ""
        if pid not in st.item_ids:
            label = getattr(item, "label", "") or ""
            matches = [p.part_id for p in st.parts if label and number_key(p.number) == number_key(label)]
            pid = matches[0] if len(matches) == 1 else ""
        coverage = (f"Searched the {self.source} ({len(st.assembly_ids)} assemblies, {len(st.lines)} BOM lines). "
                    "BOMs kept outside it (CAD files, unmanaged or unindexed projects) were NOT searched; an "
                    "empty result is not proof the item is unused.")
        if not pid:
            return {"edges": [], "coverage": "The item is not in the " + self.source + "; where-used was NOT "
                                             "searched for it. " + coverage}
        wu = where_used(st, pid)
        edges = []
        for u in wu.usages:
            revs = sorted({ln.child_revision for ln in u.via_lines if ln.parent_id == u.assembly_id}) if u.direct else []
            edges.append({"label": st.info(u.assembly_id).display_number, "id": u.assembly_id,
                          "relationship": "direct" if u.direct else "indirect", "levels": u.distance,
                          "referenced_revision": ", ".join(r or "latest" for r in revs) if u.direct else "",
                          "quantity": describe_quantity(u.quantity_per_assembly, u.unit),
                          "top_level": u.is_top_level,
                          "verification": "verification-required" if u.needs_verification else "declared-bom"})
        if wu.cycles:
            coverage += " The structure contains a cycle; quantities on it are unknown."
        return {"edges": edges, "coverage": coverage}
