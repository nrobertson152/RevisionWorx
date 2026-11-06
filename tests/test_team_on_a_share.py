# SPDX-License-Identifier: GPL-3.0-or-later
"""Team use on a real network share, when there is one (ADR-024). Skipped otherwise, and a skip says nothing about anything.

REVISIONWORX_TEST_SHARES lists paths to one empty folder on a share, separated by the platform's path separator. Each path should be a
different client's view of it, with its own caches: two mounts with separate superblocks on Linux, and on Windows the same share by
different server names, which the redirector treats as different servers.

.github/workflows/shares.yml does this on one machine, against real SMB and NFS servers. These processes and clients are real and their caches
are separate. It's still one machine and one network path, so it isn't the evidence that `revisionworx share test` between real computers
would be: cable pulls, a slow network and server reboots aren't covered at all.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from revisionworx import documents as D, projects, sharecheck
from revisionworx.projects import NewProject
from revisionworx.workspace import RecordPut, Workspace
from team_helpers import Crew, process_env

SHARES = [p for p in os.environ.get("REVISIONWORX_TEST_SHARES", "").split(os.pathsep) if p]
pytestmark = pytest.mark.skipif(not SHARES, reason="REVISIONWORX_TEST_SHARES isn't set: there's no real share to test on")
A = "prj_" + "a" * 32
TIMEOUT = 600


@pytest.fixture
def clients():
    """One new folder on the share, as each client sees it."""
    name = f"rwx-{secrets.token_hex(6)}"
    paths = [Path(share) / name for share in SHARES]
    paths[0].mkdir()
    yield paths
    shutil.rmtree(paths[0], ignore_errors=True)


@pytest.fixture
def crew(tmp_path, clients):
    return Crew(tmp_path, users=("u1", "u2", "u3"), clients=clients)


def note(title, value):
    """Goes in the CI log, because what it measured is the point."""
    print(f"\n[{title}] {json.dumps(value, default=str)}", flush=True)


def test_every_client_finds_the_folder_fit_for_team_use(clients):
    for path in clients:
        deadline = time.monotonic() + 30
        while not path.exists():                                   # this client may not have heard about the new folder yet
            assert time.monotonic() < deadline, f"{path} never appeared"
            time.sleep(0.2)
        report = sharecheck.check_folder(path)
        note(f"check {path}", {"ok": report["ok"], "network": report["network"], "failed": [c for c in report["checks"] if not c["ok"]]})
        assert report["ok"], report


def test_clients_racing_for_names_each_get_a_different_one(clients, tmp_path):
    """The same race as `share test`. It measures how stale a listing and a "not found" get between these clients."""
    env = process_env()
    procs = [subprocess.Popen([sys.executable, "-m", "revisionworx", "--json", "share", "test", str(path), "--seconds", "20"],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for path in clients for _ in range(2)]
    for proc in procs:
        _out, err = proc.communicate(timeout=TIMEOUT)
        assert proc.returncode == 0, err
    report = sharecheck.read_report(clients[0])
    note("race", {k: v for k, v in report.items() if k != "problems"})
    assert report["runners"] == len(procs) and report["problems"] == [], report
    assert report["slots_won"] > 0 and report["races_lost"] > 0
    assert not report["ok"] and not report["cross_computer"]       # one machine: it says so, and doesn't call this evidence


def test_a_change_on_one_client_is_seen_on_another_and_how_soon(crew):
    seen = []
    for _ in range(5):
        with crew.open("u1", writable=True, wait=60.0) as ws:
            seq = ws.commit("t", [RecordPut("project", A, {"at": time.time()})])
        began = time.monotonic()
        while True:
            with crew.open("u2") as ws:
                if ws.head_seq() >= seq:
                    break
            assert time.monotonic() - began < 60, "a change never reached another client"
            time.sleep(0.05)
        seen.append(round(time.monotonic() - began, 3))
    note("seconds for another client to see a change", seen)


def test_no_update_is_lost_when_every_client_writes_at_once(crew, tmp_path):
    gate = str(tmp_path / "gate")
    procs = [crew.spawn(name, "count", times=10, record=A, gate=gate) for name in crew.sessions]
    crew.go(gate)
    results = [crew.finish(p, timeout=TIMEOUT) for p in procs]
    note("counting", {r["account"]: {"conflicts": r["conflicts"], "had_to_wait": r["locked"]} for r in results})
    won = sorted(n for r in results for n in r["won"])
    assert won == list(range(1, 10 * len(crew.sessions) + 1))
    assert crew.all_agree(patience=30.0)[2][A]["n"] == 10 * len(crew.sessions)


def test_a_checkout_has_exactly_one_winner_across_clients(crew, tmp_path):
    with crew.open("boss", writable=True, wait=60.0) as ws:
        root = projects.add_root(ws, "Projects", crew.folder)
        project = projects.create_project(ws, NewProject(name="HPU", root_id=root))
        folder = projects.project_folder(ws, project)
        docs = []
        for n in range(6):
            path = folder / f"part{n}.ipt"
            path.write_bytes(f"part {n}".encode())
            docs.append(D.register_document(ws, project["id"], path)[0]["id"])
    gate = str(tmp_path / "gate")
    procs = [crew.spawn(name, "checkout", documents=docs, gate=gate) for name in crew.sessions]
    crew.go(gate)
    results = [crew.finish(p, timeout=TIMEOUT) for p in procs]
    assert sorted(d for r in results for d in r["won"]) == sorted(docs)
    assert sum(r["conflicts"] for r in results) == len(docs) * (len(crew.sessions) - 1)
    checkouts = crew.all_agree(patience=30.0)[3]
    owners = {r["account"]: set(r["won"]) for r in results}
    for doc, co in checkouts.items():
        assert doc in owners[co["owner"]["account"]]


def test_a_client_that_dies_during_its_turn_is_cleared(crew, tmp_path):
    holder = crew.spawn("u1", "hold")
    deadline = time.monotonic() + 120
    while not (tmp_path / "u1.holding").exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    holder.kill()
    holder.communicate()
    waiter = crew.spawn("u2", "commit", record=A, lease=2.0, wait=120.0)
    assert crew.finish(waiter, timeout=TIMEOUT)["won"] == [A]
    assert crew.all_agree(patience=30.0)[2][A]["by"] == "u2"


def test_a_document_checked_out_through_one_client_is_checked_in_through_another(crew):
    """The working file is in the shared folder, and each client reaches it by its own path."""
    with crew.open("boss", writable=True, wait=60.0) as ws:
        root = projects.add_root(ws, "Projects", crew.folder)
        project = projects.create_project(ws, NewProject(name="Pump", root_id=root))
        path = projects.project_folder(ws, project) / "pump.idw"
        path.write_bytes(b"v1")
        doc = D.register_document(ws, project["id"], path)[0]
    with crew.open("u1", writable=True, wait=60.0) as ws:
        co = D.checkout(ws, doc["id"])
        here = D.working_path(ws, ws.get("document", doc["id"]))
        here.write_bytes(b"v2")
    other = crew.paths["u1"]
    assert str(here).startswith(str(other))
    with Workspace.open(crew.paths["u2"], writable=True, session=crew.sessions["u1"], state_dir=crew.tmp_path / "computers" / "u1-other-desk", wait=60.0) as ws:
        deadline = time.monotonic() + 30
        while ws.get("checkout", doc["id"]) is None and time.monotonic() < deadline:
            time.sleep(0.2)
            ws.sync(fresh=True)
        there = D.working_path(ws, ws.get("document", doc["id"]))
        assert str(there).startswith(str(crew.paths["u2"]))
        deadline = time.monotonic() + 30
        while there.read_bytes() != b"v2" and time.monotonic() < deadline:     # a client can serve a file's old data for a moment
            time.sleep(0.2)
        rev = D.checkin(ws, doc["id"], co["token"], "from the other client")
    assert rev["number"] == 2
    crew.all_agree(patience=30.0)
