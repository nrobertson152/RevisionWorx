# SPDX-License-Identifier: GPL-3.0-or-later
"""Change control on a real workspace: real documents and revisions, the journaled commit path,
read-only handles, conflicts across handles, administrative lock break, interrupted commits and a real
killed process followed by recovery in another one (issue #19). Local filesystem only."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from revisionworx import changes, documents as D, projects
from revisionworx.changes import exchange, fixtures, query
from revisionworx.changes.model import ECOStatus, ECRStatus, MalformedRecord, StaleBaseline, TransitionError
from revisionworx.projects import NewProject
from revisionworx.workspace import (CommittedNotApplied, ConflictError, LockLostError, ReadOnlyError, RecordPut,
                                    Workspace)

TX = "c0ffee" + "0" * 26
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def proj(ws):
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    path = folder / "03 Drawings" / "Native" / "bracket.idw"
    path.write_bytes(b"IDW rev A")
    doc, rev = D.register_document(ws, rec["id"], path, number="DWG-100", title="Bracket")
    ws.doc, ws.rev, ws.path = doc, rev, path
    return rec


def checkin(ws, content: bytes, description="change"):
    co = D.checkout(ws, ws.doc["id"])
    ws.path.write_bytes(content)
    return D.checkin(ws, ws.doc["id"], co["token"], description)


def approved_ecr(svc, ws):
    ecr = svc.create_request("Thicker bracket", "Fatigue cracks",
                             affected=[{"item_type": "document", "item_id": ws.doc["id"]}])
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    return svc.approve(ecr.id)


def journal_ops(ws):
    return [ws.read_entry(s)["op"] for s in ws.journal_seqs()]


def test_full_cycle_on_real_documents_is_journaled_and_verifies(ws, proj, share):
    svc = changes.service_for(ws)
    ecr = approved_ecr(svc, ws)
    assert ecr.affected[0].baseline_label == "A" and ecr.affected[0].label == "DWG-100 Bracket"
    eco = svc.create_order([ecr.id], actions=["Update drawing"])
    svc.start_work(eco.id)
    rev_b = checkin(ws, b"IDW rev B", "thicker")
    eco = svc.record_implementation(eco.id, eco.affected[0].key, rev_id=rev_b["id"])
    svc.complete_action(eco.id, eco.actions[0].id, "done")
    svc.mark_implemented(eco.id)
    eco = svc.close_order(eco.id, "verified on the shop floor")
    assert eco.status is ECOStatus.CLOSED
    assert ws.verify()["problems"] == []
    ops = journal_ops(ws)
    assert "change.ecr.create" in ops and "change.eco.close" in ops
    with Workspace.open(share) as ro:                          # a fresh reader sees the same thing
        fresh = changes.service_for(ro)
        assert fresh.request(ecr.id).status is ECRStatus.CLOSED
        assert fresh.verify_history(eco.id) == [] and fresh.verify_history(ecr.id) == []
        hits = query.where_changed(fresh, ws.doc["id"])
        assert {h["number"] for h in hits} == {"ECR-0001", "ECO-0001"}
        assert [h for h in hits if h["kind"] == "eco"][0]["implemented_revision"] == "B"


def test_real_checkin_makes_the_baseline_stale(ws, proj):
    svc = changes.service_for(ws)
    ecr = svc.create_request("t", "r", affected=[{"item_type": "document", "item_id": ws.doc["id"]}])
    svc.submit(ecr.id)
    svc.begin_review(ecr.id)
    checkin(ws, b"IDW rev B by someone else")
    head = ws.head_seq()
    with pytest.raises(StaleBaseline, match="baseline A, now B"):
        svc.approve(ecr.id)
    assert ws.head_seq() == head
    svc.return_for_rework(ecr.id, "rebaseline")
    assert svc.rebaseline(ecr.id).affected[0].baseline_label == "B"


def test_intervening_real_revision_must_be_acknowledged(ws, proj):
    svc = changes.service_for(ws)
    eco = svc.create_order([approved_ecr(svc, ws).id])
    svc.start_work(eco.id)
    checkin(ws, b"B: unrelated fix")
    rev_c = checkin(ws, b"C: the change")
    with pytest.raises(StaleBaseline, match=r"\(B\)"):
        svc.record_implementation(eco.id, eco.affected[0].key, rev_id=rev_c["id"])
    eco = svc.record_implementation(eco.id, eco.affected[0].key, rev_id=rev_c["id"], acknowledge_intervening=True)
    assert eco.implementation(eco.affected[0].key).rev_label == "C"


def test_close_can_require_released_revisions(ws, proj):
    svc = changes.service_for(ws)
    svc.save_policy({"close_requires_revision_state": "released"})
    eco = svc.create_order([approved_ecr(svc, ws).id])
    svc.start_work(eco.id)
    rev_b = checkin(ws, b"IDW rev B")
    svc.record_implementation(eco.id, eco.affected[0].key, rev_id=rev_b["id"])
    svc.mark_implemented(eco.id)
    with pytest.raises(TransitionError, match="revision B is wip"):
        svc.close_order(eco.id)
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_b["id"], state)
    assert svc.close_order(eco.id).status is ECOStatus.CLOSED


def test_read_only_handle_answers_queries_and_refuses_writes(ws, proj, share):
    svc = changes.service_for(ws)
    ecr = svc.create_request("t", "r")
    ws.close()
    with Workspace.open(share) as ro:
        rsvc = changes.service_for(ro)
        assert rsvc.get(ecr.id).number == "ECR-0001"
        head = ro.head_seq()
        with pytest.raises(ReadOnlyError):
            rsvc.edit_request(ecr.id, title="x")
        assert ro.head_seq() == head


def test_conflict_between_handles_writes_nothing(ws, proj, share):
    svc = changes.service_for(ws)
    ecr = svc.create_request("t", "r")
    ws.close()
    with Workspace.open(share) as ro:
        shown = changes.service_for(ro).get(ecr.id).rev        # the GUI shows rev N
    with Workspace.open(share, writable=True) as w1:
        changes.service_for(w1).edit_request(ecr.id, title="first edit")
    with Workspace.open(share, writable=True) as w2:
        head = w2.head_seq()
        with pytest.raises(ConflictError):
            changes.service_for(w2).edit_request(ecr.id, title="second edit", expected_rev=shown)
        assert w2.head_seq() == head and changes.service_for(w2).get(ecr.id).title == "first edit"


def test_writer_whose_lock_was_broken_cannot_publish_a_change(ws, proj, share):
    svc = changes.service_for(ws)
    ecr = svc.create_request("t", "r")
    Workspace.break_writer_lock(share, ws._lock_token, reason="test: writer unresponsive")
    with pytest.raises(LockLostError):
        svc.edit_request(ecr.id, title="from the revoked writer")
    with Workspace.open(share) as ro:
        assert changes.service_for(ro).get(ecr.id).title == "t"
        assert "change.ecr.edit" not in journal_ops(ro)
    ws._lock_token = None


def test_interrupted_materialisation_is_saved_and_recovered(ws, proj, share, monkeypatch):
    svc = changes.service_for(ws)
    ecr = svc.create_request("t", "r", affected=[{"item_type": "document", "item_id": ws.doc["id"]}])
    real_apply = Workspace._apply

    def boom(self, entry):
        raise OSError("disk full while updating records")
    monkeypatch.setattr(Workspace, "_apply", boom)
    with pytest.raises(CommittedNotApplied):
        svc.submit(ecr.id, txid=TX)
    monkeypatch.setattr(Workspace, "_apply", real_apply)
    ws.close()
    with Workspace.open(share) as ro:                          # readers see the committed change already
        assert changes.service_for(ro).get(ecr.id).status is ECRStatus.SUBMITTED
    with Workspace.open(share, writable=True) as w:
        w.recover()
        s = changes.service_for(w)
        assert s.submit(ecr.id, txid=TX).status is ECRStatus.SUBMITTED and s.last_replayed
        assert s.get(ecr.id).event_count == 2 and s.verify_history(ecr.id) == []
        assert w.verify()["problems"] == []


def test_record_written_by_another_tool_with_bad_data_fails_closed(ws, proj):
    svc = changes.service_for(ws)
    good = svc.create_request("t", "r")
    bad_id = "ecr_" + "d" * 32
    ws.commit("test.bad", [RecordPut("changereq", bad_id, {"kind": "ecr", "schema": 1, "id": bad_id})])
    with pytest.raises(MalformedRecord):
        svc.get(bad_id)
    page = query.search(svc)
    assert [r["id"] for r in page["rows"]] == [good.id] and page["problems"][0]["id"] == bad_id
    with pytest.raises(MalformedRecord, match="unreadable"):
        exchange.export_package(svc)
    assert exchange.verify_package(exchange.export_package(svc, [good.id])) == []


def test_sample_workspace_fixture_is_consistent(tmp_path):
    info = fixtures.create_sample_workspace(tmp_path / "sample")
    with Workspace.open(tmp_path / "sample") as ro:
        svc = changes.service_for(ro)
        states = {k: svc.get(v).status.value for k, v in info["changes"].items()}
        assert states == {"draft": "draft", "submitted": "submitted", "rejected": "rejected",
                          "approved": "converted", "order": "planning"}
        assert ro.verify()["problems"] == []
        assert all(svc.verify_history(c) == [] for c in info["changes"].values())


# ---------------------------------------------------------------- real process death
CHILD = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    from revisionworx import changes
    from revisionworx.workspace import Workspace
    share, mode, txid, doc = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    ws = Workspace.open(share, writable=True)
    if mode == "after-commit-point":      # the journal entry is published; die before records are updated
        Workspace._apply = lambda self, entry: os._exit(17)
    elif mode == "before-commit-point":   # die as the commit starts: nothing may be published
        Workspace.commit = lambda self, *a, **k: os._exit(18)
    svc = changes.service_for(ws)
    ecr = svc.create_request("Crash test", "power cut", txid=txid,
                             affected=[{"item_type": "document", "item_id": doc}])
    print(ecr.id, "replayed" if svc.last_replayed else "new")
    ws.close()
""")


