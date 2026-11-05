# SPDX-License-Identifier: GPL-3.0-or-later
"""Toolkit-free behaviour behind the redesigned window: preferences, search everything, quick filters,
the status card, the one primary action and the command palette. Real core, real temporary files."""
from pathlib import Path

import pytest

from revisionworx.gui.controller import Controller
from revisionworx.gui.viewmodels import CommandPalette, DocumentListModel, DocumentPanel


def make(tmp_path, name="state"):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / name)


@pytest.fixture
def started(tmp_path):
    c = make(tmp_path)
    p = c.quick_start(tmp_path / "home", "Hydraulic Manifold", number="P-1042")
    docs = []
    for name, data in (("manifold_block.ipt", b"block"), ("port_plate.ipt", b"plate"), ("seal_spec.pdf", b"spec")):
        src = tmp_path / "in" / name
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(data)
        docs.append(c.import_document(p["id"], src, number=name.split(".")[0].upper().replace("_", "-"))[0])
    return c, p, docs


def test_preferences_round_trip_and_survive_damage(tmp_path):
    c = make(tmp_path)
    assert c.preference("theme", "light") == "light"
    c.set_preference("theme", "dark")
    c.set_preference("geometry", [1, 2, 3])
    assert make(tmp_path).preference("theme") == "dark" and make(tmp_path).preference("geometry") == [1, 2, 3]
    (tmp_path / "cfg" / "preferences.json").write_text("{damaged")
    assert make(tmp_path).preference("theme", "light") == "light"
    c.set_preference("recent", None)                     # also leaves the recent list readable
    assert make(tmp_path).recent_workspaces() == []


def test_search_everything_finds_projects_and_documents(started):
    c, p, docs = started
    found = c.search_everything("manifold")
    assert [x["id"] for x in found["projects"]] == [p["id"]]
    assert any(d["id"] == docs[0]["id"] and d["project_name"] == "Hydraulic Manifold" for d in found["documents"])
    assert c.search_everything("plate")["documents"][0]["number"] == "PORT-PLATE"


def test_quick_filters_and_counts(started, tmp_path):
    c, p, docs = started
    lst = DocumentListModel(c)
    lst.load(p["id"])
    assert lst.quick_counts == {"all": 3, "mine": 0, "attention": 0}
    c.checkout(docs[1]["id"])
    working = c.working_file(docs[2]["id"])
    working.unlink()
    lst.refresh()
    assert lst.quick_counts == {"all": 3, "mine": 1, "attention": 1}
    lst.set_quick("mine")
    assert [r["id"] for r in lst.rows] == [docs[1]["id"]]
    lst.set_quick("attention")
    assert [r["id"] for r in lst.rows] == [docs[2]["id"]] and lst.row_severity(0) == "error"
    lst.refresh()                                        # a refresh keeps the chosen filter
    assert lst.quick == "attention" and len(lst.rows) == 1
    lst.set_quick("bogus")
    assert lst.quick == "all" and len(lst.rows) == 3
    assert lst.row_state(0) == "wip"
    c.cancel_checkout(docs[1]["id"])
    lst.set_quick("mine")
    lst.refresh()
    assert lst.rows == [] and "no documents checked out" in lst.notice


def test_status_card_and_primary_action_follow_the_document(started, tmp_path):
    c, p, docs = started
    panel = DocumentPanel(c)
    panel.load(docs[0]["id"])
    assert panel.headline[0] == "Available" and panel.headline[2] == "ok" and panel.primary_action == "checkout"
    c.checkout(docs[0]["id"])
    panel.load(docs[0]["id"])
    assert panel.headline[0] == "Checked out by you" and panel.primary_action == "checkin"
    Path(panel.working_path).write_bytes(b"edited")
    panel.load(docs[0]["id"])
    assert panel.headline[0] == "Checked out by you · edited" and panel.headline[2] == "info"
    other = make(tmp_path, "other")
    other.open_workspace(tmp_path / "home")
    other_panel = DocumentPanel(other)
    other_panel.load(docs[0]["id"])
    assert other_panel.headline[0].startswith("Checked out by ") and other_panel.headline[2] == "warn"
    assert other_panel.primary_action == "none"
    Path(c.working_file(docs[1]["id"])).unlink()
    panel.load(docs[1]["id"])
    assert panel.headline == (panel.headline[0], panel.headline[1], "danger") and panel.headline[0] == "File missing"
    Path(c.working_file(docs[2]["id"])).write_bytes(b"changed behind our back")
    panel.load(docs[2]["id"])
    assert panel.headline[0] == "Changed without a checkout" and panel.headline[2] == "warn"
    panel.load(None)
    assert panel.headline == ("", "", "neutral") and panel.primary_action == "none"


def test_my_checkouts_is_this_session_only(started, tmp_path):
    c, p, docs = started
    c.checkout(docs[0]["id"])
    other = make(tmp_path, "other")
    other.open_workspace(tmp_path / "home")
    other.checkout(docs[1]["id"])
    assert [d["id"] for d in c.my_checkouts()] == [docs[0]["id"]]
    assert [d["id"] for d in other.my_checkouts()] == [docs[1]["id"]]


def test_command_palette_lists_actions_projects_and_documents(started):
    c, p, docs = started
    calls = []
    actions = [("Check out", "edit reserve", lambda: calls.append("co"), lambda: True),
               ("Delete everything", "never", lambda: calls.append("x"), lambda: False)]
    pal = CommandPalette(c, actions)
    assert [i["title"] for i in pal.items] == ["Check out"]          # disabled actions are not offered
    pal.search("reserve")
    kind, cb = pal.choose(0)
    cb()
    assert kind == "action" and calls == ["co"]
    pal.search("manifold")
    kinds = [i["kind"] for i in pal.items]
    assert kinds[0] == "project" and "document" in kinds
    doc_row = kinds.index("document")
    assert pal.choose(doc_row) == ("document", (p["id"], docs[0]["id"]))
    assert "rev" in pal.items[doc_row]["subtitle"]
    assert pal.choose(99) is None
    c.close_workspace()
    pal.search("manifold")
    assert pal.items == [] and pal.error is None
