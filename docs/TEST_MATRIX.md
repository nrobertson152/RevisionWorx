# RevisionWorx risk-based QA matrix

**Status:** AUTOMATED = executable test exists, not a claim it has passed every platform; PLANNED = API or test rig absent; BLOCKED = unresolved safety requirement.

| ID | Priority | Oracle | Status |
| --- | --- | --- | --- |
| ADOPT-01 | P0 | Customer/year/product archive, indexed/managed: original paths, hashes and mtimes unchanged | AUTOMATED |
| ADOPT-02 | P0 | Read-only preview; baseline makes no historical revision claim | AUTOMATED |
| START-01 | P0 | Opening workspace does not enumerate unmanaged archives | AUTOMATED |
| START-02 | P0 | 50,000 unmanaged directories remain untraversed | PLANNED (developer suite covers this; independent rerun pending) |
| JOURNAL-01 | P0 | Injected disk-full before publication creates no committed entry | AUTOMATED |
| JOURNAL-02 | P0 | Crash after journal publication remains readable and recoverable | AUTOMATED |
| JOURNAL-03 | P0 | Journal gap denies writable open | AUTOMATED |
| LOCK-01 | P0 | Multiple local writers race: only one winner | AUTOMATED (existing suite) |
| LOCK-02 | P0 | Active writer excludes another writer | AUTOMATED |
| LOCK-03 | P0 | Stale writer after admin lock break must fail fencing check | BLOCKED |
| CHECKOUT-01 | P0 | 2/3/10 simultaneous clients get one exclusive checkout | BLOCKED |
| REV-01 | P0 | Released content immutable, SHA-256 verifies, missing blob detected | PLANNED |
| REV-02 | P0 | Interrupted check-in never creates false committed revision | PLANNED |
| EDIT-01 | P0 | External file modification detected | PLANNED |
| BACKUP-01 | P0 | Restored metadata and blobs from same consistent point | PLANNED |
| DUP-01 | P0 | Failed project duplication safely rolled back | PLANNED |
| PART-01 | P0 | Number collisions and reserved ranges rejected | PLANNED |
| CAD-01 | P0 | Native assembly references are preserved | PLANNED |
| OSCH-01 | P1 | OpenSchematix content recognized, false extension rejected | AUTOMATED (existing suite) |
| BOM-01 | P0 | BOM quantity/revision/provenance correct | PLANNED |
| GRAPH-01 | P0 | Direct/indirect/cyclic/cross-project/revision Where-Used | PLANNED |
| IMPACT-01 | P0 | Before You Revise shows coverage and does not imply incompatibility | PLANNED |
| INDEX-01 | P1 | Local cache corruption/staleness rebuilt | AUTOMATED (existing suite) |
| SCALE-01 | P1 | 10k projects, 2m docs, 500k edges and actual query p95/RSS | PLANNED |
| WINDOWS-01 | P0 | NTFS exclusive publish, Windows case/path rules | PLANNED |
| SMB-NFS-01 | P0 | Two-host shared filesystem concurrency and disconnection | BLOCKED |
| GUI-01 | P1 | Live Tk GUI under Linux/Windows display | PLANNED |

No absent feature is considered a passing test.
