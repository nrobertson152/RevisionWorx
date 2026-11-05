# SPDX-License-Identifier: GPL-3.0-or-later
"""Concurrent writer handoff across real processes, with the real command line and real process death.

The interleavings behind the three protocol-only P1s on d2b7a1c, each driven step by step across separate processes.
Writer B is stopped inside a real commit(), past its lock and revocation checks, while another process displaces B's
lock on a stale decision, and that process then dies before each of its remaining filesystem changes in turn.

Oracle, from the journal: authority never interleaves. If a successor committed, B's later commit took no effect and
B's token is revoked. Local filesystem, real processes. No claim about SMB/NFS or power loss.
"""
from __future__ import annotations

import multiprocessing as mp
import uuid
from pathlib import Path

import pytest

import proc_helpers
from proc_harness import Actor, run_cli
from revisionworx import documents as D, projects
from revisionworx.projects import NewProject
from revisionworx.workspace import KINDS, REV_FIELD, Workspace

B_ID, C_ID = "root_" + "b" * 32, "root_" + "c" * 32
MOVE = {"name": "move", "op": ["replace"], "src_name": "writer.lock", "dst_prefix": ".releasing-"}
RESTORE = {"name": "restore", "op": ["link", "rename"], "src_prefix": ".releasing-", "dst_name": "writer.lock"}
MAX_POINTS = 120


def fresh(base: Path) -> Path:
    root = base / "share"
    with Workspace.create(root, "handoff"):
        pass
    return root


def writer_b(ctl: Path, root: Path) -> Actor:
    """B: acquires normally, then stops inside commit(), past every check."""
    actor = Actor(ctl, "B", root, [{"do": "open"}, {"do": "commit", "id": B_ID, "name": "B", "expect_rev": 0},
                                   {"do": "close"}], pauses=[{"name": "preflight", "hook": "after_preflight"}]).start()
    actor.wait_at("preflight")
    return actor


def dead_holder(ctl: Path, root: Path) -> str:
    """A writer whose process is gone, leaving its lock behind. Returns its token."""
    actor = Actor(ctl, "A", root, [{"do": "open"}, {"do": "sleep_forever"}]).start()
    actor.wait_at("forever")
    token = Workspace.read_writer_lock(root)["token"]
    actor.kill()
    return token


def admin_break(root: Path, token: str) -> None:
    done = run_cli("-w", str(root), "lock", "break", "--token", token, "--reason", "handoff test: writer hung")
    assert done.returncode == 0, done.stdout + done.stderr


def successor_commits(ctl: Path, root: Path, b_token: str) -> bool:
    """The next user gets in if anyone can. A lock left by a process that died is broken by the administrator, as
    it would be for real; B's own lock is not, because B is alive and this models nobody having noticed a problem."""
    holder = Workspace.read_writer_lock(root)
    if holder and holder["token"] != b_token:
        admin_break(root, holder["token"])
    with Workspace.open(root) as ws:
        if ws.get("root", C_ID) is not None:      # the mover got as far as its own commit before it died
            return True
    result = Actor(ctl, "C", root, [{"do": "open"}, {"do": "commit", "id": C_ID, "name": "C", "expect_rev": 0},
                                    {"do": "close"}]).run()
    assert not result.unexpected(), result.unexpected()
    if result.step("open")["ok"]:
        assert result.step("commit")["ok"], result.steps
        return True
    assert result.step("open")["error"] == "WorkspaceLockedError", result.steps
    return False


