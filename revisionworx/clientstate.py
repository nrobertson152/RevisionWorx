# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-machine client state: the checkout tokens this user holds (ADR-013).

Local only and not authoritative: the authoritative checkout record lives in the workspace. Losing
this file only means an administrator must release the checkout (audited).
"""
from __future__ import annotations

import os
from pathlib import Path

from . import atomicio


def default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "RevisionWorx" / "state"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "revisionworx"


class CheckoutTokens:
    def __init__(self, state_dir: Path | None = None):
        self.dir = Path(state_dir) if state_dir else default_state_dir()
        self.path = self.dir / "checkouts.json"

    def _load(self) -> dict:
        try:
            return atomicio.read_json(self.path)
        except (OSError, ValueError):
            return {}

    def get(self, workspace_id: str, document_id: str) -> str | None:
        return self._load().get(workspace_id, {}).get(document_id)

    def put(self, workspace_id: str, document_id: str, token: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = self._load()
        data.setdefault(workspace_id, {})[document_id] = token
        atomicio.atomic_write_json(self.path, data)

    def forget(self, workspace_id: str, document_id: str) -> None:
        data = self._load()
        if data.get(workspace_id, {}).pop(document_id, None) is not None:
            atomicio.atomic_write_json(self.path, data)
