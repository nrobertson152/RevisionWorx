"""Isolated immutable blob/manifest research, NOT a production SMB commit engine.

Pre-stage and verify content-addressed files, then atomically record the manifest
in local SQLite authority. Crash after staging leaves harmless orphan blobs.
This cannot guarantee SMB power-loss durability or prevent privileged tampering.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from prototype.authority import Fenced
from prototype.coordinator_research import CoordinatorResearch


class MissingContent(Fenced):
    pass


class ImmutableManifestResearch:
    def __init__(self, db_path, guard_path, objects_dir):
        self.coordinator = CoordinatorResearch(db_path, guard_path)
        self.objects = Path(objects_dir)
        self.objects.mkdir(parents=True, exist_ok=True)

    def stage(self, data: bytes):
        if not isinstance(data, bytes):
            raise TypeError("bytes required")
        digest = hashlib.sha256(data).hexdigest()
        destination = self.objects / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify(digest, len(data))
            return {"sha256": digest, "length": len(data)}
        fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            # Link never replaces an existing immutable object.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            self._verify(digest, len(data))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"sha256": digest, "length": len(data)}

    def _verify(self, digest, length):
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(c not in "0123456789abcdef" for c in digest)):
            raise MissingContent("invalid object digest")
        path = self.objects / digest[:2] / digest
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise MissingContent("missing committed/staged object") from exc
        if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
            raise MissingContent("object length/hash mismatch")

    def publish(self, resource, credential, txid, files, *, expected_revision):
        """All files verified BEFORE the authority commit; no in-place overwrite."""
        if not isinstance(files, dict) or not files:
            raise ValueError("nonempty manifest required")
        for name, metadata in files.items():
            if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                raise ValueError("invalid logical filename")
            self._verify(metadata["sha256"], metadata["length"])
        manifest = json.dumps(files, sort_keys=True, separators=(",", ":"))
        return self.coordinator.publish(resource, credential, txid, manifest,
                                        expected_revision=expected_revision)

    def recover(self):
        """Quarantine on any missing/corrupt committed content; never fabricate it."""
        with self.coordinator.authority._transaction() as db:
            rows = db.execute("SELECT txid,payload FROM commits ORDER BY sequence").fetchall()
        for txid, payload in rows:
            try:
                files = json.loads(payload)
                for metadata in files.values():
                    self._verify(metadata["sha256"], metadata["length"])
            except (ValueError, KeyError, TypeError, MissingContent) as exc:
                raise MissingContent(f"committed txid {txid} is not recoverable") from exc
        return len(rows)
