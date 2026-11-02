"""Isolated batch-commit and reconnect research tests; NO physical SMB or live service."""
from contextlib import closing
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prototype.authority import Authority, Fenced
from prototype.immutable_manifest_research import MissingContent
from prototype.record_batch_research import BatchAuthorityResearch
from prototype.witnessed_coordinator import TestIdentityProvider

_USERS = {"alice": ("alice-password", "engineer"),
          "bob": ("bob-password", "engineer"),
          "admin": ("admin-password", "administrator")}
_KEY = b"research-batch-only-hmac-fixture-key"


def _commit_worker(db, guard, witness, objects, credential, token, txid, gate, q):
    identities = TestIdentityProvider(_USERS, _KEY)
    model = BatchAuthorityResearch(db, guard, witness, objects, identities)
    gate.wait(20)
    try:
        response = model.publish_batch(
            credential, token, txid,
            [{"type": "put", "kind": "document", "id": "docA", "expect_rev": 0,
              "data": {"name": txid}}],
            expected_revision=0)
        q.put(response["state"])
    except Fenced:
        q.put("fenced")


def _crash_batch(db, guard, witness, objects, credential, token, checkpoint):
    identities = TestIdentityProvider(_USERS, _KEY)
    model = BatchAuthorityResearch(db, guard, witness, objects, identities)
    operations = [{"type": "put", "kind": "document", "id": "docA",
                   "expect_rev": 0, "data": {"name": "crash"}}]
    if checkpoint == "before":
        model.publish_batch(credential, token, "b" * 32, operations,
                            expected_revision=0, before_commit=lambda: os._exit(61))
    else:
        model.publish_batch(credential, token, "c" * 32, operations,
                            expected_revision=0)
        os._exit(62)


class BatchAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db, self.guard = root / "authority.db", root / "epoch"
        self.witness, self.objects = root / "witness", root / "objects"
        self.identities = TestIdentityProvider(_USERS, _KEY)
        self.alice = self.identities.authenticate("alice", "alice-password")
        self.bob = self.identities.authenticate("bob", "bob-password")
        self.admin = self.identities.authenticate("admin", "admin-password")
        self.model = BatchAuthorityResearch(
            self.db, self.guard, self.witness, self.objects, self.identities)
        self.lease = self.model.acquire("drawing", self.alice)

    def restart(self):
        return BatchAuthorityResearch(self.db, self.guard, self.witness,
                                      self.objects, self.identities)

    def put(self, kind, rid, data, expected=0):
        return {"type": "put", "kind": kind, "id": rid, "expect_rev": expected,
                "data": data}

    def test_multirecord_put_check_delete_single_sequence(self):
        first = self.model.publish_batch(
            self.lease, self.alice, "1" * 32,
            [self.put("document", "docA", {"status": "checked-out"}),
             self.put("checkout", "docA", {"owner": "alice"})],
            expected_revision=0)
        self.assertEqual(first, {"state": "committed", "sequence": 1, "txid": "1" * 32})
        result = self.model.publish_batch(
            self.lease, self.alice, "2" * 32,
            [{"type": "check", "kind": "document", "id": "docA", "expect_rev": 1},
             self.put("document", "docA", {"status": "committed"}, expected=1),
             self.put("revision", "rev1", {"document_id": "docA"}),
             {"type": "delete", "kind": "checkout", "id": "docA", "expect_rev": 1}],
            expected_revision=1)
        self.assertEqual(result["sequence"], 2)
        data = self.model.snapshot_batch()
        self.assertEqual(data["high_watermark"], 2)
        self.assertEqual(data["records"][("document", "docA")],
                         {"revision": 2, "data": {"status": "committed"}})
        self.assertEqual(data["records"][("revision", "rev1")]["revision"], 2)
        self.assertEqual(data["records"][("checkout", "docA")],
                         {"revision": 2, "data": None})
        self.assertEqual(self.model.outcome("2" * 32, self.alice)["sequence"], 2)

    def test_record_conflict_rejects_entire_batch(self):
        self.model.publish_batch(self.lease, self.alice, "1" * 32,
                                 [self.put("document", "docA", {"name": "initial"})],
                                 expected_revision=0)
        with self.assertRaises(Fenced):
            self.model.publish_batch(
                self.lease, self.alice, "2" * 32,
                [self.put("revision", "rev2", {"name": "new"}),
                 self.put("document", "docA", {"name": "stale"}, expected=0)],
                expected_revision=1)
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 1)
        self.assertNotIn(("revision", "rev2"),
                         self.model.snapshot_batch()["records"])
        self.assertEqual(self.model.outcome("2" * 32, self.alice)["state"], "not-found")

    def test_stale_resource_version_rejects_batch(self):
        self.model.publish_batch(self.lease, self.alice, "1" * 32,
                                 [self.put("document", "docA", {"name": "initial"})],
                                 expected_revision=0)
        with self.assertRaises(Fenced):
            self.model.publish_batch(self.lease, self.alice, "2" * 32,
                                     [self.put("revision", "rev2", {"name": "new"})],
                                     expected_revision=0)
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 1)

    def test_lost_ack_idempotency_and_replay_payload_frozen(self):
        ops = [self.put("document", "docA", {"name": "A"})]
        first = self.model.publish_batch(self.lease, self.alice, "1" * 32, ops,
                                         expected_revision=0)
        fresh = self.restart()
        again = fresh.publish_batch(self.lease, self.alice, "1" * 32, ops,
                                    expected_revision=0)
        self.assertEqual(again["state"], "already-committed")
        self.assertEqual(again["sequence"], first["sequence"])
        with self.assertRaises(Fenced):
            fresh.publish_batch(self.lease, self.alice, "1" * 32,
                                [self.put("document", "docA", {"name": "B"})],
                                expected_revision=0)
        self.assertEqual(fresh.snapshot_batch()["high_watermark"], 1)

    def test_revocation_fences_prepared_batch(self):
        self.model.override("drawing", self.lease["generation"],
                            "user abandoned session", self.admin)
        with self.assertRaises(Fenced):
            self.model.publish_batch(
                self.lease, self.alice, "1" * 32,
                [self.put("document", "docA", {"name": "stale"})],
                expected_revision=0)
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 0)

    def test_immutable_blobs_and_quarantine_on_corruption(self):
        part = self.model.stage(b"immutable CAD source")
        self.model.publish_batch(
            self.lease, self.alice, "1" * 32,
            [self.put("revision", "revA", {"sha256": part["sha256"]})],
            expected_revision=0, manifest={"cad/a.step": part})
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 1)
        location = self.objects / part["sha256"][:2] / part["sha256"]
        location.write_bytes(b"tampered")
        with self.assertRaises(Fenced) as caught:
            self.model.snapshot_batch()
        self.assertIn("quarantined", str(caught.exception))
        with self.assertRaises(Fenced):
            self.model.publish_batch(self.lease, self.alice, "2" * 32,
                                     [self.put("revision", "revB", {})],
                                     expected_revision=1)
        # A restarted process must also detect corruption during its first read.
        with self.assertRaises(Fenced):
            self.restart().snapshot_batch()

    def test_missing_blob_rejected_before_commit(self):
        part = self.model.stage(b"not committed")
        (self.objects / part["sha256"][:2] / part["sha256"]).unlink()
        with self.assertRaises(MissingContent):
            self.model.publish_batch(
                self.lease, self.alice, "1" * 32,
                [self.put("revision", "revA", {})], expected_revision=0,
                manifest={"cad/a.step": part})
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 0)

    def test_disconnect_reconnect_fail_closed_and_cursor_modes(self):
        self.model.publish_batch(self.lease, self.alice, "1" * 32,
                                 [self.put("document", "docA", {"name": "A"})],
                                 expected_revision=0)
        epoch = self.lease["epoch"]
        self.model.disconnect()
        with self.assertRaises(Fenced):
            self.model.snapshot_batch()
        with self.assertRaises(Fenced):
            self.model.publish_batch(self.lease, self.alice, "2" * 32,
                                     [self.put("revision", "revA", {})],
                                     expected_revision=1)
        result = self.model.reconnect(epoch, 0)
        self.assertEqual(result["resync"], "incremental")
        self.assertEqual(self.model.reconnect(epoch, 1)["resync"], "current")
        self.assertEqual(self.model.reconnect("older-epoch", 0)["resync"], "full")
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 1)

    def test_cursor_ahead_quarantines_reconnect(self):
        self.model.disconnect()
        with self.assertRaises(Fenced):
            self.model.reconnect(self.lease["epoch"], 999)
        with self.assertRaises(Fenced):
            self.model.reconnect(self.lease["epoch"], 0)

    def test_concurrent_contenders_single_batch_commit(self):
        ctx = mp.get_context("spawn")
        gate = ctx.Event()
        q = ctx.Queue()
        args = [str(x) for x in (self.db, self.guard, self.witness, self.objects)]
        procs = [ctx.Process(target=_commit_worker,
                             args=(*args, self.lease, self.alice, f"{i + 1:032x}", gate, q))
                 for i in range(2)]
        for proc in procs:
            proc.start()
        gate.set()
        results = [q.get(timeout=30) for _ in procs]
        for proc in procs:
            proc.join(30)
            self.assertEqual(proc.exitcode, 0)
        self.assertCountEqual(results, ["committed", "fenced"])
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 1)

    def test_crash_after_witness_before_db_commit_quarantines(self):
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_crash_batch,
                           args=(str(self.db), str(self.guard), str(self.witness),
                                 str(self.objects), self.lease, self.alice, "before"))
        proc.start()
        proc.join(30)
        self.assertEqual(proc.exitcode, 61)
        self.assertEqual(Authority(self.db).outcome("b" * 32)["state"], "not-found")
        with self.assertRaises(Fenced):
            self.restart()

    def test_crash_after_commit_before_ack_is_recoverable(self):
        ctx = mp.get_context("spawn")
        proc = ctx.Process(target=_crash_batch,
                           args=(str(self.db), str(self.guard), str(self.witness),
                                 str(self.objects), self.lease, self.alice, "after"))
        proc.start()
        proc.join(30)
        self.assertEqual(proc.exitcode, 62)
        fresh = self.restart()
        self.assertEqual(fresh.outcome("c" * 32, self.alice)["state"], "committed")
        self.assertEqual(fresh.snapshot_batch()["high_watermark"], 1)

    def test_validation_rejects_duplicate_writes_and_invalid_revisions(self):
        with self.assertRaises(ValueError):
            self.model.publish_batch(
                self.lease, self.alice, "1" * 32,
                [self.put("document", "docA", {"name": "A"}),
                 self.put("document", "docA", {"name": "B"})],
                expected_revision=0)
        with self.assertRaises(ValueError):
            self.model.publish_batch(
                self.lease, self.alice, "1" * 32,
                [self.put("document", "docA", {"name": "A"}, expected=True)],
                expected_revision=0)
        with self.assertRaises(ValueError):
            self.model.publish_batch(
                self.lease, self.alice, "1" * 32,
                [{"type": "check", "kind": "document", "id": "docA", "expect_rev": 0}],
                expected_revision=0)
        self.assertEqual(self.model.snapshot_batch()["high_watermark"], 0)


if __name__ == "__main__":
    unittest.main()
