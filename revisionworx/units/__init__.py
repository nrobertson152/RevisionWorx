# SPDX-License-Identifier: GPL-3.0-or-later
"""The unit register: serial-numbered units, their identifiers, build standards, configuration and history (ADR-025;
design and data contract in docs/UNITS.md).

Typical use, the same pattern as ``documents`` and ``changes``::

    with Workspace.open(location, writable=True) as ws:
        svc = units.service_for(ws)
        unit = svc.register(identifiers=[{"scheme": "serial", "value": "NH-26-0001"}], model={"part": "", "number": "HPU-75"})
        svc.install(unit["id"], "P1", number="PMP-10", serial="12345")

Reads work on a read-only handle; actions need a writable one. Every action is one journal entry.
"""
from __future__ import annotations

from .model import (Ambiguous, DuplicateIdentifier, ImportProblems, MalformedUnitRecord, NotFound, ReplayRefused, UnitError,
                    ValidationError, fieldworx_asset_id, unit_label)
from .service import KIND, UnitService, service_for

__all__ = ["Ambiguous", "DuplicateIdentifier", "ImportProblems", "KIND", "MalformedUnitRecord", "NotFound", "ReplayRefused",
           "UnitError", "UnitService", "ValidationError", "fieldworx_asset_id", "service_for", "unit_label"]
