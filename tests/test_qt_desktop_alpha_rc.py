# SPDX-License-Identifier: GPL-3.0-or-later
"""Offscreen regressions for defects found by rendering and auditing the alpha GUI at b4c6d72
(PR #14, issue #13). Real core and real temp files; only the failure being studied is injected.

Run with:  QT_QPA_PLATFORM=offscreen python -m pytest -m qt tests/test_qt_desktop_alpha_rc.py
"""
import os
import threading
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx import documents, projects  # noqa: E402
from revisionworx.gui.controller import Controller, describe_error  # noqa: E402
from revisionworx.workspace import CommittedNotApplied  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def ctl(tmp_path):
    return Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")


@pytest.fixture(autouse=True)
def boxes(monkeypatch):
    """Record instead of blocking on modal message boxes."""
    shown = []
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[1:])) or 0))
    return shown


def pump(qapp, ctl, until, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        ctl.drain()
        qapp.processEvents()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def window(qapp, ctl, tmp_path, names=("a.txt",)):
    from revisionworx.gui.qt.mainwindow import MainWindow
    home = tmp_path / "home"
    p = ctl.quick_start(home, "Proj", "P-1")
    docs = []
    for name in names:
        src = tmp_path / name
        src.write_bytes(name.encode())
        docs.append(ctl.import_document(p["id"], src)[0])
    w = MainWindow(ctl)
    assert w.open_location(home, quiet=True, select_project=p["id"])
    qapp.processEvents()
    return w, home, p, docs


def commits_then_lags(monkeypatch, name):
    """The real core operation commits, then reports that derived files lag (CORE_API §4)."""
    real = getattr(documents, name) if hasattr(documents, name) else getattr(projects, name)
    module = documents if hasattr(documents, name) else projects

    def lagging(ws, *a, **k):
        real(ws, *a, **k)
        raise CommittedNotApplied(0, "0" * 32, OSError("records lag"))
    monkeypatch.setattr(module, name, lagging)


# ---------------------------------------------------------------------------- layout
def _width_report(w) -> str:
    """Which widgets ask for the most width, so a failure on a platform we cannot run locally explains itself."""
    rows = []
    for child in w.findChildren(QtWidgets.QWidget):
        text = child.text() if hasattr(child, "text") and callable(child.text) else ""
        rows.append((child.minimumSizeHint().width(), type(child).__name__, child.objectName(), str(text)[:40]))
    rows.sort(key=lambda r: r[0], reverse=True)
    fm = w.fontMetrics()
    return (f"font={w.font().family()!r} pt={w.font().pointSizeF()} "
            f"advance('Compare with previous')={fm.horizontalAdvance('Compare with previous')} "
            f"dpi={w.logicalDpiX()} widest={rows[:14]}")


def _width_limit(qapp, w) -> float:
    """1000 px on a real screen. Offscreen font metrics are not real: on Windows CI the fallback font is about
    1.9x wider than Linux at the same size and DPI (CI run 41569788201), so offscreen the limit scales with the
    text. The Windows job also runs these checks on the native platform, with real fonts, unscaled."""
    if qapp.platformName() != "offscreen":
        return 1000
    return 1000 * max(1.0, w.fontMetrics().horizontalAdvance("Compare with previous") / 131)


def _row_capacity(view) -> float:
    """How many data rows the view can show."""
    return view.viewport().height() / max(1, view.verticalHeader().defaultSectionSize())


@pytest.mark.parametrize("size", ["default", "minimum"])
def test_history_and_actions_are_usable_at_default_and_minimum_size(qapp, ctl, tmp_path, size):
    """Regression: at the default 1200x760 the revision history showed no rows, and at the minimum
    size the action buttons and the history were not on screen at all."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path, ("a.txt", "b.txt", "c.txt"))
    w.documents_tab.select_document(docs[0]["id"])
    w.show()
    w.resize(1200, 760) if size == "default" else w.resize(w.minimumSizeHint())
    qapp.processEvents()
    tab = w.documents_tab
    assert _row_capacity(tab.history) >= 3, tab.history.viewport().height()
    assert _row_capacity(tab.table) >= 3, tab.table.viewport().height()
    for btn in (tab.checkout_btn, tab.checkin_btn, tab.cancel_btn, tab.open_file_btn, tab.open_dir_btn,
                tab.transition_btn, tab.export_btn, tab.compare_btn, tab.restore_btn):
        assert btn.isVisible() and btn.height() >= btn.minimumSizeHint().height(), btn.text()
        assert btn.width() >= btn.minimumSizeHint().width(), btn.text()
        right = btn.mapTo(tab, btn.rect().bottomRight())
        assert right.x() <= tab.width() and right.y() <= tab.height(), btn.text()
    w.close()


def test_minimum_size_fits_a_small_laptop_screen(qapp, ctl, tmp_path):
    """1366x768 minus title bar and taskbar: the window must be able to fit."""
    w, *_ = window(qapp, ctl, tmp_path)
    hint = w.minimumSizeHint()
    print(f"platform={qapp.platformName()} minimum={hint.width()}x{hint.height()} {_width_report(w)[:120]}")
    assert hint.width() <= _width_limit(qapp, w) and hint.height() <= 700, (hint, _width_report(w))
    w.close()


def test_long_paths_and_names_do_not_force_a_wide_window(qapp, ctl, tmp_path):
    """Regression: a Windows path has no line-break opportunities, so in a word-wrapped label it set the
    window's minimum width (1340 px on Windows CI, CI run 41569041782). Long text is now clipped, with the
    full text in a tooltip. The path is written literally so this reproduces on every platform."""
    path = "C:\\Users\\" + "engineer\\" * 30 + "bracket.ipt"
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    w.detail_vm.warnings = [path]
    w.detail_vm.fields = [("Folder", path)]
    w.detail_vm.project = {**w.detail_vm.project, "name": path}
    w.render_details()
    tab.list_vm.error = path
    tab.render_list()  # reselects and re-reads the document, so its text is set after this
    tab.panel.detail["document"]["title"] = path
    tab.panel.set_message(path, "warning")
    w.status_vm.health = {"ok": False, "blocked": False, "problems": [], "message": path}
    w.health_banner.setText(path)
    w.health_banner.show()
    w.start_page.show_error(path)
    w.start_page.preview.setText(path)
    w.start_page.preview.setToolTip(path)
    qapp.processEvents()
    assert w.minimumSizeHint().width() <= _width_limit(qapp, w), (w.minimumSizeHint(), _width_report(w))
    assert path in tab.status.toolTip() and path in tab.summary.toolTip() and path in w.warning_label.toolTip()
    assert path in w.start_page.error_label.toolTip() and path in w.start_page.preview.toolTip()
    w.close()


def test_project_warning_stays_visible_on_the_documents_tab(qapp, ctl, tmp_path):
    w, *_ = window(qapp, ctl, tmp_path)
    w.show()
    qapp.processEvents()
    w.detail_vm.warnings = ["folder missing"]
    w.render_details()
    qapp.processEvents()
    assert w.warning_label.isVisible() and "folder missing" in w.warning_label.text()
    assert w.project_heading.isVisible() and "Proj" in w.project_heading.text()
    w.close()


# ---------------------------------------------------------------------------- saved with a warning
def test_import_through_tab_saved_with_warning_does_not_raise(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: the tab's completion callback indexed ``res.value`` (None on a warning), which
    raised out of the GUI poll after the import had been saved."""
    w, _home, p, _docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    commits_then_lags(monkeypatch, "register_document")
    src = tmp_path / "new.txt"
    src.write_bytes(b"new")
    tab.import_file()
    dlg = tab.dialogs[-1]
    dlg.source.setText(str(src))
    dlg.start()
    pump(qapp, ctl, lambda: dlg.flow.state in ("done", "error") and not ctl.busy)
    assert dlg.flow.state == "done" and "saved" in dlg.flow.message
    assert any(r["rel_path"] == "new.txt" for r in tab.list_vm.rows)
    assert w.observable_state()["selected_document"] == \
        next(r["id"] for r in tab.list_vm.rows if r["rel_path"] == "new.txt")
    assert not [b for b in boxes if b[0] == "critical"]
    dlg.close()
    w.close()


def test_register_saved_with_warning_is_shown_and_selected(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: a saved-with-warning registration showed nothing at all."""
    w, _home, p, _docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    f = ctl.working_file(_docs[0]["id"]).parent / "in_place.txt"
    f.write_bytes(b"x")
    commits_then_lags(monkeypatch, "register_document")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(f), "")))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    tab.register_file()
    pump(qapp, ctl, lambda: not ctl.busy and any(r["rel_path"] == "in_place.txt" for r in tab.list_vm.rows))
    assert [b for b in boxes if b[0] == "warning"] and not [b for b in boxes if b[0] == "critical"]
    assert tab.panel.doc_id == next(r["id"] for r in tab.list_vm.rows if r["rel_path"] == "in_place.txt")
    w.close()


def test_register_success_selects_the_new_document(qapp, ctl, tmp_path, monkeypatch):
    w, _home, p, _docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    f = ctl.working_file(_docs[0]["id"]).parent / "in_place.txt"
    f.write_bytes(b"x")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(f), "")))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    tab.register_file()
    pump(qapp, ctl, lambda: not ctl.busy and tab.panel.doc_id is not None)
    assert tab.panel.detail["document"]["rel_path"] == "in_place.txt"
    w.close()


def test_quick_start_saved_with_warning_opens_without_inviting_a_duplicate(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: the project was saved but the start page stayed put showing it like a failure,
    inviting a second click."""
    from revisionworx.gui.qt.mainwindow import MainWindow
    commits_then_lags(monkeypatch, "create_project")
    w = MainWindow(ctl)
    sp = w.start_page
    sp.folder_edit.setText(str(tmp_path / "home"))
    sp.name_edit.setText("Lagging")
    sp.create_btn.click()
    qapp.processEvents()
    st = w.observable_state()
    assert st["page"] == "workspace" and [x["name"] for x in st["projects"]] == ["Lagging"]
    assert [b for b in boxes if b[0] == "warning"] and sp.name_edit.text() == ""
    w.close()


def test_new_project_saved_with_warning_closes_the_dialog(qapp, ctl, tmp_path, monkeypatch, boxes):
    from revisionworx.gui.qt.dialogs import NewProjectDialog
    w, *_ = window(qapp, ctl, tmp_path)
    commits_then_lags(monkeypatch, "create_project")
    dlg = NewProjectDialog(ctl, w)
    dlg.edits["name"].setText("Second")
    dlg.submit()
    assert dlg.result() == 1  # QDialog.Accepted: no second attempt invited
    assert [b for b in boxes if b[0] == "warning"] and not [b for b in boxes if b[0] == "critical"]
    w.close()


def test_main_window_error_presents_committed_as_a_warning(qapp, ctl, tmp_path, boxes):
    w, *_ = window(qapp, ctl, tmp_path)
    w.error("Cannot change status", CommittedNotApplied(1, "0" * 32, OSError("lag")))
    assert [b[0] for b in boxes] == ["warning"]
    w.close()


def test_adopt_saved_with_warning_does_not_raise(qapp, ctl, tmp_path, monkeypatch):
    """Regression: same ``res.value`` defect as import, in the adopt flow."""
    from revisionworx.gui.viewmodels import AdoptFlow
    from revisionworx.gui.controller import JobResult
    flow = AdoptFlow(ctl)
    flow._adopt_done(JobResult("adopt", warning="Saved. Your change is recorded."))
    assert flow.state == "adopted" and "saved" in flow.progress_text and "Saved" in flow.report_text


# ---------------------------------------------------------------------------- false failure
def test_checkin_saved_but_token_cleanup_fails_is_not_reported_as_failure(qapp, ctl, tmp_path, monkeypatch):
    """Regression: a committed check-in was shown as a red error when the local token file could
    not be updated, inviting the user to redo work that had been saved."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    tab.checkout()
    ctl.working_file(docs[0]["id"]).write_bytes(b"two")
    monkeypatch.setattr(ctl.tokens, "forget", lambda *a: (_ for _ in ()).throw(PermissionError(13, "denied")))
    tab.panel.start_checkin("Saved but token store is read-only", on_done=lambda _r: tab.list_vm.refresh())
    pump(qapp, ctl, lambda: not ctl.busy and not tab.panel.message.startswith("Checking in"))
    assert tab.panel.message.startswith("Checked in") and tab.panel.message_level == "warning"
    assert tab.history_model.rowCount() == 2
    w.close()


def test_checkout_saved_but_token_store_fails_is_a_warning(qapp, ctl, tmp_path, monkeypatch, boxes):
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    monkeypatch.setattr(ctl.tokens, "put", lambda *a: (_ for _ in ()).throw(PermissionError(13, "denied")))
    tab.checkout()
    assert tab.panel.message_level == "warning" and "administrator" in tab.panel.message
    assert tab.panel.detail["checkout"] is not None and not tab.panel.can_checkin
    assert not [b for b in boxes if b[0] == "critical"]
    w.close()


def test_cancel_saved_but_token_cleanup_fails_is_a_warning(qapp, ctl, tmp_path, monkeypatch, boxes):
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    tab.checkout()
    monkeypatch.setattr(ctl.tokens, "forget", lambda *a: (_ for _ in ()).throw(PermissionError(13, "denied")))
    tab.run(lambda: tab.panel.cancel(revert=False))
    assert tab.panel.detail["checkout"] is None and tab.panel.message_level == "warning"
    assert not [b for b in boxes if b[0] == "critical"]
    w.close()


def test_checkin_outcome_unknown_says_so(qapp, ctl, tmp_path, monkeypatch):
    """If the journal cannot be read after an unexpected error, the app must not imply either
    outcome: it says that it could not confirm and to check the history before retrying."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    tab.checkout()
    ctl.working_file(docs[0]["id"]).write_bytes(b"two")
    monkeypatch.setattr(documents, "checkin", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "I/O error")))
    monkeypatch.setattr(ctl, "transaction_outcome", lambda txid: {"state": "unknown", "error": "unreadable"})
    tab.panel.start_checkin("Outcome cannot be determined")
    pump(qapp, ctl, lambda: not ctl.busy and not tab.panel.message.startswith("Checking in"))
    assert tab.panel.message_level == "error" and "could not confirm" in tab.panel.message.lower()
    assert "Checked in" not in tab.status.text()
    w.close()


