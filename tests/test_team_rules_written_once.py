# SPDX-License-Identifier: GPL-3.0-or-later
"""Team rules 2 (ADR-024, revision of 2026-11-08): in a team workspace, records the unit register writes once (unit events,
build standards, effectivity decisions, deviations) can't be changed or removed, by anyone. The rules a workspace follows are in
its signed team settings, so every computer judges each entry by the same rules; they never go back; a team set up before keeps
rules 1 until an administrator upgrades it; and rules this version doesn't know stop it. Entries are forged by hand, as a
modified copy of RevisionWorx would write them."""
import pytest

from revisionworx import accounts, atomicio, cli, shared
from revisionworx.units import service as units_service
from revisionworx.workspace import SHARED_FORMAT_VERSION, Workspace, WorkspaceFormatError, is_shared_info
from team_helpers import PASSWORD, computer
from test_team_rules import Team, put, team  # noqa: F401 - the fixture

ONCE = sorted(shared.WRITTEN_ONCE)


def delete(kind, rid):
    return {"kind": kind, "id": rid, "delete": True}


def test_the_kinds_written_once_are_the_ones_the_unit_register_writes_once():
    assert set(shared.WRITTEN_ONCE) == {units_service.KIND[k] for k in ("event", "standard", "effectivity", "deviation")}


def test_a_team_set_up_now_is_format_3_on_rules_2(team):
    info = atomicio.read_json_object(team.folder / ".revisionworx" / "workspace.json")
    assert info["format_version"] == SHARED_FORMAT_VERSION == 3 and is_shared_info(info)
    assert team.view("settings", "sharing")["rules"] == shared.RULES == 2
    with team.open(team.alice) as ws:
        assert ws.rules == 2


@pytest.mark.parametrize("kind", ONCE)
def test_what_the_unit_register_writes_once_cant_be_changed_or_removed(team, kind):
    first = team.forge(team.alice, records=[put(kind, "r1", {"v": 1}, expect_rev=0)])
    assert team.verdicts(first) is None
    for session, records in ((team.alice, [put(kind, "r1", {"v": 2})]), (team.alice, [put(kind, "r1", {"v": 2}, expect_rev=first)]),
                             (team.boss, [put(kind, "r1", {"v": 2})]), (team.alice, [delete(kind, "r1")]),
                             (team.boss, [delete(kind, "r1")])):
        seq = team.forge(session, records=records)
        assert f"{shared.WRITTEN_ONCE[kind]} are written once" in team.verdicts(seq)
    assert team.view(kind, "r1") == {"v": 1, "_rev": first}
    assert team.verdicts(team.forge(team.bob, records=[put(kind, "r2", {"v": 1}, expect_rev=0)])) is None   # new ones still go in


@pytest.mark.parametrize("kind", ["unit", "unitkey", "unitcmp", "unitdvu", "unitseq"])
def test_the_unit_records_that_do_change_still_change(team, kind):
    first = team.forge(team.alice, records=[put(kind, "r1", {"v": 1}, expect_rev=0)])
    seq = team.forge(team.bob, records=[put(kind, "r1", {"v": 2}, expect_rev=first)])
    assert team.verdicts(seq) is None and team.view(kind, "r1") == {"v": 2, "_rev": seq}


def test_the_rules_never_go_back(team):
    sharing = {k: v for k, v in team.view("settings", "sharing").items() if k != "_rev"}
    for records in ([put("settings", "sharing", {**sharing, "rules": 1})], [put("settings", "sharing", {**sharing, "rules": "2"})],
                    [put("settings", "sharing", {k: v for k, v in sharing.items() if k != "rules"})],
                    [delete("settings", "sharing")]):
        seq = team.forge(team.boss, records=records)
        assert team.verdicts(seq) in ("the team's rules can't go back to an earlier version", "the team's settings can't be removed")
    assert team.view("settings", "sharing")["rules"] == 2
    assert "only an administrator" in team.verdicts(team.forge(team.alice, records=[put("settings", "sharing", sharing)]))


