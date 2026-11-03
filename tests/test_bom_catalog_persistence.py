# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #18 / backlog #28 item 5: transaction persistence review of the PROPOSED WorkspaceCatalogStore.

Failure modes, all with the real core (docs/bom/BOM_API.md section 7):
* a second writer is refused while a catalog writer holds the workspace;
* an administratively revoked (fenced) catalog writer publishes nothing (ADR-015/019);
* a writer process killed right after its commit returns keeps the change (durable before return);
* a read-only reader sees committed catalog data after reopen, and ``verify()`` stays clean."""
import subprocess
import sys
from pathlib import Path

import pytest

from revisionworx.bom import PartsCatalogService, WorkspaceCatalogStore
from revisionworx.workspace import LockLostError, Workspace, WorkspaceLockedError

REPO = Path(__file__).resolve().parent.parent


def test_second_writer_is_refused_while_catalog_writer_holds_the_lock(ws, share):
    svc = PartsCatalogService(WorkspaceCatalogStore(ws))
    svc.create_part("A-1")
    with pytest.raises(WorkspaceLockedError):
        Workspace.open(share, writable=True)
    with Workspace.open(share) as ro:                    # readers are never blocked
        assert PartsCatalogService(WorkspaceCatalogStore(ro)).find_by_number("a-1") is not None


def test_fenced_catalog_writer_publishes_nothing(share, monkeypatch):
    stale = Workspace.create(share, "W")
    svc = PartsCatalogService(WorkspaceCatalogStore(stale))
    svc.create_part("KEEP-1")
    real_check = stale._check_lock_still_held

    def check_then_break():          # the administrator breaks the lock between check and publish
        real_check()
        Workspace.break_writer_lock(share, stale._lock_token, reason="catalog writer unresponsive")
    monkeypatch.setattr(stale, "_check_lock_still_held", check_then_break)
    with pytest.raises(LockLostError):
        svc.create_part("LOST-1", txid="e" * 32)
    monkeypatch.undo()
    assert stale.transaction_outcome("e" * 32)["state"] == "not-found"
    stale._lock_token = None
    stale.close()
    with Workspace.open(share, writable=True) as admin:
        s2 = PartsCatalogService(WorkspaceCatalogStore(admin))
        assert s2.find_by_number("LOST-1") is None and s2.find_by_number("KEEP-1") is not None
        assert admin.verify()["problems"] == []
        s2.create_part("LOST-1")                          # the number was never claimed


CRASH = r"""
import os, sys
sys.path.insert(0, sys.argv[2])
from pathlib import Path
from revisionworx.workspace import Workspace
from revisionworx.bom import PartsCatalogService, WorkspaceCatalogStore
ws = Workspace.open(Path(sys.argv[1]), writable=True)
svc = PartsCatalogService(WorkspaceCatalogStore(ws))
a = svc.create_part("CRASH-A", kind="assembly")
b = svc.create_part("CRASH-B")
svc.add_line(a.part_id, b.part_id, "3")
print("COMMITTED", flush=True)
os._exit(9)          # die without closing: no release, no cleanup
"""


def test_commit_survives_a_killed_writer_process(share, tmp_path):
    Workspace.create(share, "W").close()
    script = tmp_path / "crash.py"
    script.write_text(CRASH)
    out = subprocess.run([sys.executable, str(script), str(share), str(REPO)], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 9 and "COMMITTED" in out.stdout, out.stderr
    with Workspace.open(share) as ro:
        svc = PartsCatalogService(WorkspaceCatalogStore(ro))
        a, b = svc.find_by_number("CRASH-A"), svc.find_by_number("CRASH-B")
        assert a and b and svc.bom(a.part_id)[0].child_id == b.part_id
        assert ro.verify()["problems"] == []
    with pytest.raises(WorkspaceLockedError):             # the dead writer's lock is not silently stolen
        Workspace.open(share, writable=True)
