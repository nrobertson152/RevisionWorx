# SPDX-License-Identifier: GPL-3.0-or-later
"""The rules every computer applies to every journal entry (ADR-024). Here the entries are written by hand, as a modified copy of
RevisionWorx or someone editing the folder directly would, because that's what the rules are for. What's checked is what the
computers conclude: all of them the same thing, and a forgery having no effect on any of them."""
import json
import random

import pytest

from revisionworx import accounts, atomicio, doctor, documents as D, ed25519, projects
from revisionworx.projects import NewProject
from revisionworx.shared import ENABLE_OP, RECOVERY, HistoryDiverged, SharedWorkspace, replica_path, signing_message
from revisionworx.workspace import RecordPut, Workspace, WorkspaceCorruptError, entry_digest, is_shared_info, new_id
from team_helpers import PASSWORD, add_user, computer, log_in, new_team

A = "prj_" + "a" * 32


class Team:
    """A workspace with an administrator, two users, a project, and a document that alice has checked out."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.folder, self.boss_pc, self.code, self.boss = new_team(tmp_path)
        add_user(self.folder, self.boss_pc, self.boss, "alice")
        add_user(self.folder, self.boss_pc, self.boss, "bob")
        self.alice_pc, self.bob_pc = computer(tmp_path, "alice"), computer(tmp_path, "bob")
        self.alice = log_in(self.folder, self.alice_pc, "alice")
        self.bob = log_in(self.folder, self.bob_pc, "bob")
        with Workspace.open(self.folder, writable=True, session=self.boss, state_dir=self.boss_pc) as ws:
            root = projects.add_root(ws, "Projects", self.folder)
            self.project = projects.create_project(ws, NewProject(name="HPU", number="J-1", root_id=root))
            path = projects.project_folder(ws, self.project) / "bracket.idw"
            path.write_bytes(b"drawing v1")
            self.doc, self.rev = D.register_document(ws, self.project["id"], path)
        with self.open(self.alice, writable=True) as ws:
            self.co = D.checkout(ws, self.doc["id"])

    def pc(self, session):
        return {"boss": self.boss_pc, "alice": self.alice_pc, "bob": self.bob_pc}[session.account]

    def open(self, session, writable=False):
        return Workspace.open(self.folder, writable=writable, session=session, state_dir=self.pc(session), wait=5.0)

    def forge(self, session=None, *, records=(), op="t", signer=None, seed=None, actor=None, prev=None, tamper=None):
        """Publish an entry by hand. By default it's properly signed by this session, whatever it says."""
        with Workspace.open(self.folder, state_dir=computer(self.tmp_path, "forger")) as ws:
            head, anchor = ws.head_seq(), ws._replica.anchor()
        account = signer if signer is not None else session.account
        entry = {"seq": head + 1, "txid": new_id("x")[2:].replace("_", "")[:32].ljust(32, "0"), "time": "2026-11-05T00:00:00+00:00",
                 "actor": actor if actor is not None else {"user": account, "account": account}, "op": op, "records": list(records),
                 "blobs": [], "checks": [], "writer_token": None, "prev": anchor if prev is None else prev, "signer": account}
        if tamper:
            tamper(entry)
        entry["sig"] = ed25519.sign(seed if seed is not None else session.seed, signing_message(entry)).hex()
        entry["digest"] = entry_digest(entry)
        atomicio.publish_exclusive(self.folder / ".revisionworx" / "journal" / f"{head + 1:020d}.json", atomicio.dumps_json(entry))
        return head + 1

    def verdicts(self, seq):
        """What each computer concludes about this entry. A computer with a new, empty copy has to agree too."""
        out = {}
        for name in ("boss", "alice", "bob", f"new-{random.random()}"):
            with Workspace.open(self.folder, state_dir=computer(self.tmp_path, name)) as ws:
                ws.sync(fresh=True)
                out[name] = ws._replica.why_ignored(seq)
        assert len(set(out.values())) == 1, out
        return next(iter(out.values()))

    def view(self, kind, rid, computer_name="boss"):
        with Workspace.open(self.folder, state_dir=computer(self.tmp_path, computer_name)) as ws:
            return ws.get(kind, rid)


@pytest.fixture
def team(tmp_path, monkeypatch):
    from revisionworx import shared
    monkeypatch.setattr(shared, "HINT_PATIENCE", 0.2)              # these damage the journal on purpose: nothing will turn up by waiting
    monkeypatch.setattr(shared, "GAP_PATIENCE", 0.5)
    return Team(tmp_path)


def put(kind, rid, data, **extra):
    return {"kind": kind, "id": rid, "data": data, **extra}


def key_for(seed):
    return {"public": ed25519.public_key(seed).hex(), "salt": "00", "kdf": dict(accounts.KDF), "device": False}


def test_a_proper_entry_by_a_user_takes_effect_everywhere(team):
    seq = team.forge(team.alice, records=[put("project", A, {"v": 1})])
    assert team.verdicts(seq) is None
    assert team.view("project", A) == {"v": 1, "_rev": seq}


