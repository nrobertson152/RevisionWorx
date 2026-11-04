"""Deterministic Phase 1 LocalIndex.rebuild snapshot-watermark probe.

Use REVISIONWORX_PHASE1_CHECKOUT pointing to an actual phase1-foundation checkout.
A pass records that the current implementation mixes a head snapshot and newer
materialized records; this test deliberately documents a defect, not a guarantee.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

PHASE1 = os.environ.get("REVISIONWORX_PHASE1_CHECKOUT")


@unittest.skipUnless(PHASE1, "requires actual phase1-foundation checkout")
class Phase1CacheWatermarkProbe(unittest.TestCase):
    def test_rebuild_can_contain_record_newer_than_stored_cursor(self):
        source = Path(PHASE1).expanduser().resolve()
        if not (source / "revisionworx" / "index.py").is_file():
            self.fail("REVISIONWORX_PHASE1_CHECKOUT does not contain revisionworx/index.py")
        sys.path.insert(0, str(source))
        from revisionworx.workspace import Workspace, RecordPut, new_id
        from revisionworx.index import LocalIndex
        self.assertEqual(Path(sys.modules[LocalIndex.__module__].__file__).resolve(),
                         source / "revisionworx" / "index.py")

        with tempfile.TemporaryDirectory() as td:
            with Workspace.create(Path(td) / "workspace", "index-rebuild-probe") as ws:
                with LocalIndex(ws, cache_dir=Path(td) / "cache") as idx:
                    original_head = ws.head_seq
                    before = original_head()
                    new_root = new_id("root")
                    injected = False

                    def capture_then_concurrent_commit():
                        nonlocal injected
                        seq = original_head()
                        if not injected:
                            injected = True
                            ws.head_seq = original_head
                            ws.commit("test.root.after.head.snapshot", [
                                RecordPut("root", new_root,
                                          {"id": new_root, "name": "newer than cursor"},
                                          expect_rev=0)])
                        return seq

                    try:
                        ws.head_seq = capture_then_concurrent_commit
                        self.assertEqual(idx.rebuild(), before)
                    finally:
                        ws.head_seq = original_head

                    self.assertTrue(injected)
                    self.assertEqual(ws.head_seq(), before + 1)
                    self.assertEqual(idx.cursor_seq(), before)
                    self.assertEqual(idx.db.execute("SELECT name FROM roots WHERE id=?",
                                                    (new_root,)).fetchone()[0], "newer than cursor")


if __name__ == "__main__":
    unittest.main()
