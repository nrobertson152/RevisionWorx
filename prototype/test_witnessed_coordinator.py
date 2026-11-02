"""Deterministic research tests for external witness, simulated identity and rollback.

These are LOCAL filesystem + local SQLite tests. They cannot certify that witness
storage is nonrollbackable, Windows authentication, or SMB/power-loss semantics.
"""
from contextlib import closing
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prototype.authority import Authority, Fenced
from prototype.witnessed_coordinator import TestIdentityProvider, WitnessedCoordinatorResearch


_ACCOUNTS = {"alice": ("alice-password", "engineer"),
             "bob": ("bob-password", "engineer"),
             "operator": ("operator-password", "administrator")}
_KEY = b"isolated-test-only-signing-key-not-prod"


def _crash_worker(db, guard, ledger, token, checkpoint):
    identity = TestIdentityProvider(_ACCOUNTS, _KEY)
    model = WitnessedCoordinatorResearch(db, guard, ledger, identity)
    lease = model.snapshot("drawing")
    credential = {"resource": "drawing", "owner": "alice",
                  "generation": lease[0], "epoch": model.core.guard.epoch}

    def crash():
        os._exit(51)

    if checkpoint == "before":
        model.publish(credential, token, "crash-before", "uncommitted", expected_revision=0,
                      before_commit=crash)
    else:
        model.publish(credential, token, "crash-after", "committed", expected_revision=0)
        os._exit(52)


def _acquire_worker(db, guard, ledger, identity_token, gate, q):
    identity = TestIdentityProvider(_ACCOUNTS, _KEY)
    model = WitnessedCoordinatorResearch(db, guard, ledger, identity)
    gate.wait(20)
    try:
        credential = model.acquire("drawing", identity_token)
        q.put(("won", credential["generation"]))
    except Fenced:
        q.put(("fenced", None))


class WitnessedCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "authority.db"
        self.guard = self.root / "epoch"
        self.ledger = self.root / "separate-test-medium" / "witness"
        self.idp = TestIdentityProvider(_ACCOUNTS, _KEY)
        self.alice = self.idp.authenticate("alice", "alice-password")
        self.bob = self.idp.authenticate("bob", "bob-password")
        self.admin = self.idp.authenticate("operator", "operator-password")
        self.co = WitnessedCoordinatorResearch(self.db, self.guard, self.ledger, self.idp)

    def restart(self):
        return WitnessedCoordinatorResearch(self.db, self.guard, self.ledger, self.idp)

    def test_credential_authentication_and_role_enforcement(self):
        with self.assertRaises(Fenced):
            self.idp.authenticate("operator", "wrong-password")
        with self.assertRaises(Fenced):
            self.idp.verify(self.alice, administrator=True)
        with self.assertRaises(Fenced):
            self.idp.verify(self.admin + "tampered", administrator=True)
        with self.assertRaises(ValueError):
            self.idp.authenticate("alice", "alice-password", ttl_seconds=0)
        self.assertEqual(self.idp.verify(self.admin, administrator=True), "operator")

    def test_revoke_and_audited_admin_override(self):
        old = self.co.acquire("drawing", self.alice)
        with self.assertRaises(Fenced):
            self.co.override("drawing", old["generation"], "not allowed", self.alice)
        with self.assertRaises(ValueError):
            self.co.override("drawing", old["generation"], "", self.admin)
        newer = self.co.override("drawing", old["generation"], "abandoned edit", self.admin)
        self.assertGreater(newer, old["generation"])
        audit = self.co.audit("drawing", self.admin)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0][2:6], ("alice", "abandoned edit", "operator", "revoked"))
        self.assertIn("T", audit[0][6])
        with self.assertRaises(Fenced):
            self.co.publish(old, self.alice, "stale", "data", expected_revision=0)
        self.assertEqual(self.co.snapshot("drawing")[0], newer)
        second = self.co.acquire("drawing", self.bob)
        self.assertGreater(second["generation"], old["generation"])

    def test_stale_admin_override_is_audited_without_revoking_new_owner(self):
        first = self.co.acquire("drawing", self.alice)
        self.co.override("drawing", first["generation"], "recovery", self.admin)
        later = self.co.acquire("drawing", self.bob)
        with self.assertRaises(Fenced):
            self.co.override("drawing", first["generation"], "stale operator view", self.admin)
        self.assertEqual(self.co.snapshot("drawing")[:2],
                         (later["generation"], "bob"))
        audits = self.co.audit("drawing", self.admin)
        self.assertEqual(audits[-1][5], "rejected-stale-generation")
        self.assertEqual(audits[-1][0], later["generation"])
        self.assertEqual(audits[-1][1], later["generation"])

    def test_lease_owner_controls_release_and_publish(self):
        cred = self.co.acquire("drawing", self.alice)
        with self.assertRaises(Fenced):
            self.co.release(cred, self.bob)
        with self.assertRaises(Fenced):
            self.co.publish(cred, self.bob, "mismatch", "bad", expected_revision=0)
        self.assertEqual(self.co.publish(cred, self.alice, "ok", "data", expected_revision=0),
                         "committed")
        self.assertEqual(self.co.release(cred, self.alice), "released")
        with self.assertRaises(Fenced):
            self.co.publish(cred, self.alice, "late", "data", expected_revision=1)
        self.assertEqual(self.co.outcome("ok", self.alice)["state"], "committed")

    def test_replay_and_conflict_after_restart(self):
        cred = self.co.acquire("drawing", self.alice)
        self.assertEqual(self.co.publish(cred, self.alice, "tx1", "data", expected_revision=0),
                         "committed")
        fresh = self.restart()
        self.assertEqual(fresh.publish(cred, self.alice, "tx1", "data",
                                       expected_revision=0), "already-committed")
        with self.assertRaises(Fenced):
            fresh.publish(cred, self.alice, "tx1", "changed", expected_revision=0)
        with self.assertRaises(Fenced):
            fresh.publish(cred, self.alice, "tx2", "new", expected_revision=0)
        self.assertEqual(fresh.snapshot("drawing")[2], 1)

    def test_backup_restore_older_database_fails_closed(self):
        old = self.co.acquire("drawing", self.alice)
        backup = self.root / "backup.db"
        with closing(sqlite3.connect(self.db)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        self.co.override("drawing", old["generation"], "operator revoke", self.admin)
        with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(self.db)) as target:
            source.backup(target)
        with self.assertRaises(Fenced) as caught:
            self.restart()
        self.assertIn("diverged", str(caught.exception))
        # Even the old credential cannot publish via the witnessed authority.
        with self.assertRaises(Fenced):
            self.co.publish(old, self.alice, "invalid", "payload", expected_revision=0)

    def test_missing_and_tampered_witness_fail_closed(self):
        self.co.acquire("drawing", self.alice)
        newest = sorted(self.ledger.glob("*.json"))[-1]
        original = newest.read_text()
        newest.write_text(original.replace("state_digest", "tampered"))
        with self.assertRaises(Fenced):
            self.restart()

    def test_witness_removed_fails_closed(self):
        self.co.acquire("drawing", self.alice)
        for entry in self.ledger.glob("*.json"):
            entry.unlink()
        with self.assertRaises(Fenced):
            self.restart()

    def test_witness_before_sql_commit_crash_fails_closed(self):
        self.co.acquire("drawing", self.alice)
        ctx = mp.get_context("spawn")
        p = ctx.Process(target=_crash_worker, args=(str(self.db), str(self.guard),
                                                   str(self.ledger), self.alice, "before"))
        p.start()
        p.join(timeout=20)
        self.assertEqual(p.exitcode, 51)
        self.assertEqual(Authority(self.db).outcome("crash-before")["state"], "not-found")
        with self.assertRaises(Fenced):
            self.restart()

    def test_after_commit_crash_is_recoverable(self):
        self.co.acquire("drawing", self.alice)
        ctx = mp.get_context("spawn")
        p = ctx.Process(target=_crash_worker, args=(str(self.db), str(self.guard),
                                                   str(self.ledger), self.alice, "after"))
        p.start()
        p.join(timeout=20)
        self.assertEqual(p.exitcode, 52)
        fresh = self.restart()
        self.assertEqual(fresh.outcome("crash-after", self.alice)["state"], "committed")

    def test_two_processes_acquire_only_one_lease(self):
        ctx = mp.get_context("spawn")
        gate = ctx.Event()
        q = ctx.Queue()
        ps = [ctx.Process(target=_acquire_worker,
                          args=(str(self.db), str(self.guard), str(self.ledger),
                                self.alice if i == 0 else self.bob, gate, q))
              for i in range(2)]
        for p in ps:
            p.start()
        gate.set()
        results = [q.get(timeout=25) for _ in ps]
        for p in ps:
            p.join(25)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sum(item[0] == "won" for item in results), 1)
        self.assertEqual(self.restart().snapshot("drawing")[0], 1)


if __name__ == "__main__":
    unittest.main()