def assert_authority_held(root: Path, b_token: str, b_commit: dict, context: str, allowed=("fenced-entry",)) -> None:
    """The oracle: from the journal alone, whatever the lock files look like."""
    __tracebackhide__ = True
    with Workspace.open(root) as ws:
        seen: list[str] = []
        effective = {}
        for entry in ws.entries_after(0):
            token = entry.get("writer_token")
            if token and (not seen or seen[-1] != token):
                assert token not in seen, f"{context}: two effective writers interleaved at entry {entry['seq']}"
                seen.append(token)
            for item in entry["records"]:
                effective[item["id"]] = entry["seq"]
        b_on, c_on = B_ID in effective, C_ID in effective
        if b_on and c_on:
            assert effective[B_ID] < effective[C_ID], f"{context}: B took effect after its successor"
        assert b_commit["ok"] == b_on or b_commit.get("error") == "CommittedNotApplied", \
            f"{context}: B was told {b_commit} but the journal says effective={b_on}"
        if c_on and not b_commit["ok"]:
            assert b_commit["error"] == "LockLostError", f"{context}: {b_commit}"
            assert b_token in Workspace._revoked_tokens(ws.meta_dir), f"{context}: B lost out without being revoked"
        assert ws.get("root", B_ID) == ({"name": "B", REV_FIELD: effective[B_ID]} if b_on else None), context
        assert ws.get("root", C_ID) == ({"name": "C", REV_FIELD: effective[C_ID]} if c_on else None), context
    # the next writer has to be able to finish the recovery and leave a clean workspace
    holder = Workspace.read_writer_lock(root)
    if holder:
        admin_break(root, holder["token"])
    with Workspace.open(root, writable=True) as ws:
        problems = [p for p in ws.verify()["problems"] if p["problem"] not in allowed]
        assert not problems, f"{context}: {problems}"
    locks = root / ".revisionworx" / "locks"
    assert not list(locks.glob(".releasing-*")) and not list(locks.glob(".fencing-*")), f"{context}: still blocked"


def stale_release(ctl: Path, root: Path, k: int):
    """A's release passed its checks and stops before moving writer.lock. An administrator revokes A (real CLI). B
    acquires and stops past its preflight. A moves B's lock. C's exclusive create takes the name but C has not scanned.
    A can no longer restore B, and dies at step k."""
    a = Actor(ctl, "A", root, [{"do": "open"}, {"do": "close"}], pauses=[MOVE, RESTORE], die_at=k,
              arm_after="restore").start()
    a.wait_at("move")
    a_token = Workspace.read_writer_lock(root)["token"]
    admin_break(root, a_token)
    b = writer_b(ctl, root)
    b_token = Workspace.read_writer_lock(root)["token"]
    a.resume("move")
    a.wait_at("restore")
    c = Actor(ctl, "C", root, [{"do": "open"}, {"do": "commit", "id": C_ID, "name": "C", "expect_rev": 0},
                               {"do": "close"}], pauses=[{"name": "scan", "hook": "before_scan"}]).start()
    c.wait_at("scan")
    a.resume("restore")
    mover = a.finish()
    c.resume("scan")
    c_result = c.finish()
    assert c_result.clean, c_result.steps
    return mover, b, b_token, True


def stale_cleanup(ctl: Path, root: Path, k: int):
    """A is revoked but its lock is stranded, because the administrator's process died right after the journal entry.
    D reads the revoked token and stops before moving the file. B archives A, acquires and stops past its preflight.
    D's move takes B's live lock, and D dies at step k."""
    token = dead_holder(ctl, root)
    admin = Actor(ctl, "admin", root, [{"do": "break_lock", "token": token}], pauses=[MOVE]).start()
    admin.wait_at("move")
    admin.kill()
    assert token in Workspace._revoked_tokens(root / ".revisionworx")
    assert Workspace.read_writer_lock(root)["token"] == token, "the revoked lock should be stranded"
    d = Actor(ctl, "D", root, [{"do": "open"}, {"do": "commit", "id": C_ID, "name": "C", "expect_rev": 0},
                               {"do": "close"}], pauses=[MOVE], die_at=k, arm_after="move").start()
    d.wait_at("move")
    b = writer_b(ctl, root)
    b_token = Workspace.read_writer_lock(root)["token"]
    d.resume("move")
    mover = d.finish()
    return mover, b, b_token, False


def stale_break(ctl: Path, root: Path, k: int):
    """The administrator has revoked dead writer A and stops before archiving its lock. B archives A, acquires and stops
    past its preflight. The administrator's move takes B's live lock, and the administrator's process dies at step k."""
    token = dead_holder(ctl, root)
    admin = Actor(ctl, "admin", root, [{"do": "break_lock", "token": token}], pauses=[MOVE], die_at=k,
                  arm_after="move").start()
    admin.wait_at("move")
    b = writer_b(ctl, root)
    b_token = Workspace.read_writer_lock(root)["token"]
    admin.resume("move")
    mover = admin.finish()
    return mover, b, b_token, False


