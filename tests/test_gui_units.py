# SPDX-License-Identifier: GPL-3.0-or-later
"""The Units window (ADR-025): the register read through the controller, a unit's drawings with their flags, the engineering
changes due on it, filtering, and the window closing with its workspace. The view model runs headless; the window offscreen.

Run with:  QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_units.py"""
import pytest

from revisionworx import changes, documents as D, projects, units
from revisionworx.gui.controller import Controller
from revisionworx.gui.viewmodels import unit_row, unit_sections
from revisionworx.projects import NewProject
from revisionworx.units import effect
from revisionworx.workspace import Workspace


def release(ws, rev_id):
    for state in ("in_review", "approved", "released"):
        D.transition(ws, rev_id, state)
    return ws.get("revision", rev_id)


@pytest.fixture
def plant(tmp_path):
    """Two units on standard A (FRM-200 rev A); ECO-0001 revises FRM-200 to B, decided as a required retrofit and installed
    (reported) on NH-26-0001. NH-26-0002 is still pending."""
    share = tmp_path / "share"
    (share / "Eng").mkdir(parents=True)
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    ctl.create_workspace(share, "Units")
    ctl.add_root("Eng", share / "Eng")
    with Workspace.open(share, writable=True) as ws:
        rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
        path = projects.project_folder(ws, rec) / "frame.idw"
        path.write_bytes(b"IDW rev A")
        doc, rev_a = D.register_document(ws, rec["id"], path, number="FRM-200", title="Frame")
        release(ws, rev_a["id"])
        svc = units.service_for(ws)
        std = svc.create_standard("HPU-75-A", "Standard A", {doc["id"]: rev_a["id"]})
        for value in ("NH-26-0001", "NH-26-0002"):
            svc.register(identifiers=[{"scheme": "serial", "value": value, "issuer": ""}], standard=std["id"],
                         customer="Acme", status="in-service", model={"part": "", "number": "HPU-75"})
        cs = changes.service_for(ws)
        ecr = cs.create_request("Thicker frame", "cracks", affected=[{"item_type": "document", "item_id": doc["id"]}])
        cs.submit(ecr.id)
        cs.begin_review(ecr.id)
        cs.approve(ecr.id)
        eco = cs.create_order([ecr.id])
        cs.start_work(eco.id)
        co = D.checkout(ws, doc["id"])
        path.write_bytes(b"IDW rev B")
        rev_b = D.checkin(ws, doc["id"], co["token"], "thicker")
        release(ws, rev_b["id"])
        cs.record_implementation(eco.id, cs.get(eco.id).affected[0].key, rev_id=rev_b["id"])
        cs.mark_implemented(eco.id)
        effect.decide(svc, eco.id, "retrofit-required", reason="cracks found in service")
        effect.install(svc, "NH-26-0001", eco.id)
    ctl.open_workspace(share)
    return ctl


def test_the_register_lists_units_with_their_changes_due(plant):
    rows = [unit_row(u) for u in plant.units_page()]
    assert [(r["label"], r["status"], r["model"], r["outstanding"]) for r in rows] == [
        ("NH-26-0001", "in service", "HPU-75", "1"), ("NH-26-0002", "in service", "HPU-75", "1")]
    assert [u["label"] for u in plant.units_page("0002")] == ["NH-26-0002"]
    assert len(plant.units_page(outstanding_only=True)) == 2


def test_a_units_detail_reads_as_it_should(plant):
    unit_id = plant.units_page("NH-26-0001")[0]["id"]
    found = unit_sections(plant.unit_detail(unit_id))
    assert found[0][0] == "NH-26-0001" and found[0][1][:2] == ["Status: in service · Model: HPU-75", "Serial: NH-26-0001"]
    sections = dict((h or f"_{i}", lines) for i, (h, lines) in enumerate(found))
    assert sections["_1"] == ["1 required engineering change not finished on this unit."]
    assert sections["Drawings"][0] == "Build standard: HPU-75-A Standard A"
    assert sections["Drawings"][1] == "FRM-200: rev B (Released)  [reported; this unit only]"
    [change] = sections["Engineering changes"]
    assert change == "ECO-0001 Thicker frame: installed (outstanding); FRM-200 rev A to B"
    assert sections["History"][0].endswith("FRM-200 rev B")


def test_a_unit_still_on_the_old_revision_says_a_later_one_is_released(plant):
    unit_id = plant.units_page("NH-26-0002")[0]["id"]
    sections = dict((h or f"_{i}", lines) for i, (h, lines) in enumerate(unit_sections(plant.unit_detail(unit_id))))
    assert sections["Drawings"][1] == ("FRM-200: rev A (Superseded)  [a later revision is released, not applied to this "
                                       "unit (rev B)]")
    assert sections["Engineering changes"] == ["ECO-0001 Thicker frame: pending (outstanding); FRM-200 rev A to B"]


@pytest.mark.qt
def test_the_window_finds_filters_and_closes_with_its_workspace(plant):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(plant)
    w.open_location(plant.location, quiet=True)
    win = w.show_units()
    assert win.table.rowCount() == 2 and win.status.text() == "2 units, 2 with engineering changes due"
    assert "1 required engineering change not finished" in win.detail_text()
    assert win.select_unit("NH-26-0002") and "rev A (Superseded)" in win.detail_text()
    win.search.setText("0001")
    win.refresh()
    assert win.table.rowCount() == 1 and win.selected_id() == plant.units_page("NH-26-0001")[0]["id"]
    win.search.setText("nothing like this")
    win.refresh()
    assert win.table.rowCount() == 0 and win.status.text() == "No units match." and win.detail_text() == ""
    assert w.show_units() is win                                  # one window at a time, refreshed
    w.close_ws()
    assert w.units_window is None and not win.isVisible()
    w.close()
