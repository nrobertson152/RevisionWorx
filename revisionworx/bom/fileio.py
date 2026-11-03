# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe export writes: the destination is either untouched or completely replaced, never partial.

The content is written to a temporary file in the destination folder, flushed and fsynced, then renamed
over the destination (``overwrite=True``) or published only if the name is free (``overwrite=False``:
``link`` on POSIX, ``rename`` on Windows, both refuse an existing name). Any failure removes the
temporary file and leaves the destination as it was. A process killed between the two steps can leave a
hidden ``.<name>.<random>.bomtmp`` file behind, never a damaged destination.

These are guarantees of the local filesystem. This module mirrors ``revisionworx.atomicio`` on purpose
instead of importing it: ``atomicio`` is internal to the core (not in docs/CORE_API.md), and this
package must not depend on, or change, the core's authority code.

Exports into a ``.revisionworx`` metadata folder are refused: that folder belongs to the core and is
written only through its journal.
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from .errors import BomSourceError, ExportError

IS_WINDOWS = os.name == "nt"
METADATA_DIR = ".revisionworx"
_RETRIES = 250 if IS_WINDOWS else 1
_DELAY = 0.02


def check_destination(path: Path | str) -> Path:
    """Refuse destinations that are directories, symbolic links, or inside a ``.revisionworx`` folder."""
    p = Path(path)
    if not p.name or p.name in (".", ".."):
        raise ExportError(f"not a file name: {p}")
    try:
        resolved = p.parent.resolve(strict=False) / p.name
    except OSError as exc:  # pragma: no cover - resolve rarely fails
        raise ExportError(f"cannot resolve {p}: {exc}") from None
    parts = {part.casefold() for part in resolved.parts} | {part.casefold() for part in p.parts}
    if METADATA_DIR in parts:
        raise ExportError(f"refusing to write into the RevisionWorx metadata folder: {p}")
    if p.is_symlink():
        raise ExportError(f"refusing to write through a symbolic link: {p}")
    if p.is_dir():
        raise ExportError(f"destination is a folder: {p}")
    if not p.parent.is_dir():
        raise ExportError(f"destination folder does not exist: {p.parent}")
    return p


def _write_temp(directory: Path, name: str, data: bytes) -> Path:
    tmp = directory / f".{name}.{secrets.token_hex(6)}.bomtmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _unlink_quietly(tmp)
        raise
    os.close(fd)
    return tmp


def _unlink_quietly(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _fsync_dir(directory: Path) -> None:
    if IS_WINDOWS:
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - some filesystems refuse directory fsync
        pass
    finally:
        os.close(fd)


def _retry(op):
    for attempt in range(_RETRIES):
        try:
            return op()
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(_DELAY)


def write_file(path: Path | str, data: bytes, *, overwrite: bool = False) -> Path:
    """Write ``data`` to ``path`` all-or-nothing. Raises :class:`ExportError` (destination unchanged)."""
    p = check_destination(path)
    if not overwrite and p.exists():
        raise ExportError(f"{p} already exists; choose another name or allow overwrite")
    try:
        tmp = _write_temp(p.parent, p.name, data)
    except OSError as exc:
        raise ExportError(f"could not write {p}: {exc.strerror or exc}") from exc
    try:
        if overwrite:
            _retry(lambda: os.replace(tmp, p))
        elif IS_WINDOWS:
            try:
                os.rename(tmp, p)
            except FileExistsError:
                raise ExportError(f"{p} already exists; choose another name or allow overwrite") from None
        else:
            try:
                os.link(tmp, p)
            except FileExistsError:
                raise ExportError(f"{p} already exists; choose another name or allow overwrite") from None
    except ExportError:
        _unlink_quietly(tmp)
        raise
    except OSError as exc:
        _unlink_quietly(tmp)
        raise ExportError(f"could not write {p}: {exc.strerror or exc}") from exc
    except BaseException:
        _unlink_quietly(tmp)
        raise
    if not overwrite and not IS_WINDOWS:
        _unlink_quietly(tmp)
    _fsync_dir(p.parent)
    return p


def read_file(path: Path | str, max_bytes: int) -> bytes:
    """Read at most ``max_bytes``; refuse larger files without reading them fully."""
    p = Path(path)
    with open(p, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BomSourceError(f"{p} is larger than the {max_bytes}-byte limit")
    return data
