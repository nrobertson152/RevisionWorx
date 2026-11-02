"""Read-only OpenSchematix adapter contract against pinned RevisionWorx Phase 2.

Runs with the exact Phase 2 source checkout supplied by CI. A throwaway
workspace is created for tests only; the adapter neither imports core modules
nor modifies the authoritative journal, documents, revisions or content store.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from revisionworx_integrations.openschematix import inspect_bytes


PHASE2 = os.environ.get("REVISIONWORX_PHASE2_SOURCE")
FIXTURES = Path(__file__).parent / "fixtures" / "openschematix"


@unittest.skipUnless(PHASE2, "requires pinned RevisionWorx Phase 2 source checkout")
class Phase2ImmutableRevisionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(PHASE2).resolve()
        if not (source / "revisionworx" / "documents.py").is_file():
            raise AssertionError(f"missing Phase 2 source checkout: {source}")
        sys.path.insert(0, str(source))
        from revisionworx import documents, projects
        from revisionworx.content import ContentStore
        from revisionworx.workspace import Workspace
        cls.documents = documents
        cls.projects = projects
        cls.ContentStore = ContentStore
        cls.Workspace = Workspace

    def _registered_fixture(self, base: Path, name: str):
        share = base / "share"
        root = share / "Engineering"
        root.mkdir(parents=True)
        ws = self.Workspace.create(share, "Adapter contract fixture")
        self.projects.add_root(ws, "Engineering", root)
        project = self.projects.create_project(
            ws, self.projects.NewProject(name="Schematic verification", number="PDM-1"))
        folder = self.projects.project_folder(ws, project)
        path = folder / "04 Schematics" / name
        original = (FIXTURES / name).read_bytes()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)
        doc, revision = self.documents.register_document(ws, project["id"], path)
        return ws, path, original, doc, revision

    def test_immutable_content_inspected_without_any_authoritative_mutation(self):
        for filename in ("demo_project.schematix", "start_stop_latch.osch"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                ws, path, original, doc, revision = self._registered_fixture(
                    Path(temp), filename)
                try:
                    self.assertEqual(doc["type_id"], "schematic")
                    self.assertEqual(revision["document_id"], doc["id"])
                    self.assertEqual(revision["sha256"], hashlib.sha256(original).hexdigest())
                    self.assertEqual(revision["source_rel_path"], doc["rel_path"])
                    store = self.ContentStore(ws)
                    self.assertTrue(store.verify(revision["sha256"]))
                    immutable_path = store.path_for(revision["sha256"])
                    immutable_bytes = immutable_path.read_bytes()
                    self.assertEqual(immutable_bytes, original)

                    before_seq = ws.head_seq()
                    before_document = ws.get("document", doc["id"])
                    before_revision = ws.get("revision", revision["id"])
                    observation = inspect_bytes(immutable_bytes, path.suffix)
                    self.assertEqual(observation["file_sha256"], revision["sha256"])
                    self.assertEqual(ws.head_seq(), before_seq)
                    self.assertEqual(ws.get("document", doc["id"]), before_document)
                    self.assertEqual(ws.get("revision", revision["id"]), before_revision)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(immutable_path.read_bytes(), original)

                    # Working files can diverge without altering an immutable
                    # committed revision. Only the committed bytes may be used
                    # for a revision-linked index observation.
                    path.write_bytes(b"uncommitted working-copy change")
                    observation_again = inspect_bytes(immutable_path.read_bytes(), path.suffix)
                    self.assertEqual(observation_again, observation)
                    self.assertEqual(ws.head_seq(), before_seq)
                    self.assertEqual(ws.get("revision", revision["id"]), before_revision)
                    self.assertTrue(store.verify(revision["sha256"]))
                finally:
                    ws.close()

    def test_copied_native_identity_is_not_global_document_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, path_a, raw, doc_a, rev_a = self._registered_fixture(
                Path(temp), "demo_project.schematix")
            try:
                # A user may duplicate a native CAD project in the same
                # managed project. Its internal source UIDs are copied too.
                path_b = path_a.with_name("demo_project_copy.schematix")
                path_b.write_bytes(raw)
                project_id = doc_a["project_id"]
                doc_b, rev_b = self.documents.register_document(
                    ws, project_id, path_b)
                self.assertNotEqual(doc_a["id"], doc_b["id"])
                self.assertNotEqual(rev_a["id"], rev_b["id"])
                self.assertEqual(rev_a["sha256"], rev_b["sha256"])
                store = self.ContentStore(ws)
                data = store.path_for(rev_a["sha256"]).read_bytes()
                before_seq = ws.head_seq()
                source_a = inspect_bytes(data, ".schematix")
                source_b = inspect_bytes(data, ".schematix")
                self.assertEqual(source_a["project_uid"], source_b["project_uid"])
                self.assertEqual(source_a["components"][0]["instance_id"],
                                 source_b["components"][0]["instance_id"])
                # The safe global association is (doc_id, rev_id, instance);
                # source instance keys alone collide after copying a project.
                self.assertNotEqual(
                    (doc_a["id"], rev_a["id"], source_a["components"][0]["instance_id"]),
                    (doc_b["id"], rev_b["id"], source_b["components"][0]["instance_id"]))
                self.assertEqual(ws.head_seq(), before_seq)
                self.assertEqual(path_a.read_bytes(), raw)
                self.assertEqual(path_b.read_bytes(), raw)
            finally:
                ws.close()

    def test_two_real_document_revisions_index_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            ws, path, original, doc, rev_a = self._registered_fixture(
                Path(temp), "start_stop_latch.osch")
            try:
                checkout = self.documents.checkout(ws, doc["id"])
                source = json.loads(path.read_text(encoding="utf-8"))
                source["sheet"]["title"] = "Edited revision B"
                path.write_text(json.dumps(source), encoding="utf-8")
                rev_b = self.documents.checkin(
                    ws, doc["id"], checkout["token"], "Revise title block")
                self.assertNotEqual(rev_a["sha256"], rev_b["sha256"])
                store = self.ContentStore(ws)
                before_seq = ws.head_seq()
                first = inspect_bytes(
                    store.path_for(rev_a["sha256"]).read_bytes(), ".osch")
                second = inspect_bytes(
                    store.path_for(rev_b["sha256"]).read_bytes(), ".osch")
                self.assertEqual(first["file_sha256"], rev_a["sha256"])
                self.assertEqual(second["file_sha256"], rev_b["sha256"])
                self.assertNotEqual(first["project_metadata"]["title"],
                                    second["project_metadata"]["title"])
                self.assertIsNone(first["project_uid"])
                self.assertIsNone(second["project_uid"])
                self.assertEqual(ws.head_seq(), before_seq)
                self.assertTrue(store.verify(rev_a["sha256"]))
                self.assertTrue(store.verify(rev_b["sha256"]))
                self.assertEqual(ws.get("document", doc["id"])["latest_rev_id"],
                                 rev_b["id"])
            finally:
                ws.close()


if __name__ == "__main__":
    unittest.main()
