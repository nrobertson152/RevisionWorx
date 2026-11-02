"""Isolated fail-closed external witness and authenticated override research.

NOT a production coordinator. The witness directory must live on a separately
protected, nonrollbackable storage domain; ordinary files have no such guarantee.
No network transport, Windows integrated authentication, or physical SMB tests.

Each mutation holds SQLite BEGIN IMMEDIATE, checks the entire durable state against
a separate hash-chained witness, writes a NEW witness entry for the prospective
state BEFORE SQLite commit, then commits. A crash between these steps leaves a
mismatch and BLOCKS all further authority work instead of resurrecting credentials.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from prototype.authority import Fenced
from prototype.coordinator_research import CoordinatorResearch


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value):
    return hashlib.sha256(_json(value).encode("ascii")).hexdigest()


class TestIdentityProvider:
    """Local in-memory mock identity authority, NOT Windows/AD authentication.

    No caller-supplied administrator flag is trusted. Tokens are MAC-signed;
    their identity and current server-side role are verified on every operation.
    Only the trusted test fixture may configure identities/passwords/roles.
    """

    def __init__(self, accounts, signing_key):
        if not signing_key or len(signing_key) < 16:
            raise ValueError("a sufficiently long test signing key is required")
        self._key = signing_key
        self._accounts = dict(accounts)

    def authenticate(self, user, password, *, ttl_seconds=120):
        record = self._accounts.get(user)
        if not record or not hmac.compare_digest(
                hashlib.sha256(password.encode()).digest(),
                hashlib.sha256(record[0].encode()).digest()):
            raise Fenced("identity authentication failed")
        if not 0 < ttl_seconds <= 3600:
            raise ValueError("invalid credential lifetime")
        payload = {"aud": "revisionworx-research", "user": user,
                   "exp": time.time() + ttl_seconds, "nonce": uuid.uuid4().hex}
        raw = _json(payload).encode("ascii")
        signature = hmac.digest(self._key, raw, "sha256")
        return (base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") + "." +
                base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="))

    def verify(self, token, *, administrator=False):
        try:
            b64, sig64 = token.split(".")
            raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            sig = base64.urlsafe_b64decode(sig64 + "=" * (-len(sig64) % 4))
            if not hmac.compare_digest(hmac.digest(self._key, raw, "sha256"), sig):
                raise Fenced("invalid signed principal")
            identity = json.loads(raw)
            if identity["aud"] != "revisionworx-research" or identity["exp"] <= time.time():
                raise Fenced("expired or wrong-audience principal")
            user = identity["user"]
            record = self._accounts.get(user)
            if not record:
                raise Fenced("unknown principal")
            if administrator and record[1] != "administrator":
                raise Fenced("administrator role required")
            return user
        except (TypeError, ValueError, KeyError, AttributeError, UnicodeError) as exc:
            raise Fenced("invalid signed principal") from exc


class WitnessedCoordinatorResearch:
    """One-process-at-a-time research authority with a separately retained ledger.

    A real deployment MUST ensure the witness is nonrollbackable, trusted,
    independently durable and protected from direct client access. The local
    test filesystem does not establish those properties.
    """

    _TABLES = ("authority", "commits", "audits", "epoch_state", "witness_binding")

    def __init__(self, db_path, epoch_guard_path, witness_dir, identities):
        self.core = CoordinatorResearch(db_path, epoch_guard_path)
        self.auth = identities
        self.witness = Path(witness_dir)
        self.witness.mkdir(parents=True, exist_ok=True)
        with self.core.authority._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS witness_binding (
                           id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL)""")
            # Upgrade research audit storage in place; preserve legacy readers.
            cols = {r[1] for r in db.execute("PRAGMA table_info(audits)")}
            if "time_utc" not in cols:
                db.execute("ALTER TABLE audits ADD COLUMN time_utc TEXT")
            binding = db.execute("SELECT binding FROM witness_binding WHERE id=1").fetchone()
            files = sorted(self.witness.glob("*.json"))
            if binding is None:
                occupied = sum(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                               for table in ("commits", "audits", "authority"))
                if files or occupied:
                    raise Fenced("cannot bootstrap witness on existing authority history")
                db.execute("INSERT INTO witness_binding VALUES(1,?)", (uuid.uuid4().hex,))
                self._append(db, previous=None)
            else:
                self._verify(db)

    def _state_digest(self, db):
        state = {}
        for table in self._TABLES:
            state[table] = db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
        return _digest(state)

    def _head(self):
        files = sorted(self.witness.iterdir())
        if not files:
            return None
        previous = None
        for index, file in enumerate(files):
            if file.name != f"{index:020d}.json":
                raise Fenced("witness ledger has a missing or unknown entry")
            try:
                entry = json.loads(file.read_text(encoding="ascii"))
                claimed = entry.pop("digest")
                if entry["index"] != index or entry["previous"] != previous or _digest(entry) != claimed:
                    raise Fenced("witness ledger chain corrupted")
                previous = claimed
                entry["digest"] = claimed
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise Fenced("witness ledger unreadable") from exc
        return entry

    def _verify(self, db):
        guard = self.core.guard
        if not guard.guard_path.exists() or guard.guard_path.read_text() != guard.epoch:
            raise Fenced("epoch guard unavailable or mismatched")
        if db.execute("SELECT epoch FROM epoch_state WHERE id=1").fetchone() != (guard.epoch,):
            raise Fenced("database epoch mismatch")
        head = self._head()
        if head is None or head["epoch"] != guard.epoch:
            raise Fenced("missing or foreign witness ledger")
        if head["state_digest"] != self._state_digest(db):
            raise Fenced("restored or diverged authority state; fail closed")
        return head

    def _append(self, db, *, previous):
        head = self._head()
        if (previous is None) != (head is None):
            raise Fenced("witness chain changed unexpectedly")
        if previous is not None and (head["digest"] != previous["digest"]):
            raise Fenced("witness head changed during mutation")
        entry = {"index": 0 if head is None else head["index"] + 1,
                 "previous": None if head is None else head["digest"],
                 "epoch": self.core.guard.epoch,
                 "state_digest": self._state_digest(db),
                 "time_utc": datetime.now(timezone.utc).isoformat()}
        entry["digest"] = _digest(entry)
        path = self.witness / f"{entry['index']:020d}.json"
        # Exclusive file create: interrupted writes leave an invalid witness
        # record and force recovery, never a silently accepted prior state.
        with path.open("x", encoding="ascii") as out:
            out.write(_json(entry))
            out.flush()
            os.fsync(out.fileno())
        if os.name != "nt":
            fd = os.open(self.witness, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return entry

    def snapshot(self, resource):
        with self.core.authority._transaction() as db:
            self._verify(db)
            return db.execute("SELECT generation,owner,record_revision FROM authority WHERE resource=?",
                              (resource,)).fetchone()

    def acquire(self, resource, signed_principal):
        user = self.auth.verify(signed_principal)
        if not resource:
            raise ValueError("resource required")
        with self.core.authority._transaction() as db:
            head = self._verify(db)
            row = db.execute("SELECT generation,owner FROM authority WHERE resource=?",
                             (resource,)).fetchone()
            if row and row[1] is not None:
                raise Fenced("resource already owned")
            generation = row[0] + 1 if row else 1
            db.execute("""INSERT INTO authority(resource,generation,owner) VALUES(?,?,?)
                       ON CONFLICT(resource) DO UPDATE SET
                       generation=excluded.generation,owner=excluded.owner""",
                       (resource, generation, user))
            self._append(db, previous=head)
        return {"resource": resource, "epoch": self.core.guard.epoch,
                "generation": generation, "owner": user}

    def release(self, credential, signed_principal):
        actor = self.auth.verify(signed_principal)
        if actor != credential.get("owner"):
            raise Fenced("cannot release another principal's checkout")
        with self.core.authority._transaction() as db:
            head = self._verify(db)
            row = db.execute("SELECT generation,owner FROM authority WHERE resource=?",
                             (credential["resource"],)).fetchone()
            if (credential.get("epoch") != self.core.guard.epoch or
                    row != (credential.get("generation"), actor)):
                raise Fenced("stale checkout release")
            db.execute("UPDATE authority SET generation=generation+1,owner=NULL WHERE resource=?",
                       (credential["resource"],))
            self._append(db, previous=head)
        return "released"

    def override(self, resource, expected_generation, reason, signed_principal):
        actor = self.auth.verify(signed_principal, administrator=True)
        if not reason or not reason.strip():
            raise ValueError("administrative reason required")
        rejected = False
        with self.core.authority._transaction() as db:
            head = self._verify(db)
            row = db.execute("SELECT generation,owner FROM authority WHERE resource=?",
                             (resource,)).fetchone()
            if row is None:
                raise KeyError(resource)
            old, owner = row
            rejected = old != expected_generation
            if not rejected:
                db.execute("UPDATE authority SET generation=?,owner=NULL WHERE resource=?",
                           (old + 1, resource))
            db.execute("""INSERT INTO audits(resource,old_generation,new_generation,
                       prior_owner,reason,actor_claim,outcome,time_utc) VALUES(?,?,?,?,?,?,?,?)""",
                       (resource, old, old if rejected else old + 1, owner, reason,
                        actor, "rejected-stale-generation" if rejected else "revoked",
                        datetime.now(timezone.utc).isoformat()))
            self._append(db, previous=head)
        if rejected:
            raise Fenced("stale administrative override attempt (audited)")
        return old + 1

    def publish(self, credential, signed_principal, txid, payload, *, expected_revision,
                before_commit=None):
        actor = self.auth.verify(signed_principal)
        if actor != credential.get("owner"):
            raise Fenced("checkout cannot be used by a different principal")
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected revision required")
        with self.core.authority._transaction() as db:
            head = self._verify(db)
            row = db.execute("""SELECT resource,generation,payload,expected_revision FROM commits
                              WHERE txid=?""", (txid,)).fetchone()
            if row is not None:
                if row != (credential["resource"], credential["generation"],
                           payload, expected_revision):
                    raise Fenced("txid reused for different content")
                return "already-committed"
            row = db.execute("SELECT generation,owner,record_revision FROM authority WHERE resource=?",
                             (credential["resource"],)).fetchone()
            if (credential.get("epoch") != self.core.guard.epoch or row is None or
                    row[:2] != (credential.get("generation"), actor)):
                raise Fenced("revoked or stale publication credential")
            if row[2] != expected_revision:
                raise Fenced("stale expected revision")
            db.execute("""INSERT INTO commits(txid,resource,generation,payload,expected_revision,
                       resulting_revision) VALUES(?,?,?,?,?,?)""",
                       (txid, credential["resource"], credential["generation"],
                        payload, expected_revision, row[2] + 1))
            db.execute("UPDATE authority SET record_revision=? WHERE resource=?",
                       (row[2] + 1, credential["resource"]))
            self._append(db, previous=head)
            if before_commit is not None:
                before_commit()  # research-only crash/fault injection before SQLite COMMIT
        return "committed"

    def outcome(self, txid, signed_principal):
        self.auth.verify(signed_principal)
        with self.core.authority._transaction() as db:
            self._verify(db)
            row = db.execute("SELECT sequence,resource,resulting_revision FROM commits WHERE txid=?",
                             (txid,)).fetchone()
        if row is None:
            return {"state": "not-found"}
        return {"state": "committed", "sequence": row[0], "revision": row[2]}

    def audit(self, resource, signed_principal):
        self.auth.verify(signed_principal, administrator=True)
        with self.core.authority._transaction() as db:
            self._verify(db)
            return db.execute("""SELECT old_generation,new_generation,prior_owner,
                              reason,actor_claim,outcome,time_utc FROM audits
                              WHERE resource=? ORDER BY id""", (resource,)).fetchall()
