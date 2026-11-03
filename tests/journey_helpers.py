# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared by the GUI journey tests (backlog #28 item 04): a real crash between commit and apply, and
independent checks through the CLI. Not a test module."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from revisionworx.cli import main as cli
from revisionworx.gui.controller import Controller

# The previous run of the app: it checks in, the journal entry is committed, and the process dies before the
# entry is applied to the records (power loss, kill -9, a crash). ``os._exit`` skips every finally/atexit, so
# the writer lock is left behind exactly as a real crash leaves it.
CRASHING_CHECKIN = r'''
import os, sys
from pathlib import Path
from revisionworx.gui.controller import Controller
from revisionworx.workspace import Workspace
root, doc_id, description = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
c = Controller(cfg_dir=root / "cfg", cache_dir=root / "cache", state_dir=root / "state")
c.open_workspace(Path(c.recent_workspaces()[0]))
Workspace._apply = lambda self, entry: os._exit(9)
c.checkin_now(doc_id, description)
os._exit(3)  # not reached if the check-in committed
'''


def make_ctl(root: Path) -> Controller:
    return Controller(cfg_dir=root / "cfg", cache_dir=root / "cache", state_dir=root / "state")


def run_crashing_checkin(root: Path, doc_id: str, description: str) -> int:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    return subprocess.run([sys.executable, "-c", CRASHING_CHECKIN, str(root), doc_id, description],
                          env=env, timeout=120).returncode


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def exported(home: Path, rev_id: str, dest: Path) -> bytes:
    assert cli(["-w", str(home), "rev", "export", rev_id, str(dest)]) in (0, None)
    return dest.read_bytes()
