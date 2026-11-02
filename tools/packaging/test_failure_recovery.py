"""Disposable storage failure/recovery qualification against a PINNED Phase 2 source.

Only temporary workspaces are written. These are deterministic injected
local I/O errors, NOT a physical disk-full, power-loss, ACL, or SMB test.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

SOURCE = os.environ.get("REVISIONWORX_PHASE2_SOURCE", "")
if SOURCE:
    sys.path.insert(0, str(Path(SOURCE).resolve()))

@unittest.skipUnless(SOURCE, "requires pinned Phase 2 source checkout")
class StorageFailureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from revisionworx import atomicio
        from revisionworx.workspace import (Workspace, RecordPut,
                                            CommittedNotApplied, WorkspaceFormatError)
        cls.atomicio = atomicio
        cls.Workspace = Workspace
        cls.RecordPut = RecordPut
        cls.CommittedNotApplied = CommittedNotApplied
        cls.WorkspaceFormatError = WorkspaceFormatError

    def _writable(self, base: Path):
        root = base / "only-disposable-workspace"
        ws = self.Workspace.create(root, "Failure contract")
        seq = ws.head_seq()
        return root, ws, seq

    def test_precommit_io_denial_is_never_reported_as_committed(self):
        for code in (errno.ENOSPC, errno.EACCES):
            with self.subTest(error=code), TemporaryDirectory() as temp:
                root, ws, previous = self._writable(Path(temp))
                try:
                    target = root / ".revisionworx" / "journal"
                    original = self.atomicio.publish_exclusive
                    def fail_journal(path, data):
                        if Path(path).parent == target:
                            raise OSError(code, "injected precommit failure")
                        return original(path, data)
                    with patch.object(self.atomicio, "publish_exclusive", side_effect=fail_journal):
                        with self.assertRaises(OSError) as found:
                            ws.commit("test.precommit-failure",
                                      [self.RecordPut("project", "fault-probe", {"name": "NOT COMMITTED"})])
                    self.assertEqual(found.exception.errno, code)
                    self.assertEqual(ws.head_seq(), previous)
                    self.assertEqual(ws.applied_seq(), previous)
                    self.assertIsNone(ws.get("project", "fault-probe"))
                    self.assertFalse(ws.verify()["problems"])
                finally:
                    ws.close()
                with self.Workspace.open(root) as reader:
                    self.assertEqual(reader.head_seq(), previous)
                    self.assertIsNone(reader.get("project", "fault-probe"))
                print(f"PRECOMMIT_INJECTED_EVIDENCE errno={code} no_journal_publication=True")

    def test_postcommit_failure_survives_and_recovers_without_duplicate_write(self):
        with TemporaryDirectory() as temp:
            root, ws, previous = self._writable(Path(temp))
            try:
                with patch.object(ws, "_apply", side_effect=OSError(errno.ENOSPC, "simulated disk full")):
                    with self.assertRaises(self.CommittedNotApplied) as caught:
                        ws.commit("test.after-commit",
                                  [self.RecordPut("project", "fault-probe", {"name": "RECOVER ME"})],
                                  txid="a" * 32)
                self.assertEqual(caught.exception.seq, previous + 1)
                self.assertEqual(ws.head_seq(), previous + 1)
                self.assertEqual(ws.applied_seq(), previous)
                self.assertEqual(ws.transaction_outcome("a" * 32)["state"], "committed")
            finally:
                ws.close()
            with self.Workspace.open(root, writable=True) as recovered:
                self.assertEqual(recovered.head_seq(), previous + 1)
                self.assertEqual(recovered.applied_seq(), previous + 1)
                self.assertEqual(recovered.get("project", "fault-probe")["name"], "RECOVER ME")
                self.assertFalse(recovered.verify()["problems"])
            with self.Workspace.open(root) as reader:
                self.assertEqual(reader.head_seq(), previous + 1)
                self.assertEqual(reader.get("project", "fault-probe")["name"], "RECOVER ME")
            print("POSTCOMMIT_INJECTED_EVIDENCE durable_txid=True recovered_once=True")

    def test_future_workspace_format_fails_closed_without_modification(self):
        import hashlib
        import json
        with TemporaryDirectory() as temp:
            root, ws, _ = self._writable(Path(temp))
            ws.close()
            config = root / ".revisionworx" / "workspace.json"
            info = json.loads(config.read_text(encoding="utf-8"))
            info["format_version"] = 999
            config.write_text(json.dumps(info), encoding="utf-8")
            digest = hashlib.sha256(config.read_bytes()).hexdigest()
            with self.assertRaises(self.WorkspaceFormatError):
                self.Workspace.open(root, writable=True)
            self.assertEqual(hashlib.sha256(config.read_bytes()).hexdigest(), digest)
            self.assertFalse((root / ".revisionworx" / "locks" / "writer.lock").exists())
            print("FUTURE_FORMAT_INJECTED_EVIDENCE refused=True unchanged=True")


if __name__ == "__main__":
    unittest.main()
