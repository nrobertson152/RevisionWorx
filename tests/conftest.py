# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # make helper modules importable in spawned processes

from revisionworx import projects  # noqa: E402
from revisionworx.workspace import Workspace  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def tree_snapshot(root: Path) -> dict:
    """Every path under root with type, size, mtime and content hash - for non-destruction checks."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = os.lstat(p)
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                snap[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                snap[rel] = ("dir", st.st_mtime_ns)
            else:
                snap[rel] = ("file", st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    snap["."] = ("dir", os.lstat(root).st_mtime_ns)
    return snap


@pytest.fixture
def share(tmp_path):
    """A fake engineering share: metadata folder and an engineering root side by side."""
    s = tmp_path / "share"
    (s / "Engineering").mkdir(parents=True)
    return s


@pytest.fixture
def ws(share):
    w = Workspace.create(share, "Test workspace")
    w.root_id = projects.add_root(w, "Engineering", share / "Engineering")
    yield w
    w.close()


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"
