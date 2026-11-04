# SPDX-License-Identifier: GPL-3.0-or-later
"""Static check that every relative import in the GUI package (including imports inside functions,
which only run when a widget action fires) names a real module and attribute. Runs without PySide6.
Regression: ``from ..workspace import`` inside gui/qt/documents.py broke every document action."""
import ast
import importlib
import importlib.util
from pathlib import Path

import revisionworx

PKG_ROOT = Path(revisionworx.__file__).parent


def _relative_imports():
    for path in sorted((PKG_ROOT / "gui").rglob("*.py")):
        module = "revisionworx." + path.relative_to(PKG_ROOT).with_suffix("").as_posix().replace("/", ".")
        package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                target = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                yield path.name, node.lineno, target, [a.name for a in node.names]


def test_all_relative_gui_imports_resolve():
    bad = []
    for fname, line, target, names in _relative_imports():
        if target.startswith("revisionworx.gui.qt"):
            spec = importlib.util.find_spec(target)  # Qt modules: existence only (PySide6 may be absent)
            if spec is None:
                bad.append(f"{fname}:{line} -> {target} (no such module)")
            continue
        try:
            mod = importlib.import_module(target)
        except ImportError as exc:
            bad.append(f"{fname}:{line} -> {target} ({exc})")
            continue
        for n in names:
            if not hasattr(mod, n) and importlib.util.find_spec(f"{target}.{n}") is None:
                bad.append(f"{fname}:{line} -> {target}.{n} (no such name)")
    assert not bad, "\n".join(bad)
