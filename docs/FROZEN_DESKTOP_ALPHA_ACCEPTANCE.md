# Frozen Desktop Alpha — independent Integration test matrix

**Status:** TEST-ONLY. Not an installer, released application, multihost filesystem certification or approval of Phase 2 storage safety.

**Owner:** Integration PR #2, integration/openschematix.
**Desktop GUI source:** Claude B draft PR #14, exact f00aa31b6f66fbe0e78ff440aed7026e869138b3.
**Build/harness:** record the workflow commit from revisionworx-alpha-packaging.yml.
**Separate baseline packaging:** revisionworx-packaging-readiness.yml remains pinned to d656884b39401134149273dd0121ef620b76eb8e. No repinning of source authority is implied. The alpha branch includes its own earlier core code and is unapproved for production.

## Evidence tiers

1. **Frozen GUI widgets, offscreen.** A separately marked non-releasable PyInstaller wrapper supports an opt-in --alpha-probe mode. It runs real MainWindow/Qt widgets: Create project, Import file, Checkout, CheckinDialog, revision history, then uses a fresh frozen GUI process to reopen. It verifies the permanent alpha warning, both SHA-256 revisions, original outside-source fixture and cleared checkout. No fake journal or mocked core.
2. **Frozen independent CLI.** The separately frozen CLI binary reopens the GUI-created workspace, resolves the document and two revision IDs, exports both blobs, independently hashes each, and verifies the document. GUI revision IDs must match CLI. A file-level snapshot of the workspace must not change merely because the GUI reopened.
3. **Relocated installation path.** The host-side smoke_alpha.py copies both frozen onedir trees into an isolated folder containing spaces and Unicode. Subprocess PATH excludes Python and proxies point to unreachable localhost. This is **not** a truly offline Windows VM.
4. **Offline Linux container.** A nonroot, read-only-root Ubuntu container, started with --network none and no Python/pip, runs relocated GUI widget acceptance, distinct GUI process reopen, frozen CLI revision exports, and normal GUI startup. Package installation occurs before this runtime test. It does not trace attempted sockets.
5. **Manual interactive GUI, NOT EXECUTED.** On clean Windows and X11/Wayland Linux desktops, operate the normal GUI with mouse and keyboard through create/open/import/checkin/history/restart; test dialogs, shortcuts, scaling, denied writes and blocked recovery. Record OS build, paths, screen captures, source and executable SHAs, before/after workspace hashes. Test a genuinely disconnected Python-free Windows VM. Qt offscreen automation is not a substitute.

## Reproducible build

The independent GitHub Actions workflow checks out the exact alpha SHA and runs:

~~~shell
python tools/packaging/test_builder.py
python tools/packaging/build.py \
  --source external/desktop-alpha \
  --output packaging-alpha-output \
  --expected-sha f00aa31b6f66fbe0e78ff440aed7026e869138b3 \
  --alpha-acceptance
~~~

The --alpha-acceptance option selects the test-only alpha_gui_entry.py in place of the normal gui_entry.py, but ordinary startup still uses the real production GUI. The test wrapper additionally bundles alpha_frozen_probe.py. **Never distribute the test wrapper as a normal installed application.**

The GUI probe accepts only a newly created rw-alpha-fixture-* directory, refuses a preexisting target workspace, and writes a JSON evidence report or error traceback into that root. A missing/mismatched report, nonzero exit, wrong revision ID, incorrect digest, or changed authoritative file makes the test fail.

## Acceptance matrix

| Property | Concrete assertion |
| --- | --- |
| GUI create/open | Actual Qt start page creates one managed project and workspace metadata |
| GUI import | Actual ImportDialog copies one source fixture unchanged and creates revision 1 |
| GUI checkout/checkin | Qt button, description dialog and changed working file create revision 2 and clear checkout |
| History and restart | Two revisions in GUI; new frozen GUI process opens same document and history |
| Frozen CLI integrity | Independently exported blobs have expected SHA-256; CLI document verification returns no problems |
| Path relocation | GUI and CLI run from copied Unicode/spaces onedir distributions |
| Nonmutation | Workspace file-level hashes unchanged during GUI/CLI read-only reopen |
| Linux isolation | No installed Python or pip, --network none, read-only root and disposable tmpfs |
| Honest evidence | Failing GUI/CLI command or malformed report fails the job |

