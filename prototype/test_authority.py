"""Reproducible local SQLite / independent-process authority tests; NOT share certification."""
import multiprocessing as mp
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prototype.authority import Authority, Fenced


def contender(path, gate, queue, name):
    authority = Authority(path)
    gate.wait()
    try:
        queue.put(("won", name, authority.acquire("drawing", name)))
    except Fenced:
        queue.put(("lost", name, None))


def publisher(path, ready, proceed, queue, name, generation):
    authority = Authority(path)
    ready.set()
    proceed.wait(20)
    try:
        queue.put(("committed", authority.publish("drawing", name, generation,
                                                 "tx-from-paused-worker", "data")))
    except Fenced:
        queue.put(("fenced", None))


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "authority.db"
        self.authority = Authority(self.path)

    def test_revoked_active_writer_cannot_publish(self):
        old = self.authority.acquire("drawing", "alice")
        self.assertGreater(self.authority.override("drawing", "operator recovery", "unverified actor"), old)
        with self.assertRaises(Fenced):
            self.authority.publish("drawing", "alice", old, "tx-old", "old")
        fresh = self.authority.acquire("drawing", "bob")
        self.assertGreater(fresh, old)
        self.assertEqual(self.authority.publish("drawing", "bob", fresh, "tx-new", "new"), "committed")

    def test_idempotency_and_replay_after_override(self):
        generation = self.authority.acquire("drawing", "alice")
        self.assertEqual(self.authority.publish("drawing", "alice", generation, "tx1", "data"), "committed")
        self.assertEqual(self.authority.publish("drawing", "alice", generation, "tx1", "data"), "already-committed")
        with self.assertRaises(Fenced):
            self.authority.publish("drawing", "alice", generation, "tx1", "changed")
        self.authority.override("drawing", "administrative recovery", "unverified actor")
        self.assertEqual(self.authority.publish("drawing", "alice", generation, "tx1", "data"), "already-committed")
        with self.assertRaises(Fenced):
            self.authority.publish("drawing", "alice", generation, "tx2", "new")
        self.assertEqual(self.authority.outcome("tx1")["state"], "committed")
        self.assertEqual(self.authority.outcome("tx2")["state"], "not-found")

    def test_process_contention(self):
        ctx = mp.get_context("spawn")
        gate, queue = ctx.Event(), ctx.Queue()
        ps = [ctx.Process(target=contender, args=(str(self.path), gate, queue, str(i)))
              for i in range(6)]
        for p in ps:
            p.start()
        gate.set()
        results = [queue.get(timeout=25) for _ in ps]
        for p in ps:
            p.join(25)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sum(x[0] == "won" for x in results), 1)

    def test_paused_writer_revoked_across_processes(self):
        generation = self.authority.acquire("drawing", "alice")
        ctx = mp.get_context("spawn")
        ready, proceed, queue = ctx.Event(), ctx.Event(), ctx.Queue()
        p = ctx.Process(target=publisher,
                        args=(str(self.path), ready, proceed, queue, "alice", generation))
        p.start()
        self.assertTrue(ready.wait(20))
        self.authority.override("drawing", "operator recovery", "unverified actor")
        proceed.set()
        self.assertEqual(queue.get(timeout=25)[0], "fenced")
        p.join(25)
        self.assertEqual(p.exitcode, 0)
        self.assertEqual(self.authority.outcome("tx-from-paused-worker")["state"], "not-found")

    def test_override_serializes_with_publication(self):
        generation = self.authority.acquire("drawing", "alice")
        self.authority.publish("drawing", "alice", generation, "tx-before", "old", expected_revision=0)
        self.authority.override("drawing", "operator recovery", "unverified actor")
        with self.assertRaises(Fenced):
            self.authority.publish("drawing", "alice", generation, "tx-after", "new", expected_revision=1)
        self.assertEqual(self.authority.outcome("tx-before")["revision"], 1)
        self.assertEqual(self.authority.snapshot("drawing")[2], 1)

    def test_stale_record_precondition_rejected(self):
        generation = self.authority.acquire("drawing", "alice")
        self.authority.publish("drawing", "alice", generation, "tx1", "first", expected_revision=0)
        with self.assertRaises(Fenced):
            self.authority.publish("drawing", "alice", generation, "tx2", "second", expected_revision=0)
        self.assertEqual(self.authority.outcome("tx2")["state"], "not-found")

    def test_restart_persists_revocation_generation_and_audit(self):
        generation = self.authority.acquire("drawing", "alice")
        self.authority.override("drawing", "restart recovery", "operator-claim")
        recovered = Authority(self.path)
        with self.assertRaises(Fenced):
            recovered.publish("drawing", "alice", generation, "tx-old", "invalid")
        newer = recovered.acquire("drawing", "bob")
        self.assertGreater(newer, generation)
        self.assertEqual(recovered.audits("drawing"),
                         [(generation, generation+1, "alice", "restart recovery", "operator-claim")])

    def test_no_reason_rejected_without_revocation(self):
        generation = self.authority.acquire("drawing", "alice")
        with self.assertRaises(ValueError):
            self.authority.override("drawing", "", "actor")
        self.assertEqual(self.authority.snapshot("drawing")[:2], (generation, "alice"))

    def test_failed_commit_rolls_back_authority_revision(self):
        generation = self.authority.acquire("drawing", "alice")
        with self.assertRaises(sqlite3.IntegrityError):
            # txid uniqueness is checked before insert, so instead violate NOT NULL payload.
            self.authority.publish("drawing", "alice", generation, "bad", None)
        self.assertEqual(self.authority.snapshot("drawing")[2], 0)
        self.assertEqual(self.authority.outcome("bad")["state"], "not-found")

    def test_sequence_order_follows_serialized_publications(self):
        generation = self.authority.acquire("drawing", "alice")
        for i in range(5):
            self.authority.publish("drawing", "alice", generation, f"tx{i}", str(i),
                                   expected_revision=i)
        sequences = [self.authority.outcome(f"tx{i}")["sequence"] for i in range(5)]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(self.authority.snapshot("drawing")[2], 5)


if __name__ == "__main__":
    unittest.main()
