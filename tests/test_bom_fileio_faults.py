# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18: exports never leave a partial or overwritten destination, even when writing fails or the
process is killed mid-export."""
import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bom_helpers import frame
from revisionworx.bom import ExportError, save, structure_to_csv
from revisionworx.bom import fileio

OLD = b"old content that must survive\n"


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.endswith(".bomtmp"))


def test_new_file_and_overwrite_rules(tmp_path):
    dest = tmp_path / "bom.csv"
    data = structure_to_csv(frame())
    save(data, dest)
    assert dest.read_bytes() == data
    with pytest.raises(ExportError, match="already exists"):
        save(b"other", dest)
    assert dest.read_bytes() == data
    save(b"replaced", dest, overwrite=True)
    assert dest.read_bytes() == b"replaced" and leftovers(tmp_path) == []


@pytest.mark.parametrize("where", ["write", "fsync", "replace"])
def test_failure_at_any_step_leaves_destination_intact(tmp_path, monkeypatch, where):
    dest = tmp_path / "bom.csv"
    dest.write_bytes(OLD)

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(fileio.os, {"write": "write", "fsync": "fsync", "replace": "replace"}[where], boom)
    with pytest.raises(ExportError, match="No space left"):
        save(b"new" * 1000, dest, overwrite=True)
    monkeypatch.undo()
    assert dest.read_bytes() == OLD and leftovers(tmp_path) == []


def test_interrupt_mid_write_cleans_up(tmp_path, monkeypatch):
    dest = tmp_path / "bom.csv"
    dest.write_bytes(OLD)
    real = os.write
    calls = []

    def interrupted(fd, data):
        calls.append(1)
        if len(calls) > 1:
            raise KeyboardInterrupt
        return real(fd, bytes(data[:10]))
    monkeypatch.setattr(fileio.os, "write", interrupted)
    with pytest.raises(KeyboardInterrupt):
        save(b"x" * 100, dest, overwrite=True)
    monkeypatch.undo()
    assert dest.read_bytes() == OLD and leftovers(tmp_path) == []


def test_exclusive_publish_loses_race_cleanly(tmp_path, monkeypatch):
    """Another program creates the name between the existence check and the publish."""
    dest = tmp_path / "bom.csv"
    real_exists = Path.exists

    def racing_exists(self):
        result = real_exists(self)
        if self == dest and not result:
            dest.write_bytes(OLD)
        return result
    monkeypatch.setattr(Path, "exists", racing_exists)
    with pytest.raises(ExportError, match="already exists"):
        save(b"mine", dest)
    monkeypatch.undo()
    assert dest.read_bytes() == OLD and leftovers(tmp_path) == []


@pytest.mark.parametrize("make", ["metadata", "metadata-case", "folder", "missing-parent"])
def test_refused_destinations(tmp_path, make):
    if make == "metadata":
        (tmp_path / ".revisionworx").mkdir()
        dest = tmp_path / ".revisionworx" / "bom.csv"
    elif make == "metadata-case":
        (tmp_path / ".RevisionWorx" / "x").mkdir(parents=True)
        dest = tmp_path / ".RevisionWorx" / "x" / "bom.csv"
    elif make == "folder":
        dest = tmp_path / "dir"
        dest.mkdir()
    else:
        dest = tmp_path / "nope" / "bom.csv"
    with pytest.raises(ExportError):
        save(b"data", dest)
    if make.startswith("metadata"):
        assert not dest.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_destination_refused(tmp_path):
    target = tmp_path / "target.csv"
    target.write_bytes(OLD)
    link = tmp_path / "link.csv"
    link.symlink_to(target)
    with pytest.raises(ExportError, match="symbolic link"):
        save(b"new", link, overwrite=True)
    assert target.read_bytes() == OLD


KILL_SCRIPT = r"""
import os, sys, time
sys.path[:0] = [sys.argv[2]]
from revisionworx.bom import fileio
real_replace = os.replace
def slow_replace(src, dst):
    print("TEMP-WRITTEN", flush=True)
    time.sleep(60)
    real_replace(src, dst)
fileio.os.replace = slow_replace
fileio.write_file(sys.argv[1], b"NEW" * 100000, overwrite=True)
"""


def test_killed_process_never_leaves_a_partial_destination(tmp_path):
    """Restart path: the exporting process is killed after its temp file is durable but before the
    rename. The destination keeps the old content; only a hidden temp file can remain."""
    dest = tmp_path / "bom.csv"
    dest.write_bytes(OLD)
    script = tmp_path / "killme.py"
    script.write_text(KILL_SCRIPT)
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen([sys.executable, str(script), str(dest), str(repo)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "TEMP-WRITTEN"
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:  # pragma: no cover
            proc.kill()
    assert dest.read_bytes() == OLD
    assert len(leftovers(tmp_path)) == 1  # the abandoned temp file, never the destination
    # a later export still works and ignores the abandoned temp file
    save(b"after restart", dest, overwrite=True)
    assert dest.read_bytes() == b"after restart"


def test_read_file_limit(tmp_path):
    from revisionworx.bom import BomSourceError
    p = tmp_path / "big.csv"
    p.write_bytes(b"x" * 101)
    with pytest.raises(BomSourceError, match="limit"):
        fileio.read_file(p, 100)
    assert fileio.read_file(p, 101) == b"x" * 101