@pytest.mark.parametrize("sig_is, text", [("missing", "isn't signed"), ("garbage", "doesn't match"), ("someone else", "doesn't match")])
def test_a_badly_signed_entry_has_no_effect(team, sig_is, text):
    seq = team.forge(team.boss, records=[put("project", A, {"v": 1})], seed=team.alice.seed if sig_is == "someone else" else None)
    path = team.folder / ".revisionworx" / "journal" / f"{seq:020d}.json"
    entry = atomicio.read_json_object(path)
    if sig_is == "missing":
        del entry["sig"]
    elif sig_is == "garbage":
        entry["sig"] = "zz"
    entry["digest"] = entry_digest(entry)
    atomicio.atomic_write_json(path, entry)
    assert text in team.verdicts(seq)
    assert team.view("project", A) is None


def test_an_entry_signed_by_nobody_it_knows_has_no_effect(team):
    seq = team.forge(signer="mallory", seed=b"m" * 32, records=[put("project", A, {"v": 1})])
    assert "no such account" in team.verdicts(seq)
    assert team.view("project", A) is None


def test_an_entry_has_to_name_its_signer_as_its_actor(team):
    seq = team.forge(team.alice, actor={"user": "boss", "account": "boss"}, records=[put("project", A, {"v": 1})])
    assert "someone other than its signer" in team.verdicts(seq)


def test_a_regular_user_cant_make_themselves_an_administrator(team):
    me = team.view("account", "alice")
    new = {k: v for k, v in me.items() if k != "_rev"}
    seq = team.forge(team.alice, op="account.role", records=[put("account", "alice", {**new, "role": "admin"})])
    assert "only an administrator" in team.verdicts(seq)
    assert team.view("account", "alice")["role"] == "user"


def test_a_regular_user_cant_add_an_account(team):
    seq = team.forge(team.alice, op="account.create", records=[put("account", "mallory", {
        "id": "mallory", "name": "M", "role": "admin", "active": True, "key": key_for(b"m" * 32)})])
    assert "only an administrator" in team.verdicts(seq)
    assert team.view("account", "mallory") is None


def test_a_user_cant_change_someone_elses_password(team):
    bob = {k: v for k, v in team.view("account", "bob").items() if k != "_rev"}
    seq = team.forge(team.alice, op="account.password", records=[put("account", "bob", {**bob, "key": key_for(b"m" * 32)})])
    assert "only an administrator" in team.verdicts(seq)


def test_a_password_change_cant_change_anything_else(team):
    me = {k: v for k, v in team.view("account", "alice").items() if k != "_rev"}
    seq = team.forge(team.alice, op="account.password", records=[put("account", "alice", {**me, "role": "admin", "key": key_for(b"m" * 32)})])
    assert "can't change anything else" in team.verdicts(seq)


def test_an_administrators_changes_to_accounts_take_effect(team):
    bob = {k: v for k, v in team.view("account", "bob").items() if k != "_rev"}
    seq = team.forge(team.boss, op="account.rename", records=[put("account", "bob", {**bob, "name": "Robert"})])
    assert team.verdicts(seq) is None
    assert team.view("account", "bob")["name"] == "Robert"


def test_changes_by_a_disabled_account_have_no_effect_and_earlier_ones_stand(team):
    before = team.forge(team.bob, records=[put("project", A, {"v": "before"})])
    with team.open(team.boss, writable=True) as ws:
        accounts.set_active_flag(ws, "bob", False)
    seq = team.forge(team.bob, records=[put("project", A, {"v": "after"})])
    assert "disabled" in team.verdicts(seq)
    assert team.view("project", A) == {"v": "before", "_rev": before}


def test_a_disabled_user_on_a_computer_that_hasnt_heard_yet_is_stopped_by_catching_up(team):
    with team.open(team.bob, writable=False):
        pass
    with team.open(team.boss, writable=True) as ws:
        accounts.set_active_flag(ws, "bob", False)
    from revisionworx.shared import SessionExpired
    with pytest.raises(SessionExpired):
        team.open(team.bob, writable=True)


def test_an_old_key_stops_working_when_the_password_changes(team):
    with team.open(team.boss, writable=True) as ws:
        accounts.reset_password(ws, "bob", PASSWORD + "new")
    seq = team.forge(team.bob, records=[put("project", A, {"v": 1})])
    assert "doesn't match" in team.verdicts(seq)


def test_someone_who_has_to_choose_a_password_can_do_nothing_else(team):
    with team.open(team.boss, writable=True) as ws:
        accounts.create_account(ws, "carol", PASSWORD)
    from revisionworx.shared import Session
    made = accounts.login(team.folder, "carol", PASSWORD, state_dir=computer(team.tmp_path, "carol"))
    assert made["must_change"]
    seq = team.forge(made["session"], records=[put("project", A, {"v": 1})])
    assert "new password first" in team.verdicts(seq)
    assert team.view("project", A) is None
    assert isinstance(made["session"], Session)