# ---------------------------------------------------------------------------- selection truth
def test_failed_document_load_never_leaves_the_previous_document_actionable(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: selecting B when B could not be read left A in the panel with A's actions
    enabled while row B was highlighted, so Check out acted on the wrong document."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path, ("a.txt", "b.txt"))
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    assert tab.panel.doc_id == docs[0]["id"] and tab.checkout_btn.isEnabled()
    real = ctl.document_detail

    def unreadable(doc_id, verify_hash=False):
        if doc_id == docs[1]["id"]:
            raise PermissionError(13, "Permission denied", "b.txt")
        return real(doc_id, verify_hash)
    monkeypatch.setattr(ctl, "document_detail", unreadable)
    tab.select_document(docs[1]["id"])
    qapp.processEvents()
    assert tab.panel.doc_id is None and not tab.checkout_btn.isEnabled() and not tab.history_model.rowCount()
    assert tab.panel.message_level == "error" and "Permission denied" in tab.status.text()
    assert not boxes  # shown inline: F5 must not produce a stream of modal dialogs
    w.close()


def test_filter_that_hides_the_selected_document_clears_the_panel(qapp, ctl, tmp_path):
    """Regression: actions stayed enabled for a document that was no longer in the list."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path, ("a.txt", "b.txt"))
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    tab.filter.setText("b.txt")
    tab.apply_filter()
    qapp.processEvents()
    assert [r["rel_path"] for r in tab.list_vm.rows] == ["b.txt"]
    assert tab.panel.doc_id is None and not tab.checkout_btn.isEnabled()
    w.close()


def test_project_list_refresh_keeps_the_selected_project(qapp, ctl, tmp_path):
    """Regression: refreshing the project list dropped the selection while the details stayed."""
    w, _home, p, docs = window(qapp, ctl, tmp_path)
    w.documents_tab.select_document(docs[0]["id"])
    qapp.processEvents()
    w.list_vm.refresh()
    qapp.processEvents()
    st = w.observable_state()
    assert st["selected_project"] == p["id"] and st["selected_document"] == docs[0]["id"]
    w.close()


def test_search_that_hides_the_selected_project_clears_the_details(qapp, ctl, tmp_path):
    w, _home, p, docs = window(qapp, ctl, tmp_path)
    w.search.setText("zzz-nothing")
    w.apply_search()
    qapp.processEvents()
    st = w.observable_state()
    assert st["selected_project"] is None and st["selected_document"] is None and not st["documents"]
    assert w.detail_vm.project is None and not w.archive_btn.isEnabled()
    w.close()


# ---------------------------------------------------------------------------- read-only, busy
def _block(home):
    import json
    entry = sorted((home / ".revisionworx" / "journal").glob("*.json"))[-1]
    data = json.loads(entry.read_text())
    data["op"] += "-tampered"
    entry.write_text(json.dumps(data))


def _menu_action(w, text):
    for top in w.menuBar().actions():
        for a in top.menu().actions():
            if a.text() == text:
                return a
    raise AssertionError(text)


WRITE_MENU = ("Add engineering root…", "New project…", "Adopt existing project…")


def test_recovery_blocked_disables_every_write_action(qapp, ctl, tmp_path, boxes):
    """Regression: Change state and three menu items stayed enabled in the read-only state."""
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    w.documents_tab.select_document(docs[0]["id"])
    qapp.processEvents()
    assert w.documents_tab.transition_btn.isEnabled()
    _block(home)
    w.refresh_all()
    qapp.processEvents()
    tab = w.documents_tab
    st = w.observable_state()
    assert st["read_only"] and st["selected_document"] == docs[0]["id"]
    assert not any(st["actions"][k] for k in ("new_project", "import", "register", "checkout", "checkin",
                                              "cancel_checkout", "restore", "change_state", "archive", "adopt"))
    assert not tab.transition_btn.isEnabled() and not w.archive_btn.isEnabled()
    for text in WRITE_MENU:
        assert not _menu_action(w, text).isEnabled(), text
    assert tab.export_btn.isEnabled()  # browsing and export still work
    assert not boxes
    w.close()


@pytest.fixture
def running_job(ctl):
    """Call it to start a background job; returns the event that lets the job finish."""
    release, entered = threading.Event(), threading.Event()

    def start():
        def job():
            entered.set()
            release.wait(30)
        assert ctl._run("test", job, None)
        entered.wait(5)
        return release
    yield start
    release.set()
    while ctl.busy:
        ctl.drain()
        time.sleep(0.01)


def test_background_job_disables_colliding_project_actions(qapp, ctl, tmp_path):
    from PySide6.QtCore import QCoreApplication
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    w.documents_tab.select_document(docs[0]["id"])
    qapp.processEvents()
    release, entered = threading.Event(), threading.Event()
    assert ctl._run("test", lambda: (entered.set(), release.wait(30)), None)
    entered.wait(5)
    w.poll()
    QCoreApplication.processEvents()
    st = w.observable_state()
    assert st["busy"] and not any(st["actions"][k] for k in ("new_project", "adopt", "archive", "change_state"))
    release.set()
    pump(qapp, ctl, lambda: not ctl.busy)
    w.poll()
    st = w.observable_state()
    assert st["actions"]["new_project"] and st["actions"]["archive"] and st["actions"]["change_state"]
    w.close()


def test_quit_is_refused_while_a_job_runs(qapp, ctl, tmp_path, running_job, boxes):
    """Regression: quitting killed the daemon worker in the middle of a check-in or import."""
    w, *_ = window(qapp, ctl, tmp_path)
    w.show()
    qapp.processEvents()
    release = running_job()
    assert w.close() is False and w.isVisible()
    assert [b[0] for b in boxes] == ["information"]
    release.set()
    pump(qapp, ctl, lambda: not ctl.busy)
    assert w.close() is True


def test_closing_or_switching_workspace_is_refused_while_a_job_runs(qapp, ctl, tmp_path, running_job, boxes):
    """Regression: closing the workspace under a running job made its completion read a different
    workspace, or none."""
    w, home, _p, _docs = window(qapp, ctl, tmp_path)
    other = tmp_path / "other"
    release = running_job()
    w.close_ws()
    assert ctl.location == home and w.observable_state()["page"] == "workspace"
    assert w.open_location(other, quiet=True) is False
    assert ctl.location == home and w.observable_state()["page"] == "workspace"
    release.set()
    pump(qapp, ctl, lambda: not ctl.busy)
    w.close_ws()
    assert ctl.location is None
    w.close()


def test_open_file_failure_is_reported(qapp, ctl, tmp_path, monkeypatch):
    """Regression: with no application for the file type, Open file silently did nothing."""
    from PySide6.QtGui import QDesktopServices
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda _u: False))
    tab.open_file()
    assert tab.panel.message_level == "warning" and "a.txt" in tab.status.text()
    w.close()


# ---------------------------------------------------------------------------- local-only wording
def test_errors_do_not_advertise_shared_network_use(qapp, ctl):
    """Director tier 1: the alpha is local-only and must not imply otherwise."""
    for exc in (PermissionError(13, "denied", "x"), OSError(5, "I/O error"), documents.DocumentError("x")):
        text = describe_error(exc).lower()
        assert "share" not in text and "network" not in text, text


def test_create_workspace_prompt_is_local(qapp, ctl, monkeypatch):
    from revisionworx.gui.qt.mainwindow import MainWindow
    titles = []
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda _parent, title, *a, **k: titles.append(title) or ""))
    w = MainWindow(ctl)
    w.create_ws()
    assert titles and "shared" not in titles[0].lower()
    w.close()


# ---------------------------------------------------------------------------- crash and write-time blocks
def _cli(home, *args):
    from revisionworx.cli import main
    assert main(["-w", str(home), *args]) in (0, None)


def test_recovery_block_found_while_writing_makes_the_gui_read_only(qapp, ctl, tmp_path, boxes):
    """Regression: the read-only health check cannot see a blocked lock namespace, so after refusing a
    write the GUI stayed writable and invited the same failure again, against contract §4."""
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    orphan = home / ".revisionworx" / "locks" / ".releasing-deadbeef"
    orphan.write_text("{}")
    assert w.observable_state()["actions"]["checkout"]
    tab.checkout()
    w.poll()
    qapp.processEvents()
    st = w.observable_state()
    assert [b[0] for b in boxes] == ["critical"] and "clear-orphan" in boxes[0][1][1]
    assert st["read_only"] and "RECOVERY BLOCKED" in st["health_banner"] and "READ-ONLY" in st["health_banner"]
    assert not any(st["actions"][k] for k in ("checkout", "import", "new_project", "change_state"))
    tab.checkout()  # guarded: no second dialog and no retry loop
    assert len(boxes) == 1
    _cli(home, "lock", "clear-orphan", orphan.name, "--reason", "test: writer confirmed stopped")
    w.refresh_all()  # F5 after the administrator's repair
    qapp.processEvents()
    st = w.observable_state()
    # F5 re-checks the write-found block with a writable open (no commit), which also applies the clear.
    assert not st["read_only"] and "RECOVERY BLOCKED" not in st["health_banner"] and st["actions"]["checkout"]
    tab.checkout()
    assert tab.panel.detail["checkout"] is not None and len(boxes) == 1
    w.refresh_all()
    assert w.observable_state()["health_banner"] == ""
    w.close()


def test_recovery_block_found_by_a_background_job_makes_the_gui_read_only(qapp, ctl, tmp_path):
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    tab.checkout()
    ctl.working_file(docs[0]["id"]).write_bytes(b"two")
    (home / ".revisionworx" / "locks" / ".releasing-deadbeef").write_text("{}")
    tab.panel.start_checkin("Refused by the block")
    pump(qapp, ctl, lambda: not ctl.busy and not tab.panel.message.startswith("Checking in"))
    w.poll()
    qapp.processEvents()
    st = w.observable_state()
    assert st["read_only"] and st["message_level"] == "error" and "Checked in" not in st["document_status"]
    assert len(st["history"]) == 1 and not st["actions"]["checkin"]
    w.close()


def test_lock_left_by_a_crash_has_a_working_way_out(qapp, ctl, tmp_path, boxes):
    """Regression: a crash left a lock and the only advice was to try again in a moment, for ever. The
    error now gives the token and the commands, and following them really does recover."""
    import subprocess
    import sys
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    code = ("import os, sys; from pathlib import Path; from revisionworx.workspace import Workspace; "
            "Workspace.open(Path(sys.argv[1]), writable=True); os._exit(9)")
    subprocess.run([sys.executable, "-c", code, str(home)], check=False,
                   env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    from revisionworx.workspace import Workspace
    token = Workspace.read_writer_lock(home)["token"]
    tab.checkout()
    assert [b[0] for b in boxes] == ["critical"]
    text = boxes[0][1][1]
    assert token in text and "lock break" in text and "crash" in text
    assert tab.panel.detail["checkout"] is None and not w.observable_state()["read_only"]  # locked, not corrupt
    _cli(home, "lock", "break", "--token", token, "--reason", "test: the session crashed")
    tab.checkout()
    assert tab.panel.detail["checkout"] is not None and len(boxes) == 1
    w.close()


# ---------------------------------------------------------------------------- job completion is one moment
def test_job_result_is_delivered_with_the_ui_already_settled(qapp, ctl, tmp_path, monkeypatch):
    """Regression (Integration, PR #14, run 41572552095): the result arrived while the job still counted as
    busy, so the history showed the new revision with Check out disabled. A caller draining results without
    the window's timer could stay that way for good. The worker's finish is slowed here to make it certain."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    w.timer.stop()  # only ctl.drain() below, as documented for test harnesses
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    qapp.processEvents()
    tab.checkout()
    ctl.working_file(docs[0]["id"]).write_bytes(b"two")
    real = ctl._busy

    class SlowRelease:
        def __getattr__(self, name):
            return getattr(real, name)

        def release(self):
            time.sleep(0.3)
            real.release()
    monkeypatch.setattr(ctl, "_busy", SlowRelease())
    seen = []
    tab.panel.start_checkin("Settled at once", on_done=lambda _r: seen.append(ctl.busy))
    pump(qapp, ctl, lambda: tab.history_model.rowCount() == 2)
    st = w.observable_state()
    assert seen == [False] and not st["busy"]
    assert st["actions"]["checkout"] and not st["actions"]["checkin"], st["actions"]
    w.timer.start(100)
    w.close()


def test_busy_stays_true_until_the_result_is_delivered(qapp, ctl):
    """``busy`` false means the outcome is visible, so a harness can stop waiting there."""
    release = threading.Event()
    done = []
    assert ctl._run("test", lambda: release.wait(30), lambda r: done.append(r))
    release.set()
    end = time.time() + 5
    while ctl.results.empty() and time.time() < end:
        time.sleep(0.01)
    assert ctl.busy and not done
    assert ctl._run("second", lambda: None, None) is False  # nothing overlaps an undelivered result
    ctl.drain()
    assert done and not ctl.busy


def test_wait_idle_delivers_a_finished_job(qapp, ctl):
    done = []
    assert ctl._run("test", lambda: None, lambda r: done.append(r))
    assert ctl.wait_idle(5) is True and done and not ctl.busy


# ---------------------------------------------------------------------------- found on a real display (Xvfb)
def test_typing_a_path_gives_the_file_name_as_title(qapp, ctl, tmp_path):
    """Regression: the title was filled once, from the first keystroke, so typing /x/bracket.ipt recorded
    a document titled "x". Found by driving the real app with real keys."""
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import ImportDialog
    w, _home, p, _docs = window(qapp, ctl, tmp_path)
    src = tmp_path / "bracket.ipt"
    src.write_bytes(b"x")
    dlg = ImportDialog(ctl, p["id"], "Proj", w)
    QTest.keyClicks(dlg.source, str(src))
    assert dlg.title.text() == "bracket"
    dlg.start()
    pump(qapp, ctl, lambda: dlg.flow.state == "done" and not ctl.busy)
    # Diagnostic only: a "done" import with no result is the saved-with-a-warning path (e.g. the Windows
    # writer-lock release gave up). Show the flow's own message so CI logs say which outcome it was.
    assert dlg.flow.result is not None, f"import ended {dlg.flow.state!r} without a result: {dlg.flow.message}"
    assert dlg.flow.result[0]["title"] == "bracket"
    w.close()


def test_a_title_the_user_typed_is_kept_when_the_path_changes(qapp, ctl, tmp_path):
    from PySide6.QtTest import QTest
    from revisionworx.gui.qt.dialogs import ImportDialog
    w, _home, p, _docs = window(qapp, ctl, tmp_path)
    dlg = ImportDialog(ctl, p["id"], "Proj", w)
    QTest.keyClicks(dlg.source, "first.ipt")
    dlg.title.clear()
    QTest.keyClicks(dlg.title, "Mounting bracket")
    dlg.source.setText("second.ipt")
    assert dlg.title.text() == "Mounting bracket"
    w.close()


def _export(qapp, ctl, tab, monkeypatch, dest):
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(dest), "")))
    tab.export()
    pump(qapp, ctl, lambda: not ctl.busy)
    qapp.processEvents()


