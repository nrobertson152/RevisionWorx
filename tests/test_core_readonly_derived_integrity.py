# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent read-only derived-record integrity checks (issue #29).

The authoritative journal is left intact. Only CLOSED disposable workspaces
are modified. This suite tests detection, not automatic repair.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from revisionworx.workspace import Workspace


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*") if p.is_file()
    }


@pytest.fixture
def closed_workspace(tmp_path: Path):
    root = tmp_path / "doctor-fixture"
    with Workspace.create(root, "doctor-fixture"):
        pass
    return root


def inspect_without_mutation(root: Path) -> dict:
    before = snapshot(root)
    with Workspace.open(root, writable=False) as ws:
        report = ws.verify(repair=False)
    assert snapshot(root) == before, "read-only verify changed workspace bytes"
    return report


def test_intact_derived_records_are_accepted_without_mutation(closed_workspace):
    report = inspect_without_mutation(closed_workspace)
    assert not [p for p in report["problems"] if p.get("severity") != "info"]
    assert report["repaired"] is False


def test_modified_derived_record_is_reported_without_repair(closed_workspace):
    root = closed_workspace
    records = sorted((root / ".revisionworx" / "records").rglob("*.json"))
    assert records, "fixture must contain at least one materialised record"
    target = records[0]
    original = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(original, dict)
    original["_doctor_tamper"] = "not-authoritative"
    target.write_text(json.dumps(original), encoding="utf-8")
    report = inspect_without_mutation(root)
    assert any(
        p["problem"] == "mismatch"
        and p["kind"] == target.parent.name
        and p["id"] == target.stem
        for p in report["problems"]
    )
    assert report["repaired"] is False


def test_unjournaled_record_is_reported_without_quarantine(closed_workspace):
    root = closed_workspace
    stray = root / ".revisionworx" / "records" / "project" / "untracked-doctor.json"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text('{"name":"untracked"}', encoding="utf-8")
    report = inspect_without_mutation(root)
    assert any(
        p["problem"] == "not-in-journal"
        and p["kind"] == "project"
        and p["id"] == "untracked-doctor"
        for p in report["problems"]
    )
    assert stray.exists()
    assert report["repaired"] is False
