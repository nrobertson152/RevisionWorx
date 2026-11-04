# SPDX-License-Identifier: GPL-3.0-or-later
"""Executable, independent Windows six-process checkout stress harness.

Run in the disposable Phase 2 checkout:
  python tests/qa/windows_checkout_stress.py --trials 5 --workers 6

Prints every worker's outcome, exit code, journal consistency and a per-trial
summary. A successful job requires EXACTLY one checkout winner, every other
process reporting 'conflict', no worker crash, no orphan lock, and clean
authoritative verification. All trials run; any failure makes the job nonzero.

This exercises local Windows NTFS contention, NOT two-host SMB/NFS semantics.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
from pathlib import Path
import platform
import queue
import sys
import tempfile
import time

# Spawned helpers live in the application test suite; no production file edits.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proc_helpers  # noqa: E402

from revisionworx import documents, projects  # noqa: E402
from revisionworx.projects import NewProject  # noqa: E402
from revisionworx.workspace import Workspace  # noqa: E402


def one_trial(ctx: mp.context.BaseContext, number: int, workers: int,
              timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="revisionworx_qa_checkout_") as temp:
        share = Path(temp) / "share"
        (share / "Engineering").mkdir(parents=True)
        with Workspace.create(share, "QA Windows checkout stress") as ws:
            projects.add_root(ws, "Engineering", share / "Engineering")
            project = projects.create_project(
                ws, NewProject(name="Skid", number=f"QA-{number}"))
            cad = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "skid.ipt"
            cad.write_bytes(b"Deterministic six-process drawing fixture")
            doc, initial = documents.register_document(ws, project["id"], cad)
            doc_id = doc["id"]
            baseline_digest = initial["sha256"]

        barrier = ctx.Barrier(workers)
        outcomes = ctx.Queue()
        procs = [
            ctx.Process(
                target=proc_helpers.race_checkout,
                args=(str(share), doc_id, barrier, outcomes),
                name=f"qa-checkout-{number}-{index}",
            )
            for index in range(workers)
        ]
        start = time.monotonic()
        for proc in procs:
            proc.start()
        observed: list[str] = []
        deadline = start + timeout
        try:
            while len(observed) < workers and time.monotonic() < deadline:
                try:
                    observed.append(outcomes.get(timeout=min(2, max(0.01, deadline - time.monotonic()))))
                except queue.Empty:
                    if all(not proc.is_alive() for proc in procs):
                        break
            for proc in procs:
                proc.join(timeout=3)
        finally:
            for proc in procs:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=5)
            outcomes.close()

        exits = [proc.exitcode for proc in procs]
        lock_left = Workspace.read_writer_lock(share)
        with Workspace.open(share) as reader:
            checkouts = reader.get("checkout", doc_id)
            revisions = reader.list("revision")
            problems = reader.verify()["problems"]
            head = reader.head_seq()

        ok = (
            observed.count("won") == 1
            and observed.count("conflict") == workers - 1
            and len(observed) == workers
            and all(code == 0 for code in exits)
            and lock_left is None
            and checkouts is not None
            and len(revisions) == 1
            and next(iter(revisions.values()))["sha256"] == baseline_digest
            and not problems
        )
        return {
            "trial": number,
            "ok": ok,
            "workers": workers,
            "outcomes": observed,
            "exitcodes": exits,
            "lock_left": lock_left is not None,
            "checkout_registered": checkouts is not None,
            "journal_head": head,
            "verify_problems": problems,
            "seconds": round(time.monotonic() - start, 2),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20 or not 2 <= args.workers <= 10 or args.timeout < 65:
        parser.error("trials 1..20, workers 2..10, timeout at least 65 seconds")
    print(f"platform={platform.platform()} python={sys.version.split()[0]} "
          f"filesystem=local temp={tempfile.gettempdir()} pid={os.getpid()}", flush=True)
    ctx = mp.get_context("spawn")
    results = []
    for number in range(1, args.trials + 1):
        try:
            result = one_trial(ctx, number, args.workers, args.timeout)
        except BaseException as exc:
            result = {"trial": number, "ok": False,
                      "exception": f"{type(exc).__name__}: {exc}"}
        results.append(result)
        print(f"TRIAL {number}/{args.trials}: {result}", flush=True)
    failures = sum(not result["ok"] for result in results)
    print(f"SUMMARY trials={len(results)} successes={len(results)-failures} "
          f"failures={failures} worker_attempts={args.trials * args.workers}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
