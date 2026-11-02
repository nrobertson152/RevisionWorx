# RevisionWorx — Risk-based QA acceptance matrix

**QA Engineer • current verified candidate: Phase 2 `f3c54386e41a0587df0b97d0bfcf4b54cd92c0a6` + QA `454391dfd6bd66059b97631701f6e5722b5e862e`**

This is the **current acceptance state**, not a historical run list. Exact evidence: [full Linux/Windows × Python 3.11/3.13 run 41387311002](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311002) and [focused #8/#9/#10 + Windows process-stress run 41387311010](https://github.com/nrobertson152/RevisionWorx/actions/runs/41387311010). Earlier failed/xfail evidence is preserved in `docs/agents/QA_PROGRESS.md` and issues #8/#9/#10.

**Status rules:** PASS LOCAL = executed and accepted under the cited runner; XFAIL PHYSICAL = a stronger physical condition deliberately remains unmet, not a passing requirement; PLANNED = no executable acceptance of that product feature; NOT CERTIFIED = certification hardware/environment not exercised. Never count an xfail or skip as a passing safety requirement.

| ID | Priority | Acceptance invariant | Current evidence and status |
| --- | --- | --- | --- |
| ADOPT-01 | P0 | Customer/year/product archive registration never rewrites, moves or renames unmanaged content | **PASS LOCAL**, fixture hashes/mtime/path and application integration |
| ADOPT-02 | P0 | Adoption preview and initial baseline never claim invented revision history | **PASS LOCAL**, adoption tests |
| START-01 | P0 | Opening a workspace does not enumerate unmanaged archives | **PASS LOCAL**, instrumented scan negative test |
| START-02 | P0 | 50,000 unmanaged folders do not trigger initial traversal | **PASS LOCAL**, developer full suite instrumented test, independently rerun in exact-head suite |
| JOURNAL-01 | P0 | Disk full before publication cannot create a committed revision | **PASS LOCAL**, QA failpoint |
| JOURNAL-02 | P0 | Failure after journal publication remains readable and recoverable; ambiguous txid is resolvable | **PASS LOCAL**, QA and application tests |
| JOURNAL-03 | P0 | Journal gap or checksum alteration fails closed | **PASS LOCAL**, QA and application tests |
| LOCK-01 | P0 | A separate process cannot acquire an active writer lock, then can acquire following clean release | **PASS LOCAL** Linux/Windows interpreter isolation |
| LOCK-02 | P0 | Authorized administrative revocation is audited and stale commits have no effective data mutation | **PASS LOCAL** #8 journal-first acceptance; strong physical no-file oracle remains **XFAIL PHYSICAL** |
| LOCK-03 | P0 | Pre-audit/lock-move race does not authorize a stale publication | **PASS LOCAL** #8 `test_writer_revoked_before_admin_audit_cannot_take_effect` |
| LOCK-04 | P0 | Post-audit fenced journal artifacts remain logically invisible to reader, cache, recovery and txid outcome | **PASS LOCAL** independent `test_revoked_writer_publication_is_logically_fenced_on_replay`; physical artifact may exist |
| INDEX-01 | P0 | Rebuild from corrupted/damaged cache repairs disposable index, never authoritative records | **PASS LOCAL**, application tests |
| INDEX-02 | P0 | Record inserted after sampled journal cursor cannot appear with an older cursor | **PASS LOCAL** #9 deterministic index snapshot regression |
| INDEX-03 | P0 | Record deleted after sampled journal cursor cannot disappear with an older cursor | **PASS LOCAL** #9 deterministic index snapshot regression |
| CHECKOUT-01 | P0 | Exact one winner in real local six-process checkout; others are conflicts; no orphan lock | **PASS LOCAL Windows Server 2025: 5/5 trials, 30 process attempts, 1 winner + 5 conflicts per trial, all exits zero** |
| CHECKOUT-02 | P0 | Stale checkout tokens rejected and checkout history unaltered | **PASS LOCAL**, independent Phase 2 suite |
| WINDOWS-LOCK-01 | P0 | Transient Windows writer.lock sharing violation gets bounded retry, verifies owner, removes own lock | **PASS LOCAL Windows**, #10 injected failure and successor acquisition |
| REV-01 | P0 | Historical revision SHA-256 and content immutable after check-in | **PASS LOCAL**, dedicated Phase 2 integrity tests |
| REV-02 | P0 | Release supersedes prior release without rewriting old history; corrupt blob refuses release | **PASS LOCAL**, dedicated Phase 2 integrity tests |
| REV-03 | P0 | Restore prior revision creates a NEW revision C, retaining revisions A/B and preserving native CAD references | **PASS LOCAL**, dedicated history-restore test |
| EDIT-01 | P0 | Explicit forced-hash verification detects unauthorized same-size/same-mtime edits | **PASS LOCAL**; metadata-only fast path cannot guarantee adversarial detection |
| CAD-01 | P0 | Registration/check-in never moves or silently rewrites unrelated assembly files | **PASS LOCAL** on synthetic CAD content and directory fingerprint; real Inventor/SolidWorks resolver integration **NOT CERTIFIED** |
| CLI-01 | P0 | Corrupt revision verify exits nonzero; revision export cannot overwrite existing path | **PASS LOCAL**, real subprocess executable |
| GUI-01 | P1 | Real PySide6 Documents tab filtering, unauthorized-edit warning, checkout-conflict error, blank message rejection | **PASS OFFSCREEN** Linux/Windows; actual installed desktop UI **NOT CERTIFIED** |
| OSCH-01 | P1 | Verified content-based OpenSchematix recognition, not extension-only assertions | **PASS LOCAL** recognition tests; BOM and part-reference adapter integration not approved |
| BACKUP-01 | P0 | Metadata and immutable blobs restored from one consistent point, with safe authority epoch | **PLANNED**; restore trust and authoritative high-water design needed |
| BOM-01 | P0 | Engineering BOM quantities, revisions and provenance are accurate | **PLANNED** |
| GRAPH-01 | P0 | Where-used direct, indirect, cyclical, cross-project and version-specific links disclose coverage | **PLANNED** |
| IMPACT-01 | P0 | Change impact does not imply compatibility without verified dependencies | **PLANNED** |
| PART-01 | P0 | Configurable numbering reserves/collisions and stable part identity | **PLANNED** |
| SCALE-01 | P1 | 10k projects, 2m documents, 500k edges: actual application p95/RSS target | **NOT MEASURED**; synthetic dataset generation is not an application benchmark |
| WINDOWS-GUI-01 | P0 | Clean installed Windows release launches and completes register/checkout/checkin/UI recovery | **NOT CERTIFIED**; no packaged installer/manual visual desktop run |
| SMB-01 | P0 | Windows coordinator and two independent SMB clients preserve atomic revocation/commit on disconnect, restart, contention | **NOT CERTIFIED**; ADR-014 coordinator and physical share rig required |
| NFS-01 | P1 | Supported Linux/NFS share semantics, if approved | **NOT CERTIFIED**, later target |

## Five separately retained regression oracles

These remain present on `tests/qa/test_integrity.py`, not folded into a single green check:

1. `test_paused_writer_break_after_lock_check_must_not_publish`: **strict XFAIL** for physical post-revocation publication, even though ADR-015 rev.2 fences the artifact's logical effect.
2. `test_writer_revoked_before_admin_audit_cannot_take_effect`: **PASS** on the corrected journal-first revocation; old implementation remains version-gated strict xfail.
3. `test_index_rebuild_does_not_include_records_newer_than_its_watermark`: **PASS** on current bounded stable snapshot; older implementation remains version-gated strict xfail.
4. `test_index_rebuild_does_not_omit_pre_watermark_records_due_to_late_delete`: **PASS** on current bounded stable snapshot; older implementation remains version-gated strict xfail.
5. `test_windows_writer_lock_close_survives_transient_sharing_violation`: **PASS** on current Windows release retry; old implementation remains version-gated strict xfail.

**Complementary positive oracle:** `test_revoked_writer_publication_is_logically_fenced_on_replay` checks replay/index/reader and `transaction_outcome=fenced`. The physical xfail must not be presented as an effective unauthorized mutation.

## Exact execution and release gates

The main QA PR is based on Phase 1. Phase 2 tests are executed using a separate disposable checkout with an exact recorded Phase 2 SHA and QA test overlay, **not** a merge into another engineer's branch. `python -m pytest -q -ra tests/qa` and `python -m pytest -q -ra` on the current test revisions reported:

- Linux Python 3.11/3.13: QA **37 passed, 1 skipped, 1 strict xfailed**; full **211 passed, 1 skipped, 1 strict xfailed**.
- Windows Python 3.11/3.13: QA **38 passed, 1 strict xfailed**; full **211 passed, 1 skipped, 1 strict xfailed**.
- Focused Windows stress: `python tests/qa/windows_checkout_stress.py --trials 5 --workers 6 --timeout 100` → **5 successes, 0 failures**, 30 actor attempts, zero orphan lock and clean verification.

The **single-user desktop application is not automatically release-ready** solely from test results: the installed native GUI, packaging/upgrade paths and backup recovery remain unqualified. **Multi-user Windows/SMB is not certified** by any local test; it requires an integrated coordinator, authenticated authority, restore fencing and real separate-host testing. The Director approves any merge or release.

See [the QA merge-readiness handoff](PR_MERGE_READINESS.md) for exact tested SHAs, ownership and retest steps.