def test_only_the_owner_or_an_administrator_can_release_a_checkout(team):
    doc = team.doc["id"]
    seq = team.forge(team.bob, op="document.checkout.cancel", records=[{"kind": "checkout", "id": doc, "delete": True}])
    assert "only its owner or an administrator" in team.verdicts(seq)
    assert team.view("checkout", doc) is not None
    seq = team.forge(team.boss, op="document.checkout.admin_release", records=[{"kind": "checkout", "id": doc, "delete": True}])
    assert team.verdicts(seq) is None
    assert team.view("checkout", doc) is None


def test_a_checkout_cant_be_taken_in_someone_elses_name(team):
    with team.open(team.alice, writable=True) as ws:
        D.cancel_checkout(ws, team.doc["id"], team.co["token"])
    co = {"document_id": team.doc["id"], "token": "x", "owner": {"user": "alice", "account": "alice"}, "acquired": "t",
          "base_rev_id": team.rev["id"], "restored_from": None}
    seq = team.forge(team.bob, op="document.checkout", records=[put("checkout", team.doc["id"], co)])
    assert "its taker's own name" in team.verdicts(seq)


def test_a_checkout_cant_be_taken_from_someone_who_has_it(team):
    co = {"document_id": team.doc["id"], "token": "x", "owner": {"user": "bob", "account": "bob"}, "acquired": "t",
          "base_rev_id": team.rev["id"], "restored_from": None}
    seq = team.forge(team.bob, op="document.checkout", records=[put("checkout", team.doc["id"], co)])
    assert "already checked out" in team.verdicts(seq)
    assert team.view("checkout", team.doc["id"])["owner"]["account"] == "alice"


def _revision_copy(team):
    return {k: v for k, v in team.view("revision", team.rev["id"]).items() if k != "_rev"}


def test_a_published_revision_cant_change(team):
    rev = _revision_copy(team)
    seq = team.forge(team.boss, records=[put("revision", rev["id"], {**rev, "sha256": "0" * 64})])
    assert "can't change" in team.verdicts(seq)


def test_a_revision_cant_be_deleted(team):
    seq = team.forge(team.boss, records=[{"kind": "revision", "id": team.rev["id"], "delete": True}])
    assert "can't be deleted" in team.verdicts(seq)
    assert team.view("revision", team.rev["id"]) is not None


def test_a_lifecycle_step_cant_be_taken_in_someone_elses_name(team):
    """Forging someone's approval is the attack on engineering records that matters most."""
    rev = _revision_copy(team)
    step = {"from": "wip", "to": "released", "by": {"user": "boss", "account": "boss"}, "time": "t", "comment": "looks good"}
    seq = team.forge(team.bob, records=[put("revision", rev["id"], {**rev, "state": "released", "transitions": [step]})])
    assert "its taker's own name" in team.verdicts(seq)


def test_a_revisions_history_can_only_be_added_to(team):
    rev = _revision_copy(team)
    step = {"from": "wip", "to": "review", "by": {"user": "bob", "account": "bob"}, "time": "t", "comment": ""}
    seq = team.forge(team.bob, records=[put("revision", rev["id"], {**rev, "state": "review", "transitions": [step]})])
    assert team.verdicts(seq) is None
    seq = team.forge(team.bob, records=[put("revision", rev["id"], {**rev, "state": "wip", "transitions": []})])
    assert "only be added to" in team.verdicts(seq)


def test_a_new_revision_has_to_come_from_a_check_in(team):
    rev = {**_revision_copy(team), "id": "rev_" + "b" * 32, "number": 2, "author": {"user": "bob", "account": "bob"}}
    seq = team.forge(team.bob, op="document.checkin", records=[put("revision", rev["id"], rev)])
    assert "come from a check-in" in team.verdicts(seq)


def test_a_new_revision_cant_be_in_someone_elses_name(team):
    rev = {**_revision_copy(team), "id": "rev_" + "b" * 32, "number": 2, "author": {"user": "boss", "account": "boss"}}
    seq = team.forge(team.alice, op="document.checkin", records=[put("revision", rev["id"], rev),
                                                                  {"kind": "checkout", "id": team.doc["id"], "delete": True}])
    assert "author's own name" in team.verdicts(seq)


def _document_copy(team):
    return {k: v for k, v in team.view("document", team.doc["id"]).items() if k != "_rev"}


@pytest.mark.parametrize("field_name", ["latest_rev_id", "released_rev_id"])
def test_a_documents_revision_cant_be_pointed_somewhere_on_its_own(team, field_name):
    """Which revision is the released one is what a drawing register reads, so forging it would be forging a release."""
    doc = _document_copy(team)
    seq = team.forge(team.bob, records=[put("document", doc["id"], {**doc, field_name: "rev_" + "e" * 32})])
    assert "along with that revision" in team.verdicts(seq)
    if field_name == "latest_rev_id":
        seq = team.forge(team.bob, records=[put("document", doc["id"], {**doc, field_name: None})])
        assert "along with that revision" in team.verdicts(seq)
    seq = team.forge(team.bob, records=[put("document", doc["id"], {**doc, field_name: ["not", "a", "name"]})])
    assert "along with that revision" in team.verdicts(seq)


