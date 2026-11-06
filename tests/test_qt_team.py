# SPDX-License-Identifier: GPL-3.0-or-later
"""The desktop window with a team workspace (ADR-024): real core and real temp files, through the widgets, offscreen. Only dialogs
that would block are driven directly."""
import os
import threading
import time

import pytest

pytestmark = pytest.mark.qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from revisionworx import accounts, documents as D, projects  # noqa: E402
from revisionworx.gui.controller import Controller, describe_error  # noqa: E402
from revisionworx.gui.qt import mainwindow as mw  # noqa: E402
from revisionworx.gui.qt import team as T  # noqa: E402
from revisionworx.gui.qt.mainwindow import MainWindow  # noqa: E402
from revisionworx.gui.viewmodels import AppStatus, DocumentListModel  # noqa: E402
from revisionworx.projects import NewProject  # noqa: E402
from revisionworx.shared import NotLoggedIn, NotPermitted  # noqa: E402
from revisionworx.workspace import RecordPut, Workspace  # noqa: E402
from team_helpers import PASSWORD, add_user, computer, log_in, new_team  # noqa: E402

OTHER = PASSWORD + "!"


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def no_modal_boxes(monkeypatch):
    shown = []
    for name in ("critical", "information", "warning"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append((_n, a[1:])) or 0))
    return shown


@pytest.fixture
def team(tmp_path):
    folder, pc, code, boss = new_team(tmp_path)
    add_user(folder, pc, boss, "alice", OTHER)
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        root = projects.add_root(ws, "Projects", folder)
        project = projects.create_project(ws, NewProject(name="HPU", root_id=root))
        path = projects.project_folder(ws, project) / "bracket.idw"
        path.write_bytes(b"v1")
        doc, _rev = D.register_document(ws, project["id"], path)
    return folder, pc, code, boss, project, doc, path, tmp_path


def controller(tmp_path, name):
    return Controller(cfg_dir=tmp_path / f"cfg-{name}", cache_dir=tmp_path / f"cache-{name}", state_dir=computer(tmp_path, name))


def test_logging_in_through_the_dialog_with_the_wrong_and_the_right_password(qapp, team):
    folder, *_rest, tmp = team
    ctl = controller(tmp, "alice")
    ctl.open_workspace(folder)
    dialog = T.LoginDialog(ctl, account="alice")
    dialog.password.setText("not the password")
    dialog.submit()
    assert dialog.result() == 0 and "don't match" in dialog.error.text() and ctl.my_account() is None
    dialog.password.setText(OTHER)
    dialog.submit()
    assert dialog.outcome["must_change"] and ctl.my_account() == "alice"
    first = T.ChangePasswordDialog(ctl, current=dialog.typed_password, first_time=True)
    assert first.current.isHidden()                                # it was just typed: not asked for again, and not left on screen
    first.new.setText(OTHER + "x")
    first.again.setText("different")
    first.submit()
    assert "don't match" in first.error.text()
    first.again.setText(OTHER + "x")
    first.submit()
    assert first.error.text() == ""
    assert ctl.team()["must_change"] is False and ctl.team()["role"] == "user"


def test_the_window_asks_for_a_login_and_stays_read_only_without_one(qapp, team, no_modal_boxes, monkeypatch):
    folder, *_rest, tmp = team
    ctl = controller(tmp, "alice")
    asked = []
    monkeypatch.setattr(T.LoginDialog, "exec", lambda self: asked.append(True) or 0)
    window = MainWindow(ctl)
    assert window.open_location(folder)
    for _ in range(5):
        qapp.processEvents()
    assert asked == [True]                                         # a modal login, once
    assert ctl.team_mode and "team workspace" in window.status_vm.notice and "Not verified" not in window.status_vm.notice
    assert "Not yet verified" in window.status_vm.notice
    assert "Not logged in" in window.team_label.text()
    assert window.login_act.isEnabled() and not window.logout_act.isEnabled() and not window.set_up_team_act.isEnabled()
    assert ctl.documents(ctl.team() and _rest_project(_rest)["id"])                # looking works
    with pytest.raises(NotLoggedIn):
        ctl.checkout(_rest_doc(_rest)["id"])
    assert "Log in" in describe_error(NotLoggedIn("log in to make changes"))
    window.close_ws()
    assert window.team_label.text() == ""


