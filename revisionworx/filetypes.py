# SPDX-License-Identifier: GPL-3.0-or-later
"""File type recognition.

Recognition by extension says only *what kind of file this claims to be*. It is not reference
extraction and implies no CAD dependency support. ``capability`` states honestly what RevisionWorx
can do with the type today.

OpenSchematix detection is verified against the format documented in the OpenSchematix repository
(docs/FILE_FORMAT.md, v1.1.0): ``.schematix`` is a ZIP whose ``mimetype`` member is
``application/x-openschematix-project``; legacy ``.osch`` is a JSON document with
``"format": "OpenSchematix"``.

FluidWorx detection is verified against the format documented in the FluidWorx repository
(docs/FILE_FORMAT.md, version 1): ``.fluidworx`` is a ZIP whose first member, ``mimetype``, is
``application/x-fluidworx-study``.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

OPENSCHEMATIX_MIMETYPE = "application/x-openschematix-project"
FLUIDWORX_MIMETYPE = "application/x-fluidworx-study"


@dataclass(frozen=True)
class FileType:
    category: str
    label: str
    capability: str  # "recognized-only" | "content-verified" | "generic"


_TYPES: dict[str, FileType] = {}


def _reg(exts: str, category: str, label: str, capability: str = "recognized-only") -> None:
    for ext in exts.split():
        _TYPES[ext] = FileType(category, label, capability)


_reg(".ipt", "cad-part", "Autodesk Inventor part")
_reg(".iam", "cad-assembly", "Autodesk Inventor assembly")
_reg(".idw", "cad-drawing", "Autodesk Inventor drawing")
_reg(".ipn", "cad-presentation", "Autodesk Inventor presentation")
_reg(".ipj", "cad-project", "Autodesk Inventor project file")
_reg(".sldprt", "cad-part", "SolidWorks part")
_reg(".sldasm", "cad-assembly", "SolidWorks assembly")
_reg(".slddrw", "cad-drawing", "SolidWorks drawing")
_reg(".step .stp", "cad-exchange", "STEP model")
_reg(".iges .igs", "cad-exchange", "IGES model")
_reg(".dxf", "drawing-export", "DXF drawing")
_reg(".dwg", "drawing-export", "DWG drawing")
_reg(".pdf", "document", "PDF document")
_reg(".schematix", "schematic", "OpenSchematix project")
_reg(".osch", "schematic", "OpenSchematix legacy sheet")
_reg(".fluidworx", "simulation", "FluidWorx simulation study")

GENERIC = FileType("other", "Other file", "generic")


def classify(path: Path) -> FileType:
    return _TYPES.get(Path(path).suffix.lower(), GENERIC)


def sniff_openschematix(path: Path) -> bool:
    """Return True only if the file's *content* matches the documented OpenSchematix format."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".schematix":
            if not zipfile.is_zipfile(path):
                return False
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if not names or names[0] != "mimetype":
                    return False
                return zf.read("mimetype").decode("ascii", "replace").strip() == OPENSCHEMATIX_MIMETYPE
        if suffix == ".osch":
            with open(path, "rb") as fh:
                head = fh.read(4 * 1024 * 1024)
            doc = json.loads(head.decode("utf-8"))
            return isinstance(doc, dict) and doc.get("format") == "OpenSchematix"
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeDecodeError):
        return False
    return False


def sniff_fluidworx(path: Path) -> bool:
    """Return True only if the file's *content* matches the documented FluidWorx study format."""
    path = Path(path)
    try:
        if path.suffix.lower() != ".fluidworx" or not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not names or names[0] != "mimetype":
                return False
            return zf.read("mimetype").decode("ascii", "replace").strip() == FLUIDWORX_MIMETYPE
    except (OSError, ValueError, zipfile.BadZipFile, UnicodeDecodeError):
        return False
