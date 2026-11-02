"""Coordinator research extension: durable epoch, release, and sync watermark.

No transport, authentication, or production guarantees. The epoch guard is a
separate local-disk file and MUST NOT be restored with an older DB snapshot.
"""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import sqlite3
import uuid

from prototype.authority import Authority, Fenced


class EpochGuard:
    """Independent monotonic epoch fence, fail closed on rollback/missing guard.

    A separate guard file alone does NOT protect against coordinated rollback of
    both database and guard. A production implementation needs a trusted durable
    non-rollbackable store and operator recovery procedure.
    """

    def __init__(self, authority: Authority, guard_path):
        self.authority = authority
        self.guard_path = Path(guard_path)
        with authority._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS epoch_state (
                id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL)""")
            row = db.execute("SELECT epoch FROM epoch_state WHERE id=1").fetchone()
            if row is None:
                if self.guard_path.exists():
                    raise Fenced("authority rollback detected: guard exists without epoch")
                epoch = uuid.uuid4().hex
                db.execute("INSERT INTO epoch_state VALUES(1,?)", (epoch,))
                self._write_guard(epoch)
            else:
                if not self.guard_path.exists() or self.guard_path.read_text() != row[0]:
                    raise Fenced("epoch guard mismatch or missing; authority unavailable")
            self.epoch = db.execute("SELECT epoch FROM epoch_state WHERE id=1").fetchone()[0]

    def _write_guard(self, epoch):
        self.guard_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.guard_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(epoch)
            out.flush()
            os.fsync(out.fileno())

    def verify(self):
        if not self.guard_path.exists() or self.guard_path.read_text() != self.epoch:
            raise Fenced("epoch guard missing or changed")
        with self.authority._transaction() as db:
            row = db.execute("SELECT epoch FROM epoch_state WHERE id=1").fetchone()
            if row is None or row[0] != self.epoch:
                raise Fenced("coordinator epoch rollback")
        return self.epoch


class CoordinatorResearch:
    """Local authority facade illustrating the proposed interface, not a service."""

    def __init__(self, db_path, guard_path):
        self.authority = Authority(db_path)
        self.guard = EpochGuard(self.authority, guard_path)

    def acquire(self, resource, owner):
        self.guard.verify()
        generation = self.authority.acquire(resource, owner)
        return {"epoch": self.guard.epoch, "generation": generation, "owner": owner}

    def verify(self, resource, credential):
        self.guard.verify()
        row = self.authority.snapshot(resource)
        if row is None or row[:2] != (credential["generation"], credential["owner"]):
            raise Fenced("stale checkout")
        if credential["epoch"] != self.guard.epoch:
            raise Fenced("stale epoch")
        return row[2]

    def release(self, resource, credential):
        self.guard.verify()
        with self.authority._transaction() as db:
            row = db.execute("SELECT generation,owner FROM authority WHERE resource=?",
                             (resource,)).fetchone()
            if (credential.get("epoch") != self.guard.epoch or row is None or
                    row != (credential.get("generation"), credential.get("owner"))):
                raise Fenced("stale release")
            db.execute("UPDATE authority SET generation=generation+1,owner=NULL WHERE resource=?",
                       (resource,))
        return "released"

    def override(self, resource, reason, actor):
        self.guard.verify()
        return self.authority.override(resource, reason, actor)

    def publish(self, resource, credential, txid, payload, *, expected_revision):
        self.guard.verify()
        if credential.get("epoch") != self.guard.epoch:
            raise Fenced("stale epoch")
        return self.authority.publish(resource, credential["owner"],
                                      credential["generation"], txid, payload,
                                      expected_revision=expected_revision)

    def changes_after(self, cursor, *, limit=100):
        """Ordered, gap-checkable incremental change feed; no local-cache authority."""
        self.guard.verify()
        if not isinstance(cursor, int) or cursor < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid cursor/limit")
        with closing(sqlite3.connect(self.authority.path)) as db:
            rows = db.execute("""SELECT sequence,txid,resource,resulting_revision,payload
                              FROM commits WHERE sequence>? ORDER BY sequence LIMIT ?""",
                              (cursor, limit)).fetchall()
            high = db.execute("SELECT COALESCE(MAX(sequence),0) FROM commits").fetchone()[0]
        if rows and rows[0][0] != cursor + 1:
            raise Fenced("missing authoritative sequence; rebuild required")
        for a, b in zip(rows, rows[1:]):
            if b[0] != a[0] + 1:
                raise Fenced("noncontiguous change feed; rebuild required")
        return {"epoch": self.guard.epoch, "high_watermark": high,
                "changes": rows, "stale": (rows[-1][0] if rows else cursor) < high}
