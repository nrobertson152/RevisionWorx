"""Actual deployed ADR-021 digest versus research canonical serialization.

Source is a pinned Phase 2 checkout, not production multiuser or SMB.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
from prototype.request_identity_research import request_digest

SOURCE = os.environ.get("REVISIONWORX_PHASE2_CHECKOUT")


@unittest.skipUnless(SOURCE, "requires real Phase 2 source")
class DeployedIdentityInterop(unittest.TestCase):
    def setUp(self):
        root = Path(SOURCE).resolve()
        sys.path.insert(0, str(root))
        import revisionworx.workspace as w
        self.assertEqual(Path(w.__file__).resolve(), root / "revisionworx" / "workspace.py")
        self.w = w

    def compare(self, op, operations, blobs=None):
        items = []
        for o in operations:
            typ = type(o).__name__.removeprefix("Record").lower()
            data = {"type": typ, "kind": o.kind, "id": o.id, "expect_rev": o.expect_rev}
            if typ == "put":
                data["data"] = o.data
            items.append(data)
        research = request_digest(op, items, blobs)
        self.assertEqual(research, self.w.request_identity(op, operations, blobs))
        return research

    def test_complete_order_and_check_only(self):
        w = self.w
        a = w.RecordCheck("project", "prj_" + "a" * 32, 1)
        b = w.RecordPut("project", "prj_" + "b" * 32, {"name": "A"}, None)
        c = w.RecordDelete("project", "prj_" + "c" * 32, 0)
        self.assertNotEqual(self.compare("publish", [a, b, c]),
                            self.compare("publish", [b, a, c]))
        self.compare("verify", [a])

    def test_unicode_rev_stripping_blob_order_and_null(self):
        w = self.w
        p = w.RecordPut("project", "prj_" + "d" * 32,
                        {"文字": "北京", "name": "é", "_rev": 999}, 0)
        cleaned = w.RecordPut("project", "prj_" + "d" * 32,
                              {"name": "é", "文字": "北京"}, 0)
        self.assertEqual(self.compare("更新", [p], ["x", "y"]),
                         self.compare("更新", [cleaned], ["x", "y"]))
        self.assertNotEqual(self.compare("更新", [p], ["y", "x"]),
                            self.compare("更新", [p], ["x", "y"]))
        self.assertNotEqual(self.compare("write", [w.RecordPut("project", p.id, {}, None)]),
                            self.compare("write", [w.RecordPut("project", p.id, {}, 0)]))

    def test_authoritative_journal_stores_identical_digest(self):
        w = self.w
        with tempfile.TemporaryDirectory() as d:
            with w.Workspace.create(Path(d) / "ws", "Interop") as ws:
                put = w.RecordPut("project", "prj_" + "b" * 32, {"v": 1}, 0)
                checks = [w.RecordCheck("settings", "workspace", 1), put]
                txid = "e" * 32
                seq = ws.commit("create", checks, txid=txid)
                self.assertEqual(ws.read_entry(seq)["request"],
                                 {"version": 2, "digest": self.compare("create", checks)})
                self.assertEqual(ws.commit("create", checks, txid=txid), seq)
                with self.assertRaises(w.TxidReuseError):
                    ws.commit("create", list(reversed(checks)), txid=txid)
                self.assertEqual(ws.transaction_outcome(txid),
                                 {"state": "committed", "seq": seq})

    def test_legacy_txid_classifies_identity_unverifiable(self):
        w = self.w
        from revisionworx import atomicio
        from revisionworx.authority import classify
        with tempfile.TemporaryDirectory() as d:
            with w.Workspace.create(Path(d) / "ws", "Legacy") as ws:
                put = w.RecordPut("project", "prj_" + "c" * 32, {"v": 1}, 0)
                seq = ws.head_seq() + 1
                txid = "f" * 32
                old = {"seq": seq, "txid": txid, "time": w.now_iso(),
                       "actor": w.current_actor(), "op": "legacy",
                       "records": [put.as_json()], "blobs": [],
                       "writer_token": ws._lock_token}
                old["digest"] = w.entry_digest(old)
                atomicio.publish_exclusive(ws.meta_dir / "journal" / f"{seq:020d}.json",
                                           atomicio.dumps_json(old))
                ws.recover()
                with self.assertRaises(w.IdentityUnverifiable) as err:
                    ws.commit("legacy", [put], txid=txid)
                self.assertEqual(classify(err.exception), "identity-unverifiable")
                self.assertEqual((err.exception.txid, err.exception.seq), (txid, seq))
                self.assertEqual(ws.transaction_outcome(txid),
                                 {"state": "committed", "seq": seq})
                self.assertEqual(ws.head_seq(), seq)


if __name__ == "__main__":
    unittest.main()
