# Offline execution and failure-recovery qualification — RevisionWorx

**Assignment:** Integration & Release Readiness Engineer, isolated PR #2.
**Verification checkpoint:** [GitHub Actions run 41392572210](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392572210).
**Frozen-build/workflow SHA:** `ccb5520b31209eeb4e03943afc89b8b981474fc1`.
**Exact tested Phase 2 source SHA:** `d656884b39401134149273dd0121ef620b76eb8e`.
**Python builder:** CPython 3.11, GitHub-hosted Ubuntu and Windows.
**Status:** experimental testing only; no production installer, release or migration.

## Governing boundary

RevisionWorx must operate without mandatory internet, accounts, cloud or
telemetry. Passing an offline **startup smoke** establishes that the specific
exercised operations can complete without network reachability. It does NOT
establish that all code paths avoid attempted connections or that no dormant
network, telemetry or updater code exists. Nor does a frozen application
running while Python is absent from `PATH` prove Python is not installed
elsewhere on Windows.

The tests run against a **new, disposable workspace** and an untouched copy,
never user/company data. No production `revisionworx/` code was changed.

## Exactly what was run

| Platform | Test command or action | Result | Qualification limit |
| --- | --- | --- | --- |
| Ubuntu + Windows, Python 3.11 | `python tools/packaging/test_builder.py` | **5/5 unit tests passed each** | Builder safety, snapshot and isolated-child environment only |
| Ubuntu + Windows, Python 3.11 | `python -m unittest discover -s tools/packaging -p 'test_failure_recovery.py' -v` | **3/3 unit tests passed each** | Pin-matched source API, injected errors in temporary workspace, not real disk/ACL/power loss |
| Ubuntu + Windows | `python tools/packaging/build.py --source external/revisionworx-phase2 --output packaging-prototype-output --expected-sha d656884b39401134149273dd0121ef620b76eb8e` | **Bundled CLI/GUI + subprocess smokes passed each** | PyInstaller onedir, no native installer, Qt offscreen |
| Ubuntu runtime only | `docker run --rm --network none --cap-drop ALL --read-only --tmpfs /tmp ... revisionworx-offline-qt /bin/sh /harness/smoke_offline_container.sh` | **Passed**: network unavailable, Python/pip absent, frozen CLI help and Qt offscreen GUI survived | Image creation permitted downloads; no network syscall trace or interactive UI |
| Windows runtime only | `python tools/packaging/probe_windows_paths.py --cli packaging-prototype-output/dist/RevisionWorxCLI/RevisionWorxCLI.exe` | **Passed probes** below | Local runner and localhost UNC only, no external SMB certification |

**Unit test count:** eight independent test methods on each OS (sixteen
executions), plus frozen GUI/CLI and container/path probes. There were **no
failed jobs** in run 41392572210. The prior run
[41392254014](https://github.com/nrobertson152/RevisionWorx/actions/runs/41392254014) also passed without the newer optional
Windows path probe. Tests were not run on Python 3.13 for packaging.

## Linux offline model

The `tools/packaging/offline_container.Dockerfile` starts from Ubuntu 24.04
and installs OS Qt EGL/XCB dependencies *during the build*, while internet is
available to build tools. The final invocation has Docker `--network none`,
no Python/pip executable in the image, nonroot runtime, read-only root and
readonly bind-mounted one-directory bundles. Its frozen GUI and CLI start
successfully.

No attempt-tracing instrumentation or full GUI feature navigation was run.
The test does not prove no telemetry code exists, only that no network access
was *required by those startup operations*. A real disconnected Linux VM
install without bundled Python and with installer/uninstaller remains untested.

## Windows offline model and optional filesystem probes

The Windows frozen child environment sets `PATH=""`, clears Python-specific
environment variables, runs under a clean temporary home/config/cache/state
profile, and sets HTTP(S)/ALL proxy values to unreachable localhost with
`PIP_NO_INDEX=1`. Windows internet access was **not physically disabled**,
and Python remains installed on the GitHub runner. A clean disconnected
Windows VM is a required release gate.

From the exact job logs:

| Probe | Actual observed result | Interpretation |
| --- | --- | --- |
| Extended-length local path (`\\?\`) | **passed** | Frozen CLI read succeeded |
| Local path of length **401** | **passed** | Frozen CLI created and read a disposable workspace at that length on the CI runner |
| Existing `\\localhost\C$` administrative share | **passed** | Read-only loopback UNC access works on that runner; NOT cross-host SMB |
| Mandatory byte-range lock of `workspace.json` | **read rejected** | Frozen CLI returned failure while source test manifest's first byte was locked; lock then released |
| Original test workspace SHA-256 | **unchanged** across all probes | No original metadata mutation observed (4 source fixture files) |
| Missing root / file masquerading as workspace | **rejected**, neither changed | Negative read-path behavior |
| Read-only metadata file flag | **read succeeded; hashes unchanged** | Not a true denied ACL or read-only install qualification |

The current Windows codepage mitigation exists only in
`tools/packaging/cli_entry.py`, not the production source CLI.

## Journal failure injection and backup rehearsal

`tools/packaging/test_failure_recovery.py` imports the pinned Phase 2
workspace module into the temporary runner process. It injects `ENOSPC`
and `EACCES` just before journal publication. Both failed without a
journal entry or `project` record appearing. It separately injects ENOSPC
*after* an entry is published, verifies `CommittedNotApplied` and durable
`txid`, then reopens the disposable workspace writable and checks recovery
materializes the one committed record. A future format version refuses
opening without changing metadata.

`smoke_workspace.py` hashes an original and its copy, rejects missing/
non-directory locations, tests a read-only metadata file attribute, corrupts
only a **third disposable copy**, verifies refusal, restores the corrupted
fixture from an unchanged backup copy, and validates the original/copy
hash sets. This rehearses byte-preserving **fixture restoration**, not an
actual product migration, installer transaction or power-loss recovery.

## Required follow-up before shipping

1. Run Windows and Linux real interactive GUI builds on clean VMs **without
   Python installed** and with network actually disconnected. Audit socket
   attempts during relevant workflows, not only startup.
2. Test a real installer/uninstaller and older-version workspace upgrade
   with verified backups; inject actual interruptions, ACL denial and disk
   full in disposable VM snapshots.
3. Audit Qt/EGL/XCB, binary signing, license notices and transitive dependency
   hashes; test networkless *installation* and cold startup on minimum
   supported OS baselines.
4. Run real two-host SMB filesystem recovery and approved authority
   coordinator certification before any concurrent multi-user release.
5. Retest the final combined app/adapter against approved later Phase 2
   HEAD after independent QA acceptance; these tests target `d656884b`,
   not newer `366d4355`.

**No installer, update or release has been created.**
