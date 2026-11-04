# SPDX-License-Identifier: GPL-3.0-or-later
import multiprocessing as mp
import os

import pytest

import proc_helpers
from revisionworx import atomicio, paths
from revisionworx.atomicio import ExclusiveCreateError


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    target = tmp_path / "a.json"
    atomicio.atomic_write_json(target, {"v": 1})
    atomicio.atomic_write_json(target, {"v": 2})
    assert atomicio.read_json(target) == {"v": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_publish_exclusive_never_overwrites(tmp_path):
    target = tmp_path / "x"
    atomicio.publish_exclusive(target, b"first")
    with pytest.raises(ExclusiveCreateError):
        atomicio.publish_exclusive(target, b"second")
    assert target.read_bytes() == b"first"
    assert [p.name for p in tmp_path.iterdir()] == ["x"]


def test_publish_exclusive_race_between_processes_has_one_winner(tmp_path):
    ctx = mp.get_context("spawn")
    n = 8
    barrier, results = ctx.Barrier(n), ctx.Queue()
    target = tmp_path / "contested"
    procs = [ctx.Process(target=proc_helpers.race_publish, args=(str(target), f"p{i}", barrier, results))
             for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    outcomes = [results.get(timeout=5) for _ in range(n)]
    winners = [payload for status, payload in outcomes if status == "won"]
    assert len(winners) == 1
    assert target.read_text() == winners[0]  # content belongs to the winner, never torn or mixed


def test_cleanup_temp_files(tmp_path):
    old = tmp_path / ".x.abc.tmp"
    old.write_text("partial")
    os.utime(old, (1, 1))  # long abandoned
    young = tmp_path / ".y.def.tmp"
    young.write_text("another client is writing this right now")
    (tmp_path / "keep.json").write_text("{}")
    removed = atomicio.cleanup_temp_files(tmp_path)
    assert [p.name for p in removed] == [".x.abc.tmp"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [".y.def.tmp", "keep.json"]


@pytest.mark.parametrize("bad", ["..", "a/../b", "/abs", "C:/x", "a/CON", "a/b:c", "trailing.", "x/ y /",
                                 "nul.txt", "a\x01b", ""])
def test_normalize_relative_rejects_unsafe(bad):
    with pytest.raises(paths.PathValidationError):
        paths.normalize_relative(bad)


def test_normalize_relative_normalises_separators_and_unicode():
    assert paths.normalize_relative("A\\B//C") == "A/B/C"
    assert paths.normalize_relative("Cafe\u0301") == "Caf\u00e9"  # NFD -> NFC
    assert paths.case_key("ABC/Déf") == paths.case_key("abc/dÉF")


def test_relative_to_root_refuses_escape_and_root_itself(tmp_path):
    root = tmp_path / "root"
    (root / "proj").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert paths.relative_to_root(root, root / "proj") == "proj"
    with pytest.raises(paths.PathValidationError):
        paths.relative_to_root(root, outside)
    with pytest.raises(paths.PathValidationError):
        paths.relative_to_root(root, root)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_resolve_under_root_refuses_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, root / "sneaky")
    except OSError:
        pytest.skip("cannot create symlinks here")
    with pytest.raises(paths.PathValidationError):
        paths.resolve_under_root(root, "sneaky")
    with pytest.raises(paths.PathValidationError):
        paths.relative_to_root(root, root / "sneaky")
