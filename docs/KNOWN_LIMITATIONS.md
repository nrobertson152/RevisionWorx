# RevisionWorx 1.0: known limitations

Stated plainly so you can decide how to use RevisionWorx with your data. Each item names the evidence or the
gap behind it.

## Where RevisionWorx is verified

- **Local disks on Windows and Linux.** The locking, journal, crash-recovery and fencing tests run on real
  Windows (NTFS) and Linux in CI, with real separate processes, killed processes at every file operation, and
  real Windows sharing violations.
- **Several sessions on one computer** (windows and command-line runs together): one writer at a time, enforced.

## Not verified

1. **Several computers writing to one shared network folder (SMB/CIFS, NFS).** The protocol relies on
   exclusive file creation, rename and directory listing behaving on the share as they do on a local disk. That
   has not been tested across real computers and servers, so RevisionWorx makes no multi-computer safety claim.
   You may keep a workspace in a network folder and open it from several computers to *read*, but let only one
   computer *make changes* at a time. RevisionWorx shows a warning when it detects a network folder (on Linux
   from the mount table; on Windows for mapped network drives and `\\server\share` paths). Detection can miss
   unusual setups, so the rule applies even without the warning. A server-side coordinator for true
   multi-user use is a future design (ADR-014, issue #5).
2. **macOS** is not tested or supported.
3. **The desktop window has not yet been accepted by a person** following `docs/GUI_ACCEPTANCE.md`. It is
   exercised automatically (offscreen widget tests on Windows and Linux, a keystroke-driven walkthrough on a real
   X display, and a packaged self-test), which does not replace a person judging that it is usable.

## By design, within the 1.0 threat model

4. **Other programs must not change files inside `.revisionworx`.** RevisionWorx protects its data against
   crashes at any point and against other RevisionWorx sessions. It cannot defend against a program that
   renames or deletes its lock or journal files while another session is working; no file-only lock can
   (ADR-019 rev. 3, pinned by `tests/test_lock_namespace_threat_model.py`). A hidden metadata folder is not a
   security boundary: use operating-system and share permissions to control who can write.
5. **A revoked writer's late journal entry can remain on disk.** After an administrator breaks a writer's lock,
   that writer is refused (`LockLostError`) and anything it writes afterwards is fenced: ignored by every reader,
   by recovery and by the index. The fenced file itself can still exist; `revisionworx verify` reports it
   (ADR-015 rev. 2).
6. **RevisionWorx does not edit CAD references.** Native Inventor/SolidWorks files are versioned byte-for-byte
   and never rewritten. Reference-aware copy/rename of assemblies is not part of 1.0.

## Not in 1.0

- Part and BOM records inside the workspace are available through the Python API (`revisionworx.bom`); the
  `revisionworx bom` command works on BOM **files**. There is no desktop screen for BOMs or change control yet:
  use `revisionworx change ...`.
- Where Used / Before You Revise across projects, CAD reference extraction, OpenSchematix import.
- Built-in backup and restore commands (see "Backups" in the [user guide](USER_GUIDE.md)).
- An installer. The standalone builds are folders you unpack. They carry the official icon; on Linux,
  `install-desktop-entry.sh` in the folder adds RevisionWorx to your application menu.
