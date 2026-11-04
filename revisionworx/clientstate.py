# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-machine client state: the checkout tokens this user holds (ADR-013).

Local only and not authoritative: the authoritative checkout record lives in the workspace. Losing
this file only means an administrator must release the checkout (audited).
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import atomicio


def default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "RevisionWorx" / "state"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "revisionworx"


def _safe_name(value: str) -> str:
    """Workspace and document ids are safe already; this keeps any other value inside the state folder."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        return value
    return "h_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


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
            return atomicio.read_json(self.path)
        except (OSError, ValueError):
            return {}

    def get(self, workspace_id: str, document_id: str) -> str | None:
        try:
            return atomicio.read_json(self._file(workspace_id, document_id))["token"]
        except (OSError, ValueError, KeyError, TypeError):
            return self._load_old().get(workspace_id, {}).get(document_id)

    def put(self, workspace_id: str, document_id: str, token: str) -> None:
        path = self._file(workspace_id, document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_json(path, {"token": token})

    def forget(self, workspace_id: str, document_id: str) -> None:
        atomicio.retry_sharing(lambda: self._file(workspace_id, document_id).unlink(missing_ok=True))
        old = self._load_old()
        if old.get(workspace_id, {}).pop(document_id, None) is not None:
            atomicio.atomic_write_json(self.path, old)
