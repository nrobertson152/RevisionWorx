# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Advanced engineering BOM and assembly layer (issue #18).

Standard library only, offline, deterministic. Nothing in this package writes to a ``.revisionworx``
folder or calls the core's journal, locks or transactions; workspace access is read-only
(:mod:`revisionworx.bom.workspace_adapter`). The base parts catalog (:mod:`~revisionworx.bom.parts`, :mod:`~revisionworx.bom.catalog_service`) is part
of this package; other catalogs can still be plugged in through :class:`~revisionworx.bom.catalog.CatalogReader`.

Public API (see docs/bom/BOM_API.md)::

    from revisionworx import bom
    s = bom.structure_from_csv(Path("frame.csv"))          # or bom.Structure.build(lines, parts)
    report = bom.validate(s, roots=["pn:frame-100"])
    rows = bom.explode(s, "pn:frame-100").rows             # indented BOM
    totals = bom.rollup(s, "pn:frame-100", 5)              # summarized quantities for 5 frames
    diff = bom.compare(old, new)                           # line-level changes
    impact = bom.revision_impact(snapshots, "pn:bolt-m8")  # Before You Revise

    svc = bom.PartsCatalogService(bom.MemoryCatalogStore())  # or WorkspaceCatalogStore(ws): PROPOSED
    frame = svc.create_part("FRAME-100", kind="assembly")
    bolt = svc.create_part("BOLT-M8")
    svc.add_line(frame.part_id, bolt.part_id, "4")          # refused if it adds a cycle or other error
"""
from .catalog import CatalogReader, InMemoryCatalog, PartInfo, unresolved_id
from .csvio import (change_impact_to_csv, diff_to_csv, explosion_to_csv, issues_to_csv, quantity_deltas_to_csv,
                    revision_impact_to_csv, rollup_to_csv, save, structure_from_csv, structure_from_indented_csv,
                    structure_to_csv)
from .diff import BomDiff, FieldChange, LineChange, PartChange, QuantityDelta, compare, compare_rollups
from .errors import (BomError, BomFormatError, BomSourceError, CsvImportError, CsvProblem, CycleError,
                     ExportError, LimitExceeded, QuantityError, StructureError, UnitError)
from .graph import (Explosion, ExplosionRow, Rollup, RollupRow, Usage, WhereUsed, explode, find_cycles, rollup,
                    topological_order, where_used)
from .impact import (AffectedProduct, ChangeImpactReport, ImpactEntry, RevisionImpactReport, change_impact,
                     revision_impact)
from .model import BomLine, Provenance, Structure
from .snapshot import (BomSnapshot, SourceRef, load_any, snapshot_from_json, snapshot_to_json, structure_digest,
                       structure_from_json, structure_to_json)
from .units import DEFAULT_LIMITS, DEFAULT_UNITS, Limits, Unit, UnitRegistry, canonical_text, parse_quantity
from .validate import Issue, ValidationReport, validate
from .parts import (DocumentLink, ManufacturerRef, NumberingRule, Part, PartKind, PartStatus, VendorRef, new_line_id,
                    new_part_id)
from .store import CatalogStore, MemoryCatalogStore, StoreConflict, StoreError, WorkspaceCatalogStore
from .catalog_service import CatalogError, EditRejected, PartsCatalogService
from .parts_csv import export_parts, import_parts, parts_from_csv, parts_to_csv
from .integrations import ChangeControlWhereUsed
from .purchasing import BuyLine, PurchasingList, purchasing_list, purchasing_to_csv
from .xlsx import (csv_to_xlsx, parts_from_xlsx, parts_to_xlsx, structure_from_indented_xlsx, structure_from_xlsx,
                   structure_to_xlsx, table_to_xlsx)

API_VERSION = 1

__all__ = [name for name in dir() if not name.startswith("_")]
