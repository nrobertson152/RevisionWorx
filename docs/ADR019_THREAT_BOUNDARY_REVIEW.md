# ADR-019 lock-namespace authority boundary — independent Distributed Systems review

**Production source reviewed:** `d2b7a1cfead5b83acecce2252e70d0a155f146b7` (Phase 2).
**Research safety suite:** `prototype/test_phase2_rev3_protocol_boundary.py`, source SHA `f74923b8af6cbb1ac44bdead74f82e428449ca2e`.
**CI:** [41566000473](https://github.com/nrobertson152/RevisionWorx/actions/runs/41566000473), Linux/Windows Python 3.11/3.13, each **6/6 PASS** (**24/24 independent positive tests**); the 22-job research workflow finished **22/22 successful**. Research CI uses local native filesystems only.

## Verdict by trust model

| Actor and capability | Writer exclusivity | Disposition |
| --- | --- | --- |
| RevisionWorx clients/releasers/administrators using supported APIs, allowed to pause or crash between instructions | Tested positive for two new orphan-fencing crash windows and conflicting administrative recovery; earlier QA/DS tests cover additional cases | **Narrow positive acceptance for the tested interleavings at the exact SHA**; not a universal correctness proof |
| Separate process with permission to rename or delete `writer.lock` or substitute a file at `.fencing-*`/`.clearing-*` | Demonstrated unsafe by QA: two distinct records become journal-effective while a writer's token was never revoked | **REJECT any claim of authority against that actor**; issue is not closed by the documented threat-model correction |
| Power loss, remote SMB/NFS/host crash or a malicious writer with journal-edit capability | Not tested by this run | **Unverified; no release or multi-host claim** |

## Protocol-only challenge

The independent suite first pauses a real writer B *after* its `Workspace.commit()` preflight and before it publishes. A previously interrupted releaser has left readable orphan A, and a normal release transition has displaced B's writer lock to a second `.releasing-*` file. A successor C attempts acquisition.

**Crash immediately after `.releasing-A -> .fencing-A`:** Fault injection executes the actual custody rename and then raises `SystemExit`. C's failed acquisition releases its own tentative lock. A's `.fencing-*` and B's `.releasing-*` persist. A later C acquisition revokes both original tokens before its commit. B's resumed commit is rejected (`LockLostError`), and only C's record is authoritative.

**Crash immediately after revocation of A, before evidence archival:** The old A revocation is durable and its `.fencing-*` remains; B's orphan is still visible. After restart the scanner does not duplicate A's revocation, revokes B exactly once, and C commits. B cannot publish an effective record.

No test substitutes a live writer lock at a private custody pathname: that requires an external namespace writer, not one of these protocol transitions. This separation is material; labeling an adversarial outside rename as a protocol-only interleaving would misstate the requirement.

## Recovery request identity

The independent conflict tests force an interruption **after** the `workspace.orphan.clear` authorization is durably published but **before** custody acquisition. A second reason is rejected with `OrphanClearConflict(outcome="pending")`. The same reason but a different user/host is also rejected. The original administrator's identical retry completes, and another retry returns the same logical result. Exactly one authorization, one terminal result and one archived evidence file remain. A late different-reason request after completion raises `OrphanClearConflict(outcome="completed")`.

Two additional exact-source tests verify **reincarnation identity** (recreated identical bytes at the same orphan pathname generate a different `clear_id` and two distinct authorized recoveries), and **post-archive forensic verification** (`Workspace.verify()` reports `orphan-evidence-altered` after changed bytes and `orphan-evidence-missing` after removal). This is detection, not tamper resistance.

The `user@host` actor is **self-reported**, not cryptographically authenticated. Request identity prevents accidental conflation among cooperative clients, not malicious impersonation.

## Director decision required: trust boundary

The two QA/Codex unsafe counterexamples against `e5b38a6` are valid *under their explicitly injected external pathname-write capability*. The baseline counterexample also works without orphan recovery: a script that renames a live `writer.lock` lets C acquire while B is paused past preflight. A check-before-unlink or inode check cannot provide an atomic compare-and-delete against such a writer. A hash of archived bytes protects forensic association, not the live single-writer grant.

Choose and document one deployment contract:

1. **Trusted local folder discipline (minimal change).** Require that all processes modifying `.revisionworx/locks/` or journal authority files use supported RevisionWorx APIs, and that incompatible clients/manual file moves are prohibited. Advertise that OS users or scripts with direct write access can violate exclusivity. This is a bounded operational safety claim, not hostile-filesystem security.
2. **Stronger isolation (architectural change).** Place writer authority and journal publication behind an OS identity or trusted service inaccessible to ordinary clients. Note that ACLs on the same user's folder do **not** distinguish RevisionWorx from another process running under that same account. A dedicated service identity and permission design would be needed.
3. **Coordinator-backed authority (ADR-014, not approved).** A trusted authority grants monotonic epochs/generations and atomically checks them at commit publication. This changes operational requirements and needs Director approval and host-failure qualification; it must not be silently introduced.

A manual clear of tokenless corrupt evidence still depends on **independent operator confirmation that the unknown previous writer has stopped**. A future globally effective epoch/barrier could strengthen this, but the current implementation does not contain that mechanism.

## Engineering recommendation

- **Do not suppress or mark QA's outside-actor P1 proofs as false.** They demonstrate a concrete guarantee boundary on Windows and Linux, not necessarily an additional in-scope cooperative-client bug.
- **Do not assert blanket ADR-019 acceptance** until the Director selects an actor/trust model. The independent testing supports a narrow protocol-only claim at `d2b7a1c`, not adversarial direct-file mutation.
- Keep `phase1-backport-8-9-10` (`36d1957`) **REJECT/HOLD**. It has a separate demonstrated successor-lock deletion race. Require a new isolated candidate SHA and native Windows/Linux acceptance.
- No physical dual-host SMB/NFS, power-loss, malicious local filesystem, installed GUI or independent proof of operator stop was tested here.

Only the research branch is changed; no production core, merge, release, retarget or coordinator deployment is authorized.
