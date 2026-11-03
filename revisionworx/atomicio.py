# RevisionWorx - lightweight engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, atomic file primitives.

Guarantees provided here are the guarantees of the *local* operating system and filesystem:

* ``atomic_write_bytes`` - readers see either the old file or the complete new file, never a torn
  file. Content is fsynced before the rename and the directory is fsynced afterwards (POSIX).
* ``publish_exclusive`` - a fully written file appears under its final name only if no file of that
  name exists. Two writers racing for the same name cannot both succeed. POSIX uses ``link()``
  (fails with EEXIST); Windows uses ``rename()``, which refuses to replace an existing file.

These semantics have **not** been verified on SMB or NFS shares. Multi-writer use of a shared
workspace stays disabled until the Phase 3 verification gate (see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, TypeVar

IS_WINDOWS = os.name == "nt"
SHARING_RETRIES = 250
SHARING_DELAY = 0.02
_T = TypeVar("_T")


def retry_sharing(op: Callable[[], _T]) -> _T:
    """Run ``op``, retrying transient Windows sharing violations with a bound (ADR-016, issue #10).

    Another program that has a file open (a virus scanner, a backup tool, an editor preview) makes a rename
    or delete fail with WinError 32 until it lets go. Elsewhere this never applies, and any other error, or
    the last failure, is raised to the caller."""
    for attempt in range(SHARING_RETRIES if IS_WINDOWS else 1):
        try:
            return op()
        except PermissionError:
            if attempt == SHARING_RETRIES - 1:
                raise
            time.sleep(SHARING_DELAY)
    return op()


def _open_sharing_delete(path: Path) -> BinaryIO:
    """Open for reading on Windows without blocking a rename or delete of the file.

    Python's open() does not grant FILE_SHARE_DELETE, so every reader of writer.lock made a concurrent
    release fail with WinError 32. Under load that starved the release until it gave up (issue #10, seen on
    real Windows in Core CI). The standard library has no other way to request this share mode."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.restype = wintypes.HANDLE
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE]
    generic_read, share_all, open_existing, attribute_normal = 0x80000000, 0x7, 3, 0x80
    handle = create(str(path), generic_read, share_all, None, open_existing, attribute_normal, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")


def _open_for_read(path: Path) -> BinaryIO:
    return _open_sharing_delete(path) if IS_WINDOWS else open(path, "rb")


class ExclusiveCreateError(FileExistsError):
    """The target name already exists; nothing was published."""


def _fsync_dir(directory: Path) -> None:
    if IS_WINDOWS:  # Directory handles cannot be fsynced on Windows; NTFS journals metadata.
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_temp(directory: Path, data: bytes, stem: str) -> Path:
    tmp = directory / f".{stem}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    return tmp


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` atomically with ``data`` (create if missing)."""
    path = Path(path)
    tmp = _write_temp(path.parent, data, path.name)
    try:
        retry_sharing(lambda: os.replace(tmp, path))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def publish_exclusive(path: Path, data: bytes) -> None:
    """Publish complete ``data`` at ``path`` only if ``path`` does not yet exist.

    Raises :class:`ExclusiveCreateError` if the name is taken. Never overwrites.
    """
    path = Path(path)
    tmp = _write_temp(path.parent, data, path.name)
    try:
        if IS_WINDOWS:
            try:
                os.rename(tmp, path)  # Windows rename refuses an existing destination.
            except FileExistsError as exc:
                raise ExclusiveCreateError(str(path)) from exc
        else:
            try:
                os.link(tmp, path)
            except FileExistsError as exc:
                raise ExclusiveCreateError(str(path)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    _fsync_dir(path.parent)


def dumps_json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, dumps_json(obj))


def read_json(path: Path) -> Any:
    def read() -> bytes:
        with _open_for_read(path) as fh:
            return fh.read()
    return json.loads(retry_sharing(read).decode("utf-8"))


ORPHAN_TEMP_MIN_AGE_SECONDS = 3600


def cleanup_temp_files(directory: Path, min_age_seconds: float = ORPHAN_TEMP_MIN_AGE_SECONDS) -> list[Path]:
    """Remove orphaned ``.*.tmp`` files left by an interrupted write. Returns what was removed.

    Only temp files older than ``min_age_seconds`` are removed: a young temp file may belong to
    another client that is writing right now (e.g. racing for the writer lock), and deleting it
    would break that client's publish. Regression: test_writer_lock_race_between_processes.
    """
    import time

    removed = []
    if not directory.is_dir():
        return removed
    cutoff = time.time() - min_age_seconds
    for entry in directory.iterdir():
        if entry.name.startswith(".") and entry.name.endswith(".tmp") and entry.is_file():
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
            except FileNotFoundError:
                continue
            entry.unlink(missing_ok=True)
            removed.append(entry)
    return removed
