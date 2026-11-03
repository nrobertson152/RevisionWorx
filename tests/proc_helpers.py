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


def race_checkout(location: str, doc_id: str, barrier, results) -> None:
    """Each process waits for the writer lock (single-writer model), then tries to check out."""
    import time
    from revisionworx import documents
    from revisionworx.workspace import Workspace, WorkspaceLockedError
    barrier.wait()
    deadline = time.time() + 60
    while True:
        try:
            ws = Workspace.open(Path(location), writable=True)
            break
        except WorkspaceLockedError:
            if time.time() > deadline:
                results.put("timeout")
                return
            time.sleep(0.01)
    try:
        documents.checkout(ws, doc_id)
        results.put("won")
    except documents.CheckoutConflict:
        results.put("conflict")
    except Exception as exc:  # report, never hide
        results.put(f"error: {type(exc).__name__}: {exc}")
    finally:
        ws.close()


def clear_orphan(share: str, name: str, reason: str) -> None:
    """Spawned administrator racing others on one orphan (ADR-019 rev. 3, DS P2)."""
    from pathlib import Path
    from revisionworx.workspace import OrphanClearConflict, Workspace
    try:
        Workspace.clear_orphaned_release(Path(share), name, reason=reason)
    except OrphanClearConflict:
        raise SystemExit(3)  # distinct exit code: a competing administrator's clear stands


def make_checkout_fixture(share) -> str:
    """A workspace with one registered document; returns its id. Used by process-race tests."""
    from pathlib import Path
    from revisionworx import documents, projects
    from revisionworx.projects import NewProject
    from revisionworx.workspace import Workspace
    share = Path(share)
    share.mkdir(parents=True)
    with Workspace.create(share, "race") as ws:
        projects.add_root(ws, "share", share)
        project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
        target = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "bracket.ipt"
        target.write_bytes(b"IPT v1")
        doc, _ = documents.register_document(ws, project["id"], target)
    return doc["id"]


def put_tokens(state_dir: str, who: int, barrier) -> None:
    """Racing writers of the per-machine token store."""
    from pathlib import Path
    from revisionworx.clientstate import CheckoutTokens
    tokens = CheckoutTokens(Path(state_dir))
    barrier.wait()
    for j in range(20):
        tokens.put("ws", f"doc_{who}_{j}", f"token-{who}-{j}")
