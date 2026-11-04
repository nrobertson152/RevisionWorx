# SPDX-License-Identifier: GPL-3.0-or-later
"""1.0.0: the whole ECR -> ECO lifecycle can be driven from the installed ``revisionworx change`` command,
against a real workspace and a real document revision. Before, the change CLI could only list, show,
verify, export and import CSV: nothing could be submitted, approved, implemented or closed without Python."""
import json

import pytest

from revisionworx import cli
from revisionworx.workspace import Workspace


def rwx(capsys, *args):
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def ws(tmp_path, monkeypatch, capsys):
    root = tmp_path / "ws"
    monkeypatch.setenv("REVISIONWORX_WORKSPACE", str(root))
    common = ["--cache-dir", str(tmp_path / "cache"), "--state-dir", str(tmp_path / "state")]
    assert rwx(capsys, "init", str(root), "--name", "E2E")[0] == 0
    (root / "Projects").mkdir()
    assert rwx(capsys, *common, "root", "add", "Projects", str(root / "Projects"))[0] == 0
    code, out, _ = rwx(capsys, *common, "--json", "project", "new", "--name", "Pump", "--number", "J-1")
    assert code == 0
    pid = json.loads(out)["id"]
    native = next(p for p in (root / "Projects").rglob("Native") if p.is_dir())
    drawing = native / "frame.idw"
    drawing.write_bytes(b"v1")
    code, out, err = rwx(capsys, *common, "--json", "doc", "register", pid, str(drawing), "--number", "DWG-1")
    assert code == 0, err
    data = json.loads(out)
    did = data.get("document", data)["id"]
    return root, common, did, drawing


def change(capsys, *args):
    code, out, err = rwx(capsys, "change", *args)
    assert code == 0, (args, err)
    return out


def test_full_change_lifecycle_from_the_command_line(ws, capsys):
    root, common, did, drawing = ws
    assert "[draft]" in change(capsys, "new-request", "Thicken plate", "--reason", "Cracks in fatigue test",
                               "--doc", "DWG-1", "--item", "part:P-100@A", "--priority", "high")
    assert "[submitted]" in change(capsys, "submit", "ECR-0001")
    assert "[in_review]" in change(capsys, "review", "ECR-0001")
    assert "[approved]" in change(capsys, "approve", "ECR-0001", "-m", "agreed")
    assert "[planning]" in change(capsys, "new-order", "ECR-0001", "--action", "Update drawing")
    shown = change(capsys, "show", "ECO-0001")
    action_id = next(line.split()[1] for line in shown.splitlines() if line.strip().startswith("action "))
    assert f"item document:{did}" in shown and "item part:label:p-100" in shown
    assert "[planning]" in change(capsys, "signoff", "ECO-0001", "approve", "-m", "release")
    assert "[in_progress]" in change(capsys, "start", "ECO-0001")

    # the implementing revision is a real check-in of the affected drawing
    assert rwx(capsys, *common, "doc", "checkout", did)[0] == 0
    drawing.write_bytes(b"v2 thicker plate")
    assert rwx(capsys, *common, "doc", "checkin", did, "-m", "thicker plate")[0] == 0

    change(capsys, "implement", "ECO-0001", f"document:{did}", "--rev", "B")
    change(capsys, "implement", "ECO-0001", "part:label:p-100", "--label", "B")
    change(capsys, "action-done", "ECO-0001", action_id, "-m", "done")
    assert "[implemented]" in change(capsys, "implemented", "ECO-0001")
    change(capsys, "signoff", "ECO-0001", "approve", "-m", "verified")
    assert "[closed]" in change(capsys, "close", "ECO-0001")
    assert "[closed]" in change(capsys, "show", "ECR-0001")
    assert "0 problem(s)" in change(capsys, "verify")

    with Workspace.open(root) as w:
        assert w.verify()["problems"] == []


def test_refusals_change_nothing(ws, capsys):
    root, common, did, drawing = ws
    change(capsys, "new-request", "Plate", "--reason", "why", "--doc", "DWG-1")
    with Workspace.open(root) as w:
        head = w.head_seq()
    for args in (["approve", "ECR-0001"],                       # not in review yet
                 ["new-order", "ECR-0001"],                      # not approved
                 ["start", "ECR-0001"],                          # a request, not an order
                 ["new-request", "X", "--reason", "y", "--doc", "NO-SUCH-DOC"],
                 ["new-request", "X", "--reason", "y", "--item", "P-100"],   # missing type:
                 ["submit", "ECR-9999"]):
        code, _, err = rwx(capsys, "change", *args)
        assert code == 2 and err.startswith("error:"), (args, code, err)
    with Workspace.open(root) as w:
        assert w.head_seq() == head


def test_implement_with_an_unknown_revision_label_is_refused(ws, capsys):
    root, common, did, drawing = ws
    change(capsys, "new-request", "Plate", "--reason", "why", "--doc", "DWG-1")
    for step in (["submit", "ECR-0001"], ["review", "ECR-0001"], ["approve", "ECR-0001"],
                 ["new-order", "ECR-0001"], ["signoff", "ECO-0001", "approve"], ["start", "ECO-0001"]):
        change(capsys, *step)
    code, _, err = rwx(capsys, "change", "implement", "ECO-0001", f"document:{did}", "--rev", "Z")
    assert code == 2 and "no revision 'Z'" in err