def test_a_documents_description_can_be_changed_by_anyone(team):
    doc = _document_copy(team)
    seq = team.forge(team.bob, records=[put("document", doc["id"], {**doc, "title": "Bracket, revised"})])
    assert team.verdicts(seq) is None


def test_documents_cant_be_deleted(team):
    seq = team.forge(team.boss, records=[{"kind": "document", "id": team.doc["id"], "delete": True}])
    assert "can't be deleted" in team.verdicts(seq)


def test_approving_your_own_revision_is_refused_by_every_computer_when_the_policy_says_so(team):
    with team.open(team.boss, writable=True) as ws:
        lifecycle = {k: v for k, v in D.get_lifecycle(ws).items() if k != "_rev"}
        D.save_lifecycle(ws, {**lifecycle, "require_distinct_approver": True})
    rev = _revision_copy(team)
    step = {"from": "in_review", "to": "approved", "by": {"user": "boss", "account": "boss"}, "time": "t", "comment": ""}
    assert rev["author"]["account"] == "boss"
    seq = team.forge(team.boss, records=[put("revision", rev["id"], {**rev, "state": "approved", "transitions": [step]})])
    assert "different person" in team.verdicts(seq)
    step = {**step, "by": {"user": "bob", "account": "bob"}}
    seq = team.forge(team.bob, records=[put("revision", rev["id"], {**rev, "state": "approved", "transitions": [step]})])
    assert team.verdicts(seq) is None


def test_audit_records_cant_be_changed_or_deleted(team):
    seq = team.forge(team.bob, records=[put("audit", "aud_" + "c" * 32, {"event": "x"})])
    assert team.verdicts(seq) is None
    assert "can't be changed" in team.verdicts(team.forge(team.bob, records=[put("audit", "aud_" + "c" * 32, {"event": "y"})]))
    assert "can't be changed" in team.verdicts(team.forge(team.boss, records=[{"kind": "audit", "id": "aud_" + "c" * 32, "delete": True}]))


@pytest.mark.parametrize("kind, rid", [("settings", "workspace"), ("lifecycle", "default"), ("doctype", "x"), ("template", "t"), ("root", "r")])
def test_workspace_settings_are_for_an_administrator(team, kind, rid):
    seq = team.forge(team.alice, records=[put(kind, rid, {"id": rid})])
    assert "only an administrator" in team.verdicts(seq)
    seq = team.forge(team.boss, records=[put(kind, rid, {"id": rid, "paths": {}})])
    assert team.verdicts(seq) is None


def test_an_entrys_expectations_are_checked_by_everyone_not_just_its_publisher(team):
    seq = team.forge(team.alice, records=[put("project", A, {"v": 1}, expect_rev=999)])
    assert "wasn't at the revision it expected" in team.verdicts(seq)
    seq = team.forge(team.alice, records=[put("project", A, {"v": 1})], tamper=lambda e: e.update(checks=[{"kind": "project", "id": "nope", "expect_rev": 5}]))
    assert "wasn't at the revision it expected" in team.verdicts(seq)
    seq = team.forge(team.alice, records=[put("project", A, {"v": 1})], tamper=lambda e: e.update(checks=[{"kind": 5, "id": [], "expect_rev": 5}]))
    assert "wasn't at the revision it expected" in team.verdicts(seq)


def test_the_last_administrator_cant_be_removed(team):
    boss = {k: v for k, v in team.view("account", "boss").items() if k != "_rev"}
    seq = team.forge(team.boss, op="account.disable", records=[put("account", "boss", {**boss, "active": False})])
    assert "no active administrator" in team.verdicts(seq)
    seq = team.forge(team.boss, op="account.role", records=[put("account", "boss", {**boss, "role": "user"})])
    assert "no active administrator" in team.verdicts(seq)
    assert team.view("account", "boss")["active"] is True
    with team.open(team.boss, writable=True) as ws:
        accounts.set_role(ws, "alice", "admin")
        accounts.set_role(ws, "boss", "user")                      # there's another now
    assert team.view("account", "boss")["role"] == "user"


def test_accounts_cant_be_deleted(team):
    seq = team.forge(team.boss, op="x", records=[{"kind": "account", "id": "bob", "delete": True}])
    assert "disabled, not deleted" in team.verdicts(seq)


@pytest.mark.parametrize("change", [{"role": "king"}, {"active": "yes"}, {"key": {"public": "zz", "salt": "00", "kdf": {}, "device": False}},
                                    {"key": "nope"}, {"name": 5}])
def test_a_malformed_account_has_no_effect(team, change):
    bob = {k: v for k, v in team.view("account", "bob").items() if k != "_rev"}
    seq = team.forge(team.boss, op="account.rename", records=[put("account", "bob", {**bob, **change})])
    assert "account bob" in team.verdicts(seq)
    assert team.view("account", "bob")["active"] is True


