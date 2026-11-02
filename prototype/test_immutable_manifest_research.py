"""Crash and corruption research for immutable content and atomic commit markers."""
from pathlib import Path
import tempfile
import unittest

from prototype.immutable_manifest_research import ImmutableManifestResearch, MissingContent


class ImmutableManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.m = ImmutableManifestResearch(root / "authority.db",
                                            root / "epoch", root / "objects")
        self.cred = self.m.coordinator.acquire("drawing", "alice")

    def test_multifile_commit_and_recovery(self):
        a = self.m.stage(b"first cad file")
        b = self.m.stage(b"second cad file")
        manifest = {"a.step": a, "b.dxf": b}
        self.assertEqual(self.m.publish("drawing", self.cred, "tx1", manifest,
                                        expected_revision=0), "committed")
        self.assertEqual(self.m.recover(), 1)
        self.assertEqual(self.m.publish("drawing", self.cred, "tx1", manifest,
                                        expected_revision=0), "already-committed")

    def test_staged_but_uncommitted_is_not_a_revision(self):
        self.m.stage(b"orphan from crashed writer")
        self.assertEqual(self.m.recover(), 0)
        self.assertEqual(self.m.coordinator.authority.snapshot("drawing")[2], 0)

    def test_missing_blob_rejected_before_commit(self):
        metadata = self.m.stage(b"original")
        path = self.m.objects / metadata["sha256"][:2] / metadata["sha256"]
        path.unlink()
        with self.assertRaises(MissingContent):
            self.m.publish("drawing", self.cred, "tx1", {"a.step": metadata},
                           expected_revision=0)
        self.assertEqual(self.m.coordinator.authority.outcome("tx1")["state"], "not-found")

    def test_committed_blob_corruption_fails_closed(self):
        metadata = self.m.stage(b"original")
        self.m.publish("drawing", self.cred, "tx1", {"a.step": metadata},
                       expected_revision=0)
        path = self.m.objects / metadata["sha256"][:2] / metadata["sha256"]
        path.write_bytes(b"tampered")
        with self.assertRaises(MissingContent):
            self.m.recover()

    def test_stale_writer_cannot_publish_prepared_blob(self):
        metadata = self.m.stage(b"prepared by alice")
        self.m.coordinator.override("drawing", "operator revocation", "unverified actor")
        with self.assertRaises(Exception) as caught:
            self.m.publish("drawing", self.cred, "tx-stale", {"a.step": metadata},
                           expected_revision=0)
        self.assertEqual(type(caught.exception).__name__, "Fenced")
        self.assertEqual(self.m.coordinator.authority.outcome("tx-stale")["state"], "not-found")

    def test_restage_corrupt_object_fails_closed(self):
        metadata = self.m.stage(b"original")
        path = self.m.objects / metadata["sha256"][:2] / metadata["sha256"]
        path.write_bytes(b"bad")
        with self.assertRaises(MissingContent):
            self.m.stage(b"original")


if __name__ == "__main__":
    unittest.main()
