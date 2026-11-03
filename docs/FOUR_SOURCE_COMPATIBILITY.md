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
- Windows Python 3.11: **SUCCESS**; eleven staging-safety tests pass, focused BOM/ECR/GUI tests **24 PASS**, full `python -m pytest -q -ra`: **1,139 PASS / 4 SKIP / 0 FAIL**. Same test-only merge SHA `6c9950b` and exact parent source SHAs; no real merge or release was performed.

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

## Source movement after frozen candidate (not covered by this evidence)

At the later GitHub check both feature branches advanced **by two commits each**. The source comparisons confirm these are **not merely documentation changes**, and their work is **not included in the tested synthetic SHA `6c9950b`**:

- BOM PR #21: `59c0f7b` → `7775f4f` → **`8f67bfedcca2ea619035556fdeed52bf0c4ca30a`**. New standard-library XLSX and purchasing functionality and a correction for two P1 catalog transaction/import regressions, plus tests. The original 1,141-test composite predates these corrections. New BOM commit needs its own exact-SHA integration and QA checks.
- Change-control PR #20: `cb68433` → `3038c9b` → **`724993c74bd7aa6f2c00ed38f8b44c39a42bdd08`**. New ECR rework semantics and corrected ECO dependency content binding, including changes to model/workflow and tests. A fresh independent review and actual combined merge test are necessary.

The original four-source CI remains useful as historical baseline interoperability evidence, not latest-head certification. Never silently replace these pinned sources inside the existing workflow or retarget PRs. A new, clearly labeled, pinned candidate should follow owner API review and completion of P1 core fixes.

## Completed cross-domain and wheel packaging evidence

[Test-only domain+wheel CI run 41584278631](https://github.com/nrobertson152/RevisionWorx/actions/runs/41584278631), pinned four-source inputs as above, workflow at Integration SHA `650a597f2711bacdd0de968334d508fae9126cd0`:

| Platform | Real doc → part/BOM → ECR native CAD preservation/restart test | Source wheel structural/metadata audit |
|---|---|---|
| Ubuntu runner, Python 3.11 | **1 PASS / 0 FAIL / 0 SKIP** | PASS |
| Windows runner, Python 3.11 | **1 PASS / 0 FAIL / 0 SKIP** | PASS |

Both runners verified the temporary real Git source tuple, opened a real disposable workspace, registered a native Inventor part with its journaled revision, associated that document to a BOM part, added a parent assembly usage, created an ECR affecting the part, and produced a WhereUsed impact report with the expected assembly while explicitly stating that the rest of the BOM universe was **not searched**. The report was read-only. The data remained correct after a separate read-only workspace reopening, with the original native CAD bytes and modification times unchanged.

After building a **test-only source wheel**, `tools/packaging/check_product_wheel.py` independently inspected the wheel: 65 archive paths, 20 `revisionworx/bom/` paths, 13 `revisionworx/changes/`, 10 GUI paths; exact expected user CLI/GUI entrypoint declarations, core files and GPL-3.0-or-later metadata present. **Zero mandatory third-party runtime dependencies and zero embedded native binaries** in that wheel. These facts concern a Python wheel, **not** a PyInstaller bundle, AppImage, Windows installer, signed release or a clean offline installed machine.

The wheel SHA-256 values **differ by platform even at the same source SHA tuple**:
- Ubuntu: `9130cf80d853c00bed7dcd2aa408e688e2eb1f22a7ce97aafd2b151b5fc97009`.
- Windows: `a0095f3f59628d655332dbd828bc82d90e090a811dde1c0c0507eb4a62d3a4c0`.

Both are recorded separately, so the present process **does not certify byte-for-byte reproducible cross-platform builds**. The initial domain/wheel [run 41584058598](https://github.com/nrobertson152/RevisionWorx/actions/runs/41584058598) was Ubuntu green/Windows **red** solely at wheel creation: the Windows CI host lacked `bdist_wheel` when `--no-build-isolation` was selected. Pinned CI-only build prerequisite `wheel==0.45.1` was added at workflow commit `650a597f`; [run 41584278631](https://github.com/nrobertson152/RevisionWorx/actions/runs/41584278631) then passed both platforms. It is a build-host tool, not a runtime dependency.

**No change to production/release HOLD:** the historic pinned BOM/ECO SHA tuple is not the latest head, and core integrity/recovery P1 issues require independent correction. Do not merge the synthetic test-only commits.
