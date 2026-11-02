#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental cross-platform PyInstaller onedir builder, NOT an installer.

Source must be a separate pinned RevisionWorx checkout. Output must be new
and outside the source checkout. No existing engineering workspace is opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REQS = HERE / "requirements-build.txt"


def source_revision(source: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()


def validate_locations(source: Path, output: Path) -> None:
    if not (source / "pyproject.toml").is_file():
        raise ValueError("source must be a RevisionWorx checkout with pyproject.toml")
    if not (source / "revisionworx" / "gui" / "app.py").is_file():
        raise ValueError("source is missing the PySide6 desktop entrypoint")
    if source == output or source in output.parents:
        raise ValueError("output must be outside the authoritative source checkout")
    if output.exists():
        raise ValueError("output exists; refusing to overwrite an earlier build")


def run(*args: str, cwd: Path | None = None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(source: Path, output: Path, python: str, expected_sha: str | None) -> None:
    source, output = source.resolve(), output.resolve()
    validate_locations(source, output)
    revision = source_revision(source)
    if expected_sha and revision != expected_sha:
        raise ValueError(f"source SHA mismatch: wanted {expected_sha}, got {revision}")

    output.mkdir(parents=True, exist_ok=False)
    env_dir = output / "build-env"
    run(python, "-m", "venv", str(env_dir))
    py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    pip = [str(py), "-m", "pip"]
    run(*pip, "install", "--disable-pip-version-check", "--no-input", "-r", str(REQS))
    run(*pip, "install", "--disable-pip-version-check", "--no-input",
        "--no-build-isolation", "--no-deps", "-e", str(source))

    version = subprocess.check_output(
        [str(py), "-c", "import revisionworx; print(revisionworx.__version__)"],
        text=True, cwd=source,
    ).strip()
    (output / "dependencies.txt").write_text(
        subprocess.check_output([*pip, "freeze"], text=True), encoding="utf-8"
    )

    dist = output / "dist"
    builds = (
        ("RevisionWorx", HERE / "gui_entry.py", ["--windowed", "--collect-submodules", "revisionworx"]),
        ("RevisionWorxCLI", HERE / "cli_entry.py", ["--console"]),
    )
    for name, entry, flags in builds:
        run(
            str(py), "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onedir", "--name", name, "--distpath", str(dist),
            "--workpath", str(output / "work" / name),
            "--specpath", str(output / "spec"), "--paths", str(source),
            *flags, str(entry), cwd=source,
        )

    ext = ".exe" if os.name == "nt" else ""
    gui = dist / "RevisionWorx" / ("RevisionWorx" + ext)
    cli = dist / "RevisionWorxCLI" / ("RevisionWorxCLI" + ext)
    if not gui.is_file() or not cli.is_file():
        raise RuntimeError("PyInstaller did not create both expected onedir executables")
    record = {
        "source_sha": revision,
        "source_version": version,
        "platform": platform.platform(),
        "build_python": sys.version,
        "requirements": REQS.read_text(encoding="utf-8").splitlines(),
        "dependencies": "dependencies.txt",
        "executables": {
            "gui": {"path": str(gui.relative_to(output)), "sha256": sha256(gui)},
            "cli": {"path": str(cli.relative_to(output)), "sha256": sha256(cli)},
        },
        "status": "experimental-only; not an installer or release",
    }
    (output / "provenance.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    run(sys.executable, str(HERE / "smoke_installed.py"),
        "--gui", str(gui), "--cli", str(cli))
    run(sys.executable, str(HERE / "smoke_workspace.py"),
        "--gui", str(gui), "--cli", str(cli))
    print("Experimental packaging + disposable copied-workspace smokes passed (no installer/release).")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--expected-sha", help="refuse an unexpected source commit")
    a = p.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        p.error("prototype packaging is currently qualified only for Python 3.11")
    try:
        build(a.source, a.output, a.python, a.expected_sha)
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"PACKAGING FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
