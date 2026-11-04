# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exceptions raised by the engineering BOM package.

Every exception subclasses :class:`BomError`, so a caller (GUI, CLI) can catch one type. Messages are
written for the engineer reading them, and name the part, line, row or column involved.
"""
from __future__ import annotations

from dataclasses import dataclass


class BomError(Exception):
    """Base class for every BOM error."""


class QuantityError(BomError, ValueError):
    """A quantity is not a positive, finite, exactly representable decimal within the limits."""


class UnitError(BomError, ValueError):
    """An unknown unit, or units of different dimensions that cannot be combined."""


class CycleError(BomError):
    """An assembly contains itself, directly or through subassemblies.

    ``cycle`` lists the item ids around the loop, starting and ending with the same id."""

    def __init__(self, cycle: tuple[str, ...], message: str | None = None):
        self.cycle = tuple(cycle)
        super().__init__(message or "assembly cycle: " + " -> ".join(self.cycle))


class LimitExceeded(BomError):
    """A configured safety limit (depth, rows, magnitude) was exceeded; the operation was abandoned."""


class StructureError(BomError, ValueError):
    """A BOM line or structure is malformed (empty ids, bad reference designators, duplicate line ids...)."""


class BomFormatError(BomError, ValueError):
    """A serialized BOM (JSON) is not in a supported format or version. Nothing was loaded."""


class BomSourceError(BomError):
    """A BOM could not be read from its source (for example a workspace revision that is too large)."""


class ExportError(BomError, OSError):
    """An export was refused or failed. The destination file is unchanged."""


@dataclass(frozen=True)
class CsvProblem:
    """One problem found while importing a CSV file. ``row`` is the 1-based physical line number."""

    row: int
    column: str
    message: str

    def __str__(self) -> str:
        where = f"row {self.row}" if self.row else "file"
        if self.column:
            where += f", column '{self.column}'"
        return f"{where}: {self.message}"


class CsvImportError(BomError, ValueError):
    """A CSV file could not be imported. Nothing was imported; ``problems`` lists every problem found
    (up to a cap), in file order."""

    def __init__(self, problems: list[CsvProblem] | tuple[CsvProblem, ...], truncated: bool = False):
        self.problems = tuple(problems)
        self.truncated = truncated
        head = "; ".join(str(p) for p in self.problems[:5])
        more = len(self.problems) - 5
        tail = f" (and {more} more)" if more > 0 else ""
        if truncated:
            tail += " (further problems not reported)"
        super().__init__(f"CSV import failed with {len(self.problems)} problem(s): {head}{tail}")
