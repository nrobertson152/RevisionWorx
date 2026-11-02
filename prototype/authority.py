"""Isolated prototype of a serialized commit authority on a LOCAL SQLite database.

This is not an approved production coordinator or a cross-host SMB/NFS protocol.
In particular, restored coordinator database snapshots require an external epoch / high-water
mark and authenticated administrative access before any real deployment is possible.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


class Fenced(Exception):
    """Checkout generation, revision precondition or transaction id conflict."""


class Authority:
    """Serialized checkout and publication for a single prototype authority database.

    Database path MUST be on the coordinator's local disk, never a network share.
    BEGIN IMMEDIATE serializes acquisition, override and journal marker insertion.
    """

    def __init__(self, path):
        self.path = str(Path(path))
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS authority (
                resource TEXT PRIMARY KEY,
                generation INTEGER NOT NULL CHECK(generation > 0),
                owner TEXT,
                record_revision INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS commits (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                txid TEXT NOT NULL UNIQUE,
                resource TEXT NOT NULL,
                generation INTEGER NOT NULL,
                payload TEXT NOT NULL,
                expected_revision INTEGER,
                resulting_revision INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS audits (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                resource TEXT NOT NULL,
                old_generation INTEGER NOT NULL,
                new_generation INTEGER NOT NULL,
                prior_owner TEXT,
                reason TEXT NOT NULL,
                actor_claim TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'revoked')""")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=20000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()
        finally:
            db.close()

    def acquire(self, resource, owner):
        if not resource or not owner:
            raise ValueError("resource and owner required")
        with self._transaction() as db:
            row = db.execute("SELECT generation, owner FROM authority WHERE resource=?",
                             (resource,)).fetchone()
            if row and row[1] is not None:
                raise Fenced("resource already owned")
            generation = row[0] + 1 if row else 1
            db.execute("""INSERT INTO authority(resource, generation, owner)
                          VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET
                          generation=excluded.generation, owner=excluded.owner""",
                       (resource, generation, owner))
            return generation

    def override(self, resource, reason, actor):
        """Atomically revoke a generation and record a claimed (NOT authenticated) actor."""
        if not reason or not reason.strip():
            raise ValueError("override reason required")
        if not actor:
            raise ValueError("actor claim required (authentication not implemented)")
        with self._transaction() as db:
            row = db.execute("SELECT generation, owner FROM authority WHERE resource=?",
                             (resource,)).fetchone()
            if row is None:
                raise KeyError(resource)
            old, previous_owner = row
            db.execute("UPDATE authority SET generation=?, owner=NULL WHERE resource=?",
                       (old + 1, resource))
            db.execute("""INSERT INTO audits(resource, old_generation, new_generation,
                          prior_owner, reason, actor_claim) VALUES(?,?,?,?,?,?)""",
                       (resource, old, old + 1, previous_owner, reason, actor))
            return old + 1

    def publish(self, resource, owner, generation, txid, payload, *, expected_revision):
        """Linearization point: authority/expect_rev test and commit row in ONE transaction.

        Identical txid replays are idempotent even if the owner was later revoked.
        New operations carrying revoked credentials always fail. Opaque payload is NOT
        an actual RevisionWorx journal or multi-file content store.
        """
        if not txid:
            raise ValueError("txid required")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("nonnegative expected_revision required")
        with self._transaction() as db:
            old = db.execute("""SELECT resource, generation, payload, expected_revision
                             FROM commits WHERE txid=?""", (txid,)).fetchone()
            if old is not None:
                if old != (resource, generation, payload, expected_revision):
                    raise Fenced("transaction id reused with different content")
                return "already-committed"
            row = db.execute("""SELECT generation, owner, record_revision FROM authority
                             WHERE resource=?""", (resource,)).fetchone()
            if row is None or (row[0], row[1]) != (generation, owner):
                raise Fenced("stale publication authority")
            revision = row[2]
            if revision != expected_revision:
                raise Fenced("record revision conflict")
            new_revision = revision + 1
            db.execute("""INSERT INTO commits(txid, resource, generation, payload,
                          expected_revision, resulting_revision) VALUES(?,?,?,?,?,?)""",
                       (txid, resource, generation, payload,
                        expected_revision, new_revision))
            db.execute("UPDATE authority SET record_revision=? WHERE resource=?",
                       (new_revision, resource))
            return "committed"

    def outcome(self, txid):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT sequence, resource, resulting_revision FROM commits WHERE txid=?",
                             (txid,)).fetchone()
        if row is None:
            return {"state": "not-found"}
        return {"state": "committed", "sequence": row[0], "resource": row[1],
                "revision": row[2]}

    def snapshot(self, resource):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT generation, owner, record_revision FROM authority WHERE resource=?",
                              (resource,)).fetchone()

    def audits(self, resource):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("""SELECT old_generation, new_generation, prior_owner, reason,
                                 actor_claim FROM audits WHERE resource=? ORDER BY sequence""",
                              (resource,)).fetchall()
