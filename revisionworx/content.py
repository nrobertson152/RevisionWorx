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
import stat
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


def _signature(st: os.stat_result) -> tuple:
    """What a writer cannot change without us noticing. File IDs of 0 (some network filesystems) compare
    equal and so add nothing; ctime is omitted on Windows, where it is the creation time."""
    sig = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    return sig if os.name == "nt" else sig + (st.st_ctime_ns,)


def _same_file(pinned: os.stat_result, at_path: os.stat_result) -> bool:
    return stat.S_ISREG(at_path.st_mode) and _signature(pinned) == _signature(at_path)


def _hash_pinned(path: Path, expected: os.stat_result) -> str:
    """Second, independent read: the SHA-256 of the file ``path`` names, which must still be ``expected``
    before and after the read; any difference raises SourceChangedError."""
    h = hashlib.sha256()
    with atomicio._open_for_read(path) as fh:
        if _signature(os.fstat(fh.fileno())) != _signature(expected):
            raise SourceChangedError(f"{path} changed while it was being stored; save it, then try again")
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
        if _signature(os.fstat(fh.fileno())) != _signature(expected):
            raise SourceChangedError(f"{path} changed while it was being stored; save it, then try again")
    if not _same_file(expected, os.stat(path, follow_symlinks=False)):
        raise SourceChangedError(f"{path} was replaced while it was being stored; save it, then try again")
    return h.hexdigest()


def hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with atomicio._open_for_read(path) as fh:    # a CAD program saving by rename must not fail because of us
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
        """Copy ``source`` into the store and return what was captured.

        Consistency guarantee (docs/CORE_INVARIANTS.md, C1-C4): the stored bytes are the bytes of one
        regular file that ``source`` named, unchanged, from before the copy began until after a second,
        independent read of it finished, and both reads hashed to the stored SHA-256. Concretely:

        * the file is pinned by its open handle; the path must name that same file (device and file
          ID where the platform reports them) before the copy, after it, and after the second read, so
          save-by-rename and replacement by a link are detected even at equal size and mtime;
        * size and mtime, and on POSIX ctime (which no writer can set back), must not change across
          the copy, so same-inode writes are detected even if mtime is preserved or restored;
        * the second read must produce the same SHA-256, which catches in-place writes that fall
          within one timestamp tick of a coarse filesystem.

        This is detection, not atomicity: RevisionWorx cannot stop another program writing. A writer
        that changes the file and restores identical bytes, size, mtime and ctime between the checks
        is not detectable, nor is one that writes the same bytes in both reads. Anything detected
        raises :class:`SourceChangedError` and nothing is stored or referenced. The object is durable
        (fsynced and published) before this returns."""
        self.ws._require_writable()
        source = Path(source)
        atomicio.make_dirs(self.root)
        tmp = self.root / f".incoming.{secrets.token_hex(8)}.tmp"
        h = hashlib.sha256()
        size = 0

        def changed(what: str) -> SourceChangedError:
            return SourceChangedError(f"{source} {what} while it was being stored; save it, then try again")

        try:
            with atomicio._open_for_read(source) as src:
                pinned = os.fstat(src.fileno())
                if not stat.S_ISREG(pinned.st_mode):
                    raise SourceChangedError(f"{source} is not a regular file")
                if not _same_file(pinned, os.stat(source, follow_symlinks=False)):
                    raise changed("was replaced or turned into a link")
                with open(tmp, "xb") as dst:
                    while True:
                        block = src.read(CHUNK)
                        if not block:
                            break
                        h.update(block)
                        dst.write(block)
                        size += len(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                after = os.fstat(src.fileno())
            if _signature(pinned) != _signature(after) or size != after.st_size:
                raise changed("was modified")
            if not _same_file(after, os.stat(source, follow_symlinks=False)):
                raise changed("was replaced (saved by rename?)")
            digest = h.hexdigest()
            if _hash_pinned(source, after) != digest:
                raise changed("was modified")
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
