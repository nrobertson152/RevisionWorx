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


def commit_loop(location: str, rid: str, rounds: int, results, until_committed_s: float = 0.0) -> None:
    """A writer doing ordinary work: open, read, commit on what it read, close. Reports anything unexpected.

    It makes ``rounds`` attempts. Then, until ``until_committed_s`` seconds after it started, it keeps going while no
    writer has committed yet (the record does not exist). Under an administrator who breaks every lock it sees, a slow
    machine can spend every round refused, and a soak without a single commit proves nothing (CI 42083782414 on main
    098a805, Windows: 0 commits in 400 attempts)."""
    import time
    from pathlib import Path
    from revisionworx.workspace import (CommittedNotApplied, ConflictError, LockLostError, RecordPut, Workspace,
                                        WorkspaceError, WorkspaceLockedError)
    tally = {"committed": 0, "locked": 0, "lock_lost": 0, "conflict": 0, "not_applied": 0, "errors": [],
             "attempts": 0, "unreadable": 0}
    start = time.monotonic()

    def nobody_has_committed() -> bool:
        try:
            with Workspace.open(Path(location)) as reader:
                return reader.get("root", rid) is None
        except Exception:  # noqa: BLE001 - cannot tell: keep trying (counted; the deadline still applies)
            tally["unreadable"] += 1
            return True

    while tally["attempts"] < rounds or (time.monotonic() - start < until_committed_s and nobody_has_committed()):
        tally["attempts"] += 1
        try:
            ws = Workspace.open(Path(location), writable=True)
        except WorkspaceLockedError:
            tally["locked"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            tally["errors"].append(f"open: {type(exc).__name__}: {exc}")
            continue
        try:
            current = ws.get("root", rid)
            ws.commit("work", [RecordPut("root", rid, {"name": "x"}, expect_rev=current["_rev"] if current else 0)])
            tally["committed"] += 1
        except LockLostError:
            tally["lock_lost"] += 1
        except ConflictError:
            tally["conflict"] += 1
        except CommittedNotApplied:
            tally["not_applied"] += 1
        except Exception as exc:  # noqa: BLE001
            tally["errors"].append(f"commit: {type(exc).__name__}: {exc}")
        try:
            ws.close()
        except WorkspaceError as exc:
            tally["errors"].append(f"close: {exc}")
    results.put(tally)


def read_loop(location: str, rid: str, stop_file: str, results) -> None:
    """A reader doing ordinary work as fast as it can, until told to stop."""
    from pathlib import Path
    from revisionworx.workspace import Workspace
    reads, errors = 0, []
    while not Path(stop_file).exists():
        try:
            with Workspace.open(Path(location)) as ws:
                ws.get("root", rid)
                ws.list("root")
                ws.applied_seq()
                Workspace.read_writer_lock(Path(location))
            reads += 1
        except Exception as exc:  # noqa: BLE001 - reported, never hidden
            errors.append(f"{type(exc).__name__}: {exc}")
    results.put({"reads": reads, "errors": errors[:5]})


def break_loop(location: str, stop_file: str, results, rid: str | None = None, patience: int = 25,
               hold_s: float = 3.0) -> None:
    """An impatient administrator: breaks whichever lock it sees, repeatedly.

    With ``rid`` it isn't a denial of service. After every ``patience`` breaks it looks at that record. If nobody has
    committed since it last looked, it holds off until someone has, for up to ``hold_s`` seconds. Without this, on a slow
    machine it broke every lock before the commit could land (issue #76: CI 42505527481 on main 996f8eb, Windows: 0
    commits in about 1,900 attempts over 120 s), and a soak without commits proves nothing. While writers are
    committing it never holds off, so it's as impatient as before."""
    import time
    from pathlib import Path
    from revisionworx.workspace import Workspace, WorkspaceError
    breaks, holds, errors = 0, 0, []
    since_look, last_rev = 0, 0

    def record_rev():
        try:
            with Workspace.open(Path(location)) as reader:
                record = reader.get("root", rid)
                return record["_rev"] if record else 0
        except Exception:  # noqa: BLE001 - can't tell right now: hold off, as for no commit (the wait is bounded)
            return None

    while not Path(stop_file).exists():
        try:
            holder = Workspace.read_writer_lock(Path(location))
            if holder:
                Workspace.break_writer_lock(Path(location), holder["token"], "soak test")
                breaks += 1
                since_look += 1
        except WorkspaceError:
            pass                       # the lock changed since it was inspected, as it is told to report
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
        if rid is not None and since_look >= patience:
            since_look = 0
            rev, until = record_rev(), time.monotonic() + hold_s
            if rev is None or rev == last_rev:
                holds += 1
                while ((rev is None or rev == last_rev) and time.monotonic() < until
                       and not Path(stop_file).exists()):
                    time.sleep(0.05)
                    rev = record_rev()
            if rev is not None:
                last_rev = rev
        time.sleep(0.03)
    results.put({"breaks": breaks, "holds": holds, "errors": errors[:5]})


def subsystem_commit_then_die(location: str, kind: str, rid: str, txid: str) -> None:
    """A domain subsystem (BOM, change control) registers its own record kind and commits through the
    public contract, then the process dies after the journal entry is durable and before it is applied
    (CORE_API §8)."""
    import os
    from revisionworx.workspace import RecordPut, Workspace, register_kind
    register_kind(kind)
    ws = Workspace.open(Path(location), writable=True)
    Workspace._apply = lambda self, entry: os._exit(3)     # crash point: committed, not applied
    ws.commit("subsys.create", [RecordPut(kind, rid, {"name": "from subsystem"}, expect_rev=0)], txid=txid)
    os._exit(4)                                           # unreachable if the crash point fired
