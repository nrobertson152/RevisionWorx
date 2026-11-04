# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-platform path validation and normalisation.

Projects are stored as ``(root_id, relative POSIX path)`` so Windows and Linux clients that mount
the same share at different locations agree on identity. Relative paths are validated against both
platforms' rules because a share is often used by both.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path, PurePosixPath

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
WINDOWS_FORBIDDEN_CHARS = set('<>:"|?*') | {chr(c) for c in range(32)}


class PathValidationError(ValueError):
    pass


def validate_component(name: str) -> None:
    """Reject a single path component that is unsafe on Windows or Linux."""
    if name in ("", ".", ".."):
        raise PathValidationError(f"invalid path component {name!r}")
    if "/" in name or "\\" in name:
        raise PathValidationError(f"path separator inside component {name!r}")
    bad = WINDOWS_FORBIDDEN_CHARS.intersection(name)
    if bad:
        raise PathValidationError(f"{name!r} contains characters not allowed on Windows: {''.join(sorted(bad))!r}")
    if name.endswith((" ", ".")):
        raise PathValidationError(f"{name!r} ends with a space or dot, which Windows strips")
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        raise PathValidationError(f"{name!r} is a reserved device name on Windows")


def normalize_relative(rel: str) -> str:
    """Validate and normalise a relative path to NFC, ``/``-separated form."""
    if not rel or not rel.strip():
        raise PathValidationError("empty relative path")
    rel = unicodedata.normalize("NFC", rel.replace("\\", "/"))
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise PathValidationError(f"path must be relative: {rel!r}")
    parts = [p for p in rel.split("/") if p != ""]
    for part in parts:
        validate_component(part)
    return "/".join(parts)


def case_key(rel: str) -> str:
    """Key used to detect names that collide on case-insensitive filesystems (Windows/SMB)."""
    return unicodedata.normalize("NFC", rel).casefold()


def relative_to_root(root: Path, target: Path) -> str:
    """Express ``target`` relative to ``root``; refuse escapes, including via symlinks."""
    root_r = Path(os.path.realpath(root))
    target_r = Path(os.path.realpath(target))
    try:
        rel = target_r.relative_to(root_r)
    except ValueError as exc:
        raise PathValidationError(f"{target} is not inside root {root}") from exc
    if str(rel) == ".":
        raise PathValidationError("a project cannot be the root itself")
    return normalize_relative(PurePosixPath(*rel.parts).as_posix())


def resolve_under_root(root: Path, rel: str) -> Path:
    """Turn a stored relative path back into an absolute path, refusing escapes."""
    rel = normalize_relative(rel)
    candidate = Path(root).joinpath(*rel.split("/"))
    real_root = Path(os.path.realpath(root))
    real = Path(os.path.realpath(candidate))
    if real != real_root and real_root not in real.parents:
        raise PathValidationError(f"{rel!r} resolves outside its root (symlink or junction?)")
    return candidate


# ------------------------------------------------------------------ network locations (1.0.0 notice)
NETWORK_FS_TYPES = frozenset({
    "nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "smbfs", "ncpfs", "afs", "9p", "ceph", "glusterfs",
    "lustre", "gpfs", "davfs", "fuse.sshfs", "sshfs", "fuse.rclone", "fuse.davfs2", "fuse.glusterfs",
    "fuse.cephfs", "fuse.smbnetfs", "fuse.gvfsd-fuse",
})


def _unescape_mount(field: str) -> str:
    """/proc/self/mounts escapes space, tab, newline and backslash as octal."""
    return (field.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n")
            .replace("\\134", "\\"))


def _linux_fs_type(path: Path, mounts_text: str) -> str | None:
    best, fstype = "", None
    target = str(path)
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = _unescape_mount(fields[1])
        prefix = mount_point.rstrip("/") + "/"
        if (target == mount_point or target.startswith(prefix) or mount_point == "/") \
                and len(mount_point) >= len(best):
            best, fstype = mount_point, fields[2]
    return fstype


def _windows_is_remote(path: Path) -> bool | None:
    s = str(path)
    if s.startswith(("\\\\", "//")) and not s.startswith(("\\\\?\\", "\\\\.\\")):
        return True                                      # UNC path: \\server\share
    if s.startswith("\\\\?\\UNC\\"):
        return True
    drive = path.drive
    if s.startswith("\\\\?\\"):
        drive = s[4:6]
    if len(drive) != 2 or drive[1] != ":":
        return None
    import ctypes
    drive_remote = 4
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == drive_remote  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def is_network_location(location: Path, *, mounts_text: str | None = None) -> bool | None:
    """True when ``location`` is on a network filesystem (SMB/CIFS, NFS, ...), False when it is local,
    None when that cannot be told. Used only to warn: the file protocol's guarantees are tested on
    local disks, not on shares (docs/ARCHITECTURE.md). Never raises."""
    try:
        path = Path(os.path.abspath(location))
        if os.name == "nt":
            return _windows_is_remote(path)
        if mounts_text is None:
            try:
                mounts_text = Path("/proc/self/mounts").read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None                              # e.g. macOS: unknown, no warning
        path = Path(os.path.realpath(path))
        fstype = _linux_fs_type(path, mounts_text)
        if fstype is None:
            return None
        return fstype.casefold() in NETWORK_FS_TYPES
    except Exception:  # noqa: BLE001 - a notice must never break opening a workspace
        return None


NETWORK_NOTICE = ("This workspace is in a shared network folder. RevisionWorx 1.0 protects your data on one "
                  "computer's local disk; several computers writing to the same network folder at the same "
                  "time has not been verified. Let only one computer make changes at a time, and keep backups.")