def _rest_project(rest):
    return rest[3]


def _rest_doc(rest):
    return rest[4]


def _logged_in_window(qapp, team, name, password, monkeypatch):
    folder, pc, _code, boss, project, doc, path, tmp = team
    ctl = controller(tmp, name)
    if name == "boss":
        ctl = Controller(cfg_dir=tmp / "cfg-boss", cache_dir=tmp / "cache-boss", state_dir=pc)
    ctl.open_workspace(folder)
    made = ctl.log_in(name, password)
    if made["must_change"]:
        ctl.change_password(password, password + "x")
    window = MainWindow(ctl)
    window.open_location(folder)
    return ctl, window


def test_checking_out_and_in_with_a_login_shows_who_has_it(qapp, team, monkeypatch):
    folder, pc, _code, boss, project, doc, path, tmp = team
    ctl, window = _logged_in_window(qapp, team, "alice", OTHER, monkeypatch)
    assert window.team_label.text().startswith("alice (user)")
    assert not window.login_act.isEnabled() and window.logout_act.isEnabled()
    ctl.checkout(doc["id"])
    detail = ctl.document_detail(doc["id"])
    assert detail["mine"] and detail["checkout"]["owner"]["account"] == "alice"
    assert [d["id"] for d in ctl.my_checkouts()] == [doc["id"]]
    vm = DocumentListModel(ctl)
    vm.load(project["id"])
    vm.set_quick("mine")
    assert [r["id"] for r in vm.rows] == [doc["id"]]
    path.write_bytes(b"v2")
    rev = ctl.checkin_now(doc["id"], "second")
    assert rev["author"]["account"] == "alice"
    # someone else, on another computer
    other = Controller(cfg_dir=tmp / "cfg-b", cache_dir=tmp / "cache-b", state_dir=pc)
    other.open_workspace(folder)
    other.log_in("boss", PASSWORD)
    other.checkout(doc["id"])
    vm.refresh()
    assert vm.rows == []                                           # it isn't hers now
    assert ctl.document_detail(doc["id"])["mine"] is False
    with pytest.raises(D.CheckoutConflict):
        ctl.checkout(doc["id"])
    window.team_log_out()
    assert ctl.my_account() is None and "Not logged in" in window.team_label.text()
    window.close()


def test_the_window_notices_when_someone_else_changes_something(qapp, team, monkeypatch):
    folder, pc, _code, boss, project, doc, path, tmp = team
    monkeypatch.setattr(mw, "TEAM_WATCH_TICKS", 1)
    ctl, window = _logged_in_window(qapp, team, "alice", OTHER, monkeypatch)
    window.timer.stop()
    refreshed = []
    real = window.refresh_all
    monkeypatch.setattr(window, "refresh_all", lambda: refreshed.append(True) or real())
    window.poll()
    deadline = time.monotonic() + 10
    while window._team_probe is not None and window._team_probe.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    window.poll()
    assert refreshed == []                                         # nothing to see yet
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        ws.commit("t", [RecordPut("project", "prj_" + "a" * 32, {"v": 1})])
    deadline = time.monotonic() + 10
    while not refreshed and time.monotonic() < deadline:
        window.poll()
        qapp.processEvents()
        time.sleep(0.02)
    assert refreshed and "The Boss made a change" in window.statusBar().currentMessage()
    window.close()


