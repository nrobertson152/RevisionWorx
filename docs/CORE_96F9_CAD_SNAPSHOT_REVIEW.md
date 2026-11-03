# Independent core audit — CAD source identity during save-by-rename

**Exact production source:** `96f9c368a11fd43c0440d404c7258c7dfeacabf6` (`phase2-documents`).  
**Research regression source:** `prototype/test_core_96f9_cad_atomic_save_identity.py`, updated research commit `7d3e4da09c3cc133b8014ae8b9dc94bcfefa23fe`.  
**Pinned Linux/Windows Python 3.11/3.13 job:** [GitHub Actions 41580265762](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580265762).  
**Scope:** Tier 1 local workspace; working CAD files (not the trusted `.revisionworx` internal namespace) may be saved by ordinary external editors.

## Property / impact

A successful new revision snapshot should reflect the working file captured and should not silently accept the old contents of a file displaced during the capture. `ContentStore.store_file` uses

```python
before = os.stat(source)
with atomicio._open_for_read(source) as src:
    ...   # read via held handle and hash
after = os.stat(source)
if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
    raise SourceChangedError(...)
```

There is no comparison of **file identity** across the initial pathname, open handle and final pathname.

One ordinary save workflow writes a temporary replacement, moves the original to a backup, then renames the replacement to the original name. Each individual rename is atomic. If the replacement has the **same size and modification timestamp** as the original, `before` and `after` both compare equal, but the held handle reads the **old** object while the source name now refers to **new** bytes. `store_file` returns success and publishes the old SHA-256 object. The object itself remains correctly hashed and immutable; the defect is **source-to-revision provenance, not journal corruption or an invalid SHA-256**.

This is deterministic under actual file I/O: the test intercepts opening the source immediately **after** obtaining the genuine old file handle and performs real native `os.replace(source, backup)`, `os.rename(replacement, source)`, with identical size/mtime on the replacement. It asserts that the source now contains `NEW-CAD-DATA` while the successful stored object contains `OLD-CAD-DATA`. It also includes the positive property that this situation should raise `SourceChangedError` (currently a strict expected failure) and control that an ordinary new mtime *does* lead to rejection.

**Preliminary severity: P1 correctness**, conditional on working-file save-by-rename with preserved size and mtime. This does not prove a common CAD editor normally preserves mtime; it does prove that the implementation's stated "source changed during copy" detection is incomplete.

## Windows save-mode qualification

The same actual kernel test on Windows confirms `atomicio._open_for_read` allows moving the **open source** to a backup, enabling that save workflow. It also separately shows that one-step `os.replace(temp, open_source)` may raise genuine **WinError 5** while the reader is open, despite requesting FILE_SHARE_DELETE. This is a *distinct* Windows Save As / replace-open-destination compatibility concern, not proof of lost revision bytes. Do not conflate moving an open **source** with overwriting an open **destination**.

## Recommended production correction for Claude A

- At minimum, compare `st_dev/st_ino` (or equivalent OS-specific file identity / file ID) of the *initial pathname*, the handle `os.fstat(src.fileno())`, and the final pathname; refuse with `SourceChangedError` when any differs. Compare size/mtime of the handle too, and preserve the existing digest/content verification and temp-file cleanup. On Windows, independently verify file-ID reliability on native runners and handle a platform where `st_ino` cannot be trusted.
- Add positive ordinary integration tests for a legitimate backup-and-rename save that preserves the same size/mtime, plus changed-mtime control, on Linux and Windows 3.11/3.13. Add a native Windows guard for the exact one-step `os.replace(temp, open_destination)` issue and decide whether it belongs to the documented compatibility contract; do not assume FILE_SHARE_DELETE solves every atomic-replace operation.
- File identity alone does not detect **in-place modification** that preserves both size and mtime. For a stronger invariant against unconstrained external writes, consider extra reread/digest verification and explicitly document the remaining unavoidable concurrency boundary. Do not claim a formally atomic external snapshot without writer coordination or filesystem snapshots.

## Acceptance boundary

The existing historically accepted ADR-019 writer-lock protocol is unaffected by the proof above. **Do not merge Phase 1 alone**: `phase1-foundation` still has the read-then-unlink P0 stale successor-lock flaw. No production branch/code, PR target or release was changed by Distributed Systems. Requalify any fix by exact SHA.


## Four-platform complete results and verified remediation primitive

