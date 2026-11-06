# SPDX-License-Identifier: GPL-3.0-or-later
"""Accounts, logins and turning team use on (ADR-024)."""
import json
import os
import stat
import time

import pytest

from conftest import tree_snapshot
from revisionworx import accounts, atomicio, cli, documents as D, ed25519, projects, shared
from revisionworx.projects import NewProject
from revisionworx.shared import NotLoggedIn, NotPermitted, SessionExpired, SharedWorkspace, team_dir
from revisionworx.workspace import FORMAT_VERSION, RecordPut, Workspace, WorkspaceError, WorkspaceFormatError
from team_helpers import PASSWORD, add_user, computer, log_in, new_team

A = "prj_" + "a" * 32
OTHER = PASSWORD + "!"


def test_real_accounts_stretch_passwords_hard(monkeypatch):
    """Anyone who can read the folder can try guesses on their own computer, so this is the only thing slowing them down."""
    monkeypatch.undo()
    import importlib
    fresh = importlib.reload(accounts)
    try:
        assert fresh.KDF["name"] == "scrypt" and fresh.KDF["n"] >= 2 ** 17 and fresh.KDF["r"] >= 8
    finally:
        importlib.reload(accounts)


@pytest.fixture
def team(tmp_path):
    folder, pc, code, session = new_team(tmp_path)
    return folder, pc, code, session, tmp_path


# ------------------------------------------------------------------ turning it on
def test_turning_it_on_keeps_everything_that_was_there(tmp_path):
    folder = tmp_path / "share"
    (folder / "Projects").mkdir(parents=True)
    with Workspace.create(folder, "Existing") as ws:
        root = projects.add_root(ws, "Projects", folder / "Projects")
        project = projects.create_project(ws, NewProject(name="HPU", number="J-1", root_id=root))
        path = projects.project_folder(ws, project) / "a.idw"
        path.write_bytes(b"v1")
        doc, rev = D.register_document(ws, project["id"], path)
        co = D.checkout(ws, doc["id"])
        path.write_bytes(b"v2")
        rev2 = D.checkin(ws, doc["id"], co["token"], "second")
        before = {kind: {k: v for k, v in ws.list(kind).items()} for kind in ("project", "document", "revision", "root")}
        head = ws.head_seq()
    files_before = {p: b for p, b in ((q, q.read_bytes()) for q in (folder / ".revisionworx" / "journal").iterdir())}
    made = accounts.enable_team_use(folder, "Boss", "The Boss", PASSWORD, state_dir=computer(tmp_path, "boss"))
    assert made["session"].account == "boss" and made["check"]["ok"]
    assert all(p.read_bytes() == b for p, b in files_before.items())    # nothing already written was touched
    with Workspace.open(folder, state_dir=computer(tmp_path, "other")) as ws:
        assert isinstance(ws, SharedWorkspace)
        assert ws._replica.enabled_at() == head + 1
        assert D.history(ws, doc["id"])[-1]["id"] == rev2["id"]
        for kind, rows in before.items():
            now = ws.list(kind)
            for rid, row in rows.items():
                expected = {k: v for k, v in row.items() if k not in ("_rev", "relative")}
                assert {k: v for k, v in now[rid].items() if k not in ("_rev", "relative")} == expected
        assert D.document_status(ws, doc["id"], verify_hash=True)["state"] == "unchanged"
        assert ws.settings()["collaboration_mode"] == "shared-folder"


def test_a_workspace_that_isnt_shared_is_the_same_as_ever(tmp_path):
    folder = tmp_path / "share"
    folder.mkdir()
    with Workspace.create(folder, "Plain") as ws:
        assert not isinstance(ws, SharedWorkspace) and ws.materialises
        assert ws.info["format_version"] == FORMAT_VERSION == 1 and "collaboration" not in ws.info
        ws.commit("t", [RecordPut("project", A, {"v": 1})])
        assert "prev" not in ws.read_entry(ws.head_seq()) and "sig" not in ws.read_entry(ws.head_seq())
        assert ws.actor()["user"] and "account" not in ws.actor()
    assert (folder / ".revisionworx" / "records" / "project" / f"{A}.json").is_file()


def test_older_versions_refuse_a_team_workspace(team):
    """The format number is what stops an old copy writing unsigned entries."""
    folder, *_ = team
    info = atomicio.read_json_object(folder / ".revisionworx" / "workspace.json")
    assert info["format_version"] > FORMAT_VERSION and info["collaboration"] == "shared"


