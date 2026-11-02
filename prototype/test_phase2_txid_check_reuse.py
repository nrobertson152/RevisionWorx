"""Cross-check ADR-017 txid request identity on actual Phase 2 implementation.

The exact ADR-017 contract promises that only *identical content* may reuse a
txid. RecordCheck is an input to the command but is omitted from the Phase 2
journal record list and idempotency comparison. The altered-RecordCheck test
is intentionally an expected failure until Claude resolves this contract gap.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

PHASE2 = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(PHASE2, "needs actual phase2-documents checkout")
class Phase2TxidRequestIdentity(unittest.TestCase):
    def _workspace(self):
        source = Path(PHASE2).resolve()
        sys.path.insert(0, str(source))
        from revisionworx.workspace import (Workspace, RecordPut, RecordCheck,
                                            TxidReuseError)
        self.assertEqual(Path(sys.modules[Workspace.__module__].__file__).resolve(),
                         source / "revisionworx" / "workspace.py")
        return Workspace, RecordPut, RecordCheck, TxidReuseError

    def test_identical_txid_and_identical_preconditions_are_idempotent(self):
        Workspace, RecordPut, RecordCheck, _ = self._workspace()
        with tempfile.TemporaryDirectory() as directory:
            with Workspace.create(Path(directory) / "ws", "SameTransaction") as ws:
                rid = "prj_" + "e" * 32
                ops = [RecordCheck("settings", "workspace", 1),
                       RecordPut("project", rid, {"name": "A"}, expect_rev=0)]
                first = ws.commit("project.create", ops, txid="1" * 32)
                self.assertEqual(ws.commit("project.create", ops, txid="1" * 32),
                                 first)
                self.assertEqual(ws.transaction_outcome("1" * 32),
                                 {"state": "committed", "seq": first})

    @unittest.expectedFailure
    def test_altered_recordcheck_with_same_txid_must_not_count_as_same_request(self):
        """EXPECTED FAILURE: checks omitted by current idempotency payload."""
        Workspace, RecordPut, RecordCheck, TxidReuseError = self._workspace()
        with tempfile.TemporaryDirectory() as directory:
            with Workspace.create(Path(directory) / "ws", "ChangedCheck") as ws:
                rid = "prj_" + "f" * 32
                content = RecordPut("project", rid, {"name": "A"}, expect_rev=0)
                txid = "2" * 32
                seq = ws.commit("project.create",
                                [RecordCheck("settings", "workspace", 1), content],
                                txid=txid)
                with self.assertRaises(TxidReuseError):
                    ws.commit("project.create",
                              [RecordCheck("settings", "workspace", 999), content],
                              txid=txid)
                self.assertEqual(ws.transaction_outcome(txid),
                                 {"state": "committed", "seq": seq})


if __name__ == "__main__":
    unittest.main()