def test_an_administrator_can_release_someone_elses_checkout_and_a_regular_user_isnt_offered_it(qapp, team, monkeypatch):
    folder, pc, _code, boss, project, doc, path, tmp = team
    alice, alice_window = _logged_in_window(qapp, team, "alice", OTHER, monkeypatch)
    alice.checkout(doc["id"])
    boss_ctl, boss_window = _logged_in_window(qapp, team, "boss", PASSWORD, monkeypatch)
    tab = boss_window.documents_tab
    tab.load_project(project["id"])
    tab.panel.load(doc["id"])
    tab.render_panel()
    assert tab.panel.can_release and tab.release_btn.isEnabled() and not tab.release_btn.isHidden()
    assert "alice (on " in tab.panel.ownership_text and "Read-only" in tab.panel.ownership_text
    assert "alice (on " in tab.panel.headline[0]
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("", True)))
    tab.release()
    assert boss_ctl.document_detail(doc["id"])["checkout"] is not None     # a reason is required
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("she's on holiday", True)))
    tab.release()
    assert boss_ctl.document_detail(doc["id"])["checkout"] is None
    with Workspace.open(folder, state_dir=pc) as ws:
        audit = [a for a in ws.list("audit").values() if a.get("event") == "checkout-admin-release"]
    assert audit[0]["reason"] == "she's on holiday" and audit[0]["by"]["account"] == "boss"
    boss_ctl.checkout(doc["id"])
    tab = alice_window.documents_tab
    tab.load_project(project["id"])
    tab.panel.load(doc["id"])
    tab.render_panel()
    assert not tab.panel.can_release and tab.release_btn.isHidden()
    tab.release()                                                  # nothing happens, however it's reached
    assert boss_ctl.document_detail(doc["id"])["mine"]
    with pytest.raises(NotPermitted):
        alice.release_checkout(doc["id"], "mine now")
    boss_window.close()
    alice_window.close()


def test_a_plain_workspace_doesnt_offer_it(qapp, tmp_path):
    folder = tmp_path / "share"
    folder.mkdir()
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    project = ctl.quick_start(folder, "P")
    source = tmp_path / "a.txt"
    source.write_bytes(b"x")
    doc, _rev = ctl.import_document(project["id"], source)
    window = MainWindow(ctl)
    window.open_location(folder)
    tab = window.documents_tab
    tab.load_project(project["id"])
    doc_id = doc["id"]
    ctl.checkout(doc_id)
    tab.panel.load(doc_id)
    tab.render_panel()
    assert not tab.panel.can_release and tab.release_btn.isHidden()
    assert ctl.document_detail(doc_id)["can_release"] is False
    window.close()


def test_a_slow_first_read_of_the_history_keeps_the_window_alive(qapp, team, monkeypatch):
    from revisionworx.shared import SharedWorkspace
    folder, *_rest, tmp = team
    ctl = controller(tmp, "alice")
    window = MainWindow(ctl)
    monkeypatch.setattr(T.LoginDialog, "exec", lambda self: 0)
    shown = []
    real_progress = mw.QProgressDialog

    def recording(*args):
        shown.append(args[0])
        return real_progress(*args)
    monkeypatch.setattr(mw, "QProgressDialog", recording)
    assert window.open_location(folder)                            # quick: nothing to show
    assert shown == []
    real_sync = SharedWorkspace.sync
    ticks = []

    def slow_sync(self, *args, **kwargs):
        time.sleep(0.7)
        return real_sync(self, *args, **kwargs)
    monkeypatch.setattr(SharedWorkspace, "sync", slow_sync)
    timer = mw.QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(20)
    assert window.open_location(folder)
    timer.stop()
    assert shown and "first time" in shown[0]
    assert ticks                                                   # the window kept running while it read
    window.close()


def test_your_own_changes_arent_announced_as_someone_elses(qapp, team, monkeypatch):
    folder, pc, _code, boss, project, doc, path, tmp = team
    monkeypatch.setattr(mw, "TEAM_WATCH_TICKS", 1)
    ctl, window = _logged_in_window(qapp, team, "alice", OTHER, monkeypatch)
    window.timer.stop()
    window.update_team_state()
    head = window._team_head
    ctl.checkout(doc["id"])
    refreshed = []
    monkeypatch.setattr(window, "refresh_all", lambda: refreshed.append(True))
    deadline = time.monotonic() + 10
    while window._team_head == head and time.monotonic() < deadline:
        window.poll()
        qapp.processEvents()
        time.sleep(0.02)
    assert window._team_head != head and refreshed == []
    assert "made a change" not in window.statusBar().currentMessage()
    window.close()


