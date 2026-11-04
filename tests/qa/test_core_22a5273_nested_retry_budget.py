# SPDX-License-Identifier: GPL-3.0-or-later
"""P0 independent Windows sharing-budget test: retry wrappers must not multiply.

Workspace._read_holder (formerly _read_lock_file) calls atomicio.read_json; on Windows read_json
already calls atomicio.retry_sharing. If the caller adds another N retries
around the same operation, a permanent WinError 32 lasts N*N delays instead
of N delays. Tests use tiny deterministic budgets, no wall-clock sleeping.
"""
import importlib.util
from pathlib import Path

import pytest

if importlib.util.find_spec("revisionworx.documents") is None:
    pytest.skip("Phase 2 not present on QA branch base", allow_module_level=True)

from revisionworx import atomicio
from revisionworx.workspace import Workspace


def _deny_all_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(atomicio, "IS_WINDOWS", True)
    monkeypatch.setattr(atomicio, "RETRY_SHARING", True)
    monkeypatch.setattr(atomicio, "SHARING_RETRIES", 3)
    monkeypatch.setattr(atomicio, "SHARING_DELAY", 0)
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_ATTEMPTS", 4)
    monkeypatch.setattr(Workspace, "LOCK_RELEASE_DELAY", 0)
    attempts = []

    def refuse(path):
        attempts.append(str(path))
        raise PermissionError(13, "simulated persistent Windows sharing violation")
    monkeypatch.setattr(atomicio, "_open_for_read", refuse)
    return attempts, tmp_path / "writer.lock"


def test_atomic_json_reader_has_one_bounded_retry_layer(tmp_path, monkeypatch):
    """The primitive directly should make exactly three open attempts."""
    attempts, path = _deny_all_reads(monkeypatch, tmp_path)
    with pytest.raises(PermissionError):
        atomicio.read_json(path)
    assert len(attempts) == 3


def test_workspace_lock_reader_must_not_multiply_retry_budgets(tmp_path, monkeypatch):
    """One bounded retry budget should suffice, not four complete inner budgets."""
    attempts, path = _deny_all_reads(monkeypatch, tmp_path)
    with pytest.raises(PermissionError):
        Workspace._read_holder(path)  # renamed from _read_lock_file
    assert len(attempts) <= max(3, 4), (
        f"nested Windows sharing retry amplification: {len(attempts)} calls "
        "for inner budget 3 and outer budget 4 (expected one bounded layer)"
    )
