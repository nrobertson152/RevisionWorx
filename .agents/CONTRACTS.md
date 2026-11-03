# Interface contracts for parallel work

Use this when two in-flight issues depend on a shared interface: an API, a file format, a schema, or a function signature another issue will call. Disjoint allowed paths prevent edit conflicts, but they don't stop two agents inventing incompatible interfaces.

The coordinator, or an issue assigned to architecture work, records the contract in an ADR under `docs/adr/` or in the owning issue's contract block, before the dependent work is dispatched. It covers:

- Contract id and version; owning issue; baseline commit.
- Signatures, error behaviour, input/output schemas, stable identifiers.
- File-format, persistence and migration implications; compatibility with existing files.
- Invariants and non-goals.
- Contract tests or fixtures that both sides run.

To change a contract, the owner comments on every dependent issue and PR with the new version. The coordinator re-sequences the work. Nobody adapts another agent's branch to fit.

Cross-repository work: reading another repository needs the issue to name it; writing to one needs its own issue in that repository.
