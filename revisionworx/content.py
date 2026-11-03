# SPDX-License-Identifier: GPL-3.0-or-later
"""Content-addressed, immutable revision storage (ADR-011).

``.revisionworx/content/sha256/<aa>/<64 hex>`` holds raw bytes named by their SHA-256. Objects are
written once (exclusive publication) and never modified. Existing objects are re-hashed before
being reused, so a damaged object is detected instead of silently referenced.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import atomicio
from .workspace import Workspace, WorkspaceCorruptError

_HEX = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1024 * 1024


class ContentTooLarge(ValueError):
    """A content object exceeds the caller's read limit; nothing was returned."""


class SourceChangedError(OSError):
    """The source file changed while it was being copied; nothing was stored."""


@dataclass(frozen=True)
class StoredContent:
    sha256: str
    size: int
    source_mtime_ns: int
    reused: bool


def hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


class ContentStore:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.root = ws.meta_dir / "content" / "sha256"

    def path_for(self, sha256: str) -> Path:
        if not _HEX.match(sha256):
            raise ValueError(f"invalid SHA-256 {sha256!r}")
        return self.root / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def verify(self, sha256: str) -> bool:
        """Re-hash a stored object. False if it is missing or damaged."""
        path = self.path_for(sha256)
        if not path.is_file():
            return False
        return hash_file(path)[0] == sha256

    def store_file(self, source: Path) -> StoredContent:
        """Copy ``source`` into the store, hashing in the same single read.

        The object is durable (fsynced and published) before this returns. Raises
        :class:`SourceChangedError` if the source's size or mtime changed during the copy.
        """
        self.ws._require_writable()
        source = Path(source)
        before = os.stat(source)
        atomicio.make_dirs(self.root)
        tmp = self.root / f".incoming.{secrets.token_hex(8)}.tmp"
        h = hashlib.sha256()
        size = 0
        try:
            with open(source, "rb") as src, open(tmp, "xb") as dst:
                while True:
                    block = src.read(CHUNK)
                    if not block:
                        break
                    h.update(block)
                    dst.write(block)
                    size += len(block)
                dst.flush()
                os.fsync(dst.fileno())
            after = os.stat(source)
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
                raise SourceChangedError(f"{source} changed while it was being stored; save it, then try again")
            digest = h.hexdigest()
            final = self.path_for(digest)
            atomicio.make_dirs(final.parent)
            reused = False
            if final.exists():
                if hash_file(final)[0] != digest:
                    raise WorkspaceCorruptError(f"stored content object {digest} is damaged")
                reused = True
            else:
                try:
                    atomicio.move_exclusive(tmp, final)
                    atomicio._fsync_dir(final.parent)
                except FileExistsError:
                    if hash_file(final)[0] != digest:
                        raise WorkspaceCorruptError(f"stored content object {digest} is damaged") from None
                    reused = True
            return StoredContent(digest, size, after.st_mtime_ns, reused)
        finally:
            atomicio.remove_quietly(tmp)

    def read_verified(self, sha256: str, max_bytes: int, expected_size: int | None = None) -> bytes:
        """Return the exact bytes of an immutable content object, verified (ADR-018).

        The SHA-256 is computed over the very bytes returned, so a caller parsing them never relies on
        an earlier check of a different read. Refuses objects larger than ``max_bytes`` without reading
        them fully, and raises ``WorkspaceCorruptError`` on a missing object, a size mismatch or a hash
        mismatch. Read-only; never touches mutable working files."""
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        src = self.path_for(sha256)
        try:
            fh = open(src, "rb")
        except FileNotFoundError:
            raise WorkspaceCorruptError(f"content object {sha256} is missing from the store") from None
        with fh:
            size = os.fstat(fh.fileno()).st_size
            if expected_size is not None and size != expected_size:
                raise WorkspaceCorruptError(f"content object {sha256} has size {size}, expected {expected_size}")
            if size > max_bytes:
                raise ContentTooLarge(f"content object {sha256} is {size} bytes, over the {max_bytes}-byte limit")
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ContentTooLarge(f"content object {sha256} exceeds the {max_bytes}-byte limit")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise WorkspaceCorruptError(f"content object {sha256} failed SHA-256 verification")
        return data

    def export(self, sha256: str, dest: Path, overwrite: bool = False) -> Path:
        """Write a stored object to ``dest`` (new file unless ``overwrite``), then verify it."""
        src = self.path_for(sha256)
        if not src.is_file():
            raise WorkspaceCorruptError(f"content object {sha256} is missing from the store")
        dest = Path(dest)
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(6)}.tmp")
        try:
            with open(src, "rb") as fin, open(tmp, "xb") as fout:
                shutil.copyfileobj(fin, fout, CHUNK)
                fout.flush()
                os.fsync(fout.fileno())
            if hash_file(tmp)[0] != sha256:
                raise WorkspaceCorruptError(f"content object {sha256} failed verification on export")
            if overwrite:
                atomicio.retry_sharing(lambda: os.replace(tmp, dest))
            else:  # exclusive, like atomicio.publish_exclusive: never replaces an existing file
                try:
                    atomicio.move_exclusive(tmp, dest)
                except FileExistsError:
                    raise FileExistsError(f"{dest} already exists") from None
        finally:
            atomicio.remove_quietly(tmp)
        return dest