def test_export_says_where_the_file_went(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: a successful export showed nothing at all."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    dest = tmp_path / "out.txt"
    _export(qapp, ctl, tab, monkeypatch, dest)
    assert dest.read_bytes() == b"a.txt" and "out.txt" in tab.status.text() and tab.panel.message_level == "ok"
    assert not boxes
    w.close()


def test_export_replaces_a_file_the_save_dialog_confirmed(qapp, ctl, tmp_path, monkeypatch, boxes):
    """Regression: after the dialog's own "replace?" was answered yes, export failed with a disk error."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"old")
    _export(qapp, ctl, tab, monkeypatch, dest)
    assert dest.read_bytes() == b"a.txt" and tab.panel.message_level == "ok" and not boxes
    w.close()


def test_export_cannot_overwrite_a_managed_working_file(qapp, ctl, tmp_path, monkeypatch):
    """Exporting an older revision over the working file would change it without a checkout."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    wf = ctl.working_file(docs[0]["id"])
    wf.write_bytes(b"precious edits")
    _export(qapp, ctl, tab, monkeypatch, wf)
    assert wf.read_bytes() == b"precious edits"
    assert tab.panel.message_level == "error" and "Restore" in tab.status.text()
    w.close()


def test_export_cannot_write_inside_the_workspace_metadata(qapp, ctl, tmp_path, monkeypatch):
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    dest = home / ".revisionworx" / "out.txt"
    _export(qapp, ctl, tab, monkeypatch, dest)
    assert not dest.exists() and tab.panel.message_level == "error"
    w.close()


def test_export_failure_is_reported(qapp, ctl, tmp_path, monkeypatch):
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    _export(qapp, ctl, tab, monkeypatch, tmp_path / "no_such_folder" / "out.txt")
    assert tab.panel.message_level == "error" and "Exported" not in tab.status.text()
    assert "out.txt" in tab.status.text() and "checkout" not in tab.panel.message  # about the export, not a checkout
    w.close()


def test_export_runs_in_the_background(qapp, ctl, tmp_path, monkeypatch):
    """A CAD revision can be gigabytes; the window must not freeze."""
    w, _home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "bg.txt"), "")))
    release = threading.Event()
    real = documents.export_revision
    monkeypatch.setattr(documents, "export_revision", lambda *a, **k: (release.wait(30), real(*a, **k))[1])
    tab.export()
    assert ctl.busy and tab.panel.message.startswith("Exporting")
    release.set()
    pump(qapp, ctl, lambda: not ctl.busy)
    assert (tmp_path / "bg.txt").exists()
    w.close()


