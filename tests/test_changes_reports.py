# SPDX-License-Identifier: GPL-3.0-or-later
"""Change control: impact summaries, search/filter, register and package export, CSV import and the
GUI-facing helpers (issue #19)."""
import json

import pytest

from revisionworx import changes, documents as D, projects
from revisionworx.changes import exchange, fixtures, impact, presentation, query
from revisionworx.changes.exchange import ImportRejected
from revisionworx.changes.model import (DuplicateNumber, ValidationError,
                                        StaleBaseline, TransitionError)
from revisionworx.projects import NewProject
from revisionworx.workspace import CommittedNotApplied, ConflictError, LockLostError, WorkspaceCorruptError


@pytest.fixture
def sample():
    return fixtures.sample_memory_service()


# ---------------------------------------------------------------- impact
def test_impact_flags_declared_items_concurrent_changes_and_blockers(sample):
    svc, _, cat, ids = sample
    rep = impact.impact_summary(svc, ids["order"])
    doc, asm = rep["items"]
    assert doc["verification"] == "verified" and "concurrent-change" in doc["classes"]
    assert doc["other_changes"] == ["ECR-0001"]                 # the open draft touching the same drawing
    assert asm["verification"] == "declared" and "verification-required" in asm["classes"]
    assert rep["counts"]["declared"] == 1 and rep["counts"]["pending_implementation"] == 1
    assert any("NOT searched" in c for c in rep["coverage"])    # never suppressed
    assert "required action open: Notify supplier" in rep["blockers"]


def test_impact_reports_stale_and_checked_out_documents(sample):
    svc, _, cat, ids = sample
    doc_id = next(iter(cat.documents))
    cat.add_revision(doc_id, "rev_" + "7" * 32, "B")
    cat.documents[doc_id]["checked_out_by"] = {"user": "dave", "host": "cad-7"}
    row = impact.impact_summary(svc, ids["draft"])["items"][0]
    assert row["stale"] and {"stale-baseline", "checked-out"} <= set(row["classes"])
    assert row["checked_out_by"] == "dave@cad-7" and row["current"] == "B"


def test_impact_dependency_tree_and_exports(sample):
    svc, _, _, ids = sample
    svc.set_dependencies(ids["draft"], [ids["submitted"]])
    rep = impact.impact_summary(svc, ids["draft"])
    assert [(d["number"], d["complete"]) for d in rep["dependencies"]] == [("ECR-0002", False)]
    assert impact.impact_summary(svc, ids["submitted"])["dependents"] == ["ECR-0001"]
    text = impact.impact_text(rep)
    assert "Requires:" in text and "review signal, not proof" in text
    csv_text = impact.impact_csv(rep)
    assert csv_text.splitlines()[0].startswith("change,type,label") and ",coverage," in csv_text


def test_impact_without_catalog_says_nothing_was_verified():
    svc, _ = fixtures.deterministic(changes.MemoryStore())
    ecr = svc.create_request("t", "r", affected=[{"item_type": "part", "label": "P-1"}])
    rep = impact.impact_summary(svc, ecr.id)
    assert any("no document state was verified" in c for c in rep["coverage"])


# ---------------------------------------------------------------- search
def test_filters_sorting_and_paging(sample):
    svc, _, _, ids = sample
    assert query.search(svc)["total"] == 5
    assert [r["number"] for r in query.search(svc, query.ChangeFilter(kinds=("eco",)))["rows"]] == ["ECO-0001"]
    open_rows = query.search(svc, query.ChangeFilter(open_only=True), sort="priority")["rows"]
    assert [r["priority"] for r in open_rows] == ["urgent", "urgent", "high", "normal"]
    assert query.search(svc, query.ChangeFilter(statuses=("rejected",)))["rows"][0]["id"] == ids["rejected"]
    assert query.search(svc, query.ChangeFilter(categories=("DOCUMENTATION",)))["total"] == 1
    assert query.search(svc, query.ChangeFilter(requested_by="ALICE"))["total"] == 5
    page = query.search(svc, limit=2, offset=2)
    assert len(page["rows"]) == 2 and page["has_more"]
    assert query.search(svc, sort="created", descending=True)["rows"][0]["number"] == "ECO-0001"
    with pytest.raises(ValidationError):
        query.search(svc, sort="colour")


def test_text_search_is_unicode_and_case_insensitive(sample):
    svc, _, _, _ = sample
    svc.create_request("Ändere Halter", "Kundenwunsch", affected=[{"item_type": "part", "label": "TEIL-Ø12"}])
    assert query.search(svc, query.ChangeFilter(text="ändere"))["total"] == 1
    assert query.search(svc, query.ChangeFilter(text="teil-ø12"))["total"] == 1
    assert query.search(svc, query.ChangeFilter(text="bracket thickness"))["total"] == 2
    assert query.search(svc, query.ChangeFilter(text="nothing-like-this"))["rows"] == []


