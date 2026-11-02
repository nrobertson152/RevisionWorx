# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible journal performance measurement.

    python tools/bench_journal.py [N ...]      (default sizes: 200 2000 10000)

For each journal size N it reports (milliseconds):
  open        writable open (lock + journal check + recovery)
  head        Workspace.head_seq()
  outcome     transaction_outcome() for a txid that does NOT exist (worst case)
  commit_tx   one commit carrying a txid (idempotency lookup + revocation check + publish)
  100_commits 100 consecutive commits on one handle (exposes per-commit O(n) work)
"""
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from revisionworx.workspace import RecordPut, Workspace  # noqa: E402


def ms(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def build(n: int) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"rwx-bench-{n}-"))
    ws = Workspace.create(d, "bench")
    for i in range(n - ws.head_seq()):
        ws.commit("bench", [RecordPut("project", f"prj_{i:032x}", {"i": i})], txid=uuid.uuid4().hex)
    ws.close()
    return d


def run(n: int) -> dict:
    d = build(n)
    r = {"N": n}
    holder = {}
    r["open"] = ms(lambda: holder.setdefault("ws", Workspace.open(d, writable=True)))
    ws = holder["ws"]
    r["head"] = ms(ws.head_seq)
    r["outcome"] = ms(lambda: ws.transaction_outcome("f" * 32))
    r["commit_tx"] = ms(lambda: ws.commit("x", [RecordPut("project", "prj_" + "e" * 32, {"v": 1})],
                                          txid=uuid.uuid4().hex))
    r["100_commits"] = ms(lambda: [ws.commit("y", [RecordPut("project", f"prj_{k:032x}", {"k": k})],
                                             txid=uuid.uuid4().hex) for k in range(100)])
    ws.close()
    return r


if __name__ == "__main__":
    sizes = [int(a) for a in sys.argv[1:]] or [200, 2000, 10000]
    cols = ["N", "open", "head", "outcome", "commit_tx", "100_commits"]
    print(" ".join(f"{c:>12}" for c in cols))
    for n in sizes:
        r = run(n)
        print(" ".join(f"{r[c]:>12.1f}" if c != "N" else f"{r[c]:>12}" for c in cols))
