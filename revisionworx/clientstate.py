# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-machine client state: the checkout tokens this user holds (ADR-013).

Local only and not authoritative: the authoritative checkout record lives in the workspace. Losing
this file only means an administrator must release the checkout (audited).
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

from . import atomicio


def default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "RevisionWorx" / "state"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "revisionworx"


def safe_name(value: str) -> str:
    """Workspace and document ids are safe already; this keeps any other value inside the state folder."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        return value
    return "h_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


_safe_name = safe_name


def write_private(path: Path, obj: dict) -> None:
    """Write a file only this operating-system user can read: it holds a key that signs in their name. On Windows the profile's
    own permissions do this; the mode here is for Linux. It's created private, and never exists with looser permissions."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(atomicio.dumps_json(obj))
            fh.flush()
            os.fsync(fh.fileno())
        atomicio.retry_sharing(lambda: os.replace(tmp, path))
    finally:
        atomicio.remove_quietly(tmp)


class CheckoutTokens:
    """One small file per checkout, so writers never read, change and rewrite a shared file: two processes
    cannot erase each other's tokens, and a failed read cannot wipe them. ``checkouts.json`` from older builds
    is still honoured for reading and is only ever shrunk."""

    def __init__(self, state_dir: Path | None = None):
        self.dir = Path(state_dir) if state_dir else default_state_dir()
        self.path = self.dir / "checkouts.json"

    def _file(self, workspace_id: str, document_id: str) -> Path:
        return self.dir / "checkouts" / _safe_name(workspace_id) / f"{_safe_name(document_id)}.json"

    def _load_old(self) -> dict:
        try:
            return atomicio.read_json_object(self.path)
        except (OSError, ValueError):
            return {}

    def get(self, workspace_id: str, document_id: str) -> str | None:
        try:
            token = atomicio.read_json_object(self._file(workspace_id, document_id)).get("token")
        except (OSError, ValueError):
            token = None
        if not isinstance(token, str):
            old = self._load_old().get(workspace_id)
            token = old.get(document_id) if isinstance(old, dict) else None
        return token if isinstance(token, str) else None

    def put(self, workspace_id: str, document_id: str, token: str) -> None:
        path = self._file(workspace_id, document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_json(path, {"token": token})

    def forget(self, workspace_id: str, document_id: str) -> None:
        atomicio.retry_sharing(lambda: self._file(workspace_id, document_id).unlink(missing_ok=True))
        old = self._load_old()
        if old.get(workspace_id, {}).pop(document_id, None) is not None:
            atomicio.atomic_write_json(self.path, old)