def test_a_future_format_is_still_refused(team):
    folder, pc, *_ = team
    path = folder / ".revisionworx" / "workspace.json"
    info = atomicio.read_json_object(path)
    atomicio.atomic_write_json(path, {**info, "format_version": 3})
    with pytest.raises(WorkspaceFormatError):
        Workspace.open(folder, state_dir=pc)


def test_turning_it_on_twice_is_refused(team):
    folder, pc, *_ = team
    with pytest.raises(accounts.AccountError, match="already"):
        accounts.enable_team_use(folder, "other", "Other", PASSWORD, state_dir=pc)


def test_it_cant_be_turned_on_while_someone_is_writing(tmp_path):
    folder = tmp_path / "share"
    folder.mkdir()
    ws = Workspace.create(folder, "Busy")
    from revisionworx.workspace import WorkspaceLockedError
    with pytest.raises(WorkspaceLockedError):
        accounts.enable_team_use(folder, "boss", "Boss", PASSWORD, state_dir=computer(tmp_path, "boss"))
    assert atomicio.read_json_object(folder / ".revisionworx" / "workspace.json")["format_version"] == 1
    ws.close()


def test_it_isnt_turned_on_if_the_folder_fails_its_check(tmp_path, monkeypatch):
    folder = tmp_path / "share"
    folder.mkdir()
    Workspace.create(folder, "Bad share").close()
    before = tree_snapshot(folder)
    real = atomicio.move_exclusive

    def clobbers(src, dst):                                        # a share where "exclusive" create isn't
        os.replace(src, dst)
    monkeypatch.setattr(atomicio, "move_exclusive", clobbers)
    with pytest.raises(accounts.ShareCheckFailed, match="exclusive create isn't exclusive"):
        accounts.enable_team_use(folder, "boss", "Boss", PASSWORD, state_dir=computer(tmp_path, "boss"))
    monkeypatch.setattr(atomicio, "move_exclusive", real)
    after = tree_snapshot(folder)
    assert {k: v for k, v in after.items() if k != ".revisionworx"} == {k: v for k, v in before.items() if k != ".revisionworx"}
    assert atomicio.read_json_object(folder / ".revisionworx" / "workspace.json")["format_version"] == 1


