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

import errno
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, TypeVar

IS_WINDOWS = os.name == "nt"
RETRY_SHARING = IS_WINDOWS  # only Windows has transient sharing violations; elsewhere they are real denials
SHARING_RETRIES = 250       # about 5 s: long enough for a virus scanner or backup tool to let go of a file
SHARING_DELAY = 0.02
_T = TypeVar("_T")
_kernel32 = None


def retry_sharing(op: Callable[[], _T]) -> _T:
    """Run ``op``, retrying transient Windows sharing violations with a bound (ADR-016, issue #10).

    Another program that has a file open (a virus scanner, a backup tool, an editor preview) makes a rename
    or delete fail with WinError 32 until it lets go. Elsewhere this never applies, and any other error, or
    the last failure, is raised to the caller."""
    attempts = SHARING_RETRIES if RETRY_SHARING else 1
    for attempt in range(attempts):
        try:
            return op()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(SHARING_DELAY)
    raise AssertionError("unreachable")


def _open_sharing_delete(path: Path) -> BinaryIO:
    """Open for reading on Windows without blocking a rename or delete of the file.

    Python's open() does not grant FILE_SHARE_DELETE, so every reader of writer.lock made a concurrent
    release fail with WinError 32. Under load that starved the release until it gave up (issue #10, seen on
    real Windows in Core CI). The standard library has no other way to request this share mode."""
    global _kernel32
    import ctypes
    import msvcrt
    from ctypes import wintypes
    if _kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        _kernel32 = kernel32
    generic_read, share_all, open_existing, attribute_normal = 0x80000000, 0x7, 3, 0x80
    handle = _kernel32.CreateFileW(str(path), generic_read, share_all, None, open_existing, attribute_normal, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")


def _open_for_read(path: Path) -> BinaryIO:
    return _open_sharing_delete(path) if IS_WINDOWS else open(path, "rb")


def remove_quietly(path: Path) -> None:
    """Remove a temp file we no longer need. Failing to is never worth reporting: an old temp file is
    harmless and later cleanup takes it, while raising here would hide the result of the real work."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def move_exclusive(src: Path, dst: Path) -> None:
    """Give ``src`` the name ``dst`` only if nothing has that name; FileExistsError otherwise. Never
    clobbers. POSIX uses link() then unlink(), so a crash can leave both names; Windows uses rename()."""
    if IS_WINDOWS:
        retry_sharing(lambda: os.rename(src, dst))
    else:
        os.link(src, dst)
        remove_quietly(src)


class ExclusiveCreateError(FileExistsError):
    """The target name already exists; nothing was published."""


def _fsync_dir(directory: Path) -> None:
    if IS_WINDOWS:  # Directory handles cannot be fsynced on Windows; NTFS journals metadata.
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS):  # some network filesystems can't sync a folder at all
            raise
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


def make_dirs(path: Path) -> None:
    """Create a folder and its missing parents, syncing each new entry into its parent. Otherwise a synced file inside
    can vanish with its folder on power loss, after a journal entry has already been written that refers to it."""
    missing, current = [], Path(path)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for folder in reversed(missing):
        try:
            folder.mkdir()
        except FileExistsError:
            pass
        _fsync_dir(folder.parent)


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
        move_exclusive(tmp, path)
    except FileExistsError as exc:
        raise ExclusiveCreateError(str(path)) from exc
    finally:
        remove_quietly(tmp)
    _fsync_dir(path.parent)


def dumps_json(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, dumps_json(obj))


def read_json(path: Path) -> Any:
    """Parse a JSON file. ValueError means the file is there and is not usable JSON, whatever the kind of damage: bad
    bytes, nesting too deep to parse, or a folder sitting at that name. Callers treat all of them alike, as damage."""
    try:
        raw = read_bytes(path)
    except OSError:
        if Path(path).is_dir():
            raise ValueError(f"{path} is a folder, not a file") from None
        raise
    try:
        return json.loads(raw.decode("utf-8"))
    except RecursionError:
        raise ValueError(f"{path} is nested too deeply to read") from None


def read_json_object(path: Path) -> dict:
    """Like :func:`read_json`, for files that must hold a JSON object. Anything else is ValueError, so damage never
    reaches a caller as an AttributeError on a list or a null."""
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return value


def read_bytes(path: Path) -> bytes:
    """Read a whole file without blocking a rename of it, as :func:`read_json` does."""
    def read() -> bytes:
        with _open_for_read(path) as fh:
            return fh.read()
    return retry_sharing(read)


ORPHAN_TEMP_MIN_AGE_SECONDS = 3600


def cleanup_temp_files(directory: Path, min_age_seconds: float = ORPHAN_TEMP_MIN_AGE_SECONDS) -> list[Path]:
    """Remove orphaned ``.*.tmp`` files left by an interrupted write. Returns what was removed.

    Only temp files older than ``min_age_seconds`` are removed: a young temp file may belong to
    another client that is writing right now (e.g. racing for the writer lock), and deleting it
    would break that client's publish. Regression: test_writer_lock_race_between_processes.
    """
    removed = []
    if not directory.is_dir():
        return removed
    cutoff = time.time() - min_age_seconds
    for entry in directory.iterdir():
        if entry.name.startswith(".") and entry.name.endswith(".tmp") and entry.is_file():
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                entry.unlink(missing_ok=True)
            except OSError:      # cleanup is a courtesy: it must never block opening a workspace
                continue
            removed.append(entry)
    return removed
