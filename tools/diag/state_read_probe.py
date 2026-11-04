"""Diagnostic only. Count refused reads of a file straight after core replaces it, with the real core calls."""
import json, sys, tempfile, time
from pathlib import Path
from revisionworx import atomicio

N = int(sys.argv[1])
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "state.json"
    denied, errors, worst = 0, {}, 0.0
    for i in range(N):
        atomicio.atomic_write_json(p, {"applied_seq": i})
        t = time.perf_counter()
        try:
            assert atomicio.read_json(p)["applied_seq"] == i
        except PermissionError as e:
            denied += 1
            k = f"winerror={getattr(e, 'winerror', None)} errno={e.errno}"
            errors[k] = errors.get(k, 0) + 1
        worst = max(worst, time.perf_counter() - t)
    print(json.dumps({"iterations": N, "denied": denied, "errors": errors, "slowest_read_s": round(worst, 4)}))