def run_child(share, mode, txid, doc):
    env = dict(os.environ, PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return subprocess.run([sys.executable, "-c", CHILD, str(share), mode, txid, doc], env=env,
                          capture_output=True, text=True, timeout=120)


def break_dead_lock(share):
    holder = Workspace.read_writer_lock(share)
    if holder:
        Workspace.break_writer_lock(share, holder["token"], reason="test: writer process died")


@pytest.mark.parametrize("mode, code, committed", [("after-commit-point", 17, True),
                                                   ("before-commit-point", 18, False)])
def test_killed_process_then_recovery_in_another_process(ws, proj, share, mode, code, committed):
    doc_id = ws.doc["id"]
    ws.close()
    with Workspace.open(share) as ro:
        head = ro.head_seq()
    result = run_child(share, mode, TX, doc_id)
    assert result.returncode == code, result.stderr
    break_dead_lock(share)
    with Workspace.open(share) as ro:
        assert ro.transaction_outcome(TX)["state"] == ("committed" if committed else "not-found")
        assert (len(changes.service_for(ro).scan()[0]) == 1) is committed
    with Workspace.open(share, writable=True) as w:            # recovery by an ordinary writer
        w.recover()
        assert w.verify()["problems"] == []
    retry = run_child(share, "normal", TX, doc_id)              # the user retries the same action
    assert retry.returncode == 0, retry.stderr
    assert retry.stdout.split()[1] == ("replayed" if committed else "new")
    with Workspace.open(share) as ro:
        svc = changes.service_for(ro)
        found, problems = svc.scan()
        assert len(found) == 1 and problems == []                # exactly one change, never two
        assert found[0].number == "ECR-0001" and svc.verify_history(found[0].id) == []
        assert ro.verify()["problems"] == []
        assert ro.head_seq() > head


def test_rework_and_dependency_invalidation_survive_reopen(ws, proj, share):
    """Codex P2s on PR #20 (cb68433), across a restart on a real workspace."""
    svc = changes.service_for(ws)
    svc.save_policy({"eco_release_approvals_required": 1})
    ecr = approved_ecr(svc, ws)
    checkin(ws, b"IDW rev B by someone else")
    svc.return_for_rework(ecr.id, "baseline moved")
    prereq = svc.create_request("prereq", "r")
    other = svc.create_request("other", "r", affected=[{"item_type": "part", "label": "P-9"}])
    svc.submit(other.id)
    svc.begin_review(other.id)
    eco = svc.create_order([svc.approve(other.id).id])
    svc.set_dependencies(eco.id, [prereq.id])
    svc.sign_off_order(eco.id, "approve")
    svc.set_dependencies(eco.id, [])
    ws.close()
    with Workspace.open(share, writable=True) as w:
        s = changes.service_for(w)
        assert s.request(ecr.id).status is ECRStatus.DRAFT
        assert s.rebaseline(ecr.id).affected[0].baseline_label == "B"
        with pytest.raises(changes.SignoffError, match="0 of 1"):
            s.start_work(eco.id)
        assert s.verify_history(ecr.id) == [] and s.verify_history(eco.id) == []
        assert w.verify()["problems"] == []