def test_the_recovery_key_can_only_recover_an_administrator(team):
    seed = accounts._recovery_seed(team.code, team.boss.workspace_id)
    actor = {"user": RECOVERY, "account": RECOVERY}
    seq = team.forge(signer=RECOVERY, seed=seed, actor=actor, op="t", records=[put("project", A, {"v": 1})])
    assert "only recover an administrator" in team.verdicts(seq)
    boss = {k: v for k, v in team.view("account", "boss").items() if k != "_rev"}
    seq = team.forge(signer=RECOVERY, seed=b"x" * 32, actor=actor, op="account.recover", records=[put("account", "boss", {**boss, "key": key_for(b"m" * 32)})])
    assert "doesn't match the recovery key" in team.verdicts(seq)
    seq = team.forge(signer=RECOVERY, seed=seed, actor=actor, op="account.recover", records=[put("account", "bob", {**boss, "role": "user", "id": "bob"})])
    assert "only recover an administrator" in team.verdicts(seq)
    seq = team.forge(signer=RECOVERY, seed=seed, actor=actor, op="account.recover", records=[put("account", "boss", {**boss, "key": key_for(b"n" * 32)})])
    assert team.verdicts(seq) is None


def test_a_second_enabling_entry_doesnt_replace_the_first(team):
    seed = b"m" * 32
    account = {"id": "mallory", "name": "M", "role": "admin", "active": True, "key": key_for(seed), "must_change": False}
    seq = team.forge(signer="mallory", seed=seed, op=ENABLE_OP, records=[put("account", "mallory", account),
                                                                         put("settings", "sharing", {"id": "sharing", "recovery_public": "0" * 64})])
    assert "no such account" in team.verdicts(seq)
    assert team.view("account", "mallory") is None


# ------------------------------------------------------------------ damage
def _journal(team, seq):
    return team.folder / ".revisionworx" / "journal" / f"{seq:020d}.json"


def test_an_altered_old_entry_is_noticed_and_this_computer_can_put_it_back(team):
    seq = 3
    path = _journal(team, seq)
    good = path.read_bytes()
    entry = json.loads(good)
    entry["time"] = "1999-01-01T00:00:00+00:00"
    entry["digest"] = entry_digest(entry)
    atomicio.atomic_write_json(path, entry)
    # a computer that read it before carries on from its own copy, and the check reports it
    with team.open(team.boss) as ws:
        assert [p["problem"] for p in ws.verify()["problems"] if p.get("severity") != "info"] == ["journal-entry-altered"]
    # a computer that has never seen this journal refuses to trust what follows, but can still read
    with Workspace.open(team.folder, state_dir=computer(team.tmp_path, "new")) as ws:
        assert ws.health()["ok"] is False
    report = SharedWorkspace.repair(team.folder / ".revisionworx", state_dir=team.boss_pc)
    assert report["repaired"] and report["restored"] == [seq]
    assert json.loads(path.read_bytes())["digest"] == json.loads(good)["digest"]
    with Workspace.open(team.folder, state_dir=computer(team.tmp_path, "new2")) as ws:
        assert ws.health()["ok"] is True
    assert list((team.folder / ".revisionworx" / "quarantine").rglob("*.json"))     # the altered one is kept, not destroyed


def test_a_missing_last_entry_stops_writes_until_its_put_back(team):
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    _journal(team, head).unlink()
    with pytest.raises(HistoryDiverged):
        team.open(team.boss, writable=True)
    with team.open(team.boss) as ws:                                # reads keep working
        assert ws.get("checkout", team.doc["id"]) is not None
        assert ws.health()["ok"] is False
    assert SharedWorkspace.repair(team.folder / ".revisionworx", state_dir=team.boss_pc)["restored"] == [head]
    with team.open(team.boss, writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"v": 1})])
    assert team.verdicts(head + 1) is None


def test_after_a_deliberate_restore_a_computer_can_forget_what_it_knew(team):
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    _journal(team, head).unlink()
    SharedWorkspace.forget_local(team.folder, state_dir=team.boss_pc)
    with team.open(team.boss, writable=True) as ws:
        assert ws._replica.head() == head - 1
        ws.commit("t", [RecordPut("project", A, {"v": 1})])


def test_a_gap_stops_writes_on_a_computer_that_never_saw_the_missing_entry(team):
    _journal(team, 4).unlink()
    new = computer(team.tmp_path, "new")
    with Workspace.open(team.folder, state_dir=new) as ws:
        assert ws._replica.head() == 3                             # reads what it can
    with pytest.raises(WorkspaceCorruptError, match="missing"):
        Workspace.open(team.folder, writable=True, session=team.boss, state_dir=new, wait=1.0)
    assert SharedWorkspace.repair(team.folder / ".revisionworx", state_dir=team.boss_pc)["restored"] == [4]
    with Workspace.open(team.folder, writable=True, session=team.boss, state_dir=new, wait=1.0) as ws:
        assert ws._replica.head() > 3


def test_a_replaced_entry_breaks_the_chain_for_new_computers(team):
    path = _journal(team, 5)
    entry = json.loads(path.read_bytes())
    entry["records"] = []                                          # a forger can recompute a digest, but not the next entry's prev
    entry["digest"] = entry_digest(entry)
    atomicio.atomic_write_json(path, entry)
    with Workspace.open(team.folder, state_dir=computer(team.tmp_path, "new")) as ws:
        assert ws._replica.head() == 5
        assert ws.health()["ok"] is False and "altered or replaced" in ws.health()["reason"]
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(team.folder, writable=True, session=team.boss, state_dir=computer(team.tmp_path, "new"), wait=1.0)


