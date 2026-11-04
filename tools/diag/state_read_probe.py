"""Diagnostic only. Count refused reads of a file, with the real core calls: one read after each replace,
then a reader thread running while another thread replaces the file (what the GUI did mid-job)."""
import json, sys, tempfile, threading, time
from pathlib import Path
from revisionworx import atomicio

N = int(sys.argv[1])

def note(errors, e):
    k = f"winerror={getattr(e, 'winerror', None)} errno={e.errno}"
    errors[k] = errors.get(k, 0) + 1

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "state.json"
    denied, errors = 0, {}
    for i in range(N):
        atomicio.atomic_write_json(p, {"applied_seq": i})
        try:
            atomicio.read_json(p)
        except PermissionError as e:
            denied += 1
            note(errors, e)
    print(json.dumps({"mode": "sequential", "iterations": N, "denied_reads": denied, "errors": errors}))

    stop = threading.Event()
    reads, denied, errors, worst = [0], [0], {}, [0.0]
    def reader():
        while not stop.is_set():
            t = time.perf_counter()
            try:
                atomicio.read_json(p)
                reads[0] += 1
            except PermissionError as e:
                denied[0] += 1
                note(errors, e)
            worst[0] = max(worst[0], time.perf_counter() - t)
    th = threading.Thread(target=reader)
    th.start()
    wfail, werrors = 0, {}
    for i in range(N // 4):
        try:
            atomicio.atomic_write_json(p, {"applied_seq": i})
        except PermissionError as e:
            wfail += 1
            note(werrors, e)
    stop.set()
    th.join()
    print(json.dumps({"mode": "concurrent", "writes": N // 4, "reads_ok": reads[0], "denied_reads": denied[0],
                      "read_errors": errors, "failed_writes": wfail, "write_errors": werrors,
                      "slowest_read_s": round(worst[0], 3)}))
