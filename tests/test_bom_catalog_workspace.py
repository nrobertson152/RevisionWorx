# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18 (base catalog): the PROPOSED workspace store on real, disposable core workspaces.

Every catalog action must be one journal entry through ``Workspace.commit`` (no direct file access),
survive closing and reopening the workspace (restart), refuse writes on a read-only handle, retry safely
with a txid, keep ``ws.verify()`` clean and fail closed on conflicts."""
from decimal import Decimal as D

import pytest

from revisionworx.bom import (CatalogError, EditRejected, NumberingRule, PartsCatalogService, StoreConflict,
                              WorkspaceCatalogStore, rollup, structure_from_csv)
from revisionworx.bom.store import Commit, Put
from revisionworx.workspace import ReadOnlyError, Workspace


def svc_for(ws):
    return PartsCatalogService(WorkspaceCatalogStore(ws))


def test_catalog_persists_across_restart_one_entry_per_action(ws, share):
    s = svc_for(ws)
    s.save_rule(NumberingRule("std", prefix="P-", digits=5))
    head0 = ws.head_seq()
    frame = s.create_part(rule_id="std", description="Frame", kind="assembly")
    bolt = s.create_part("BOLT-M8", description="Hex bolt")
    line = s.add_line(frame.part_id, bolt.part_id, "4", find_number="10")
    assert ws.head_seq() == head0 + 3            # one journal entry per user action
    ws.close()
    with Workspace.open(share) as ro:          # restart, read-only
        s2 = svc_for(ro)
        assert s2.find_by_number("p-00001") == frame and s2.part(bolt.part_id) == bolt
        assert rollup(s2.structure(), frame.part_id).total(bolt.part_id) == D(4)
        assert s2.bom(frame.part_id)[0].line_id == line.line_id
        with pytest.raises(ReadOnlyError):
            s2.create_part("NEW-1")
        assert ro.verify()["problems"] == []


def test_conflicts_and_rejections_write_nothing(ws):
    s = svc_for(ws)
    a, b = s.create_part("A"), s.create_part("B")
    s.add_line(a.part_id, b.part_id, 1)
    head = ws.head_seq()
    with pytest.raises(EditRejected):
        s.add_line(b.part_id, a.part_id, 1)                 # cycle
    with pytest.raises(StoreConflict):
        s.update_part(a.part_id, 99, description="stale")   # stale read
    with pytest.raises(CatalogError):
        s.create_part("a")                                  # duplicate number
    from revisionworx.bom.store import partnum_id
    with pytest.raises(StoreConflict):                      # claim record refuses even a raw commit
        s.store.commit(Commit("raw", [Put("partnum", partnum_id("A"), {"id": partnum_id("A")}, 0)]))
    assert ws.head_seq() == head and ws.verify()["problems"] == []


def test_txid_retry_after_ambiguous_failure_is_idempotent(ws):
    """Store level: the same request with the same txid is applied once (ADR-017/021). Service level:
    after an ambiguous failure the caller asks ``ws.transaction_outcome(txid)`` (CORE_API section 4)."""
    s = svc_for(ws)
    txid = "ab" * 16
    p = s.create_part("ONCE-1", txid=txid)
    assert ws.transaction_outcome(txid)["state"] == "committed"
    rule = Commit("bom.rule.save", [Put("partrule", "std", NumberingRule("std").to_record(), 0)])
    seq = s.store.commit(rule, "cd" * 16)
    head = ws.head_seq()
    assert s.store.commit(rule, "cd" * 16) == seq and ws.head_seq() == head   # retried, not re-applied
    from revisionworx.bom import StoreError
    with pytest.raises(StoreError):
        s.store.commit(Commit("other", []), "cd" * 16)                         # txid reused for another request
    with pytest.raises(CatalogError, match="already used"):
        s.create_part("ONCE-1")
    assert [x.part_id for x in s.parts()] == [p.part_id]


def test_import_structure_into_workspace_is_one_entry(ws, share):
    s = svc_for(ws)
    head = ws.head_seq()
    s.import_structure(structure_from_csv(b"parent_number,child_number,quantity\nFRAME,SUB,2\nSUB,BOLT,3\n"))
    assert ws.head_seq() == head + 1
    frame = s.find_by_number("FRAME")
    assert rollup(s.structure(), frame.part_id).total(s.find_by_number("BOLT").part_id) == D(6)
    ws.close()
    with Workspace.open(share) as ro:
        assert ro.verify()["problems"] == [] and len(svc_for(ro).parts()) == 3


def test_catalog_records_do_not_disturb_documents(ws, share):
    """The new kinds coexist with Phase 2 records; the document API still works after catalog writes."""
    from revisionworx import documents as Docs, projects
    from revisionworx.projects import NewProject
    s = svc_for(ws)
    s.create_part("P-1")
    rec = projects.create_project(ws, NewProject(name="P", number="J-1"))
    f = projects.project_folder(ws, rec) / "a.txt"
    f.write_text("x")
    doc, rev = Docs.register_document(ws, rec["id"], f)
    assert Docs.history(ws, doc["id"])[0]["id"] == rev["id"] and s.find_by_number("p-1")