def test_where_changed_and_counts(sample):
    svc, _, cat, ids = sample
    doc_id = next(iter(cat.documents))
    numbers = [r["number"] for r in query.where_changed(svc, doc_id)]
    assert set(numbers) == {"ECR-0001", "ECR-0003", "ECR-0004", "ECO-0001"}
    assert "ECR-0003" not in [r["number"] for r in query.where_changed(svc, doc_id, open_only=True)]
    counts = query.counts_by_status(svc)
    assert counts["ecr"] == {"draft": 1, "submitted": 1, "rejected": 1, "converted": 1}
    assert counts["eco"] == {"planning": 1} and counts["unreadable"] == 0


def test_register_csv_neutralises_formulas(sample):
    svc, _, _, _ = sample
    svc.create_request("=HYPERLINK(\"http://x\")", "r")
    text = query.register_csv(query.search(svc)["rows"])
    assert "'=HYPERLINK" in text and text.splitlines()[0].startswith("Number,Type,Title")


# ---------------------------------------------------------------- package export
def test_package_round_trip_and_tamper_detection(sample, tmp_path):
    svc, _, _, ids = sample
    pkg = exchange.export_package(svc)
    assert exchange.verify_package(pkg) == []
    path = exchange.write_export(tmp_path / "changes.json", exchange.package_json(pkg))
    loaded = exchange.load_package(path)
    assert exchange.verify_package(loaded) == []
    loaded["changes"][0]["title"] = "quietly rewritten"
    found = {p["problem"] for p in exchange.verify_package(loaded)}
    assert "package-digest-mismatch" in found
    events = json.loads(json.dumps(pkg))
    first = events["changes"][0]["id"]
    events["events"][first][0]["comment"] = "edited history"
    assert "altered-event" in {p["problem"] for p in exchange.verify_package(events)}
    assert exchange.verify_package({"format": "x"}) == [{"problem": "not-a-change-package"}]


def test_write_export_never_overwrites_by_default_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "report.csv"
    exchange.write_export(target, "first\n")
    with pytest.raises(FileExistsError):
        exchange.write_export(target, "second\n")
    assert target.read_text() == "first\n"
    exchange.write_export(target, "second\n", overwrite=True)
    assert target.read_text() == "second\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.csv"]


def test_load_package_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe not json")
    with pytest.raises(changes.MalformedRecord):
        exchange.load_package(bad)


# ---------------------------------------------------------------- CSV import
@pytest.fixture
def doc_ws(ws):
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    path = projects.project_folder(ws, rec) / "03 Drawings" / "Native" / "bracket.idw"
    path.write_bytes(b"A")
    doc, _ = D.register_document(ws, rec["id"], path, number="DWG-100")
    ws.doc = doc
    return ws


def test_csv_import_creates_all_rows_in_one_commit(doc_ws):
    svc = changes.service_for(doc_ws)
    text = ("﻿title,reason,priority,documents,items,number\r\n"
            "Chamfer edges,Injury,HIGH,dwg-100,part:P-1@B;assembly:A-2,\r\n"
            "Notes,Old standard,,,,CR-77\r\n"
            ",,,,,\r\n")
    head = doc_ws.head_seq()
    made = exchange.import_requests_csv(svc, text, exchange.document_lookup(doc_ws))
    assert doc_ws.head_seq() == head + 1
    assert [c.number for c in made] == ["ECR-0001", "CR-77"]
    assert made[0].priority.value == "high"
    assert [i.item_type.value for i in made[0].affected] == ["document", "part", "assembly"]
    assert made[0].affected[0].baseline_label == "A" and made[0].affected[1].baseline_label == "B"


@pytest.mark.parametrize("text, match", [
    ("title\nx\n", "at least 'title' and 'reason'"),
    ("title,reason,colour\nx,y,z\n", "unknown columns: colour"),
    ("title,reason,documents\nx,y,DWG-999\n", "unknown document"),
    ("title,reason,items\nx,y,P-1\n", "must look like part:P-100"),
    ("title,reason\nx,\n", "reason is required"),
    ("title,reason,priority\nx,y,asap\n", "priority"),
    ("title,reason,number\nx,y,N-1\nz,w,n-1\n", "repeats row 2"),
    ("title,reason\nx,y,extra\n", "more cells than columns"),
    ("title,reason\n", "no rows"),
    ("title,reason\nt\x00x,y\n", "control character"),
])
def test_csv_import_rejects_bad_files_without_writing(doc_ws, text, match):
    svc = changes.service_for(doc_ws)
    head = doc_ws.head_seq()
    with pytest.raises(ImportRejected, match=match):
        exchange.import_requests_csv(svc, text, exchange.document_lookup(doc_ws))
    assert doc_ws.head_seq() == head


def test_csv_import_reports_every_bad_row_and_existing_numbers(doc_ws):
    svc = changes.service_for(doc_ws)
    svc.create_request("t", "r", number="CR-1")
    with pytest.raises(ImportRejected) as info:
        exchange.import_requests_csv(svc, "title,reason,number\na,b,cr-1\nc,,\ne,f,\n",
                                     exchange.document_lookup(doc_ws))
    assert [r for r, _ in info.value.errors] == [2, 3]
    assert len(svc.scan()[0]) == 1


