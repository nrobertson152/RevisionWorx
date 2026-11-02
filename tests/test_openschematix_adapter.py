"""Contract tests for the isolated OpenSchematix source indexer."""
import hashlib
import json
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest
import zipfile

from revisionworx_integrations.openschematix import inspect, InvalidProject, UnsupportedFormat


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def legacy(self, data=None):
        path = self.root / "a.osch"
        path.write_text(json.dumps(data if data is not None else {
            "format": "OpenSchematix", "schema": 1, "sheet": {"title": "Example"},
            "components": [{"id": 1, "symbol": "fl.hyd.dcv", "x": 10, "y": 20,
                            "designator": "V1", "props": {"manufacturer": "Acme",
                            "part_number": "ABC", "custom": "preserved"}}],
            "wires": [],
        }), encoding="utf-8")
        return path

    def project(self, schema=2):
        path = self.root / "project.schematix"
        def data(obj):
            return json.dumps(obj).encode()
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/x-openschematix-project",
                       compress_type=zipfile.ZIP_STORED)
            z.writestr("manifest.json", data({"format": "OpenSchematix Project", "version": 1,
                "sheet_schema": schema, "project_uid": "p1",
                "sheets": [{"uid": "s1", "name": "S", "path": "sheets/s1.json"}]}))
            z.writestr("project.json", data({"uid": "p1", "meta": {"title": "Circuit"},
                                            "sheet_order": ["s1"]}))
            z.writestr("sheets/s1.json", data({"uid": "s1", "name": "S",
                "components": [{"id": 3, "symbol": "el.coil.iec", "x": 20, "y": 30,
                   "designator": "K1", "props": {"manufacturer": "ACME", "part_number": "P1"}}],
                "wires": [{"id": 4, "start": [3, "A1"], "end": None}]}))
        return path

    def test_native_readonly_and_metadata(self):
        path = self.project()
        before = path.read_bytes()
        result = inspect(path)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(result["project_uid"], "p1")
        self.assertEqual(result["sheets"][0]["wire_count"], 1)
        self.assertEqual(result["components"][0]["instance_id"], "p1/s1/3")
        self.assertEqual(result["dependencies"][0]["verification"], "unresolved")
        self.assertIsNone(result["bom_candidates"][0]["quantity"])

    def test_legacy_and_custom_properties(self):
        path = self.legacy()
        result = inspect(path)
        self.assertEqual(result["source_format"], "osch")
        self.assertIsNone(result["project_uid"])
        self.assertEqual(result["components"][0]["properties"]["custom"], "preserved")

    def test_duplicate_component_ids(self):
        path = self.legacy()
        obj = json.loads(path.read_text())
        obj["components"].append(dict(obj["components"][0]))
        self.legacy(obj)
        with self.assertRaises(InvalidProject):
            inspect(path)

    def test_reject_new_schema(self):
        with self.assertRaises(UnsupportedFormat):
            inspect(self.project(schema=99))

    def test_bad_json(self):
        path = self.legacy()
        path.write_text('{"format":')
        with self.assertRaises(InvalidProject):
            inspect(path)

    def test_digest_matches_parsed_snapshot(self):
        path = self.project()
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        result = inspect(path)
        self.assertEqual(result["file_sha256"], expected)

    def test_reject_oversized_source_before_parsing(self):
        path = self.legacy()
        with patch("revisionworx_integrations.openschematix.MAX_SOURCE", 10):
            with self.assertRaisesRegex(InvalidProject, "source file exceeds size limit"):
                inspect(path)

    def test_reject_oversized_mimetype_member(self):
        path = self.root / "oversized.schematix"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/x-openschematix-project" + "X" * 500,
                       compress_type=zipfile.ZIP_STORED)
        with self.assertRaises(InvalidProject):
            inspect(path)

    def test_unknown_extension(self):
        path = self.root / "example.step"
        path.write_text("{}")
        with self.assertRaises(UnsupportedFormat):
            inspect(path)


if __name__ == "__main__":
    unittest.main()
