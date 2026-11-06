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

DraftWorx detection is verified against the format documented in the DraftWorx repository
(docs/FILE_FORMAT.md): ``.draftworx`` is UTF-8 JSON, one object, whose first member is
``"format": "DraftWorx drawing"``. Only the start of the file is read, so a large drawing costs nothing extra.

Other programs' files are recognised by extension, so they are labelled and suggested the right document type:
KiCad, Altium, Eagle, QElectroTech, LibrePCB, EPLAN and AutoCAD Electrical for electrical design; FreeCAD,
Solid Edge, CATIA, Creo, NX, Fusion, Rhino and SketchUp for mechanical design; Gerber and drill files for boards.
KiCad's own files are also verified by content: KiCad 6 and later write S-expressions that start with the kind of
file, ``(kicad_sch``, ``(kicad_pcb``, ``(kicad_symbol_lib`` or ``(footprint``/``(module``. Creo numbers its
saves (``bracket.prt.3``): the number is not the type.
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

OPENSCHEMATIX_MIMETYPE = "application/x-openschematix-project"
FLUIDWORX_MIMETYPE = "application/x-fluidworx-study"
DRAFTWORX_FORMAT = "DraftWorx drawing"
_DRAFTWORX_HEAD = re.compile(rb'\A(?:\xef\xbb\xbf)?\s*\{\s*"format"\s*:\s*"DraftWorx drawing"\s*[,}]')


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
_reg(".draftworx", "cad-drawing", "DraftWorx drawing")
_reg(".osym", "library", "OpenSchematix symbol")
_reg(".osymlib", "library", "OpenSchematix symbol library")

# Electrical design: schematics, boards, projects and libraries from other programs
_reg(".kicad_sch", "schematic", "KiCad schematic")
_reg(".kicad_pcb", "pcb", "KiCad PCB")
_reg(".kicad_pro", "ecad-project", "KiCad project")
_reg(".kicad_sym", "library", "KiCad symbol library")
_reg(".kicad_mod", "library", "KiCad footprint")
_reg(".schdoc", "schematic", "Altium schematic")
_reg(".pcbdoc", "pcb", "Altium PCB")
_reg(".prjpcb", "ecad-project", "Altium PCB project")
_reg(".schlib .pcblib", "library", "Altium library")
_reg(".sch", "schematic", "Schematic (KiCad 5, Eagle or gEDA)")
_reg(".brd", "pcb", "PCB (Eagle, KiCad 5 or Allegro)")
_reg(".lbr", "library", "Eagle library")
_reg(".qet", "schematic", "QElectroTech project")
_reg(".elmt", "library", "QElectroTech element")
_reg(".lppz .lpp", "ecad-project", "LibrePCB project")
_reg(".elk", "ecad-project", "EPLAN project")
_reg(".wdp", "ecad-project", "AutoCAD Electrical project")
_reg(".gbr .gtl .gbl .gto .gbo .gts .gbs .gtp .gbp .gko .gm1", "manufacturing", "Gerber file")
_reg(".drl .xln", "manufacturing", "Drill file")

# Mechanical design from other programs
_reg(".fcstd", "cad-part", "FreeCAD model")
_reg(".par", "cad-part", "Solid Edge part")
_reg(".psm", "cad-part", "Solid Edge sheet metal part")
_reg(".asm", "cad-assembly", "Assembly (Solid Edge or Creo)")
_reg(".dft", "cad-drawing", "Solid Edge draft")
_reg(".catpart", "cad-part", "CATIA part")
_reg(".catproduct", "cad-assembly", "CATIA product")
_reg(".catdrawing", "cad-drawing", "CATIA drawing")
_reg(".prt", "cad-part", "Part (Creo or NX)")
_reg(".drw", "cad-drawing", "Creo drawing")
_reg(".f3d", "cad-part", "Fusion design")
_reg(".f3z", "cad-assembly", "Fusion design with references")
_reg(".3dm", "cad-part", "Rhino model")
_reg(".skp", "cad-part", "SketchUp model")
_reg(".x_t .x_b", "cad-exchange", "Parasolid model")
_reg(".jt", "cad-exchange", "JT model")
_reg(".stl", "cad-exchange", "STL mesh")
_reg(".3mf", "cad-exchange", "3MF model")

GENERIC = FileType("other", "Other file", "generic")

_CREO_VERSION = re.compile(r"\.(prt|asm|drw|frm|sec|lay|mfg)\.\d+$", re.IGNORECASE)


def extension(path) -> str:
    """The extension that says what a file is, in lower case: ``.prt`` for Creo's ``bracket.prt.3`` too."""
    name = Path(path).name
    m = _CREO_VERSION.search(name)
    return "." + m.group(1).lower() if m else Path(name).suffix.lower()


def classify(path: Path) -> FileType:
    return _TYPES.get(extension(path), GENERIC)


_KICAD_HEADS = {".kicad_sch": (b"kicad_sch",), ".kicad_pcb": (b"kicad_pcb",), ".kicad_sym": (b"kicad_symbol_lib",),
                ".kicad_mod": (b"footprint", b"module")}
_KICAD_HEAD = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*\(\s*([a-z_]+)[\s)]")


def sniff_kicad(path: Path) -> bool:
    """Return True only if the file starts as KiCad 6 and later write it: an S-expression whose first word is the
    kind of file its extension says (``(kicad_sch`` for a schematic, and so on)."""
    path = Path(path)
    heads = _KICAD_HEADS.get(path.suffix.lower())
    if not heads:
        return False
    try:
        with open(path, "rb") as fh:
            m = _KICAD_HEAD.match(fh.read(4096))
    except OSError:
        return False
    return m is not None and m.group(1) in heads


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


def sniff_draftworx(path: Path) -> bool:
    """Return True only if the file's *content* starts as the documented DraftWorx drawing format does."""
    path = Path(path)
    try:
        if path.suffix.lower() != ".draftworx":
            return False
        with open(path, "rb") as fh:
            return _DRAFTWORX_HEAD.match(fh.read(4096)) is not None
    except OSError:
        return False