def test_changing_workspace_json_doesnt_turn_the_rules_off(team):
    """Otherwise anyone could put the old format back and write whatever they liked."""
    path = team.folder / ".revisionworx" / "workspace.json"
    info = atomicio.read_json_object(path)
    assert is_shared_info(info)
    atomicio.atomic_write_json(path, {k: v for k, v in info.items() if k != "collaboration"} | {"format_version": 1})
    with Workspace.open(team.folder) as ws:
        with pytest.raises(WorkspaceCorruptError, match="set this workspace up for a team"):
            ws.list("account")
    with pytest.raises(WorkspaceCorruptError):
        Workspace.open(team.folder, writable=True)


@pytest.mark.parametrize("bad", [b"", b"not json", b"[]", b"{}", b'{"seq": "x"}', b'{"seq": 999, "records": []}', b"\xff\xfe\x00",
                                 b'{"seq": 0, "records": "x"}', b"[" * 100000])
def test_a_damaged_next_entry_blocks_writes_but_never_crashes_and_reads_keep_working(team, bad):
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    path = _journal(team, head + 1)
    path.write_bytes(bad)
    with team.open(team.boss) as ws:
        assert ws.get("account", "boss") is not None
        assert ws.health()["ok"] is False
    with pytest.raises(WorkspaceCorruptError):
        team.open(team.boss, writable=True)
    path.unlink()
    with team.open(team.boss, writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"v": 1})])


def test_a_damaged_next_entry_can_be_moved_out_of_the_way(team):
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    path = _journal(team, head + 1)
    path.write_bytes(b"nonsense")
    with team.open(team.boss) as ws:
        problems = [p["problem"] for p in ws.verify()["problems"]]
    assert "journal-damaged" in problems
    report = SharedWorkspace.repair(team.folder / ".revisionworx", state_dir=team.boss_pc)
    assert report["removed"] == [] and path.exists()               # not unless it's asked for
    report = SharedWorkspace.repair(team.folder / ".revisionworx", state_dir=team.boss_pc, remove_damaged=True)
    assert report["removed"] == [head + 1] and not path.exists()
    assert list((team.folder / ".revisionworx" / "quarantine").rglob(path.name))
    assert [p for p in report["problems"] if p.get("severity") != "info"] == []
    with team.open(team.boss, writable=True) as ws:
        ws.commit("t", [RecordPut("project", A, {"v": 1})])


def test_damage_isnt_moved_if_this_computers_own_history_cant_be_put_right(team, monkeypatch):
    """Then it may be this computer that's wrong, and the entry that's fine."""
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    _journal(team, head).unlink()
    damaged = _journal(team, head + 1)
    damaged.write_bytes(b"nonsense")
    monkeypatch.setattr(SharedWorkspace, "_restore_entries", lambda self, problems: [])
    with pytest.raises(HistoryDiverged):
        SharedWorkspace.repair(team.folder / ".revisionworx", state_dir=team.boss_pc, remove_damaged=True)
    assert damaged.exists()


def test_a_far_too_big_next_entry_isnt_read(team, monkeypatch):
    import revisionworx.shared as shared
    monkeypatch.setattr(shared, "MAX_ENTRY_BYTES", 1000)
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    _journal(team, head + 1).write_bytes(b"x" * 2000)
    with team.open(team.boss) as ws:
        assert "too big" in ws.health()["reason"]


def test_hand_written_entries_of_every_odd_shape_never_crash_a_computer(team):
    """Fuzz: whatever's published next, a computer reaches a verdict, and all computers reach the same one."""
    rng = random.Random(20261105)
    junk = [None, True, 0, -1, 1.5, "", "x", [], {}, [1], {"a": 1}, "boss", "alice", "@recovery", "\ud800", "0" * 64]
    fields = ["seq", "txid", "time", "actor", "op", "records", "blobs", "checks", "request", "writer_token", "prev", "signer", "sig", "digest"]
    kinds = ["account", "checkout", "revision", "audit", "settings", "project", "document"]
    for round_ in range(150):
        with team.open(team.boss) as ws:
            head, anchor = ws.head_seq(), ws._replica.anchor()
        record = {"kind": rng.choice(kinds), "id": rng.choice(["boss", "alice", team.doc["id"], team.rev["id"], A])}
        if rng.random() < 0.3:
            record["delete"] = rng.choice([True, False, 1, "x"])
        else:
            record["data"] = rng.choice([{}, {"owner": rng.choice(junk)}, {"key": rng.choice(junk), "role": rng.choice(junk), "active": rng.choice(junk), "name": rng.choice(junk)},
                                         {"transitions": rng.choice(junk), "author": rng.choice(junk), "document_id": rng.choice(junk)}, rng.choice(junk)])
        entry = {"seq": head + 1, "txid": "f" * 32, "time": "t", "actor": {"user": "boss", "account": "boss"}, "op": rng.choice(["t", "account.password", ENABLE_OP, "account.recover"]),
                 "records": [record], "blobs": [], "checks": [], "writer_token": None, "prev": anchor, "signer": "boss"}
        for _ in range(rng.randrange(0, 3)):
            entry[rng.choice(fields)] = rng.choice(junk)
        if entry.get("seq") != head + 1:
            entry["seq"] = head + 1
        entry["prev"] = anchor
        entry["sig"] = ed25519.sign(team.boss.seed, signing_message(entry)).hex()
        if rng.random() < 0.5:
            entry["sig"] = rng.choice(junk) if isinstance(rng.choice(junk), str) else entry["sig"]
        try:
            entry["digest"] = entry_digest(entry)
        except UnicodeEncodeError:
            entry["digest"] = "0" * 64                             # someone writing the file by hand doesn't need a valid one
        text = json.dumps(entry)
        path = _journal(team, head + 1)
        path.write_text(text, encoding="utf-8")
        with team.open(team.boss) as ws:
            healthy = ws.health()["ok"]
        if not healthy:
            path.unlink()                                          # refused as damage: take it out and carry on
            continue
        team.verdicts(head + 1)
    assert round_ == 149


