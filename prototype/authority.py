"""Isolated, local-only serialized authority model; NOT an SMB/NFS implementation."""
import sqlite3
from pathlib import Path


class Fenced(Exception):
    pass


class Authority:
    """SQLite must reside on coordinator LOCAL disk, never a shared network filesystem."""

    def __init__(self, path):
        self.path = str(Path(path))
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS authority (resource TEXT PRIMARY KEY, generation INTEGER NOT NULL, owner TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS commits (txid TEXT PRIMARY KEY, resource TEXT NOT NULL, generation INTEGER NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS audits (id INTEGER PRIMARY KEY, resource TEXT, old_generation INTEGER, new_generation INTEGER, reason TEXT, actor TEXT)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def acquire(self, resource, owner):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,owner FROM authority WHERE resource=?", (resource,)).fetchone()
            if row and row[1] is not None:
                db.rollback()
                raise Fenced("already owned")
            generation = row[0] + 1 if row else 1
            db.execute("INSERT INTO authority(resource,generation,owner) VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET generation=excluded.generation,owner=excluded.owner", (resource,generation,owner))
            db.commit()
            return generation

    def override(self, resource, reason, actor):
        if not reason.strip():
            raise ValueError("override reason required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation FROM authority WHERE resource=?", (resource,)).fetchone()
            if row is None:
                db.rollback()
                raise KeyError(resource)
            old = row[0]
            db.execute("UPDATE authority SET generation=?,owner=NULL WHERE resource=?", (old+1,resource))
            db.execute("INSERT INTO audits(resource,old_generation,new_generation,reason,actor) VALUES(?,?,?,?,?)",(resource,old,old+1,reason,actor))
            db.commit()
            return old+1

    def publish(self, resource, owner, generation, txid, payload):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous=db.execute("SELECT resource,generation,payload FROM commits WHERE txid=?",(txid,)).fetchone()
            if previous:
                db.rollback()
                if previous != (resource,generation,payload):
                    raise Fenced("transaction id reused with different content")
                return "already-committed"
            row=db.execute("SELECT generation,owner FROM authority WHERE resource=?",(resource,)).fetchone()
            if row != (generation,owner):
                db.rollback()
                raise Fenced("stale publication authority")
            db.execute("INSERT INTO commits(txid,resource,generation,payload) VALUES(?,?,?,?)",(txid,resource,generation,payload))
            db.commit()
            return "committed"
