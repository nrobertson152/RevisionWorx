# Offline backup and restore: operator rehearsal and acceptance gates

Status: **TEST PLAN ONLY — NOT A CERTIFIED BACKUP OR RESTORE PROCEDURE**. Integration branch `integration/openschematix`; issue #32; reserve backlog #28 item 12. This document does not authorize backup of a live workspace, overwrite of an existing workspace, or production disaster recovery.

## Safety boundary

The prototype `tools/packaging/offline_backup_rehearsal.py` and its isolated regression fixtures are not evidence that a RevisionWorx authoritative workspace can be backed up consistently while active. Treat the authoritative `.revisionworx` journal, records, immutable content objects, revision metadata, and relevant configuration as one consistency boundary. A file-by-file copy while writers remain active may capture incompatible generations even if every copied file hashes correctly.

For a rehearsal, use a **disposable workspace copy**, never the production workspace. Shut down every writer and client accessing that copy, including GUI sessions and background tasks. Record the exact source commit, workspace format/schema version, application version, host OS, Python version, and tool invocation. Never copy over an existing destination or delete an original. Keep the source unchanged and independently hash source files before and after the operation.

## Rehearsal sequence (requires implementation and review before use)

1. Create a disposable managed workspace using the actual supported RevisionWorx API at an exact pinned commit. Register a document, check out, check in at least two distinct byte sequences, and record immutable revision IDs, content hashes, and exported revision bytes. Include nested CAD-like paths, Unicode names and an unrelated unmanaged file. Do not claim CAD native-reference parsing from file extensions alone.
2. Close the workspace and stop all writers. Capture a read-only inventory of the authoritative metadata tree and immutable objects. Record SHA-256 per file, relative paths, sizes, and the application/schema version. Reject symlinks, traversal, name collisions, unreadable files, and unsupported versions rather than following or repairing them.
3. Produce the backup in a separate disposable destination with a manifest binding the file inventory, source version, and snapshot provenance. Simulate interruption before publication: the partial result must not be accepted as a complete backup.
4. Verify every manifest entry and every file hash, reject missing/extra files, and refuse restore when the manifest, version, or integrity evidence is ambiguous. Verify that a tampered manifest and a tampered object both fail closed.
5. Restore into a **new, empty** disposable directory. Do not replace a live workspace, change the source, or automatically upgrade the restored schema.
6. Reopen the restored workspace through the actual public workspace API. Independently verify the authoritative journal/revision chain and export each recorded immutable revision; compare exported bytes and SHA-256 with pre-backup evidence. Check document identity, revision ordering, and original relative CAD path references. Confirm that the unrelated unmanaged source file is unchanged.
7. Close and reopen once more to check persistence. Repeat with a deliberately unsupported newer schema/version and with an interrupted restore. Both must fail safely without altering the source or leaving a destination that appears valid.
8. Rehearse rollback: leave the original untouched, discard the failed **disposable** restored copy, and reopen the original. Compare its authoritative hashes and exports to the baseline. Never silently reverse or rewrite published revision history.

## Acceptance evidence matrix

| Gate | Positive evidence required | Failure/negative evidence required | Current status |
| --- | --- | --- | --- |
| Isolated utility regression | Exact commit and 15/15 discovered test names; Ubuntu and Windows Python 3.11 logs with pass/fail/skip/xfail | Interrupted backup, tampered manifest, duplicate keys, case collisions, source mutation, empty directories | **UNVERIFIED at current head** |
| Closed authoritative snapshot | Source is closed, consistent metadata/object inventory, full manifest and stable pre/post SHA-256 | Concurrent writer or changed source refused | **NOT EXECUTED** |
| Actual restore and reopen | New destination, real workspace API reopen, journal verification, revision export byte equality | Unsupported version, missing/corrupt object, partial restore refused | **NOT EXECUTED** |
| Native CAD path preservation | Original and restored relative paths and byte hashes match; no automatic path rewrites | Unsupported reference parsing explicitly reported unknown | **NOT EXECUTED** |
| Rollback | Original reopened unchanged after simulated failed restore | No automatic destructive overwrite | **NOT EXECUTED** |
| Release/installer | Pinned branch graph, native Windows/Linux artifact installation and launch, license/manifest verification, independent QA | No unsupported SMB/NFS or production recovery claims | **HOLD** |

## Evidence record template

Record: UTC timestamp; branch; full source SHA; workflow URL/run/job/attempt; host OS/version; Python version; exact commands; tests discovered; passed/failed/skipped/xfail; SHA-256 of source manifest and resulting archive; source and destination absolute paths (redact private identifiers); application/schema version; revision IDs and export hashes; failure-injection results; independent QA reviewer; unresolved risks.

**Known evidence gap:** the GitHub connector's commit workflow lookup is restricted to PR-triggered runs. An empty result does **not** establish that push-triggered Actions did not run. Inspect push-triggered runs and downloadable logs using an Actions-capable client before asserting any CI result. Repeated changes to workflow YAML without native execution are not an acceptance substitute.

## Go/no-go

Release remains **HOLD** until every required positive test is demonstrated on supported operating systems, failure cases reject unsafe inputs, independent QA accepts the exact integrated candidate, and the Director authorizes release. A green negative-test suite by itself is not proof of successful recovery.
