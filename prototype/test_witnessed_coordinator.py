"""Deterministic research tests for external witness, simulated identity and rollback.

These are LOCAL filesystem + local SQLite tests. They cannot certify that witness
storage is nonrollbackable, Windows authentication, or SMB/power-loss semantics.
"""
from contextlib import closing
import multiprocessing as mp
import os
import shutil
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



def _rotate_crash_worker(db, guard, ledger, token):
    identity = TestIdentityProvider(_ACCOUNTS, _KEY)
    model = WitnessedCoordinatorResearch(db, guard, ledger, identity)
    model.rotate_epoch("revoke all leases", token,
                       after_guard_replace=lambda: os._exit(54))



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

    def test_witness_capacity_blocks_mutation_without_commit(self):
        """Bounded model must fail closed, not append uncheckable history."""
        self.co._MAX_WITNESS_ENTRIES = 2  # genesis plus one transaction
        credential = self.co.acquire("drawing", self.alice)
        with self.assertRaises(Fenced):
            self.co.publish(credential, self.alice, "over-cap", "payload",
                            expected_revision=0)
        self.assertEqual(self.co.outcome("over-cap", self.alice)["state"], "not-found")
        self.assertEqual(self.co.snapshot("drawing")[2], 0)

    def test_record_limit_rolls_back_without_advancing_witness(self):
        """Even a valid checkout must not be published beyond verification bound."""
        self.co._MAX_STATE_ROWS = 2  # epoch_state + witness_binding already occupy two
        with self.assertRaises(Fenced):
            self.co.acquire("drawing", self.alice)
        self.co._MAX_STATE_ROWS = 10000
        self.assertIsNone(self.co.snapshot("drawing"))
        self.assertEqual(len(list(self.ledger.glob("*.json"))), 1)

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

    def test_supervised_epoch_rotation_fences_all_credentials(self):
        first = self.co.acquire("drawing", self.alice)
        second = self.co.acquire("assembly", self.bob)
        self.co.publish(first, self.alice, "prior-tx", "durable", expected_revision=0)
        result = self.co.rotate_epoch("planned authority recovery", self.admin)
        self.assertNotEqual(result["old_epoch"], result["new_epoch"])
        self.assertEqual(result["invalidated_checkouts"], 2)
        fresh = self.restart()
        self.assertEqual(fresh.core.guard.epoch, result["new_epoch"])
        self.assertEqual(fresh.outcome("prior-tx", self.alice)["state"], "committed")
        with self.assertRaises(Fenced):
            fresh.publish(first, self.alice, "old-lease", "bad", expected_revision=1)
        with self.assertRaises(Fenced):
            fresh.release(second, self.bob)
        new_lease = fresh.acquire("drawing", self.alice)
        self.assertEqual(new_lease["epoch"], result["new_epoch"])
        self.assertGreater(new_lease["generation"], first["generation"])
        self.assertEqual(fresh.publish(new_lease, self.alice, "fresh-lease", "good",
                                       expected_revision=1), "committed")
        audit = fresh.audit("assembly", self.admin)
        self.assertEqual(audit[-1][5], "epoch-rotated")
        self.assertEqual(audit[-1][4], "operator")

    def test_epoch_rotation_requires_admin_and_reason(self):
        old = self.co.acquire("drawing", self.alice)
        with self.assertRaises(Fenced):
            self.co.rotate_epoch("no authority", self.alice)
        with self.assertRaises(ValueError):
            self.co.rotate_epoch("", self.admin)
        fresh = self.restart()
        self.assertEqual(fresh.core.guard.epoch, old["epoch"])
        self.assertEqual(fresh.snapshot("drawing")[:2],
                         (old["generation"], "alice"))

    def test_empty_authority_can_rotate_epoch(self):
        old = self.co.core.guard.epoch
        rotated = self.co.rotate_epoch("planned bootstrap rotation", self.admin)
        self.assertNotEqual(old, rotated["new_epoch"])
        self.assertEqual(rotated["invalidated_checkouts"], 0)
        self.assertEqual(self.restart().core.guard.epoch, rotated["new_epoch"])

    def test_guard_updated_but_db_commit_interrupted_fails_closed(self):
        self.co.acquire("drawing", self.alice)
        ctx = mp.get_context("spawn")
        p = ctx.Process(target=_rotate_crash_worker,
                        args=(str(self.db), str(self.guard), str(self.ledger), self.admin))
        p.start()
        p.join(20)
        self.assertEqual(p.exitcode, 54)
        with self.assertRaises(Fenced):
            self.restart()

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

    def test_rollback_of_both_db_and_witness_resurrects_revoked_owner(self):
        """KNOWN UNSAFE: two restorable files are not a nonrollbackable trust root."""
        old = self.co.acquire("drawing", self.alice)
        backup = self.root / "older-authority.db"
        witness_copy = self.root / "older-witness"
        with closing(sqlite3.connect(self.db)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        shutil.copytree(self.ledger, witness_copy)
        self.co.override("drawing", old["generation"], "recover lost lock", self.admin)
        with self.assertRaises(Fenced):
            self.co.publish(old, self.alice, "revoked", "old", expected_revision=0)
        with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(self.db)) as target:
            source.backup(target)
        shutil.rmtree(self.ledger)
        shutil.copytree(witness_copy, self.ledger)
        restored = self.restart()
        self.assertEqual(restored.snapshot("drawing")[:2],
                         (old["generation"], "alice"))
        self.assertEqual(restored.publish(old, self.alice, "resurrected", "unsafe",
                                          expected_revision=0), "committed")
        # This is an intentionally passing *vulnerability demonstration*.
        # The acceptance criterion in production is the OPPOSITE.
        self.assertEqual(restored.outcome("resurrected", self.alice)["state"], "committed")

    def test_reverted_witness_only_fails_closed(self):
        self.co.acquire("drawing", self.alice)
        witness_copy = self.root / "older-witness"
        shutil.copytree(self.ledger, witness_copy)
        self.co.override("drawing", 1, "administrative recovery", self.admin)
        shutil.rmtree(self.ledger)
        shutil.copytree(witness_copy, self.ledger)
        with self.assertRaises(Fenced):
            self.restart()

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