def test_a_failed_publish_puts_workspace_json_back(tmp_path, monkeypatch):
    folder = tmp_path / "share"
    folder.mkdir()
    Workspace.create(folder, "T").close()
    real = atomicio.publish_exclusive

    def refuses(path, data):
        if path.parent.name == "journal" and b"shared.enable" in data:
            raise OSError(28, "no space left on device")
        return real(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", refuses)
    with pytest.raises(OSError):
        accounts.enable_team_use(folder, "boss", "Boss", PASSWORD, state_dir=computer(tmp_path, "boss"))
    monkeypatch.undo()
    assert atomicio.read_json_object(folder / ".revisionworx" / "workspace.json")["format_version"] == 1
    assert Workspace.read_writer_lock(folder) is None
    with Workspace.open(folder, writable=True) as ws:              # and it still works the old way
        ws.commit("t", [RecordPut("project", A, {"v": 1})])


def test_roots_inside_the_shared_folder_work_from_a_different_path(tmp_path):
    """One computer has Z:\\ and another \\\\server\\share. Here, the same folder under two names."""
    folder = tmp_path / "share"
    (folder / "Projects").mkdir(parents=True)
    with Workspace.create(folder, "T") as ws:
        root = projects.add_root(ws, "Projects", folder / "Projects")
        project = projects.create_project(ws, NewProject(name="HPU", root_id=root))
    made = accounts.enable_team_use(folder, "boss", "Boss", PASSWORD, state_dir=computer(tmp_path, "boss"))
    moved = tmp_path / "other-name-for-the-share"
    folder.rename(moved)
    with Workspace.open(moved, session=made["session"], state_dir=computer(tmp_path, "boss")) as ws:
        assert ws.get("root", root)["relative"] == "Projects"
        assert projects.root_path(ws, root) == moved / "Projects"
        assert projects.project_folder(ws, ws.get("project", project["id"])).is_dir()
    with Workspace.open(moved, writable=True, session=made["session"], state_dir=computer(tmp_path, "boss")) as ws:
        (moved / "Projects" / "New").mkdir()
        second = projects.add_root(ws, "New", moved / "Projects" / "New")
        assert ws.get("root", second)["relative"] == "Projects/New"
        assert projects.find_root_for(ws, moved / "Projects" / "New" / "x")[0] == second


# ------------------------------------------------------------------ logging in
def test_logging_in_and_being_told_to_choose_a_password(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    alice_pc = computer(tmp, "alice")
    made = accounts.login(folder, "ALICE ", OTHER, state_dir=alice_pc)              # names aren't case-sensitive
    assert made["session"].account == "alice" and made["must_change"] and made["role"] == "user"
    with pytest.raises(NotPermitted, match="new password first"):
        with Workspace.open(folder, writable=True, session=made["session"], state_dir=alice_pc) as ws:
            ws.commit("t", [RecordPut("project", A, {"v": 1})])
    new = accounts.change_password(folder, OTHER, OTHER + "x", session=made["session"], state_dir=alice_pc)
    assert accounts.login(folder, "alice", OTHER + "x", state_dir=alice_pc)["must_change"] is False
    with Workspace.open(folder, writable=True, session=new, state_dir=alice_pc) as ws:
        seq = ws.commit("t", [RecordPut("project", A, {"v": 1})])
        assert ws.read_entry(seq)["actor"]["account"] == "alice"


def test_a_wrong_password_and_an_unknown_name_look_the_same(team):
    folder, pc, *_ = team
    with pytest.raises(accounts.LoginFailed) as wrong:
        accounts.login(folder, "boss", "not the password", state_dir=pc)
    with pytest.raises(accounts.LoginFailed) as unknown:
        accounts.login(folder, "nobody", "not the password", state_dir=pc)
    assert str(wrong.value) == str(unknown.value)


def test_a_disabled_account_cant_log_in(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        accounts.set_active_flag(ws, "alice", False)
    with pytest.raises(accounts.LoginFailed, match="disabled"):
        accounts.login(folder, "alice", OTHER, state_dir=computer(tmp, "alice"))
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        accounts.set_active_flag(ws, "alice", True)
    assert accounts.login(folder, "alice", OTHER, state_dir=computer(tmp, "alice"))


def test_there_is_nothing_to_log_in_to_in_a_plain_workspace(tmp_path):
    folder = tmp_path / "share"
    folder.mkdir()
    Workspace.create(folder, "Plain").close()
    with pytest.raises(accounts.AccountError, match="isn't set up for a team"):
        accounts.login(folder, "boss", PASSWORD)


def test_writing_without_logging_in_is_refused_but_reading_isnt(team):
    folder, _pc, _code, _boss, tmp = team
    new = computer(tmp, "nobody")
    with Workspace.open(folder, state_dir=new) as ws:
        assert ws.session is None and ws.list("account")
    with pytest.raises(NotLoggedIn):
        Workspace.open(folder, writable=True, state_dir=new)
    assert Workspace.read_writer_lock(folder) is None              # it didn't even take a turn


def test_the_password_itself_is_never_stored(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    log_in(folder, computer(tmp, "alice"), "alice")
    for base in (folder, tmp / "computers"):
        for path in base.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                assert PASSWORD.encode() not in data and OTHER.encode() not in data, path


def test_a_session_doesnt_show_its_key(team):
    *_rest, session, _tmp = team
    assert session.seed.hex() not in repr(session) and repr(session.seed) not in repr(session)


def test_a_saved_login_works_from_another_process_until_it_expires(team, monkeypatch):
    folder, pc, _code, boss, _tmp = team
    shared.save_session(boss, pc, hours=1.0)
    with Workspace.open(folder, writable=True, state_dir=pc) as ws:
        assert ws.session.account == "boss"
        ws.commit("t", [RecordPut("project", A, {"v": 1})])
    path = team_dir(boss.workspace_id, pc) / "session.json"
    raw = atomicio.read_json_object(path)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600        # it signs in this user's name
    atomicio.atomic_write_json(path, {**raw, "expires": time.time() - 1})
    assert shared.saved_session(boss.workspace_id, pc) is None
    assert not path.exists()
    with pytest.raises(NotLoggedIn):
        Workspace.open(folder, writable=True, state_dir=pc)


@pytest.mark.parametrize("content", [b"", b"nonsense", b"[]", b'{"expires": "x"}', b'{"expires": 9e99, "seed": "zz", "account": "a", "name": "b", "public": "c"}'])
def test_a_damaged_saved_login_is_no_login_and_no_crash(team, content):
    _folder, pc, _code, boss, _tmp = team
    path = team_dir(boss.workspace_id, pc) / "session.json"
    path.write_bytes(content)
    assert shared.saved_session(boss.workspace_id, pc) is None


def test_logging_out(team):
    folder, pc, _code, boss, _tmp = team
    shared.save_session(boss, pc)
    accounts.logout(boss.workspace_id, pc)
    with Workspace.open(folder, state_dir=pc) as ws:
        assert ws.session is None


@pytest.mark.parametrize("password", ["short", "boss", "BOSSBOSS"[:4], "x" * 1025])
def test_passwords_have_to_be_reasonable(tmp_path, password):
    with pytest.raises(accounts.AccountError):
        accounts.check_password(password, "boss")


@pytest.mark.parametrize("name", ["", " ", "a b", "a/b", "../x", "-x", ".x", "x" * 65, "é", "@recovery"])
def test_names_have_to_be_reasonable(name):
    with pytest.raises(accounts.AccountError):
        accounts.normal_id(name)


def test_a_password_that_would_be_typed_differently_on_another_keyboard_still_works(team):
    """The same text can arrive as different Unicode from different systems."""
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", "caf\u00e9-caf\u00e9")
    assert accounts.login(folder, "alice", "cafe\u0301-cafe\u0301", state_dir=computer(tmp, "alice"))


@pytest.mark.parametrize("kdf", [{"name": "md5"}, "nope", {"name": "scrypt", "n": 2 ** 40, "r": 8, "p": 1}, {"name": "scrypt", "n": 3, "r": 8, "p": 1},
                                 {"name": "scrypt", "n": 16, "r": 10 ** 6, "p": 1}, {"name": "scrypt", "n": "x", "r": 8, "p": 1}])
def test_a_doctored_account_cant_make_logging_in_eat_all_the_memory(kdf):
    key = {"salt": "00", "kdf": kdf, "public": "0" * 64, "device": False}
    with pytest.raises(accounts.LoginFailed):
        accounts._stretch("password", key, "ws", "a")


# ------------------------------------------------------------------ an administrator's key belongs to their computer
def test_an_administrators_login_needs_their_computer(team):
    folder, pc, _code, _boss, tmp = team
    assert accounts.login(folder, "boss", PASSWORD, state_dir=pc)["role"] == "admin"
    with pytest.raises(accounts.ComputerNotSetUp, match="recovery code"):
        accounts.login(folder, "boss", PASSWORD, state_dir=computer(tmp, "another"))


def test_an_administrators_key_cant_be_guessed_from_the_folder_alone(team):
    """Even with the right password: without this computer's secret, what comes out isn't the key."""
    folder, pc, _code, boss, _tmp = team
    with Workspace.open(folder, state_dir=pc) as ws:
        key = ws.get("account", "boss")["key"]
    assert key["device"] is True
    assert accounts._stretch(PASSWORD, key, boss.workspace_id, "boss") != boss.seed
    assert ed25519.public_key(accounts._stretch(PASSWORD, key, boss.workspace_id, "boss")).hex() != key["public"]


def test_a_second_administrator_is_tied_to_their_own_computer_when_they_choose_a_password(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "carol", OTHER, role="admin")
    carol_pc = computer(tmp, "carol")
    session = accounts.login(folder, "carol", OTHER, state_dir=carol_pc)["session"]
    new = accounts.change_password(folder, OTHER, OTHER + "x", session=session, state_dir=carol_pc)
    with Workspace.open(folder, session=new, state_dir=carol_pc) as ws:
        assert ws.is_admin and ws.get("account", "carol")["key"]["device"] is True
    with pytest.raises(accounts.ComputerNotSetUp):
        accounts.login(folder, "carol", OTHER + "x", state_dir=computer(tmp, "elsewhere"))


def test_the_recovery_code_gets_an_administrator_back_and_stops_the_old_key(team):
    folder, pc, code, boss, tmp = team
    new_pc = computer(tmp, "new-laptop")
    session = accounts.recover_admin(folder, code.lower().replace("-", " "), "boss", PASSWORD + "new", state_dir=new_pc)
    with Workspace.open(folder, writable=True, session=session, state_dir=new_pc) as ws:
        assert ws.is_admin
        accounts.create_account(ws, "alice", OTHER)
    with pytest.raises(SessionExpired):
        Workspace.open(folder, writable=True, session=boss, state_dir=pc)
    with pytest.raises(accounts.LoginFailed):
        accounts.login(folder, "boss", PASSWORD, state_dir=pc)


def test_the_recovery_code_can_make_a_new_administrator(team):
    folder, _pc, code, _boss, tmp = team
    session = accounts.recover_admin(folder, code, "stand-in", PASSWORD, name="Stand In", state_dir=computer(tmp, "x"))
    with Workspace.open(folder, session=session, state_dir=computer(tmp, "x")) as ws:
        assert ws.is_admin and ws.get("account", "stand-in")["name"] == "Stand In"


@pytest.mark.parametrize("code", ["AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA-AAAA", "not a code!", ""])
def test_a_wrong_recovery_code_changes_nothing(team, code):
    folder, pc, *_ = team
    with Workspace.open(folder, state_dir=pc) as ws:
        head = ws.head_seq()
    with pytest.raises(accounts.LoginFailed):
        accounts.recover_admin(folder, code, "boss", PASSWORD, state_dir=pc)
    with Workspace.open(folder, state_dir=pc) as ws:
        assert ws.head_seq() == head


def test_the_recovery_code_isnt_kept_anywhere(team):
    folder, _pc, code, _boss, tmp = team
    compact = code.replace("-", "").encode()
    for base in (folder, tmp / "computers"):
        for path in base.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                assert code.encode() not in data and compact not in data, path


# ------------------------------------------------------------------ what an administrator does
def test_only_an_administrator_manages_accounts(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    alice_pc = computer(tmp, "alice")
    alice = log_in(folder, alice_pc, "alice", OTHER)
    with Workspace.open(folder, writable=True, session=alice, state_dir=alice_pc) as ws:
        assert ws.role == "user" and not ws.is_admin
        for call in (lambda: accounts.create_account(ws, "x", PASSWORD), lambda: accounts.set_active_flag(ws, "boss", False),
                     lambda: accounts.set_role(ws, "alice", "admin"), lambda: accounts.reset_password(ws, "boss", PASSWORD),
                     lambda: accounts.set_name(ws, "alice", "Queen")):
            with pytest.raises(NotPermitted):
                call()


def test_a_regular_user_can_change_their_own_password(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    alice_pc = computer(tmp, "alice")
    alice = log_in(folder, alice_pc, "alice", OTHER)
    with pytest.raises(accounts.LoginFailed, match="current password"):
        accounts.change_password(folder, "wrong wrong", PASSWORD, session=alice, state_dir=alice_pc)
    new = accounts.change_password(folder, OTHER + "?", PASSWORD + "2", session=alice, state_dir=alice_pc)
    assert new.public != alice.public
    with pytest.raises(SessionExpired):
        Workspace.open(folder, writable=True, session=alice, state_dir=alice_pc)


def test_a_reset_makes_them_choose_again(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    log_in(folder, computer(tmp, "alice"), "alice", OTHER)
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        row = accounts.reset_password(ws, "alice", PASSWORD + "temp")
        assert "key" not in row and row["must_change"]
    made = accounts.login(folder, "alice", PASSWORD + "temp", state_dir=computer(tmp, "alice"))
    assert made["must_change"]


def test_duplicate_and_unknown_accounts(team):
    folder, pc, _code, boss, _tmp = team
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        with pytest.raises(accounts.AccountError, match="already"):
            accounts.create_account(ws, "BOSS", PASSWORD)
        with pytest.raises(accounts.AccountError, match="no account"):
            accounts.set_role(ws, "nobody", "admin")
        with pytest.raises(accounts.AccountError, match="a role"):
            accounts.create_account(ws, "x", PASSWORD, role="king")
        with pytest.raises(NotPermitted, match="no active administrator"):
            accounts.set_active_flag(ws, "boss", False)
        rows = accounts.list_accounts(ws)
    assert [r["id"] for r in rows] == ["boss"] and "key" not in rows[0]


def test_breaking_the_lock_by_hand_is_for_an_administrator_and_on_the_record(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    alice_pc = computer(tmp, "alice")
    alice = log_in(folder, alice_pc, "alice", OTHER)
    holder = Workspace.open(folder, writable=True, session=alice, state_dir=alice_pc)
    token = holder._lock_token
    with pytest.raises(NotPermitted):
        SharedWorkspace.break_lock(folder / ".revisionworx", token, "because", session=alice, state_dir=alice_pc)
    with pytest.raises(WorkspaceError, match="changed since"):
        SharedWorkspace.break_lock(folder / ".revisionworx", "not-the-token", "because", session=boss, state_dir=pc)
    SharedWorkspace.break_lock(folder / ".revisionworx", token, "because", session=boss, state_dir=pc)
    assert Workspace.read_writer_lock(folder) is None
    holder.close()
    with Workspace.open(folder, state_dir=pc) as ws:
        audit = [a for a in ws.list("audit").values() if a.get("event") == "writer-lock-cleared"]
    assert audit[0]["reason"] == "because" and audit[0]["automatic"] is False and audit[0]["by"]["account"] == "boss"
    with pytest.raises(WorkspaceError, match="no orphaned lock files"):
        Workspace.clear_orphaned_release(folder, ".releasing-abc", "x")


# ------------------------------------------------------------------ documents, in people's names
@pytest.fixture
def drawing(team):
    folder, pc, _code, boss, tmp = team
    add_user(folder, pc, boss, "alice", OTHER)
    add_user(folder, pc, boss, "bob", OTHER)
    alice = log_in(folder, computer(tmp, "alice"), "alice", OTHER)
    bob = log_in(folder, computer(tmp, "bob"), "bob", OTHER)
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        root = projects.add_root(ws, "Projects", folder)
        project = projects.create_project(ws, NewProject(name="HPU", root_id=root))
        path = projects.project_folder(ws, project) / "bracket.idw"
        path.write_bytes(b"v1")
        doc, _rev = D.register_document(ws, project["id"], path)
    return folder, doc, path, alice, bob, tmp


def _as(folder, session, tmp, name, writable=True):
    return Workspace.open(folder, writable=writable, session=session, state_dir=computer(tmp, name))


def test_one_person_checks_out_and_another_is_told_who_has_it(drawing):
    folder, doc, path, alice, bob, tmp = drawing
    with _as(folder, alice, tmp, "alice") as ws:
        co = D.checkout(ws, doc["id"], note="fixing the hole")
        assert co["owner"]["account"] == "alice" and co["owner"]["user"] == "alice"
    with _as(folder, bob, tmp, "bob") as ws:
        with pytest.raises(D.CheckoutConflict, match="alice"):
            D.checkout(ws, doc["id"])
        with pytest.raises(D.TokenMismatch):                       # and can't check in on her behalf
            D.checkin(ws, doc["id"], D.my_token(ws, _NoTokens(), doc["id"]) or "", "mine now")
    path.write_bytes(b"v2")
    with _as(folder, alice, tmp, "alice") as ws:
        rev = D.checkin(ws, doc["id"], D.my_token(ws, _NoTokens(), doc["id"]), "fixed the hole")
        assert rev["author"]["account"] == "alice" and rev["label"]
    with _as(folder, bob, tmp, "bob") as ws:
        D.checkout(ws, doc["id"])
    with _as(folder, bob, tmp, "bob", writable=False) as ws:
        assert [r["author"].get("account") for r in D.history(ws, doc["id"])][-1] == "alice"


class _NoTokens:
    """This computer kept no token: someone moved to another desk."""

    @staticmethod
    def get(_workspace_id, _doc):
        return None


def test_a_checkout_belongs_to_the_person_on_whichever_computer(drawing):
    folder, doc, path, alice, _bob, tmp = drawing
    with _as(folder, alice, tmp, "alice") as ws:
        D.checkout(ws, doc["id"])
    path.write_bytes(b"v2")
    with _as(folder, alice, tmp, "alices-other-desk") as ws:
        token = D.my_token(ws, _NoTokens(), doc["id"])
        assert token and D.is_mine(ws, _NoTokens(), ws.get("checkout", doc["id"]))
        D.checkin(ws, doc["id"], token, "from the other desk")


def test_an_administrator_can_release_someone_elses_checkout_and_it_says_so(drawing):
    folder, doc, _path, alice, bob, tmp = drawing
    with _as(folder, alice, tmp, "alice") as ws:
        co = D.checkout(ws, doc["id"])
    with _as(folder, bob, tmp, "bob") as ws:
        with pytest.raises(NotPermitted):
            D.admin_release_checkout(ws, doc["id"], co["token"], "she's on holiday")
    made = accounts.login(folder, "boss", PASSWORD, state_dir=computer(tmp, "boss"))
    with _as(folder, made["session"], tmp, "boss") as ws:
        D.admin_release_checkout(ws, doc["id"], co["token"], "she's on holiday")
        audit = [a for a in ws.list("audit").values() if a.get("event") == "checkout-admin-release"]
        assert audit[0]["by"]["account"] == "boss" and audit[0]["reason"] == "she's on holiday"
    with _as(folder, alice, tmp, "alice", writable=False) as ws:
        assert ws.get("checkout", doc["id"]) is None


def test_distinct_approver_compares_people_not_computers(drawing):
    folder, doc, path, alice, bob, tmp = drawing
    made = accounts.login(folder, "boss", PASSWORD, state_dir=computer(tmp, "boss"))
    with _as(folder, made["session"], tmp, "boss") as ws:
        lc = D.get_lifecycle(ws)
        D.save_lifecycle(ws, {**{k: v for k, v in lc.items() if k != "_rev"}, "require_distinct_approver": True})
    with _as(folder, alice, tmp, "alice") as ws:
        co = D.checkout(ws, doc["id"])
        path.write_bytes(b"v2")
        rev = D.checkin(ws, doc["id"], co["token"], "mine")
    from revisionworx.lifecycle import LifecycleError
    with _as(folder, alice, tmp, "alice") as ws:
        lc = D.get_lifecycle(ws)
        to_review = [t for f, t in lc["transitions"] if f == rev["state"] and t != lc["approved"]][0]
        rev = D.transition(ws, rev["id"], to_review)
        with pytest.raises(LifecycleError, match="different person"):
            D.transition(ws, rev["id"], lc["approved"])
    with _as(folder, bob, tmp, "bob") as ws:
        approved = D.transition(ws, rev["id"], lc["approved"], "looks right")
        assert approved["transitions"][-1]["by"]["account"] == "bob"


def test_changes_and_projects_are_in_the_users_name(drawing):
    folder, doc, _path, alice, _bob, tmp = drawing
    from revisionworx import changes
    with _as(folder, alice, tmp, "alice") as ws:
        ecr = changes.service_for(ws).create_request("Thicken bracket", "It cracks")
        assert ecr.created_by["account"] == "alice" if hasattr(ecr, "created_by") else True
        entry = ws.read_entry(ws.head_seq())
        assert entry["actor"]["account"] == "alice" and entry["signer"] == "alice"
        project = projects.create_project(ws, NewProject(name="Another", root_id=next(iter(ws.list("root")))))
        assert project["created_by"]["account"] == "alice"


def test_regular_users_cant_change_settings_through_the_ordinary_calls(drawing):
    folder, _doc, _path, alice, _bob, tmp = drawing
    with _as(folder, alice, tmp, "alice") as ws:
        with pytest.raises(NotPermitted):
            D.save_doctype(ws, {"id": "mine", "name": "Mine"})
        with pytest.raises(NotPermitted):
            D.save_lifecycle(ws, D.get_lifecycle(ws))
        (folder / "Another").mkdir()
        with pytest.raises(NotPermitted):
            projects.add_root(ws, "Another", folder / "Another")
        with pytest.raises(NotPermitted):
            ws.commit("content.gc", [RecordPut("audit", "aud_" + "d" * 32, {"event": "content-gc"})])


# ------------------------------------------------------------------ from the command line
def run(capsys, *args, stdin=None, monkeypatch=None):
    if stdin is not None:
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    capsys.readouterr()
    code = cli.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_whole_thing_from_the_command_line(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "share"
    folder.mkdir()
    boss, alice = ["--state-dir", str(computer(tmp_path, "boss"))], ["--state-dir", str(computer(tmp_path, "alice"))]
    w = ["-w", str(folder)]
    assert run(capsys, "init", str(folder), "--name", "Team")[0] == 0
    code, out, _ = run(capsys, *w, *boss, "team", "enable", "--admin", "boss", "--password-stdin", stdin=PASSWORD + "\n", monkeypatch=monkeypatch)
    assert code == 0 and "Recovery code:" in out and "Not verified" in out
    recovery = out.split("Recovery code:")[1].split()[0]
    assert run(capsys, *w, *boss, "whoami")[1].startswith("boss (admin)")
    assert run(capsys, *w, *boss, "user", "add", "alice", "--display", "Alice A", "--password-stdin", stdin=OTHER + "\n", monkeypatch=monkeypatch)[0] == 0
    code, out, _ = run(capsys, *w, *boss, "--json", "user", "list")
    assert {r["id"]: r["role"] for r in json.loads(out)} == {"alice": "user", "boss": "admin"}
    assert "Not logged in" in run(capsys, *w, *alice, "whoami")[1]
    code, _out, err = run(capsys, *w, *alice, "root", "add", "Projects", str(folder))
    assert code == 2 and "log in" in err
    code, out, _ = run(capsys, *w, *alice, "login", "alice", "--password-stdin", stdin=OTHER + "\n", monkeypatch=monkeypatch)
    assert code == 0 and "passwd" in out
    assert run(capsys, *w, *alice, "passwd", "--password-stdin", stdin=f"{OTHER}\n{OTHER}x\n", monkeypatch=monkeypatch)[0] == 0
    code, _out, err = run(capsys, *w, *alice, "user", "add", "x", "--password-stdin", stdin=PASSWORD + "\n", monkeypatch=monkeypatch)
    assert code == 2 and "administrator" in err
    assert run(capsys, *w, *boss, "root", "add", "Projects", str(folder))[0] == 0
    code, out, _ = run(capsys, *w, *alice, "--json", "project", "new", "--name", "HPU", "--number", "J-1")
    assert code == 0, out
    project = json.loads(out)
    target = folder / project["rel_path"] / "bracket.idw"
    target.write_bytes(b"v1")
    code, out, err = run(capsys, *w, *alice, "--json", "doc", "register", project["id"], str(target))
    assert code == 0, err
    doc = json.loads(out)["document"] if "document" in json.loads(out) else json.loads(out)
    assert run(capsys, *w, *alice, "doc", "checkout", doc["id"])[0] == 0
    target.write_bytes(b"v2")
    # checked in from another computer, with no token there: it's the account that counts
    other = ["--state-dir", str(computer(tmp_path, "alices-other-desk"))]
    assert run(capsys, *w, *other, "login", "alice", "--password-stdin", stdin=OTHER + "x\n", monkeypatch=monkeypatch)[0] == 0
    code, out, err = run(capsys, *w, *other, "doc", "checkin", doc["id"], "-m", "second")
    assert code == 0, err
    assert run(capsys, *w, *boss, "user", "disable", "alice")[0] == 0
    code, _out, err = run(capsys, *w, *alice, "doc", "checkout", doc["id"])
    assert code == 2 and "isn't active" in err
    assert run(capsys, *w, *boss, "user", "enable", "alice")[0] == 0
    assert run(capsys, *w, *boss, "user", "role", "alice", "admin")[0] == 0
    assert run(capsys, *w, *boss, "user", "role", "alice", "user")[0] == 0
    assert run(capsys, *w, *boss, "user", "reset", "alice", "--password-stdin", stdin=OTHER + "y\n", monkeypatch=monkeypatch)[0] == 0
    code, out, _ = run(capsys, *w, *boss, "--json", "team", "status")
    status = json.loads(out)
    assert status["team"] and status["health"]["ok"] and status["logged_in"] == "boss" and status["role"] == "admin"
    assert run(capsys, *w, *boss, "verify")[0] == 0
    assert run(capsys, *w, *boss, "doctor", "--quick")[0] == 0
    assert run(capsys, *w, *boss, "status")[0] == 0
    # recovery, with the code, on a computer nobody has used
    laptop = ["--state-dir", str(computer(tmp_path, "laptop"))]
    code, out, _ = run(capsys, *w, *laptop, "team", "recover", "--admin", "boss", "--code", recovery, "--password-stdin", stdin=PASSWORD + "2\n", monkeypatch=monkeypatch)
    assert code == 0 and "administrator again" in out
    assert run(capsys, *w, *laptop, "whoami")[1].startswith("boss (admin)")
    assert run(capsys, *w, *boss, "logout")[0] == 0
    assert run(capsys, *w, *boss, "team", "forget-local")[0] == 0
    assert run(capsys, *w, *boss, "team", "repair")[0] == 0


def test_the_command_line_on_a_plain_workspace(tmp_path, capsys):
    folder = tmp_path / "share"
    folder.mkdir()
    w = ["-w", str(folder)]
    run(capsys, "init", str(folder), "--name", "Plain")
    assert "isn't set up for a team" in run(capsys, *w, "team", "status")[1]
    code, _out, err = run(capsys, *w, "user", "list")
    assert code == 2 and "isn't set up for a team" in err
    assert "Not logged in" in run(capsys, *w, "whoami")[1]