@pytest.mark.parametrize("scenario", [stale_release, stale_cleanup, stale_break], ids=lambda f: f.__name__)
def test_displacing_a_paused_writer_never_makes_two_effective_writers(tmp_path, scenario):
    k = 0
    while True:
        base = tmp_path / f"k{k}"
        base.mkdir()
        root, ctl = fresh(base), base / "ctl"
        mover, b, b_token, c_done = scenario(ctl, root, k)
        assert not mover.unexpected(), mover.unexpected()
        context = f"{scenario.__name__}, mover " + (f"killed before change {k}, {mover.died_before}" if mover.killed
                                                     else "completed")
        if not c_done and not any(s["do"] == "commit" and s["ok"] for s in mover.steps):
            successor_commits(ctl, root, b_token)
        b.resume("preflight")
        b_result = b.finish()
        assert not b_result.unexpected(), b_result.unexpected()
        assert_authority_held(root, b_token, b_result.step("commit"), context)
        if not mover.killed:
            assert k >= 2, f"{scenario.__name__} only covered {k} crash points"
            return
        k += 1
        assert k < MAX_POINTS


def test_checkin_paused_past_its_checks_loses_cleanly_to_the_next_owner(tmp_path):
    """The whole path through a document: B stored its content and stopped before publishing. The administrator breaks
    B's lock and releases its checkout with the real CLI. C checks out and checks in. B then resumes."""
    root = tmp_path / "share"
    (root / "Engineering").mkdir(parents=True)
    txid = uuid.uuid4().hex
    with Workspace.create(root, "handoff") as ws:
        projects.add_root(ws, "Engineering", root / "Engineering")
        project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
        path = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "bracket.ipt"
        path.write_bytes(b"v1")
        doc, _ = D.register_document(ws, project["id"], path)
        token = D.checkout(ws, doc["id"])["token"]
    path.write_bytes(b"v2 from B")
    ctl = tmp_path / "ctl"
    args = {"do": "checkin", "doc": doc["id"], "token": token, "description": "B", "txid": txid}
    b = Actor(ctl, "B", root, [{"do": "open"}, args, {"do": "close"}],
              pauses=[{"name": "preflight", "hook": "after_preflight"}]).start()
    b.wait_at("preflight")
    admin_break(root, Workspace.read_writer_lock(root)["token"])
    released = run_cli("-w", str(root), "doc", "release-checkout", doc["id"], "--token", token, "--reason", "B hung")
    assert released.returncode == 0, released.stdout + released.stderr
    path.write_bytes(b"v2 from C")
    c = Actor(ctl, "C", root, [{"do": "open"}, {"do": "checkout", "doc": doc["id"], "save": "co"},
                               {"do": "checkin", "doc": doc["id"], "token": "$co", "description": "C"},
                               {"do": "close"}]).run()
    assert c.clean, c.steps
    b.resume("preflight")
    b_result = b.finish()
    assert not b_result.step("checkin")["ok"], "B must not have succeeded"
    assert b_result.step("checkin")["error"] in ("ConflictError", "LockLostError"), b_result.steps
    with Workspace.open(root) as ws:
        chain = D.history(ws, doc["id"])
        assert [r["description"] for r in chain] == ["Initial revision", "C"]
        assert ws.get("checkout", doc["id"]) is None
        assert ws.transaction_outcome(txid)["state"] != "committed"
        assert ws.get("document", doc["id"])["rev_count"] == 2
        kinds = {k: ws.list(k) for k in KINDS}
        assert kinds["revision"] and not kinds["checkout"]
    retried = Actor(ctl, "B2", root, [{"do": "open"}, args, {"do": "close"}]).run()
    assert not retried.step("checkin")["ok"], retried.steps          # B's attempt can't come back to life
    with Workspace.open(root) as ws:
        assert len(D.history(ws, doc["id"])) == 2


def test_readers_never_disturb_a_committing_writer(tmp_path):
    """Issue #10: on Windows a reader that holds a file open used to be able to fail a writer's rename. Readers here use
    every read path as fast as they can, while a writer commits and reopens."""
    root = tmp_path / "share"
    with Workspace.create(root, "readers"):
        pass
    rid = "root_" + "a" * 32
    stop = tmp_path / "stop"
    ctx = mp.get_context("spawn")
    writer_results, reader_results = ctx.Queue(), ctx.Queue()
    readers = [ctx.Process(target=proc_helpers.read_loop, args=(str(root), rid, str(stop), reader_results))
               for _ in range(3)]
    writer = ctx.Process(target=proc_helpers.commit_loop, args=(str(root), rid, 60, writer_results))
    for p in readers:
        p.start()
    writer.start()
    tally = writer_results.get(timeout=240)
    writer.join(60)
    stop.write_text("")
    read_tallies = [reader_results.get(timeout=120) for _ in readers]
    for p in readers:
        p.join(60)
    assert tally["errors"] == [] and tally["not_applied"] == 0 and tally["committed"] == 60, tally
    assert all(t["errors"] == [] and t["reads"] > 0 for t in read_tallies), read_tallies
    with Workspace.open(root) as ws:
        assert ws.verify()["problems"] == []
        assert ws.get("root", rid)[REV_FIELD] == ws.head_seq()


