#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only audit of a test-only wheel produced by the four-source Git merge.

Checks which real application packages and entrypoints a wheel contains, rejects
unexpected mandatory third-party dependencies, writes an SHA-256 receipt.
This is NOT a signed installer, reproducible-build claim, or release approval.
"""
from __future__ import annotations

import argparse
import hashlib
from email.parser import Parser
import json
from pathlib import Path
import zipfile

REQUIRED = (
    "revisionworx/__init__.py",
    "revisionworx/cli.py",
    "revisionworx/workspace.py",
    "revisionworx/gui/qt/mainwindow.py",
    "revisionworx/bom/__init__.py",
    "revisionworx/changes/__init__.py",
)
ENTRYPOINTS = ("revisionworx = revisionworx.cli:main",
               "revisionworx-gui = revisionworx.gui.app:main")


def audit(wheel_dir: Path, receipt: Path) -> dict:
    wheel_dir, receipt = wheel_dir.resolve(), receipt.resolve()
    files = list(wheel_dir.glob("*.whl"))
    if len(files) != 1:
        raise ValueError(f"expected exactly one wheel, found {len(files)}")
    if receipt.exists():
        raise ValueError("receipt already exists; will not overwrite")
    wheel = files[0]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(set(REQUIRED) - names)
        if missing:
            raise ValueError("required product package paths absent: " + ", ".join(missing))
        metadata_files = [x for x in names if x.endswith(".dist-info/METADATA")]
        entries_files = [x for x in names if x.endswith(".dist-info/entry_points.txt")]
        if len(metadata_files) != 1 or len(entries_files) != 1:
            raise ValueError("expected exactly one package METADATA and entry_points.txt")
        metadata = Parser().parsestr(archive.read(metadata_files[0]).decode("utf-8"))
        entrypoints = archive.read(entries_files[0]).decode("utf-8")
        if metadata.get("Name", "").lower().replace("_", "-") != "revisionworx":
            raise ValueError("wheel project name does not match RevisionWorx")
        license_name = metadata.get("License-Expression", metadata.get("License", ""))
        if "GPL-3.0-or-later" not in license_name and "GNU General Public License" not in license_name:
            raise ValueError("wheel metadata lacks declared GPL-3.0-or-later license")
        missing_commands = [x for x in ENTRYPOINTS if x not in entrypoints]
        if missing_commands:
            raise ValueError("missing user-facing command entrypoints: " + str(missing_commands))
        mandatory = [
            value for value in metadata.get_all("Requires-Dist", [])
            if ";" not in value  # extra-qualified GUI/dev dependencies are not mandatory
        ]
        if mandatory:
            raise ValueError("stdlib-only base app gained mandatory runtime dependencies: " + str(mandatory))
        native = [x for x in names if x.lower().endswith((".pyd", ".so", ".dll", ".dylib"))]
        if native:
            raise ValueError("unexpected native binary embedded in base wheel: " + str(native[:8]))
        counts = {section: len([n for n in names if n.startswith(f"revisionworx/{section}/")])
                  for section in ("gui", "bom", "changes")}
        if not all(counts.values()):
            raise ValueError("one of the three integrated product packages is empty")
    data = {
        "purpose": "TEST-ONLY wheel contents/integrity inspection, no release authorization",
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "file_count": len(names),
        "package_counts": counts,
        "required_paths": list(REQUIRED),
        "entrypoints": list(ENTRYPOINTS),
        "declared_license": license_name,
        "mandatory_dependencies": mandatory,
        "native_binaries": native,
        "signed_installer_tested": False,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("TEST_ONLY_PRODUCT_WHEEL_AUDIT " + json.dumps(data, sort_keys=True), flush=True)
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wheels", required=True, type=Path)
    p.add_argument("--receipt", required=True, type=Path)
    a = p.parse_args()
    audit(a.wheels, a.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