## Release-blocking limitations

- Phase 2 ADR-019 on a newer production SHA must receive explicit independent QA and Distributed Systems acceptance; two custody/fencing P1 races are under review. No Phase 1 backport or production repin.
- Visible interactive desktop QA, native installer, Windows clean disconnected VM without Python, keyboard and accessibility review.
- Real ACL denial, attempted socket tracing, power failure and disk exhaustion, installed-app upgrade/rollback and genuine historical schema migration.
- Multiple-machine shared-filesystem certification.

Only temporary single-user, local-filesystem workspaces are used. Neither green CI nor the alpha UI is suitable for irreplaceable production data. No merge or release is authorized.


## Verified result and negative evidence

[CI 41566749753](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566749753)
at exact harness SHA 7fbd7b31135663f498bb15cc50dc80ba4919db9e,
desktop alpha SHA f00aa31b6f66fbe0e78ff440aed7026e869138b3,
**PASS on ubuntu-latest and windows-latest, Python 3.11 build runners**.
Each ran 5/5 packaging safeguard unittests and completed the frozen
GUI/CLI create, import, checkout/checkin, two-revision history, restart,
historical SHA-256 exports and copied-workspace integrity checks.
Ubuntu also **passed** the fully network-unreachable/Python-free container
runtime for these operations. The separate older-source packaging run
[41566749764](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566749764)
passed both OS at the same harness SHA and unmodified d656884b source pin.

The first alpha matrix [41566452430](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566452430)
failed on both OS only during repeated test verification: the CLI correctly
refused to overwrite its prior export target. Unique destination files were
introduced in 7fbd7b3, with unchanged SHA-256 and exclusive-write assertions.

The newer GUI branch e5725df17218b5bb9fb975389a0580aed63f2205 is
NOT covered by this accepted test. Its own [CI 41566802123](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566802123)
failed 10 Qt tests on each OS due to an invalid relative import of
CommittedNotApplied in the GUI document tab. Reported to Claude B. Keep the
frozen-alpha source pinned until a fixed GUI SHA passes independently.
No interactive/manual app acceptance or release authorization is implied.


## Newest accepted GUI source and observable-state evidence

The test-only alpha workflow now pins Claude B GUI source
17651af68128572c7c83429513044a5948dd07bd. Its native Windows/Linux
[GUI CI 41570211687](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570211687)
passed on both OS. Integration harness SHA
b4866ec56e20af02f882699749feddd7cab23e0f,
[alpha CI 41570682514](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570682514):
**Ubuntu and Windows frozen-app matrix PASSED.**

Alongside the original Qt actions, two revision exports and separate-process
reopen, the frozen GUI wrapper now asserts the **documented version 1
GUI observable-state contract** at initial start, project creation,
first import, checkout ownership, second check-in, F5 refresh and new GUI
process restart. In particular, the visible selected project/document,
two history rows and enabled checkout versus check-in actions must agree
with the durable storage state. The test explicitly fails if F5 loses
selection or if an ordinary successful check-in is shown as an error.
The host-side verifier requires named JSON evidence for the contract and F5
assertions rather than accepting a nominal process-success status.

The Ubuntu job also passed the complete widget path and normal GUI start
inside the **network=none Python/pip-free** test container. The Windows
job passed the frozen widget path, 401-character local and extended-length
path probes, local UNC loopback read, and locked-file rejection. This is
not human-driven GUI acceptance or physical two-host SMB.

The separate older-source packaging [CI 41570682310](https://github.com/nrobertson152/RevisionWorx/actions/runs/41570682310)
also PASSED on Ubuntu/Windows with production source
d656884b39401134149273dd0121ef620b76eb8e unchanged. These two exact
source pins are intentionally separate. Nothing authorizes a release,
Phase 1 backport or newer Phase 2 safety acceptance; the unaccepted
Phase 2 core d2b7a1c has three independently reproduced in-scope
protocol-only writer exclusivity P1 failures. No clean Windows VM or actual
manual interactive installed GUI has been executed.
