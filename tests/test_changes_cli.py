# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless change-control commands (python -m revisionworx.changes), issue #19."""
import json

from revisionworx.changes import fixtures
from revisionworx.changes.__main__ import main
from revisionworx.workspace import RecordPut, Workspace


def test_list_show_impact_verify_export(tmp_path, capsys):
    loc = tmp_path / "sample"
    fixtures.create_sample_workspace(loc)
    assert main([str(loc), "list"]) == 0
    assert "ECO-0001" in capsys.readouterr().out
    assert main([str(loc), "list", "--open", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 4
    assert main([str(loc), "show", "ecr-0003"]) == 0
    assert "rejected" in capsys.readouterr().out
    assert main([str(loc), "impact", "ECO-0001"]) == 0
    assert "NOT searched" in capsys.readouterr().out
    assert main([str(loc), "verify"]) == 0
    assert "5 change(s) checked, 0 problem(s)" in capsys.readouterr().out
    pkg = tmp_path / "out.json"
    assert main([str(loc), "export", str(pkg)]) == 0
    assert main(["-", "verify-package", str(pkg)]) == 0
    assert main([str(loc), "export", str(pkg)]) == 2           # never overwrites silently
    assert main([str(loc), "show", "NOPE-1"]) == 2


def test_verify_reports_unreadable_records_and_import_csv(tmp_path, capsys):
    loc = tmp_path / "sample"
    fixtures.create_sample_workspace(loc)
    csv_file = tmp_path / "in.csv"
    csv_file.write_text("title,reason,documents\nNew note,Typo,DWG-200\n", encoding="utf-8")
    assert main([str(loc), "import-csv", str(csv_file)]) == 0
    assert "created ECR-0005" in capsys.readouterr().out
    with Workspace.open(loc, writable=True) as ws:
        bad = "ecr_" + "d" * 32
        ws.commit("test.bad", [RecordPut("changereq", bad, {"kind": "ecr", "id": bad})])
    assert main([str(loc), "verify"]) == 1
    assert "UNREADABLE" in capsys.readouterr().out
