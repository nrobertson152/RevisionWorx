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
