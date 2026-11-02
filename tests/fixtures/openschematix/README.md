# OpenSchematix integration test fixtures

These two test inputs are copied **byte-for-byte**, using existing Git blob IDs, from the
Lead Architect's `phase1-foundation` OpenSchematix fixtures. They originate in
`nrobertson152/OpenSchematix/examples/` at upstream commit `74e4c79`
(OpenSchematix 1.1.0). OpenSchematix is GPL-3.0; this repository is GPL-3.0.

| Fixture | Git blob SHA | Expected adapter observation | Native BOM baseline reported by Lead Architect |
| --- | --- | --- | --- |
| `demo_project.schematix` | `655833c981622e6417c716975d5af09ab92d3244` | 3 sheets, 26 components, 5 candidates | 16 native BOM lines |
| `hydraulic_cylinder_circuit.osch` | `ae01e0ce70ae89292f2a1a3f05b3904a6959c62c` | 1 sheet, 9 components, 0 candidates | 7 native BOM lines |\n| `start_stop_latch.osch` | `647724fad7fe8a1f2222cd0b2235593bed652e01` | 1 sheet, 7 components, 0 candidates | 5 native BOM lines |

Native BOM baselines are **not** adapter outputs. `bom_candidates` is incomplete
manufacturer/part-number extraction and never asserts purchasable quantities.
Refer to `docs/OPENSchematix_INTEGRATION.md` for native `bom.generate` semantics.

These fixtures must remain unchanged. Tests run the read-only adapter on these
fixtures without rewriting or modifying their contents.

The hydraulic fixture was copied directly from the OpenSchematix 1.1.0 upstream Git blob; the other two reuse the Phase 1 branch's byte-identical Git blobs.