def test_rules_this_version_doesnt_know_stop_it(team):
    sharing = {k: v for k, v in team.view("settings", "sharing").items() if k != "_rev"}
    seq = team.forge(team.boss, records=[put("settings", "sharing", {**sharing, "rules": 3})])
    after = team.forge(team.alice, records=[put("project", "prj_" + "b" * 32, {"v": 1})])
    with Workspace.open(team.folder, state_dir=computer(team.tmp_path, "new")) as ws:
        ws.sync(fresh=True)
        assert ws._replica.head() == seq and ws.rules == 3          # read up to the change, and no further
        assert ws.get("project", "prj_" + "b" * 32) is None
        assert "team rules 3, which this RevisionWorx doesn't know" in ws.health()["reason"]
    with pytest.raises(WorkspaceFormatError, match="update RevisionWorx"):
        team.open(team.alice, writable=True)
    assert after == seq + 1


def test_a_team_set_up_before_keeps_rules_1_until_an_administrator_upgrades_it(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "HINT_PATIENCE", 0.2)
    monkeypatch.setattr(shared, "GAP_PATIENCE", 0.5)
    monkeypatch.setattr(accounts, "NEW_TEAM_FORMAT", 2)              # as a team was set up before 2026-11-08
    monkeypatch.setattr(accounts, "NEW_TEAM_RULES", 1)
    old = Team(tmp_path)
    info_path = old.folder / ".revisionworx" / "workspace.json"
    assert atomicio.read_json_object(info_path)["format_version"] == 2 and "rules" not in old.view("settings", "sharing")
    first = old.forge(old.alice, records=[put("unitevt", "r1", {"v": 1}, expect_rev=0)])
    overwrite = old.forge(old.alice, records=[put("unitevt", "r1", {"v": 2})])
    assert old.verdicts(overwrite) is None                           # rules 1, as every computer judged it before
    with old.open(old.alice, writable=True) as ws:
        assert ws.rules == 1
        with pytest.raises(accounts.NotPermitted):
            accounts.upgrade_rules(ws)
    with old.open(old.boss, writable=True) as ws:
        assert accounts.upgrade_rules(ws) == {"rules": 2, "changed": True}
        assert accounts.upgrade_rules(ws) == {"rules": 2, "changed": False}
    assert atomicio.read_json_object(info_path)["format_version"] == 3
    later = old.forge(old.alice, records=[put("unitevt", "r1", {"v": 3})])
    assert "unit events are written once" in old.verdicts(later)
    assert old.verdicts(overwrite) is None and old.view("unitevt", "r1") == {"v": 2, "_rev": overwrite}   # the past stands
    assert first < overwrite


def test_an_upgrade_interrupted_after_workspace_json_is_finished_by_running_it_again(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "NEW_TEAM_FORMAT", 2)
    monkeypatch.setattr(accounts, "NEW_TEAM_RULES", 1)
    old = Team(tmp_path)
    info_path = old.folder / ".revisionworx" / "workspace.json"
    atomicio.atomic_write_json(info_path, {**atomicio.read_json_object(info_path), "format_version": 3})   # the crash
    with old.open(old.alice) as ws:
        assert ws.rules == 1                                          # format 3 on rules 1: read as it is
    with old.open(old.boss, writable=True) as ws:
        assert accounts.upgrade_rules(ws)["changed"]
    with old.open(old.alice) as ws:
        assert ws.rules == 2


def test_formats_this_version_opens(tmp_path):
    assert is_shared_info({"collaboration": "shared", "format_version": 2})
    assert is_shared_info({"collaboration": "shared", "format_version": 3})
    assert not is_shared_info({"collaboration": "shared", "format_version": 4})


def test_team_status_and_upgrade_from_the_command_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(accounts, "NEW_TEAM_FORMAT", 2)
    monkeypatch.setattr(accounts, "NEW_TEAM_RULES", 1)
    folder = tmp_path / "share"
    folder.mkdir()
    boss = ["--state-dir", str(computer(tmp_path, "boss"))]
    w = ["-w", str(folder)]
    assert cli.main(["init", str(folder), "--name", "Team"]) == 0
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main([*w, *boss, "team", "enable", "--admin", "boss", "--password-stdin"]) == 0
    capsys.readouterr()
    assert cli.main([*w, *boss, "team", "status"]) == 0
    assert "team rules 1" in capsys.readouterr().out
    assert cli.main([*w, *boss, "team", "upgrade"]) == 0
    assert "now follows team rules 2" in capsys.readouterr().out
    assert cli.main([*w, *boss, "team", "upgrade"]) == 0
    assert "already follows team rules 2" in capsys.readouterr().out
    assert cli.main([*w, *boss, "--json", "team", "status"]) == 0
    assert '"rules": 2' in capsys.readouterr().out