**Revised source test:** `prototype/test_core_96f9_cad_atomic_save_identity.py` at research commit `b7cad63c9317d6930a0d971f856d9b7c1c61896d`. [Exact-source run 41580498940](https://github.com/nrobertson152/RevisionWorx/actions/runs/41580498940) **all four jobs completed SUCCESS**, native logs inspected:

| Native runner | Positive/control | Desired safety | Windows-specific | Verdict |
|---|---|---|---|---|
| Ubuntu Python 3.11 | 4 PASS | 1 expected safety XFAIL | 1 SKIP | negative source-identity finding confirmed |
| Ubuntu Python 3.13 | 4 PASS | 1 expected safety XFAIL | 1 SKIP | confirmed |
| Windows Python 3.11 | 5 PASS | 1 expected safety XFAIL | 0 SKIP | confirmed |
| Windows Python 3.13 | 5 PASS | 1 expected safety XFAIL | 0 SKIP | confirmed |

Controls include non-replaced identity consistency, same-metadata backup-then-rename file identity **detection by comparing stat+fstat**, changed-mtime rejection, old-content unsafe success, and (on Windows) genuine WinError 5 on one-step os.replace into an open target. The expected-failure source-provenance safety check means **new core is not accepted on this property**, despite green negative-control CI.

Most importantly, the positive **file-identity probe** confirms that the portable tuple `(st_dev, st_ino)` from initial `Path.stat()`, `os.fstat(reader.fileno())`, and final `Path.stat()` is stable for an unchanged source and changes across the backup-and-rename swap on **both actual Windows CPython 3.11/3.13 and Ubuntu CPython 3.11/3.13**. This directly tests the minimum proposed correction on the same OS versions instead of relying on a platform assumption. It cannot by itself certify every possible filesystem or in-place modification; the stronger source snapshot contract should be documented.


## November 3 P0 follow-up: exact 96f9 two-pass mitigation pilot and its residual race

**Independent executable prototype:** `prototype/test_phase2_cad_two_pass_pilot.py` introduced at research commit `30315d450e093cde7902c96ad299cd26bfb14bf4`; standalone CI workflow `.github/workflows/distributed-cad-two-pass.yml` commit `3cef027eaf3e4742756c08a42af619afb8592a1f`. It pins the **unmodified Phase 2 production source `96f9c368a11fd43c0440d404c7258c7dfeacabf6`**, not a floating branch. [GitHub Actions run 41583080098](https://github.com/nrobertson152/RevisionWorx/actions/runs/41583080098) **COMPLETE SUCCESS, four native jobs**, raw logs inspected:

| OS / Python | Ordinary positive/control | Strict atomic-snapshot requirement | Result |
|---|---:|---:|---|
| Ubuntu 3.11 | 4 PASS | 1 expected failure | Focused research green; strict guarantee not accepted |
| Ubuntu 3.13 | 4 PASS | 1 expected failure | Same |
| Windows 3.11 | 4 PASS | 1 expected failure | Same |
| Windows 3.13 | 4 PASS | 1 expected failure | Same |

**What was independently established:**

1. The **test-only** `guarded_two_pass` candidate calls the current `ContentStore.store_file`, checks that the pathname still names the original `(st_dev, st_ino)`, then rereads the source, hashes the actual new bytes and compares digest and size with the captured SHA-256. An unchanged file succeeds and its stored content verifies.
2. If a second *real OS file handle* rewrites the original inode **after the original read returns old bytes**, fsyncs and restores the same file size/mtime, the second digest catches the difference and a `SourceChangedError` is raised. Source identity alone would miss this.
3. If an external CAD save moves the opened source to a backup and renames an equal-size/equal-mtime replacement into the same pathname, the candidate rejects the changed file identity/digest with `SourceChangedError`. Both cases refuse before the caller can commit a new journaled revision (`head_seq` unchanged); an unreferenced immutable content object may remain, which is not authority or a committed revision.
4. **Residual counterexample:** another actual writer changes the *same inode after the second digest completes but before the candidate returns*. File ID, size and mtime are preserved, so the candidate returns success with the old digest despite the current pathname containing new bytes. This is a strict expected-failure test, not a successfully implemented requirement. No finite number of rereads can remove an arbitrary external writer's race window.

**Engineering recommendation to Claude A:** distinguish two contracts explicitly.

- **Practical Tier 1 consistency checking:** verify initial/open/final file identity plus stable metadata, and optionally an independent full reread/hash before returning or journal commit. This would close both *reproduced mid-capture interleavings* and is independently proven to work on native Linux and Windows. Budget for approximately two full file reads; an interrupted second read must never convert into a committed revision. Add **ordinary positive** tests on production corrected exact SHA (no XFAIL), including real OS writes/renames, and avoid deleting externally edited working files.
- **Strict atomic/linearizable external CAD snapshot:** impossible to establish by metadata + finite repeated hashes when unrelated processes can rewrite in place at any time. Achieving that stronger claim requires an enforced exclusive source writer barrier, application cooperation, an OS/filesystem snapshot capability, or equivalent consistency protocol. Document this exclusion. RevisionWorx's **journal and stored object** can still be internally immutable and correctly hashed; this study concerns provenance between a changing working pathname and the committed revision.

**Disposition:** **RESEARCH ACCEPT** for the reduced-risk mitigation proof; **NO PRODUCTION ACCEPTANCE** of the existing source semantics or strict atomicity. The existing P1 source-provenance and malformed-journal health blockers remain HOLD pending Claude A's source fix, independent positive QA/DS exact-SHA verification and Director merge authorization.
