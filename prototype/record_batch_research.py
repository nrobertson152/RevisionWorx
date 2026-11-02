"""Transactional multi-record publication research, never a production save path.

Local SQLite BEGIN IMMEDIATE is the *only* authority. All per-record CAS checks,
the single ordered commit marker, updates/deletes, and the witness attestation
share one transaction. Immutable content objects are on a local filesystem;
these tests DO NOT prove SMB persistence, two-host authority, or Windows auth.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3

from prototype.authority import Authority, Fenced
from prototype.immutable_manifest_research import ImmutableManifestResearch, MissingContent
from prototype.witnessed_coordinator import WitnessedCoordinatorResearch, _json

_SHA = re.compile(r"^[0-9a-f]{64}$")


class BatchAuthorityResearch(WitnessedCoordinatorResearch):
    """Isolated schema extension with transactionally checked record versions.

    A documented research-only limitation: adding batch_records to an existing
    witnessed authority changes its state commitment; upgrade/migration requires
    an explicitly verified quiescent procedure. Create this model on a new DB.
    """

    _TABLES = WitnessedCoordinatorResearch._TABLES + ("batch_records",)

    def __init__(self, db_path, epoch_guard_path, witness_dir, objects_dir, identities):
        with Authority(db_path)._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS batch_records (
                          kind TEXT NOT NULL, id TEXT NOT NULL,
                          revision INTEGER NOT NULL, data TEXT,
                          PRIMARY KEY(kind,id))""")
        super().__init__(db_path, epoch_guard_path, witness_dir, identities)
        self.objects = Path(objects_dir)
        self.objects.mkdir(parents=True, exist_ok=True)
        self._online = True
        self._quarantined = False

    def _require_online(self):
        if self._quarantined:
            raise Fenced("authority quarantined; no authoritative access")
        if not self._online:
            raise Fenced("coordinator unavailable; writes and authoritative reads refused")

    def disconnect(self):
        """Simulates losing authority connectivity; not a network transport."""
        self._online = False

    def stage(self, data: bytes):
        self._require_online()
        return ImmutableManifestResearch(
            self.core.authority.path, self.core.guard.guard_path, self.objects).stage(data)

    def _validate_manifest(self, files):
        if not isinstance(files, dict) or len(files) > 256:
            raise ValueError("manifest must be a mapping of at most 256 objects")
        for name, value in files.items():
            if (not isinstance(name, str) or not name or len(name) > 255 or
                    "/" in name or "\\" in name or name in (".", "..")):
                raise ValueError("invalid logical filename")
            if not isinstance(value, dict) or set(value) != {"sha256", "length"}:
                raise ValueError("blob metadata must contain sha256 and length only")
            digest, length = value["sha256"], value["length"]
            if not isinstance(digest, str) or not _SHA.fullmatch(digest):
                raise ValueError("invalid sha256")
            if isinstance(length, bool) or not isinstance(length, int) or length < 0:
                raise ValueError("invalid blob length")

    def _check_blobs(self, files):
        for meta in files.values():
            digest, length = meta["sha256"], meta["length"]
            try:
                data = (self.objects / digest[:2] / digest).read_bytes()
            except OSError as exc:
                raise MissingContent("missing immutable object") from exc
            if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
                raise MissingContent("immutable object hash or length mismatch")

    @staticmethod
    def _normalize(operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 256:
            raise ValueError("1..256 batch operations required")
        seen = {}
        writes = 0
        result = []
        for raw in operations:
            if not isinstance(raw, dict):
                raise ValueError("operation must be mapping")
            typ = raw.get("type")
            if typ not in ("put", "delete", "check"):
                raise ValueError("unknown operation type")
            required = {"type", "kind", "id", "expect_rev"}
            if set(raw) != (required | ({"data"} if typ == "put" else set())):
                raise ValueError("unexpected/missing operation field")
            kind, rid, expected = raw["kind"], raw["id"], raw["expect_rev"]
            if (not isinstance(kind, str) or not kind or not isinstance(rid, str)
                    or not rid or len(kind) > 64 or len(rid) > 128):
                raise ValueError("invalid record key")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
                raise ValueError("nonnegative record expect_rev required")
            if typ == "put" and not isinstance(raw["data"], dict):
                raise ValueError("put data must be mapping")
            key = (kind, rid)
            prior = seen.get(key)
            if prior is not None:
                if prior["expected"] != expected or (prior["write"] and typ != "check") or (
                        prior["checked"] and typ == "check"):
                    raise ValueError("conflicting or duplicate operations")
                if prior["write"] and typ == "check":
                    prior["checked"] = True
                elif prior["checked"] and typ != "check":
                    prior["write"] = True
            else:
                seen[key] = {"expected": expected, "write": typ != "check",
                             "checked": typ == "check"}
            writes += typ != "check"
            result.append(raw)
        if not writes:
            raise ValueError("a batch must change at least one record")
        if len(_json(result).encode("ascii")) > 1_048_576:
            raise ValueError("operation payload too large")
        return result

    def publish_batch(self, credential, signed_principal, txid, operations, *,
                      expected_revision, manifest=None, before_commit=None):
        self._require_online()
        actor = self.auth.verify(signed_principal)
        if actor != credential.get("owner"):
            raise Fenced("wrong authenticated lease owner")
        if not isinstance(txid, str) or not re.fullmatch(r"[0-9a-f]{32}", txid):
            raise ValueError("txid must be 32 lowercase hex digits")
        if (isinstance(expected_revision, bool) or
                not isinstance(expected_revision, int) or expected_revision < 0):
            raise ValueError("expected_revision must be a nonnegative integer")
        ops = self._normalize(operations)
        files = {} if manifest is None else manifest
        self._validate_manifest(files)
        # Out-of-band immutable SMB publication is not transactional with
        # SQLite. This local pre-commit check detects missing/corrupt objects,
        # but physical SMB durability must be certified independently.
        request = _json({"epoch": credential.get("epoch"),
                         "resource": credential.get("resource"),
                         "operations": ops, "manifest": files})
        with self.core.authority._transaction() as db:
            head = self._verify(db)
            row = db.execute("""SELECT sequence,resource,generation,payload,expected_revision
                                FROM commits WHERE txid=?""", (txid,)).fetchone()
            if row is not None:
                if row[1:] != (credential.get("resource"), credential.get("generation"),
                               request, expected_revision):
                    raise Fenced("txid reused for a different batch")
                return {"state": "already-committed", "sequence": row[0], "txid": txid}
            lease = db.execute("""SELECT generation,owner,record_revision FROM authority
                                  WHERE resource=?""",
                               (credential.get("resource"),)).fetchone()
            if (credential.get("epoch") != self.core.guard.epoch or lease is None
                    or lease[:2] != (credential.get("generation"), actor)):
                raise Fenced("revoked or stale batch lease")
            if lease[2] != expected_revision:
                raise Fenced("resource expected_revision conflict")

            expected_by_key = {}
            for op in ops:
                key = (op["kind"], op["id"])
                expected_by_key[key] = op["expect_rev"]
            for (kind, rid), expected in expected_by_key.items():
                rec = db.execute("""SELECT revision FROM batch_records
                                    WHERE kind=? AND id=?""", (kind, rid)).fetchone()
                actual = rec[0] if rec else 0
                if actual != expected:
                    raise Fenced(f"record revision conflict: {kind}/{rid}: {actual} != {expected}")

            # Check bytes under the same authority serialization lock.
            self._check_blobs(files)
            new_revision = expected_revision + 1
            cur = db.execute("""INSERT INTO commits(txid,resource,generation,payload,
                                 expected_revision,resulting_revision) VALUES(?,?,?,?,?,?)""",
                             (txid, credential["resource"], credential["generation"],
                              request, expected_revision, new_revision))
            seq = cur.lastrowid
            for op in ops:
                if op["type"] == "check":
                    continue
                value = _json(op["data"]) if op["type"] == "put" else None
                db.execute("""INSERT INTO batch_records(kind,id,revision,data)
                              VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET
                              revision=excluded.revision,data=excluded.data""",
                           (op["kind"], op["id"], seq, value))
            db.execute("UPDATE authority SET record_revision=? WHERE resource=?",
                       (new_revision, credential["resource"]))
            self._append(db, previous=head)
            if before_commit is not None:
                before_commit()  # research fault-injection only
        return {"state": "committed", "sequence": seq, "txid": txid}

    def _verify_committed_objects(self, db):
        for (raw,) in db.execute("SELECT payload FROM commits ORDER BY sequence"):
            try:
                manifest = json.loads(raw).get("manifest", {})
                self._validate_manifest(manifest)
                self._check_blobs(manifest)
            except (ValueError, KeyError, TypeError, MissingContent, AttributeError) as exc:
                self._quarantined = True
                raise Fenced("committed content absent or corrupt; authority quarantined") from exc

    def snapshot_batch(self):
        self._require_online()
        with self.core.authority._transaction() as db:
            self._verify(db)
            self._verify_committed_objects(db)
            rows = db.execute("""SELECT kind,id,revision,data FROM batch_records
                                 ORDER BY kind,id""").fetchall()
            high = db.execute("SELECT COALESCE(MAX(sequence),0) FROM commits").fetchone()[0]
        records = {(k, r): {"revision": v, "data": json.loads(data) if data is not None else None}
                   for k, r, v, data in rows}
        return {"epoch": self.core.guard.epoch, "high_watermark": high, "records": records}

    def reconnect(self, client_epoch, cursor):
        """Simulated reconnect: no silent standalone fallback or invented currentness."""
        if self._quarantined:
            raise Fenced("authority quarantined")
        if not isinstance(cursor, int) or cursor < 0:
            raise ValueError("invalid cursor")
        with self.core.authority._transaction() as db:
            self._verify(db)
            self._verify_committed_objects(db)
            seqs = [row[0] for row in db.execute(
                "SELECT sequence FROM commits ORDER BY sequence").fetchall()]
            if seqs != list(range(1, len(seqs) + 1)):
                self._quarantined = True
                raise Fenced("journal gap: reconnect quarantined")
            high = seqs[-1] if seqs else 0
            if client_epoch == self.core.guard.epoch and cursor > high:
                self._quarantined = True
                raise Fenced("client watermark ahead of authority")
        self._online = True
        return {"state": "connected", "epoch": self.core.guard.epoch,
                "high_watermark": high,
                "resync": "full" if client_epoch != self.core.guard.epoch else
                          "incremental" if cursor < high else "current"}
