# SPDX-License-Identifier: GPL-3.0-or-later
"""Functions executed in separate processes by the concurrency tests."""
from pathlib import Path


def race_publish(path: str, payload: str, barrier, results) -> None:
    from revisionworx.atomicio import ExclusiveCreateError, publish_exclusive
    barrier.wait()
    try:
        publish_exclusive(Path(path), payload.encode())
        results.put(("won", payload))
    except ExclusiveCreateError:
        results.put(("lost", payload))


def race_open_writable(location: str, barrier, release, results) -> None:
    from revisionworx.workspace import Workspace, WorkspaceLockedError
    barrier.wait()
    try:
        ws = Workspace.open(Path(location), writable=True)
    except WorkspaceLockedError:
        results.put("locked")
        return
    results.put("acquired")
    release.wait(30)  # hold the lock until every competitor has tried
    ws.close()
