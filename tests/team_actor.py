# SPDX-License-Identifier: GPL-3.0-or-later
"""One computer of a team, as a real process, for tests that need several at once or need one to die.

    python team_actor.py <spec.json>

Failpoints from proc_actor: it can die with os._exit before its Nth filesystem change, with no cleanup, exactly as a crash does.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def session_for(spec):
    from revisionworx.shared import Session
    return Session(spec["workspace_id"], spec["account"], spec["account"], spec["public"], bytes.fromhex(spec["seed"]))


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    from revisionworx import accounts, documents, shared
    from revisionworx.workspace import ConflictError, RecordPut, Workspace, WorkspaceLockedError
    accounts.KDF = {"name": "scrypt", "n": 2 ** 4, "r": 8, "p": 1}
    shared.LEASE_SECONDS = spec.get("lease", shared.LEASE_SECONDS)
    shared.BEAT_SECONDS = spec.get("beat", shared.BEAT_SECONDS)
    folder, state = Path(spec["folder"]), Path(spec["state"])
    session = session_for(spec)
    results: dict = {"account": spec["account"], "won": [], "conflicts": 0, "locked": 0}
    open_ws = lambda: Workspace.open(folder, writable=True, session=session, state_dir=state, wait=spec.get("wait", 60.0))

    if spec.get("die_at") is not None:
        from proc_actor import Failpoints
        fp = Failpoints({"ctl": spec["ctl"], "name": spec["account"], "die_at": spec["die_at"], "arm_after": "start", "pauses": []})
        fp.armed = True
        fp.install()

    gate = spec.get("gate")
    if gate:
        Path(gate + "." + spec["account"]).write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 120
        while not Path(gate).exists():
            if time.monotonic() > deadline:
                return 99
            time.sleep(0.002)

    if spec["do"] == "count":                        # add one to a record many times, with no update lost
        for _ in range(spec["times"]):
            while True:
                try:
                    with open_ws() as ws:
                        rec = ws.get("project", spec["record"]) or {"n": 0, "_rev": 0}
                        ws.commit("count", [RecordPut("project", spec["record"], {"n": rec["n"] + 1, "by": spec["account"]},
                                                      expect_rev=rec["_rev"])])
                    results["won"].append(rec["n"] + 1)
                    break
                except ConflictError:
                    results["conflicts"] += 1
                except WorkspaceLockedError:
                    results["locked"] += 1
    elif spec["do"] == "checkout":                   # everyone wants the same documents
        for doc in spec["documents"]:
            while True:                              # a workspace busy for longer than `wait` is tried again, as a person would
                try:
                    with open_ws() as ws:
                        documents.checkout(ws, doc)
                    results["won"].append(doc)
                except documents.CheckoutConflict:
                    results["conflicts"] += 1
                except WorkspaceLockedError:
                    results["locked"] += 1
                    continue
                break
    elif spec["do"] == "commit":                     # one change, for a test that kills this process somewhere inside it
        with open_ws() as ws:
            ws.commit("t", [RecordPut("project", spec["record"], {"by": spec["account"]})], txid=spec.get("txid"))
        results["won"].append(spec["record"])
    elif spec["do"] == "checkout-one":
        with open_ws() as ws:
            documents.checkout(ws, spec["document"])
        results["won"].append(spec["document"])
    elif spec["do"] == "hold":                       # take a turn and never finish it, until killed
        ws = open_ws()
        Path(spec["ctl"], spec["account"] + ".holding").write_text("yes", encoding="utf-8")
        time.sleep(600)
        ws.close()
    Path(spec["out"]).write_text(json.dumps(results), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