def test_two_computers_reach_exactly_the_same_state(team):
    """Random valid and invalid entries from everyone, read by a computer that followed along and one that read it all at the end."""
    rng = random.Random(7)
    sessions = [team.boss, team.alice, team.bob]
    for _ in range(40):
        who = rng.choice(sessions)
        rid = "prj_" + rng.choice("abc") * 32
        team.forge(who, records=[put("project", rid, {"n": rng.random()}, **({"expect_rev": rng.randrange(0, 60)} if rng.random() < 0.3 else {}))],
                   seed=who.seed if rng.random() < 0.8 else b"z" * 32)
        with team.open(team.alice):                                # alice's computer reads every entry as it comes
            pass
    late = computer(team.tmp_path, "late")
    with team.open(team.alice) as a, Workspace.open(team.folder, state_dir=late) as b:
        assert a._replica.head() == b._replica.head()
        assert a._replica.anchor() == b._replica.anchor()
        assert a._replica.all_entries() == b._replica.all_entries()
        for kind in ("project", "account", "checkout", "document", "revision"):
            assert a.list(kind) == b.list(kind)


def test_this_computers_copy_can_be_lost_and_rebuilt(team):
    path = replica_path(team.folder / ".revisionworx", team.boss.workspace_id, team.boss_pc)
    assert path.is_file()
    with team.open(team.boss) as ws:
        before = ws.list("document")
        head = ws.head_seq()
    path.write_bytes(b"this is not a database")
    with team.open(team.boss) as ws:
        assert ws.head_seq() == head and ws.list("document") == before


def test_a_copy_of_the_folder_is_a_different_history_to_this_computer(team, tmp_path):
    """A backup or a training copy has the same id. It mustn't be read as if it were the original, or stop this computer using either."""
    import shutil
    other = tmp_path / "training-copy"
    shutil.copytree(team.folder, other)
    with Workspace.open(other, writable=True, session=team.boss, state_dir=team.boss_pc) as ws:
        ws.commit("t", [RecordPut("project", A, {"where": "copy"})])
    with team.open(team.boss, writable=True) as ws:
        assert ws.get("project", A) is None
        ws.commit("t", [RecordPut("project", A, {"where": "original"})])
    with Workspace.open(other, session=team.boss, state_dir=team.boss_pc) as ws:
        assert ws.get("project", A)["where"] == "copy" and ws.health()["ok"]


def test_a_writer_whose_turn_was_cleared_has_no_path_to_publish(team, monkeypatch):
    """Administrative lock override stays and is fenced (docs/ADMINISTRATIVE_LOCK_FENCING_DIRECTIVE.md). The journal entry that clears a
    lock also revokes its token, and journal order decides."""
    from revisionworx.workspace import LockLostError
    holder = team.open(team.alice, writable=True)
    token = holder._lock_token
    SharedWorkspace.break_lock(team.folder / ".revisionworx", token, "she went to lunch", session=team.boss, state_dir=team.boss_pc)
    # it was stopped past every lock check, so it doesn't notice the lock is gone
    monkeypatch.setattr(holder, "_check_lock_still_held", lambda: None)
    with pytest.raises(LockLostError, match="cleared"):
        holder.commit("t", [RecordPut("project", A, {"who": "alice"})])
    holder._lock_token = None
    holder.close()
    # and a modified program that goes ahead and publishes anyway gets nowhere, on any computer
    seq = team.forge(team.alice, records=[put("project", A, {"who": "alice"})], tamper=lambda e: e.update(writer_token=token))
    assert "turn had been cleared" in team.verdicts(seq)
    assert team.view("project", A) is None