def test_the_login_window_remembers_the_name_and_never_the_password(qapp, team, monkeypatch):
    folder, _pc, _code, _boss, _project, _doc, _path, tmp = team
    ctl = controller(tmp, "alice")
    window = MainWindow(ctl)
    monkeypatch.setattr(T.LoginDialog, "exec", lambda self: 0)
    window.open_location(folder)
    seen = []

    def logs_in(dialog):
        seen.append(dialog.account.text())
        dialog.account.setText("alice")
        dialog.password.setText(OTHER)
        dialog.submit()
        return dialog.result() or 1
    monkeypatch.setattr(T.LoginDialog, "exec", logs_in)
    monkeypatch.setattr(T.ChangePasswordDialog, "exec", lambda self: 0)
    window.ask_login()
    window.team_log_out()
    window.ask_login()
    assert seen == ["", "alice"]
    assert OTHER not in (ctl.cfg_dir / "preferences.json").read_text(encoding="utf-8") if (ctl.cfg_dir / "preferences.json").exists() else True
    window.close()


def test_the_look_for_changes_doesnt_hold_up_the_window(qapp, team, monkeypatch):
    """A network share can stall for seconds. The window has to stay alive while it does."""
    monkeypatch.setattr(mw, "TEAM_WATCH_TICKS", 1)
    ctl, window = _logged_in_window(qapp, team, "alice", OTHER, monkeypatch)
    window.timer.stop()
    release = threading.Event()
    calls = []

    def stalls(_head):
        calls.append(True)
        release.wait(10)
        return {"head": None, "others": []}
    monkeypatch.setattr(ctl, "changes_since", stalls)
    started = time.monotonic()
    for _ in range(5):
        window.poll()
    assert time.monotonic() - started < 2.0
    assert calls == [True]                                         # and only one look at a time
    release.set()
    window._team_probe.join(5)
    window.close()


def test_setting_up_a_team_through_the_window(qapp, tmp_path, no_modal_boxes, monkeypatch):
    folder = tmp_path / "share"
    folder.mkdir()
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=computer(tmp_path, "boss"))
    ctl.create_workspace(folder, "Plain")
    window = MainWindow(ctl)
    window.open_location(folder)
    window.update_team_state()
    assert window.set_up_team_act.isEnabled() and not window.login_act.isEnabled() and window.team_label.text() == ""
    codes = []

    def fill_in(dialog):
        dialog.account.setText("boss")
        dialog.name.setText("The Boss")
        dialog.new.setText(PASSWORD)
        dialog.again.setText(PASSWORD + "typo")
        dialog.submit()
        assert "don't match" in dialog.error.text()
        dialog.again.setText(PASSWORD)
        dialog.submit()
        return dialog.result()

    def see_code(dialog):
        codes.append(dialog.code.text())
        assert not dialog.close_btn.isEnabled()                    # it can't be dismissed by accident
        dialog.reject()
        assert dialog.result() == 0 and dialog.isVisible() is False
        dialog.done_box.setChecked(True)
        assert dialog.close_btn.isEnabled()
        return 1
    monkeypatch.setattr(T.SetUpTeamDialog, "exec", fill_in)
    monkeypatch.setattr(T.RecoveryCodeDialog, "exec", see_code)
    window.set_up_team()
    assert len(codes[0].split("-")) == 8
    assert ctl.team_mode and ctl.my_account() == "boss" and ctl.team()["role"] == "admin"
    assert "boss" in window.team_label.text().lower()
    assert not window.set_up_team_act.isEnabled() and window.accounts_act.isEnabled()
    window.close()


def test_a_folder_that_fails_its_check_says_why_and_nothing_happens(qapp, tmp_path, monkeypatch):
    from revisionworx import atomicio
    folder = tmp_path / "share"
    folder.mkdir()
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=computer(tmp_path, "boss"))
    ctl.create_workspace(folder, "Plain")
    dialog = T.SetUpTeamDialog(ctl)
    dialog.account.setText("boss")
    dialog.new.setText(PASSWORD)
    dialog.again.setText(PASSWORD)
    monkeypatch.setattr(atomicio, "move_exclusive", lambda src, dst: os.replace(src, dst))
    dialog.submit()
    assert "can't safely be shared" in dialog.error.text() and dialog.made is None
    monkeypatch.undo()
    ctl.open_workspace(folder)
    assert not ctl.team_mode


