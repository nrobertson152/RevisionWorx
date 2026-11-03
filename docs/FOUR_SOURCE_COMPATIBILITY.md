# Four-source offline product integration: test-only compatibility matrix

**Owner:** ChatGPT Integration A, [Issue #32](https://github.com/nrobertson152/RevisionWorx/issues/32), [draft PR #2](https://github.com/nrobertson152/RevisionWorx/pull/2).

**Scope:** Pinned Phase 2 core + desktop GUI + advanced BOM/parts + ECR/ECO. All merges are local to disposable GitHub Actions checkouts. **No source branch is modified; this is not a production or release candidate.**

## Exact upstream revisions

| Source | Draft PR | Immutable commit | Role |
|---|---|---|---|
| Phase 2 core | #7 | `f01b37d345c5208ab0fa520588a2abe8c66c894a` | Authoritative journal, revisions, checkout, document APIs; final commit is documentation-only relative to `96f9c36` |
| PySide6 desktop | #14 | `0773bde599f39aee88a8a536c28673bc4a1e13f5` | GUI, controller, native-document workflow |
| Parts/BOM | #21 | `59c0f7b56c7e135c6d4179e18bea51b3232d8c9b` | Parts, assemblies, BOM graphs, read-only WhereUsed adapter and PROPOSED workspace record kinds |
| ECR/ECO | #20 | `cb684336fbef2edf21bb4bda700146af70d58ee4` | Change-control lifecycle, audit, provisional workspace persistence |
| Integration harness | #2 | `integration/openschematix` | Only test staging and packaging work, never product authority |

## Test-only composition and conflict boundaries

`tools/packaging/stage_product_stack.py` checks exact 40-character Git commit identities against independent *clean* checkout heads. For each candidate it transfers objects exclusively from another local checkout and performs **real, non-fast-forward Git merges**, in this exact order: core + GUI, then BOM, then change control. The tool verifies each synthetic two-parent commit, writes a receipt with SHA and merge base, and fails on any conflict without resolving it. It verifies that BOM, change-control and GUI merging did not change any of six Phase 2 authority source blobs: `atomicio.py`, `authority.py`, `content.py`, `documents.py`, `index.py` or `workspace.py`. Core, GUI, BOM and change-control source refs remain untouched; no refs are pushed.

`tools/packaging/test_stage_product_stack.py` has **11 adversarial local-Git regression tests**, including wrong SHAs, dirty sources, source alias/nesting, receipts inside inputs, synthetic conflict refusal and detection of an unauthorized core authority change. No GitHub network is necessary for these fixtures.

The initial synthetic four-source sequence on native Ubuntu CI produced **test-only SHA `6c9950baa65fa802c614d25289a7748ec7eec066`**:
- Core + GUI: `afdc05d3abb0584884dce291e4559debf96bee68`.
- + BOM: `5c2a90b3bc3e97749d850e8b7b66c64d842801b7`.
- + Change Control: `6c9950baa65fa802c614d25289a7748ec7eec066`.

It modifies **114 paths relative to the core**, with all six core authority Git blob IDs unchanged. This is a *synthetic* commit chain in CI; it does not exist on any shared branch and **must not be used as a cherry-pick or release**.

## CI and integration evidence

[Four-source CI 41583331179](https://github.com/nrobertson152/RevisionWorx/actions/runs/41583331179) is the first native Windows/Linux compatibility matrix at harness `6e7afa20`:
- Ubuntu Python 3.11: actual three Git merges **PASS**; 11 staged-source regression tests **PASS**; native full `python -m pytest -q -ra`: **1,141 PASS / 2 SKIP / 0 FAIL**. Focused BOM, ECR/ECO and Qt interface tests passed before the full suite. Normal BOM/change-control/core CLI `--help` modules passed.
- Windows Python 3.11: **record completed result before claiming**. Passing on Linux does not imply Windows acceptance.

**Follow-on Integration-only oracles authored:** `tools/packaging/test_product_stack_contract.py` combines a registered native CAD document, BOM parts and WhereUsed relation with an ECR impact query on a disposable core workspace. It verifies declared-vs-verified identity, coverage disclosure, journal integrity, stable CAD original bytes/mtime and read-only restart. `tools/packaging/check_product_wheel.py` audits a test-only wheel for all three product packages, core and both CLI entrypoints, declared GPL license, no required third-party core dependencies, no unexpected native objects, and SHA-256 manifest. **Neither follow-on test is accepted until its exact CI run is recorded.**

## Unapproved interfaces and safety blocks

BOM `WorkspaceCatalogStore` uses kinds `part`, `partnum`, `bomline`, `bomstruct`, `partrule`, `partseq`; the core architect has not approved this interface. Change-control `WorkspaceStore` uses `changereq`, `changeord`, `changeevt`, `changenum`, `changecfg`, and similarly requests a reviewed domain-persistence contract. The BOM `ChangeControlWhereUsed` adapter is read-only and reports declared coverage for parts not verified against core document ids. No GUI production widget for these two modules has been accepted.

Independent QA/Distributed Systems have already **rejected/halted** the exact `96f9c36` core runtime for malformed journal/false-positive `health()` and same-size/mtime CAD source replacement or in-place overwrite provenance. Broad green functional tests do not override strict negative safety evidence. The previous Phase 1 PR #6 remains blocked by a `docs/DEVELOPMENT_PROGRESS.md` modify/delete conflict. No acceptance for real two-host SMB/NFS, physical power loss, manually installed Windows/Linux GUI, signed installer, hardware fault injection or clean offline Windows VM.

**Director-controlled merge sequencing:** accept a *new corrected exact core SHA* with positive QA and Distributed tests, merge PR #7 into Phase1 before PR #6 into main, resolve PR #6 conflict, merge accepted GUI #14, and only then consider separately approved BOM #21 and ECR/ECO #20 adapters and Integration #2. Every merged tree needs its own native Windows/Linux regression and release gate. No step here authorizes merging.

## Next gates

1. Complete first Windows four-source source matrix and record exact test counts, skips and failures.
2. Run the cross-domain native CAD/BOM/ECR integration test and test-only wheel packaging audit on both OSes.
3. Re-pin corrected core/feature SHAs after owner acceptance, rerun exact results and report all divergences.
4. Only after independent safety acceptance consider a separate frozen PyInstaller module-inclusion test; current source/wheel tests do not prove the application bundles BOM or ECR/ECO.

**Permitted use:** exploratory native local-workspace integration testing. **Not permitted:** production use, installation/release claims, multiuser certification, unsupported auto migration, or manufacturing document safety claims.