def test_default_window_leaves_room_for_the_title_bar(qapp, ctl, monkeypatch):
    """Regression: on a 768 px high screen the status bar was cut off (seen on a real display)."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from revisionworx.gui.qt.mainwindow import MainWindow

    class Screen:
        def availableGeometry(self):
            return QRect(0, 0, 1366, 768)
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: Screen()))
    w = MainWindow(ctl)
    assert w.height() <= 768 - 30 and w.width() <= 1366
    w.close()


def test_opening_a_project_selects_its_first_document(qapp, ctl, tmp_path):
    """Regression: after a restart the panel was empty and Ctrl+K silently did nothing (real display)."""
    w, _home, p, docs = window(qapp, ctl, tmp_path, ("a.txt", "b.txt"))
    st = w.observable_state()
    assert st["selected_document"] == docs[0]["id"] and st["actions"]["checkout"]
    w.close()


def test_an_empty_project_selects_no_document(qapp, ctl, tmp_path):
    from revisionworx.gui.qt.mainwindow import MainWindow
    p = ctl.quick_start(tmp_path / "home", "Empty")
    w = MainWindow(ctl)
    assert w.open_location(tmp_path / "home", quiet=True, select_project=p["id"])
    st = w.observable_state()
    assert st["selected_project"] == p["id"] and st["selected_document"] is None
    w.close()


def test_error_text_can_be_selected_and_copied(qapp, ctl, tmp_path, monkeypatch):
    """Regression: the crash-lock message holds a command with a 32-character token, and on Linux the
    dialog's text could not be selected (seen on a real display)."""
    from PySide6.QtCore import Qt
    w, *_ = window(qapp, ctl, tmp_path)
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Critical, "t", "copy me", parent=w)
    box.show()
    qapp.processEvents()
    labels = [x for x in box.findChildren(QtWidgets.QLabel) if x.text() == "copy me"]
    assert labels and labels[0].textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    box.close()
    w.close()


def test_crash_lock_message_names_the_real_folder(qapp, ctl, tmp_path, boxes):
    from revisionworx.workspace import Workspace
    w, home, _p, docs = window(qapp, ctl, tmp_path)
    tab = w.documents_tab
    tab.select_document(docs[0]["id"])
    holder = Workspace.open(home, writable=True)
    try:
        tab.checkout()
    finally:
        holder.close()
    assert f'-w "{home}"' in boxes[0][1][1]
    w.close()
