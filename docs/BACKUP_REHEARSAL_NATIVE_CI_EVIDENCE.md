# Offline backup/restore rehearsal — native CI evidence

Status: **focused regression gate PASS; closed real-workspace restore acceptance NOT YET ESTABLISHED**.

## Immutable execution identity

- Source branch: `integration/openschematix`; source commit: `aed62eb08ab6b27e52ef756655f40098a262178e`.
- Workflow: `.github/workflows/revisionworx-backup-rehearsal.yml`.
- Push-triggered GitHub Actions run: [41630067018](https://github.com/nrobertson152/RevisionWorx/actions/runs/41630067018), 2026-11-03 11:45 UTC, conclusion **success**.
- Ubuntu native job [125897060106](https://github.com/nrobertson152/RevisionWorx/actions/runs/41630067018/job/125897060106): Python **3.11.17**, `python -m unittest -v tests.test_offline_backup_rehearsal`: **5 passed, 0 failed, 0 skipped, 0 expected failures**; CLI `python tools/packaging/offline_backup_rehearsal.py --help` succeeded.
- Windows native job [125897060632](https://github.com/nrobertson152/RevisionWorx/actions/runs/41630067018/job/125897060632): Python **3.11.9**, same test command: **5 passed, 0 failed, 0 skipped, 0 expected failures**; CLI help succeeded.
- Both results were independently checked against the actual Actions run and job logs, not inferred from workflow YAML or PR check status.

## Accepted scope and limits

These are five **isolated fixture** tests of the offline copy-only utility. They do **not** establish restoration of a closed authoritative RevisionWorx workspace, version compatibility, concurrent writer snapshot consistency, interrupted backup recovery, real filesystem power-loss durability, or an installed application rollback. Source files are not changed by the tested utility; production source-integrity blockers remain separate release gates. The Node.js 20 deprecation warnings emitted by checkout/setup-python are nonblocking but should be tracked for CI maintenance.

## Next independently testable gate

Create a disposable **closed** RevisionWorx workspace using the pinned production core API; populate an authoritative document/revision and record expected immutable content digests. Close all handles and writers, then invoke the backup/verify/restore CLI against fresh temporary destinations. Reopen the restored workspace with the matching pinned core and verify authoritative revision-chain and content SHA identity; compare original source tree hashes and reject manifest tampering. Include Windows and Linux native execution, explicit pass/fail/skip/xfail counts and exact SHA. No live concurrent snapshot or release approval claim.

**Release recommendation:** HOLD pending the above end-to-end acceptance and independent core/QA clearance.
