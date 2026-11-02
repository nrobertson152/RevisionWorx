# Synthetic performance dataset

`generate_metadata.py` creates a standalone SQLite corpus, not a RevisionWorx database and **not an application benchmark**. Defaults: 10,000 projects; 2,000,000 documents; 500,000 dependency edges. It validates cardinalities and refuses to overwrite an existing output path.

```sh
python benchmarks/generate_metadata.py --out /tmp/rw-bench.sqlite
python benchmarks/generate_metadata.py --out /tmp/rw-smoke.sqlite --projects 100 --documents 200 --edges 50
```

Future application benchmarks need real APIs and must separately measure cold startup, exact lookup, FTS search, where-used, impact, index sync, UI latency and RSS, recording repeated p50/p95/p99 and exact host, filesystem, cache state and commit. The requirements' <50 ms exact lookup, <100 ms search and <200 ms where-used values are aspirational, not observed.