def test_a_forged_clearing_of_someones_turn_has_no_effect(team):
    """Unsigned by anyone real: it can't revoke anything."""
    data = {"event": "writer-lock-cleared", "previous_holder": {"token": "abc"}}
    seq = team.forge(signer="mallory", seed=b"m" * 32, op="workspace.lock.cleared", records=[put("audit", "aud_" + "f" * 32, data)])
    assert "no such account" in team.verdicts(seq)
    later = team.forge(team.alice, records=[put("project", A, {"v": 1})], tamper=lambda e: e.update(writer_token="abc"))
    assert team.verdicts(later) is None


def test_a_workspace_set_up_part_way_refuses_writes_and_can_be_finished(tmp_path):
    """A crash between writing workspace.json and publishing the entry."""
    folder = tmp_path / "share"
    folder.mkdir()
    Workspace.create(folder, "Team").close()
    path = folder / ".revisionworx" / "workspace.json"
    info = atomicio.read_json_object(path)
    atomicio.atomic_write_json(path, {**info, "format_version": 2, "collaboration": "shared"})
    from revisionworx.shared import SetupIncomplete
    pc = computer(tmp_path, "boss")
    session_free = Workspace.open(folder, state_dir=pc)
    assert session_free.health()["ok"] is False
    session_free.close()
    made = accounts.enable_team_use(folder, "boss", "Boss", PASSWORD, state_dir=pc)
    assert made["recovery_code"]
    with Workspace.open(folder, writable=True, session=made["session"], state_dir=pc) as ws:
        ws.commit("t", [RecordPut("project", A, {"v": 1})])
    # and the half-way state can't be written to by a session either
    other = tmp_path / "other"
    other.mkdir()
    Workspace.create(other, "Other").close()
    info = atomicio.read_json_object(other / ".revisionworx" / "workspace.json")
    atomicio.atomic_write_json(other / ".revisionworx" / "workspace.json", {**info, "format_version": 2, "collaboration": "shared"})
    with pytest.raises(SetupIncomplete):
        Workspace.open(other, writable=True, session=made["session"], state_dir=pc, wait=1.0)


def test_history_from_before_team_use_means_what_it_always_did(tmp_path):
    """Including a stale writer's late entry, which was fenced then and has to stay fenced."""
    folder = tmp_path / "share"
    folder.mkdir()
    ws = Workspace.create(folder, "Old")
    ws.commit("t", [RecordPut("project", A, {"v": "good"})])
    stale_token = ws._lock_token
    Workspace.break_writer_lock(folder, stale_token, "the writer stopped")
    seq = ws.head_seq() + 1
    late = {"seq": seq, "txid": "e" * 32, "time": "t", "actor": {"user": "x"}, "op": "t", "writer_token": stale_token, "blobs": [], "checks": [],
            "records": [{"kind": "project", "id": A, "data": {"v": "late"}}]}
    late["digest"] = entry_digest(late)
    atomicio.publish_exclusive(folder / ".revisionworx" / "journal" / f"{seq:020d}.json", atomicio.dumps_json(late))
    ws._lock_token = None
    ws.close()
    with Workspace.open(folder) as plain:
        assert plain.get("project", A)["v"] == "good" and plain.is_fenced(late)
    pc = computer(tmp_path, "boss")
    made = accounts.enable_team_use(folder, "boss", "Boss", PASSWORD, state_dir=pc)
    with Workspace.open(folder, state_dir=computer(tmp_path, "new"), session=made["session"]) as team_ws:
        assert team_ws.get("project", A)["v"] == "good"
        assert team_ws.is_fenced(late) and "revoked" in team_ws.fenced_reason(late)
        assert team_ws.transaction_outcome("e" * 32)["state"] == "fenced"
    with Workspace.open(folder, writable=True, session=made["session"], state_dir=pc) as team_ws:
        team_ws.commit("t", [RecordPut("project", A, {"v": "new"}, expect_rev=2)])
        assert team_ws.get("project", A)["v"] == "new"


def test_doctor_reports_on_a_team_workspace_and_changes_nothing_in_the_folder(team):
    from conftest import tree_snapshot
    before = tree_snapshot(team.folder)
    report = doctor.examine(team.folder)
    assert report["errors"] == 0, report
    assert "team" in report["checked"]
    assert tree_snapshot(team.folder) == before
    seq = team.forge(signer="mallory", seed=b"m" * 32, records=[put("project", A, {"v": 1})])
    findings = {f["code"]: f for f in doctor.examine(team.folder)["findings"]}
    assert "J005" in findings and "no such account" in findings["J005"]["message"]
    assert findings["J005"]["severity"] == "info"
    assert team.verdicts(seq)


def test_doctor_on_a_team_workspace_finds_a_lost_entry_this_computer_has_read(team, monkeypatch):
    import revisionworx.clientstate as cs
    monkeypatch.setattr(cs, "default_state_dir", lambda: team.boss_pc)
    with team.open(team.boss) as ws:
        head = ws.head_seq()
    assert doctor.examine(team.folder)["errors"] == 0
    _journal(team, head).unlink()
    codes = {f["code"] for f in doctor.examine(team.folder)["findings"] if f["severity"] == "error"}
    assert "J010" in codes and "T001" in codes
