# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent RED regression gates for doctor/health fail-closed behavior.

Baseline: phase2-documents at 673bec2de2cab95f91dd1d4e24ac3b13f98ab006.
This file intentionally changes no production source. A large journal stem is tested
with a guarded range function, so a broken implementation cannot exhaust CI memory.
"""
from __future__ import annotations

import builtins
import itertools

import pytest

from revisionworx import cli, doctor, documents, projects, workspace
from revisionworx.projects import NewProject
from revisionworx.workspace import Workspace


@pytest.fixture
def populated_workspace(tmp_path):
    root = tmp_path / "share"
    (root / "Engineering").mkdir(parents=True)
    with Workspace.create(root, "independent-review") as ws:
        projects.add_root(ws, "Engineering", root / "Engineering")
        project = projects.create_project(
            ws, NewProject(name="Test HPU", number="REV-1", customer="Test")
        )
        path = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "part.ipt"
        path.write_bytes(b"v1")
        documents.register_document(ws, project["id"], path)
    return root


def _create_extreme_sequence_name(root):
    # A single accepted 20-digit journal name, plus the existing real entries.
    # There are billions of implied gaps, but no requirement to materialize them.
    (root / ".revisionworx" / "journal" / "00000000010000000000.json").write_text(
        "{}", encoding="utf-8"
    )


def _guard_large_range(*args):
    # Intercept only the module's global 'range'. This deliberately turns an
    # unbounded allocation into a deterministic failure, with no OOM risk.
    if args[-1] > 100_000:
        raise AssertionError("unbounded journal gap enumeration attempted")
    return builtins.range(*args)


def test_doctor_reports_sparse_high_seq_without_unbounded_allocation(populated_workspace, monkeypatch):
    _create_extreme_sequence_name(populated_workspace)
    monkeypatch.setattr(doctor, "range", _guard_large_range, raising=False)
    report = doctor.examine(populated_workspace)
    assert report["ok"] is False
    assert "J001" in {finding["code"] for finding in report["findings"]}
    assert any("journal" in name for name in report["checked"])


def test_workspace_health_fails_closed_on_sparse_high_seq(populated_workspace, monkeypatch):
    _create_extreme_sequence_name(populated_workspace)
    monkeypatch.setattr(workspace, "range", _guard_large_range, raising=False)
    with Workspace.open(populated_workspace) as ws:
        report = ws.health()
    assert report["ok"] is False
    assert report["reason"]


def test_doctor_never_says_healthy_without_stable_view(populated_workspace, monkeypatch):
    sequence = itertools.count()
    monkeypatch.setattr(doctor, "_head", lambda _meta: next(sequence))
    report = doctor.examine(populated_workspace)
    assert any("consistent view" in what for what in report["skipped"])
    assert report["ok"] is False, "inconclusive snapshot cannot be healthy"


def test_cli_doctor_returns_failure_for_unstable_view(populated_workspace, monkeypatch, capsys):
    sequence = itertools.count()
    monkeypatch.setattr(doctor, "_head", lambda _meta: next(sequence))
    with pytest.raises(SystemExit) as exc:
        cli.main(["-w", str(populated_workspace), "--json", "doctor"])
    assert exc.value.code == 1
    import json
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any("consistent view" in what for what in report["skipped"])