# ---------------------------------------------------------------- GUI helpers
def test_available_actions_follow_state_and_policy(sample):
    svc, actors, _, ids = sample
    names = lambda c, **k: {a["action"]: a for a in presentation.available_actions(svc, svc.get(c), **k)}
    assert set(names(ids["draft"])) == {"edit", "submit", "cancel"}
    assert set(names(ids["rejected"])) == set()
    eco = names(ids["order"])
    assert eco["start_work"]["enabled"] and "mark_implemented" not in eco
    assert not any(a["enabled"] for a in names(ids["draft"], read_only=True).values())
    svc.save_policy({"require_distinct_approver": True})
    svc.begin_review(ids["submitted"])
    approve = names(ids["submitted"])["approve"]
    assert not approve["enabled"] and "requester" in approve["reason"]
    with actors.as_(fixtures.BOB):
        assert names(ids["submitted"])["approve"]["enabled"]
    svc.start_work(ids["order"])
    blocked = names(ids["order"])["mark_implemented"]
    assert not blocked["enabled"] and "Notify supplier" in blocked["reason"]


@pytest.mark.parametrize("exc, ui", [
    (CommittedNotApplied(5, "t" * 32, OSError("x")), "saved"),
    (ConflictError("changereq", "ecr_x", 3, 4), "reload"),
    (LockLostError("gone"), "reopen"),
    (WorkspaceCorruptError("journal gap"), "read-only"),
    (changes.ReplayRefused("x"), "new-attempt"),
    (StaleBaseline("x", ["k"]), "reload"),
    (ValidationError("x"), "fix-input"),
    (DuplicateNumber("x"), "fix-input"),
    (TransitionError("x"), "fix-input"),
    (PermissionError(13, "Permission denied", "/x"), "fix-input"),
    (RuntimeError("?"), "reload"),
])
def test_describe_error_maps_every_failure_to_a_ui_action(exc, ui):
    assert presentation.describe_error(exc)["ui"] == ui


def test_change_detail_has_everything_for_a_pane(sample):
    svc, _, _, ids = sample
    d = presentation.change_detail(svc, ids["order"])
    assert d["status_name"] == "Planning" and d["history_problems"] == []
    assert d["gates"]["eco-release"] == {"approvals": 0, "rejections": 0}
    assert {a["title"] for a in d["open_actions"]} == {"Update bracket model and drawing", "Notify supplier"}
    assert d["impact"]["change"]["number"] == "ECO-0001"


def test_write_export_works_without_hard_links(tmp_path, monkeypatch):
    import os

    def no_links(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "link", no_links)
    target = tmp_path / "usb.json"
    exchange.write_export(target, "{}\n")
    assert target.read_text() == "{}\n"
    with pytest.raises(FileExistsError):
        exchange.write_export(target, "second\n")
    assert target.read_text() == "{}\n" and [p.name for p in tmp_path.iterdir()] == ["usb.json"]


def test_actions_explain_an_unreadable_dependency_instead_of_failing(sample):
    svc, _, _, ids = sample
    svc.start_work(ids["order"])
    eco = svc.store.records["eco"][ids["order"]]
    ghost = "ecr_" + "6" * 32
    eco["dependencies"] = [{"change_id": ghost, "type": "requires"}]
    actions = {a["action"]: a for a in presentation.available_actions(svc, svc.get(ids["order"]))}
    assert not actions["mark_implemented"]["enabled"] and ghost in actions["mark_implemented"]["reason"]


def test_csv_import_retry_with_same_txid_returns_the_original_requests(doc_ws):
    """Codex P2 on PR #20: a retry was rejected because the first attempt's numbers already existed."""
    svc = changes.service_for(doc_ws)
    text = "title,reason,number\nA,r,CR-1\nB,r,\n"
    tx = "ab" * 16
    first = exchange.import_requests_csv(svc, text, exchange.document_lookup(doc_ws), txid=tx)
    again = exchange.import_requests_csv(svc, text, exchange.document_lookup(doc_ws), txid=tx)
    assert [c.id for c in again] == [c.id for c in first] and len(svc.scan()[0]) == 2


def test_where_used_provider_contract_matches_pr21_shape(sample):
    """Duck-typed provider with the edge keys Claude C published for revisionworx.bom (PR #21)."""
    svc, _, _, ids = sample

    class Provider:
        def where_used(self, item):
            if item.item_type.value not in ("part", "assembly", "bom"):
                return {"edges": [], "coverage": f"{item.item_type.value} items were not searched"}
            return {"edges": [{"label": "A-200 Frame", "id": "prt_" + "1" * 32, "relationship": "direct",
                               "levels": 1, "referenced_revision": "latest", "quantity": 2,
                               "top_level": True, "verification": "declared-bom"}],
                    "coverage": "CAD files and unmanaged projects were not searched"}
    rep = impact.impact_summary(svc, ids["order"], where_used=Provider())
    asm = [r for r in rep["items"] if r["type"] == "assembly"][0]
    assert asm["where_used"][0]["relationship"] == "direct"
    assert "CAD files" in asm["where_used_coverage"]
    assert impact.NO_WHERE_USED not in rep["coverage"]