def test_commit_loop_keeps_trying_while_nobody_has_committed(tmp_path):
    """The soak's writers must not stop before anyone has done real work (see commit_loop)..."""
    import queue
    root = tmp_path / "share"
    with Workspace.create(root, "loop"):
        pass
    q = queue.Queue()
    proc_helpers.commit_loop(str(root), "root_" + "b" * 32, 0, q, until_committed_s=60)
    tally = q.get_nowait()
    assert (tally["committed"], tally["attempts"], tally["errors"]) == (1, 1, [])
    proc_helpers.commit_loop(str(root), "root_" + "b" * 32, 0, q, until_committed_s=60)   # someone already has
    tally = q.get_nowait()
    assert (tally["committed"], tally["attempts"]) == (0, 0)
    proc_helpers.commit_loop(str(root), "root_" + "b" * 32, 2, q)          # the old contract: exactly ``rounds``
    tally = q.get_nowait()
    assert (tally["committed"], tally["attempts"]) == (2, 2)


def test_commit_loop_gives_up_at_its_deadline(tmp_path):
    """...but a writer that can never get the lock stops at its deadline instead of running forever."""
    import queue
    import time
    root = tmp_path / "share"
    with Workspace.create(root, "loop"):
        pass
    q = queue.Queue()
    held = Workspace.open(root, writable=True)
    try:
        started = time.monotonic()
        proc_helpers.commit_loop(str(root), "root_" + "c" * 32, 1, q, until_committed_s=0.5)
        elapsed = time.monotonic() - started
    finally:
        held.close()
    tally = q.get_nowait()
    assert tally["committed"] == 0 and tally["locked"] == tally["attempts"] >= 2 and tally["errors"] == []
    assert 0.5 <= elapsed < 30


def test_soak_with_competing_writers_and_an_impatient_administrator(tmp_path):
    """Not deterministic, deliberately: four writers and an administrator breaking every lock it sees, as fast as it can.
    Refusals are expected. A wrong answer, a lost update or two effective writers are not."""
    root = tmp_path / "share"
    with Workspace.create(root, "soak"):
        pass
    rid = "root_" + "a" * 32
    stop = tmp_path / "stop"
    ctx = mp.get_context("spawn")
    results, admin_results = ctx.Queue(), ctx.Queue()
    # after their 100 rounds the writers keep trying (up to 120 s) while nobody has committed: the safety checks
    # below need real commits
    writers = [ctx.Process(target=proc_helpers.commit_loop, args=(str(root), rid, 100, results, 120.0))
               for _ in range(4)]
    admin = ctx.Process(target=proc_helpers.break_loop, args=(str(root), str(stop), admin_results))
    admin.start()
    for p in writers:
        p.start()
    tallies = [results.get(timeout=300) for _ in writers]
    for p in writers:
        p.join(60)
    stop.write_text("")
    admin_tally = admin_results.get(timeout=60)
    admin.join(60)
    errors = [e for t in tallies for e in t["errors"]] + admin_tally["errors"]
    assert errors == [], errors
    holder = Workspace.read_writer_lock(root)
    if holder:
        admin_break(root, holder["token"])
    with Workspace.open(root, writable=True) as ws:
        previous, seen = 0, []
        for entry in ws.entries_after(0):
            token = entry.get("writer_token")
            if token and (not seen or seen[-1] != token):
                assert token not in seen, f"two effective writers interleaved at entry {entry['seq']}"
                seen.append(token)
            for item in entry["records"]:
                if item["id"] == rid:
                    assert item["expect_rev"] == previous, f"lost update at entry {entry['seq']}"
                    previous = entry["seq"]
        assert ws.get("root", rid) == ({"name": "x", REV_FIELD: previous} if previous else None)
        assert [p for p in ws.verify()["problems"] if p["problem"] != "fenced-entry"] == []
    committed = sum(t["committed"] for t in tallies)
    assert committed > 0, tallies
