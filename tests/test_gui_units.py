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


def test_a_units_detail_shows_its_deviations_and_says_when_one_is_past_its_end(plant, tmp_path):
    from revisionworx.units import deviation
    with Workspace.open(tmp_path / "share", writable=True) as ws:
        svc = units.service_for(ws)
        unit_id = svc.resolve("NH-26-0001")["id"]
        [rec] = [r for r in effect.all_compliance(svc)[0] if r["unit"] == unit_id]
        effect.verify(svc, "NH-26-0001", rec["change"])                   # nothing else due on it
        deviation.approve(svc, "concession", title="Weld undercut", departure="W4 undercut 0.6 mm",
                          reason="load test passed", units=["NH-26-0001"])
        deviation.approve(svc, "deviation", title="PMP-11 for PMP-10", departure="P1 is a PMP-11", reason="shortage",
                          units=["NH-26-0001"], temporary=True, until="2020-01-31")

    def sections():
        found = unit_sections(plant.unit_detail(unit_id))
        return dict((h or f"_{i}", lines) for i, (h, lines) in enumerate(found))
    first = sections()
    assert first["_1"] == ["1 temporary deviation past its end date on this unit: DEV-0001."]
    assert first["Deviations and concessions"] == [
        "DEV-0001 deviation: PMP-11 for PMP-10: in effect (temporary, until 2020-01-31) — PAST ITS END DATE. P1 is a PMP-11",
        "CON-0001 concession: Weld undercut: in effect (permanent). W4 undercut 0.6 mm"]
    with Workspace.open(tmp_path / "share", writable=True) as ws:
        deviation.clear(units.service_for(ws), "NH-26-0001", "DEV-0001", reason="PMP-10 fitted")
    after = sections()
    assert after["_1"] == ["Nothing is due on this unit. It differs from its drawings where CON-0001 allows it."]
    assert after["Deviations and concessions"][0] == "CON-0001 concession: Weld undercut: in effect (permanent). W4 undercut 0.6 mm"
    assert after["Deviations and concessions"][1].startswith("DEV-0001 deviation: PMP-11 for PMP-10: cleared 20")


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


def test_the_controller_makes_a_handover_pack_and_records_it_as_issued(plant, tmp_path):
    from revisionworx.units import pack
    unit_id = plant.units_page("NH-26-0001")[0]["id"]
    with Workspace.open(plant.location) as ws:
        head = ws.head_seq()
    made = plant.unit_pack(unit_id, tmp_path / "out" / "NH-26-0001 handover pack")
    assert made["unit"] == "NH-26-0001" and made["pdfs"] == 0 and made["pack"] == 1   # no PDFs attached to FRM-200 here
    assert [(m["document"], m["revision"]) for m in made["missing"]] == [("FRM-200", "B")]
    assert pack.check(tmp_path / "out" / "NH-26-0001 handover pack")["ok"]
    with Workspace.open(plant.location) as ws:
        assert ws.head_seq() == head + 1                                       # the one entry: pack 1 issued
        report = pack.check(tmp_path / "out" / "NH-26-0001 handover pack", units.service_for(ws))
    assert report["register"]["state"] == "issued" and report["register"]["pack"] == 1
    assert plant.unit_detail(unit_id)["history"][-1]["summary"] == "handover pack 1 issued"


def test_the_controller_records_no_pack_when_the_workspace_is_read_only(plant, tmp_path):
    from revisionworx.gui.controller import RecoveryBlocked
    unit_id = plant.units_page("NH-26-0001")[0]["id"]
    plant._set_block("recovery is needed")
    with pytest.raises(RecoveryBlocked):
        plant.unit_pack(unit_id, tmp_path / "out" / "pack")
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


@pytest.mark.qt
def test_the_window_makes_a_handover_pack_in_a_new_folder(plant, tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from revisionworx.gui.qt import units as units_window
    from revisionworx.gui.qt.mainwindow import MainWindow
    w = MainWindow(plant)
    w.open_location(plant.location, quiet=True)
    win = w.show_units()
    packs = tmp_path / "packs"
    packs.mkdir()
    asked = []
    monkeypatch.setattr(units_window.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: (asked.append(a[1] if len(a) > 1 else ""), str(packs))[1]))
    assert win.select_unit("NH-26-0002") and win.pack_button.isEnabled()
    win.pack_button.click()
    assert asked == ["Where should the handover pack of NH-26-0002 go?"]
    assert (packs / "NH-26-0002 handover pack" / "SHA256SUMS").is_file()
    assert win.status.text().startswith(f"Handover pack 1 of NH-26-0002 saved in {packs / 'NH-26-0002 handover pack'}")
    assert "recorded in its history as issued" in win.status.text()
    assert "Not included, with the reason on its first page: FRM-200 rev A." in win.status.text()
    assert "It isn't signed" in win.status.text()
    assert "handover pack 1 issued" in win.detail_text()                    # the history shown is the new one
    win.pack_button.click()                                       # the folder is taken: the next one is used, never that one
    assert (packs / "NH-26-0002 handover pack (2)" / "SHA256SUMS").is_file()
    assert win.status.text().startswith("Handover pack 2 of NH-26-0002")
    monkeypatch.setattr(units_window.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    win.pack_button.click()                                       # cancelled: nothing made
    assert sorted(p.name for p in packs.iterdir()) == ["NH-26-0002 handover pack", "NH-26-0002 handover pack (2)"]
    win.search.setText("nothing like this")
    win.refresh()
    assert not win.pack_button.isEnabled()
    win.search.setText("")
    win.refresh()
    plant._set_block("recovery is needed")                        # read-only: a pack can't be recorded, so none is made
    win.select_unit("NH-26-0001")
    win.show_selected()
    assert not win.pack_button.isEnabled() and "read-only" in win.pack_button.toolTip()
    plant._set_block(None)
    win.show_selected()
    assert win.pack_button.isEnabled() and "recorded in the unit's history" in win.pack_button.toolTip()
    w.close_ws()
    w.close()
