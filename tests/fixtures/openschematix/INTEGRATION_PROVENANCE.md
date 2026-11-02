# OpenSchematix integration fixture provenance and expectations

The base `README.md` is retained byte-for-byte from Claude's Phase 1
fixture provenance to avoid competing edits to the same file during PR
stacking. The files below originate from
`nrobertson152/OpenSchematix/examples/` at upstream commit
`74e4c79053721997ae899eb9817ddbcb76eec5a0` (OpenSchematix
1.1.0), under GPL-3.0-or-later. The existing two fixtures are shared
byte-identical Git blobs with Claude's Phase 1 branch.

| Fixture | Git blob SHA-1 | Adapter parse baseline | Native `bom.generate` baseline |
| --- | --- | --- | --- |
| `demo_project.schematix` | `655833c981622e6417c716975d5af09ab92d3244` | 3 sheets, 26 instances, 5 unresolved manufacturer-part candidates | 16 lines |
| `start_stop_latch.osch` | `647724fad7fe8a1f2222cd0b2235593bed652e01` | 1 sheet, 7 instances, 0 candidates | 5 lines |
| `hydraulic_cylinder_circuit.osch` | `ae01e0ce70ae89292f2a1a3f05b3904a6959c62c` | 1 sheet, 9 instances, 0 candidates | 7 lines |

These are Git *blob SHA-1* values, not RevisionWorx revision SHA-256
digests. Native BOM counts are verified against the pinned upstream
generator in `tests/test_openschematix_native_oracle.py`, while
read-only adapter counts are checked in
`tests/test_openschematix_adapter.py`. Neither tool modifies the
fixtures. Candidate count is not native BOM line count, nor a verified
part identity or purchasable quantity.

The historical legacy wrapper sheet UID is not stable. The core
document/revision ID must scope any indexing observation, especially
after project copies or revision changes.
