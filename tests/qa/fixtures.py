# SPDX-License-Identifier: GPL-3.0-or-later
"""Generated, non-confidential engineering folder layouts for integrity testing."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

STRUCTURES = {
    "customer": ("Customer A/Project 1001", "Customer A/Project 1002", "Customer B/Project 2001"),
    "year": ("2025/Job-001", "2026/Job-002"),
    "product": ("Hydraulic Systems/Assembly A", "Electrical Systems/Control Panel B"),
}

def fingerprint(root: Path) -> dict:
    result = {}
    for directory, _, files in os.walk(root, followlinks=False):
        current = Path(directory)
        result[current.relative_to(root).as_posix() or "."] = ("directory",)
        for name in files:
            file = current / name
            rel = file.relative_to(root).as_posix()
            if file.is_symlink():
                result[rel] = ("symlink", os.readlink(file))
            else:
                stat = file.stat()
                result[rel] = ("file", stat.st_size, stat.st_mtime_ns, hashlib.sha256(file.read_bytes()).hexdigest())
    return result

def populate(root: Path, layout: str) -> dict:
    if layout not in STRUCTURES:
        raise ValueError(layout)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    folders = []
    for idx, relative in enumerate(STRUCTURES[layout]):
        folder = root / relative
        (folder / "Models" / "Subassembly").mkdir(parents=True)
        (folder / "Drawings").mkdir()
        (folder / "BOM").mkdir()
        files = {
            "Models/shared-bracket.ipt": b"synthetic CAD placeholder",
            "Models/Subassembly/valve block.sldprt": b"synthetic CAD placeholder",
            "Drawings/assembly A.idw": b"synthetic drawing placeholder",
            "Drawings/design #1 (final).pdf": b"%PDF-1.4 synthetic placeholder",
            "BOM/items.csv": b"part,quantity\nPN-0001,2\n",
            "Models/café component.step": b"ISO-10303-21;",
            "Models/references.txt": b"REF: ../MISSING-COMPONENT.ipt\n",
        }
        for name, data in files.items():
            (folder / name).write_bytes(data + str(idx).encode())
        folders.append(folder)
    return {"root": root, "projects": folders, "before": fingerprint(root),
            "missing": "MISSING-COMPONENT.ipt", "duplicate": "shared-bracket.ipt"}
