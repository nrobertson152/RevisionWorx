# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, read-only scanning of an explicitly selected folder.

Only ever called on a scope the user chose (Adopt Existing Project). Never follows symbolic links or
junctions, never writes, honours exclusions, a file limit and cancellation, and reports exactly what
it did not cover.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import filetypes, paths


class ScanCancelled(Exception):
    pass


@dataclass
class ScannedFile:
    rel_path: str
    size: int
    mtime_ns: int
    category: str
    sha256: str | None = None
    verified_format: str | None = None

    def as_json(self) -> dict:
        d = {"path": self.rel_path, "size": self.size, "mtime_ns": self.mtime_ns, "category": self.category}
        if self.sha256:
            d["sha256"] = self.sha256
        if self.verified_format:
            d["verified_format"] = self.verified_format
        return d


@dataclass
class ScanReport:
    scope: str
    files: list[ScannedFile] = field(default_factory=list)
    directories: int = 0
    excluded: list[str] = field(default_factory=list)
    links_skipped: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    name_problems: list[str] = field(default_factory=list)
    case_collisions: list[list[str]] = field(default_factory=list)
    truncated: bool = False
    hashed: bool = False

    @property
    def complete(self) -> bool:
        return not (self.truncated or self.unreadable)

    def counts_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.files:
            counts[f.category] = counts.get(f.category, 0) + 1
        return dict(sorted(counts.items()))

    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def warnings(self) -> list[str]:
        w = []
        if self.truncated:
            w.append("Scan stopped at the file limit; the inventory is incomplete.")
        if self.unreadable:
            w.append(f"{len(self.unreadable)} item(s) could not be read (permissions?) and are not covered.")
        if self.links_skipped:
            w.append(f"{len(self.links_skipped)} symbolic link(s)/junction(s) were not followed.")
        if self.case_collisions:
            w.append(f"{len(self.case_collisions)} name(s) differ only by letter case; they collide on Windows/SMB.")
        if self.name_problems:
            w.append(f"{len(self.name_problems)} name(s) are not portable between Windows and Linux.")
        if self.excluded:
            w.append(f"{len(self.excluded)} item(s) matched exclusion patterns and were skipped.")
        return w

    def summary(self) -> dict:
        return {"scope": self.scope, "files": len(self.files), "directories": self.directories,
                "bytes": self.total_bytes(), "by_category": self.counts_by_category(),
                "hashed": self.hashed, "complete": self.complete, "truncated": self.truncated,
                "excluded": len(self.excluded), "links_skipped": self.links_skipped[:200],
                "unreadable": self.unreadable[:200], "name_problems": self.name_problems[:200],
                "case_collisions": self.case_collisions[:200], "warnings": self.warnings()}


def _excluded(name: str, patterns: list[str]) -> bool:
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, p.casefold()) for p in patterns)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _is_link_or_junction(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+
    return bool(is_junction and is_junction())


def scan_scope(scope: Path, exclusions: list[str], max_files: int, hash_files: bool = False,
               progress: Callable[[int, str], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> ScanReport:
    scope = Path(scope)
    if not scope.is_dir():
        raise NotADirectoryError(str(scope))
    report = ScanReport(scope=str(scope), hashed=hash_files)
    seen_case: dict[str, str] = {}
    stack: list[tuple[Path, str]] = [(scope, "")]
    while stack:
        directory, rel_dir = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            report.unreadable.append(rel_dir or ".")
            continue
        report.directories += 1
        for entry in entries:
            if cancelled and cancelled():
                raise ScanCancelled()
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            if _excluded(entry.name, exclusions):
                report.excluded.append(rel)
                continue
            try:
                paths.validate_component(entry.name)
            except paths.PathValidationError as exc:
                report.name_problems.append(f"{rel}: {exc}")
            key = paths.case_key(rel)
            if key in seen_case:
                report.case_collisions.append([seen_case[key], rel])
            else:
                seen_case[key] = rel
            try:
                if _is_link_or_junction(entry):
                    report.links_skipped.append(rel)
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                report.unreadable.append(rel)
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append((Path(entry.path), rel))
                continue
            if not stat.S_ISREG(st.st_mode):
                report.links_skipped.append(rel)  # devices, fifos: not followed or read
                continue
            if len(report.files) >= max_files:
                report.truncated = True
                stack.clear()
                break
            ft = filetypes.classify(Path(entry.name))
            sf = ScannedFile(rel, st.st_size, st.st_mtime_ns, ft.category)
            if ft.category == "schematic" and filetypes.sniff_openschematix(Path(entry.path)):
                sf.verified_format = "openschematix"
            elif ft.category == "simulation" and filetypes.sniff_fluidworx(Path(entry.path)):
                sf.verified_format = "fluidworx"
            elif ft.category == "cad-drawing" and filetypes.sniff_draftworx(Path(entry.path)):
                sf.verified_format = "draftworx"
            elif filetypes.sniff_kicad(Path(entry.path)):
                sf.verified_format = "kicad"
            if hash_files:
                try:
                    sf.sha256 = sha256_file(Path(entry.path))
                except OSError:
                    report.unreadable.append(rel)
                    continue
            report.files.append(sf)
            if progress and len(report.files) % 200 == 0:
                progress(len(report.files), rel)
    report.files.sort(key=lambda f: f.rel_path)
    if progress:
        progress(len(report.files), "")
    return report
