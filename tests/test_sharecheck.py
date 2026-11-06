# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding out whether a folder is fit for team use (ADR-024). On a local disk here, which says nothing about any network share."""
import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import revisionworx
from revisionworx import atomicio, cli, sharecheck


def test_a_local_folder_passes_and_nothing_is_left_behind(tmp_path):
    report = sharecheck.check_folder(tmp_path)
    assert report["ok"], report
    assert {c["name"] for c in report["checks"]} >= {"exclusive create", "racing for a name", "atomic replace", "rename while open"}
    assert list(tmp_path.iterdir()) == []
    assert "one computer" in report["scope"]


def test_exclusive_create_that_isnt_is_found(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio, "move_exclusive", lambda src, dst: os.replace(src, dst))
    report = sharecheck.check_folder(tmp_path)
    assert not report["ok"]
    bad = {c["name"] for c in report["checks"] if not c["ok"]}
    assert "exclusive create" in bad and "racing for a name" in bad
    assert list(tmp_path.iterdir()) == []


def test_a_share_that_says_access_denied_instead_of_already_exists_is_found(tmp_path, monkeypatch):
    """Some NAS servers do. RevisionWorx couldn't tell a taken name from a refusal, so that's a failure, not a guess."""
    real = atomicio.move_exclusive

    def denied(src, dst):
        if Path(dst).exists():
            raise PermissionError(13, "access denied")
        real(src, dst)
    monkeypatch.setattr(atomicio, "move_exclusive", denied)
    monkeypatch.setattr(atomicio, "RETRY_SHARING", False)
    assert not sharecheck.check_folder(tmp_path)["ok"]


def test_a_share_without_hard_links_is_found(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows publishes by rename, not by hard link")
    def no_links(_src, _dst):
        raise OSError(95, "operation not supported")
    monkeypatch.setattr(os, "link", no_links)
    report = sharecheck.check_folder(tmp_path)
    assert not report["ok"]


def test_a_folder_that_cant_be_written_to_is_a_finding_not_a_crash(tmp_path):
    report = sharecheck.check_folder(tmp_path / "not" / "there")
    assert not report["ok"] and report["checks"][0]["name"] == "creating files"


def test_one_computer_racing_itself_is_no_evidence(tmp_path):
    report = sharecheck.run_test(tmp_path, seconds=0.2, runner="only-one")
    assert report["runners"] == 1 and not report["ok"] and not report["cross_computer"]
    assert "isn't evidence" in report["verdict"]
    assert report["slots_won"] > 0 and not report["problems"]


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(revisionworx.__file__).parent.parent), env.get("PYTHONPATH", "")])
    return env


def test_a_share_saying_try_again_during_the_race_is_counted_and_survived(tmp_path, monkeypatch):
    real = atomicio.publish_exclusive
    calls = []

    def sometimes_busy(path, data):
        calls.append(path)
        if len(calls) % 5 == 0:
            raise OSError(errno.EBUSY, "busy")
        return real(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", sometimes_busy)
    report = sharecheck.run_test(tmp_path, seconds=0.2, runner="only-one")
    assert report["busy_answers"] > 0 and not report["problems"]


def test_a_folder_another_computer_just_made_can_look_missing_for_a_moment(tmp_path, monkeypatch):
    """Having been told the folder exists, an NFS client told this one it didn't, for a few seconds."""
    real, calls = os.listdir, []

    def missing_at_first(path):
        calls.append(path)
        if len(calls) < 3:
            raise FileNotFoundError(path)
        return real(path)
    monkeypatch.setattr(os, "listdir", missing_at_first)
    sharecheck._make_shared_folder(tmp_path / "a" / "slots")
    assert len(calls) >= 3


def test_two_processes_really_race_and_each_name_has_one_winner(tmp_path):
    """Real processes on one local disk. It proves the test works, not that any share does."""
    command = [sys.executable, "-m", "revisionworx", "--json", "share", "test", str(tmp_path), "--seconds", "3"]
    procs = [subprocess.Popen(command, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
    report = sharecheck.read_report(tmp_path)
    assert report["runners"] == 3 and not report["problems"], report
    assert report["slots_won"] > 0 and report["races_lost"] > 0
    assert report["hosts"] == sorted({report["hosts"][0]}) and not report["ok"]


def _result(folder, name, **fields):
    base = Path(folder) / sharecheck.TEST_FOLDER / "runners"
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{name}.json").write_text(json.dumps({"runner": name, "host": name, "won": [], "seen": {}, "problems": [], "slowest_read": 0.0,
                                                   "stale_listings": 0, "oldest_stale_listing": 0.0, **fields}), encoding="utf-8")


def test_two_computers_that_agree_are_evidence(tmp_path):
    _result(tmp_path, "pc1", won=[1, 3], seen={"2": "pc2"})
    _result(tmp_path, "pc2", won=[2], seen={"1": "pc1", "3": "pc1"})
    report = sharecheck.read_report(tmp_path)
    assert report["ok"] and report["cross_computer"] and report["hosts"] == ["pc1", "pc2"]
    assert "passed between 2 computers" in report["verdict"]


def test_two_computers_that_never_met_arent_evidence(tmp_path):
    _result(tmp_path, "pc1", won=[1])
    _result(tmp_path, "pc2", won=[2], seen={"1": "pc1"})
    assert not sharecheck.read_report(tmp_path)["ok"]


def test_two_winners_of_one_name_is_a_failure(tmp_path):
    _result(tmp_path, "pc1", won=[1], seen={"2": "pc2"})
    _result(tmp_path, "pc2", won=[1, 2], seen={"1": "pc1"})
    report = sharecheck.read_report(tmp_path)
    assert not report["ok"] and "FAILED" in report["verdict"] and any("2 winners" in p for p in report["problems"])


def test_seeing_the_wrong_winner_is_a_failure(tmp_path):
    _result(tmp_path, "pc1", won=[1], seen={"2": "pc2"})
    _result(tmp_path, "pc2", won=[2], seen={"1": "pc3"})
    report = sharecheck.read_report(tmp_path)
    assert not report["ok"] and any("read pc3" in p for p in report["problems"])


def test_a_torn_read_reported_by_a_computer_is_a_failure(tmp_path):
    _result(tmp_path, "pc1", won=[1], seen={"2": "pc2"}, problems=["slot 2: a torn or damaged file"])
    _result(tmp_path, "pc2", won=[2], seen={"1": "pc1"})
    assert "FAILED" in sharecheck.read_report(tmp_path)["verdict"]


def test_a_damaged_result_file_is_a_problem_not_a_crash(tmp_path):
    _result(tmp_path, "pc1")
    (tmp_path / sharecheck.TEST_FOLDER / "runners" / "bad.json").write_bytes(b"nonsense")
    assert sharecheck.read_report(tmp_path)["problems"]


def test_nothing_run_yet(tmp_path):
    report = sharecheck.read_report(tmp_path)
    assert not report["ok"] and "nothing has been run" in report["verdict"]


def test_the_commands(tmp_path, capsys):
    assert cli.main(["share", "check", str(tmp_path)]) == 0
    assert "passed" in capsys.readouterr().out
    assert cli.main(["share", "test", str(tmp_path), "--seconds", "0.2"]) == 0
    assert "isn't evidence" in capsys.readouterr().out
    assert cli.main(["share", "report", str(tmp_path)]) == 0
    _result(tmp_path, "pc1", won=[1], problems=["bad"])
    with pytest.raises(SystemExit):
        cli.main(["share", "report", str(tmp_path)])
