#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline, copy-only backup and disposable restore rehearsal.

Operator MUST close all writers first. Two source scans detect many changes,
but cannot prove an atomic snapshot or power-loss durability. No repair,
no overwrite, no network access, and no modification of the source workspace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys

MANIFEST = "revisionworx-backup-manifest.json"
VERSION = 1
CHUNK = 1024 * 1024


class BackupError(Exception):
    pass


def digest(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            size += len(block)
            h.update(block)
    return size, h.hexdigest()


def files_under(root: Path, *, allow_manifest: bool = False) -> dict[str, tuple[int, str]]:
    if not root.is_dir() or root.is_symlink():
        raise BackupError("source must be a real directory")
    found = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        # A file-only manifest cannot faithfully restore empty directories.
        # Refuse them rather than silently producing an incomplete workspace.
        if parent != root and not dirs and not files:
            raise BackupError("empty directory cannot be preserved: " + str(parent))
        for name in dirs + files:
            item = parent / name
            if item.is_symlink():
                raise BackupError("symlink refused: " + str(item))
        for name in files:
            item = parent / name
            if not item.is_file():
                raise BackupError("non-regular file refused: " + str(item))
            rel = item.relative_to(root).as_posix()
            if rel == MANIFEST:
                if allow_manifest:
                    continue
                raise BackupError("reserved manifest name in source")
            found[rel] = digest(item)
    # Reject names that alias on common case-insensitive Windows filesystems.\n    folded = {}\n    for name in found:\n        key = name.casefold()\n        if key in folded and folded[key] != name:\n            raise BackupError("case-insensitive path collision: " + folded[key] + " / " + name)\n        folded[key] = name\n    return dict(sorted(found.items()))


def safe_name(name: object) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise BackupError("invalid manifest path")
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ("", ".", "..") for x in name.split("/")):
        raise BackupError("unsafe manifest path")
    if p.as_posix() != name or name == MANIFEST:
        raise BackupError("noncanonical manifest path")
    return name


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON objects rather than silently accepting the last key."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("duplicate manifest JSON key: " + key)
        result[key] = value
    return result


def load_manifest(root: Path) -> dict[str, tuple[int, str]]:
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (OSError, ValueError) as exc:
        raise BackupError("manifest unreadable or malformed") from exc
    if not isinstance(data, dict) or set(data) != {"version", "files"} or data["version"] != VERSION:
        raise BackupError("unsupported manifest version/schema")
    if not isinstance(data["files"], dict):
        raise BackupError("invalid file table")
    result = {}
    for raw, entry in data["files"].items():
        name = safe_name(raw)
        if not isinstance(entry, dict) or set(entry) != {"size", "sha256"}:
            raise BackupError("invalid manifest entry")
        size, sha = entry["size"], entry["sha256"]
        if type(size) is not int or size < 0 or not isinstance(sha, str) or len(sha) != 64:
            raise BackupError("invalid manifest digest/size")
        try:
            bytes.fromhex(sha)
        except ValueError as exc:
            raise BackupError("invalid hex digest") from exc
        result[name] = (size, sha)
    return result


def verify(root: Path) -> dict[str, tuple[int, str]]:
    expected = load_manifest(root)
    actual = files_under(root, allow_manifest=True)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(k for k in set(actual) & set(expected) if actual[k] != expected[k])
        raise BackupError(f"verification failed: missing={missing}, extra={extra}, changed={changed}")
    return expected


def require_new_destination(src: Path, dst: Path) -> None:
    src, dst = src.resolve(), dst.resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        raise BackupError("source/destination may not contain one another")
    if dst.exists() or dst.is_symlink():
        raise BackupError("destination already exists; overwrite refused")


def backup(src: Path, dst: Path) -> int:
    require_new_destination(src, dst)
    first = files_under(src)
    dst.mkdir(parents=True, exist_ok=False)
    try:
        for name in first:
            target = dst / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / name, target, follow_symlinks=False)
        second = files_under(src)
        if first != second:
            raise BackupError("source changed during copy; close writers and retry")
        copied = files_under(dst)
        if copied != first:
            raise BackupError("copy digest mismatch")
        manifest = {"version": VERSION, "files": {
            name: {"size": size, "sha256": sha} for name, (size, sha) in first.items()
        }}
        (dst / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verify(dst)
        return len(first)
    except BaseException:
        # Keep failed copy for forensic review; never silently delete evidence.
        raise


def restore(src: Path, dst: Path) -> int:
    require_new_destination(src, dst)
    expected = verify(src)
    dst.mkdir(parents=True, exist_ok=False)
    for name in expected:
        target = dst / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / name, target, follow_symlinks=False)
    if files_under(dst) != expected:
        raise BackupError("restored files failed digest verification")
    return len(expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "verify", "restore"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", nargs="?", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "verify":
            if args.destination is not None:
                raise BackupError("verify accepts only a source")
            count = len(verify(args.source))
        else:
            if args.destination is None:
                raise BackupError("destination required")
            count = backup(args.source, args.destination) if args.action == "backup" else restore(args.source, args.destination)
        print(f"{args.action}: verified {count} files")
        return 0
    except (BackupError, OSError) as exc:
        print(f"{args.action}: FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
