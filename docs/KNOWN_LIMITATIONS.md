# RevisionWorx 1.0: known limitations

Stated plainly so you can decide how to use RevisionWorx with your data. Each item names the evidence or the
gap behind it.

## Where RevisionWorx is verified

- **Local disks on Windows and Linux.** The locking, journal, crash-recovery and fencing tests run on real
  Windows (NTFS) and Linux in CI, with real separate processes, killed processes at every file operation, and
  real Windows sharing violations.
- **Several sessions on one computer** (windows and command-line runs together): one writer at a time, enforced.

## Not verified

1. **A team working from one shared network folder (SMB/CIFS, NFS), across real computers.** A workspace can be set up for a team
   (ADR-024): each person logs in, and they take turns saving changes through the folder, with no server. Its design doesn't rely on
   fresh directory listings, clocks or locks, but it does rely on the file server deciding who gets a name, and it has **not been tested
   between real computers and servers**. Tested so far: real processes on one disk, simulated client caches, and a real SMB share on
   one Windows machine, where every client shares one set of caches. To find out about your share, run `revisionworx share test FOLDER`
   at the same time on two or more computers, then `revisionworx share report FOLDER`. Please report what you find, either way.

   Without team setup, the rule stays: you may keep a workspace in a network folder and open it from several computers to *read*, but let
   only one computer *make changes* at a time. RevisionWorx shows a warning when it detects a network folder (on Linux from the
   mount table; on Windows for mapped network drives and `\\server\share` paths). Detection can miss unusual setups, so the rule
   applies even without the warning.
2. **macOS** is not tested or supported.
3. **The desktop window has not yet been accepted by a person** following `docs/GUI_ACCEPTANCE.md`. It is
   exercised automatically (offscreen widget tests on Windows and Linux, a keystroke-driven walkthrough on a real
   X display, and a packaged self-test), which does not replace a person judging that it is usable.

## By design, in a team workspace

- **A shared folder can't enforce anything against someone who can write to it.** There's no server to refuse them. Every change is
  signed, and every computer checks every signature and the rules, so nobody can make themselves an administrator, act in someone else's
  name or forge an approval. But anyone with write access can delete or damage files. RevisionWorx notices and stops accepting changes,
  and `revisionworx team repair` can put back journal entries from any computer that has read them. It can't prevent it, and it can't
  bring back a deleted drawing's content. Use folder permissions, and keep backups.
- **Anyone who can read the folder can try to guess passwords on their own computer.** Passwords are stretched hard, and an
  administrator's key also needs a secret kept on their computer, but a weak password can be guessed. Choose long ones.
- **Checkout doesn't stop anyone editing a file.** As ever, RevisionWorx detects edits made without a checkout, and doesn't prevent them.
- **There are no per-project permissions.** An account is an administrator or a regular user, and every regular user can do the same things.
- **It can't be undone**, and older versions of RevisionWorx can't open the workspace afterwards. Make a backup first.
- **Saves take turns.** A long check-in holds everyone else's saves until it finishes. Reading is never held up.
- **If the computer that has the only administrator account is lost and the recovery code is too,** nobody can administer accounts any
  more. Everyone can still work.

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