def test_managing_accounts_in_the_dialog(qapp, team, monkeypatch):
    ctl, _window = _logged_in_window(qapp, team, "boss", PASSWORD, monkeypatch)
    dialog = T.AccountsDialog(ctl)
    assert {r["id"] for r in dialog.rows} == {"alice", "boss"}
    add = T.AddAccountDialog(ctl)
    add.account.setText("Bob")
    add.name.setText("Bob B")
    add.password.setText("short")
    add.submit()
    assert "at least" in add.error.text()
    add.password.setText(OTHER)
    add.submit()
    assert add.error.text() == ""
    dialog.refresh("bob")
    assert dialog.selected()["id"] == "bob" and dialog.selected()["must_change"]
    assert dialog.table.item(dialog.table.currentRow() if dialog.table.currentRow() >= 0 else 1, 3) is not None
    dialog.toggle_active()
    assert dialog.selected()["active"] is False and dialog.active_btn.text() == "&Enable"
    dialog.toggle_active()
    dialog.toggle_role()
    assert dialog.selected()["role"] == "admin" and "regular" in dialog.role_btn.text()
    dialog.toggle_role()
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: (OTHER + "n", True)))
    dialog.reset()
    assert dialog.error.text() == ""
    dialog.refresh("boss")
    dialog.toggle_active()                                         # the last administrator
    assert "no active administrator" in dialog.error.text()
    assert dialog.selected()["active"] is True


def test_a_regular_user_sees_the_refusal_in_the_accounts_dialog(qapp, team, monkeypatch):
    ctl, _window = _logged_in_window(qapp, team, "alice", OTHER, monkeypatch)
    dialog = T.AccountsDialog(ctl)
    dialog.refresh("boss")
    dialog.toggle_active()
    assert "administrator" in dialog.error.text()
    add = T.AddAccountDialog(ctl)
    add.account.setText("x")
    add.password.setText(OTHER)
    add.submit()
    assert "administrator" in add.error.text()
    assert "Nothing was written" in describe_error(NotPermitted("only an administrator can do that"))


def test_the_recovery_code_through_the_login_dialog(qapp, team, monkeypatch):
    folder, pc, code, boss, project, doc, path, tmp = team
    ctl = controller(tmp, "laptop")
    ctl.open_workspace(folder)
    dialog = T.RecoverDialog(ctl, account="boss")
    dialog.code.setText("wrong code")
    dialog.new.setText(PASSWORD)
    dialog.again.setText(PASSWORD)
    dialog.submit()
    assert "recovery code" in dialog.error.text()
    dialog.code.setText(code)
    dialog.submit()
    assert dialog.error.text() == "" and ctl.my_account() == "boss" and ctl.team()["role"] == "admin"


def test_a_plain_workspace_looks_the_same_as_ever(qapp, tmp_path):
    folder = tmp_path / "share"
    folder.mkdir()
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    ctl.create_workspace(folder, "Plain")
    assert ctl.team_mode is False and ctl.my_account() is None and ctl.team()["team"] is False
    assert ctl.changes_since(0) == {"head": None, "others": []}
    assert "local disk" in AppStatus(ctl).notice


def test_check_this_folder_reports(qapp, tmp_path, no_modal_boxes):
    folder = tmp_path / "share"
    folder.mkdir()
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache", state_dir=tmp_path / "state")
    ctl.create_workspace(folder, "Plain")
    T.show_share_check(ctl)
    kind, (title, text) = no_modal_boxes[-1]
    assert kind == "information" and "passed" in text and "share test" in text


def test_log_in_helper_used_by_other_tests_works(team):
    folder, pc, _code, _boss, _project, _doc, _path, tmp = team
    assert log_in(folder, computer(tmp, "x"), "alice", OTHER).account == "alice"
    assert accounts.login(folder, "boss", PASSWORD, state_dir=pc)["role"] == "admin"
