"""Contract tests for the isolated OpenSchematix source indexer."""
import hashlib
import os
import json
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest
import zipfile

from revisionworx_integrations.openschematix import inspect, inspect_bytes, InvalidProject, UnsupportedFormat


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


    def test_real_upstream_native_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "openschematix" / "demo_project.schematix"
        raw = fixture.read_bytes()
        result = inspect(fixture)
        self.assertEqual(result["file_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(result["sheets"]), 3)
        self.assertEqual(len(result["components"]), 26)
        self.assertEqual(len(result["bom_candidates"]), 5)
        self.assertEqual(result["bom_status"], "candidate_only_not_equivalent_to_native_bom")
        self.assertEqual(fixture.read_bytes(), raw)

    def test_real_upstream_legacy_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "openschematix" / "start_stop_latch.osch"
        raw = fixture.read_bytes()
        result = inspect(fixture)
        self.assertEqual(len(result["sheets"]), 1)
        self.assertEqual(len(result["components"]), 7)
        self.assertEqual(len(result["bom_candidates"]), 0)
        self.assertEqual(fixture.read_bytes(), raw)

    def test_real_upstream_hydraulic_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "openschematix" / "hydraulic_cylinder_circuit.osch"
        before = fixture.read_bytes()
        result = inspect(fixture)
        self.assertEqual(result["file_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(len(result["sheets"]), 1)
        self.assertEqual(len(result["components"]), 9)
        self.assertEqual(len(result["bom_candidates"]), 0)
        self.assertEqual(result["bom_status"], "candidate_only_not_equivalent_to_native_bom")
        self.assertEqual(fixture.read_bytes(), before)

    def test_reject_archive_member_traversal(self):
        path = self.project()
        with zipfile.ZipFile(path, "a") as z:
            z.writestr("../escape.json", "{}")
        with self.assertRaisesRegex(InvalidProject, "unsafe ZIP member"):
            inspect(path)

    def test_reject_archive_member_backslash(self):
        path = self.project()
        with zipfile.ZipFile(path, "a") as z:
            z.writestr("sheets\\\\other.json", "{}")
        with self.assertRaisesRegex(InvalidProject, "unsafe ZIP member"):
            inspect(path)

    def test_reject_archive_duplicate_members(self):
        path = self.project()
        with zipfile.ZipFile(path, "a") as z:
            z.writestr("manifest.json", "{}")
        with self.assertRaisesRegex(InvalidProject, "duplicate ZIP"):
            inspect(path)

    def test_reject_missing_component_record(self):
        path = self.legacy()
        obj = json.loads(path.read_text())
        obj["components"] = [None]
        path = self.legacy(obj)
        with self.assertRaises(InvalidProject):
            inspect(path)

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links unavailable")
    def test_reject_source_symlink(self):
        target = self.legacy()
        link = self.root / "linked.osch"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links not permitted")
        with self.assertRaisesRegex(InvalidProject, "regular file"):
            inspect(link)

    def test_reject_excess_archive_members(self):
        path = self.project()
        with patch("revisionworx_integrations.openschematix.MAX_INSPECTED_MEMBERS", 3):
            with self.assertRaisesRegex(InvalidProject, "member limit"):
                inspect(path)

    def test_reject_nonboolean_bom_flag(self):
        path = self.legacy()
        obj = json.loads(path.read_text())
        obj["components"][0]["in_bom"] = "false"
        self.legacy(obj)
        with self.assertRaisesRegex(InvalidProject, "in_bom"):
            inspect(path)

    def test_reject_unhashable_sheet_order(self):
        path = self.project()
        with zipfile.ZipFile(path, "r") as archive:
            data = [(info.filename, archive.read(info), info.compress_type)
                    for info in archive.infolist()]
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload, compression in data:
                if name == "project.json":
                    obj = json.loads(payload)
                    obj["sheet_order"] = [["s1"]]
                    payload = json.dumps(obj).encode()
                archive.writestr(name, payload, compress_type=compression)
        with self.assertRaisesRegex(InvalidProject, "sheet ordering"):
            inspect(path)

    def test_immutable_bytes_entry_point_matches_file_entry_point(self):
        for path in (self.project(), self.legacy()):
            self.assertEqual(inspect_bytes(path.read_bytes(), path.suffix), inspect(path))

    def test_immutable_bytes_entry_point_needs_only_original_extension(self):
        path = self.project()
        raw = path.read_bytes()
        result = inspect_bytes(raw, ".schematix")
        self.assertEqual(result["file_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["project_uid"], "p1")

    def test_immutable_bytes_entry_point_rejects_unknown_extension(self):
        with self.assertRaises(UnsupportedFormat):
            inspect_bytes(b"{}", ".bin")

    def test_immutable_revision_snapshot_survives_working_file_edit(self):
        path = self.legacy()
        pinned = path.read_bytes()
        expected = hashlib.sha256(pinned).hexdigest()
        path.write_text('{"format":"not OpenSchematix"}', encoding="utf-8")
        report = inspect_bytes(pinned, ".osch")
        self.assertEqual(report["file_sha256"], expected)
        self.assertEqual(len(report["components"]), 1)
        with self.assertRaises(UnsupportedFormat):
            inspect(path)

    def test_reject_zip_member_decompression_limit(self):
        path = self.project()
        with patch("revisionworx_integrations.openschematix.MAX_MEMBER", 20):
            with self.assertRaisesRegex(InvalidProject, "oversized member"):
                inspect(path)

    def test_reject_zip_aggregate_decompression_limit(self):
        path = self.project()
        with patch("revisionworx_integrations.openschematix.MAX_TOTAL", 20):
            with self.assertRaisesRegex(InvalidProject, "ZIP size/member count"):
                inspect(path)

    def test_reject_truncated_zip_container(self):
        path = self.project()
        raw = path.read_bytes()
        path.write_bytes(raw[:len(raw) // 2])
        with self.assertRaises(InvalidProject):
            inspect(path)

    def test_reject_mismatched_manifest_sheet_uid(self):
        path = self.project()
        with zipfile.ZipFile(path, "r") as archive:
            records = [(info.filename, archive.read(info), info.compress_type)
                       for info in archive.infolist()]
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload, compression in records:
                if name == "sheets/s1.json":
                    doc = json.loads(payload)
                    doc["uid"] = "different-sheet"
                    payload = json.dumps(doc).encode()
                archive.writestr(name, payload, compress_type=compression)
        with self.assertRaisesRegex(InvalidProject, "manifest/sheet UID mismatch"):
            inspect(path)

    def test_reject_future_legacy_schema(self):
        path = self.legacy()
        doc = json.loads(path.read_text())
        doc["schema"] = 999
        self.legacy(doc)
        with self.assertRaises(UnsupportedFormat):
            inspect(path)

    def test_reject_nonfinite_component_coordinates(self):
        path = self.legacy()
        doc = json.loads(path.read_text())
        doc["components"][0]["x"] = float("nan")
        self.legacy(doc)
        with self.assertRaises(InvalidProject):
            inspect(path)

    def test_reject_duplicate_legacy_json_keys(self):
        path = self.legacy()
        raw = path.read_text()
        self.assertTrue(raw.startswith("{"))
        path.write_text('{"format":"OpenSchematix",' + raw[1:], encoding="utf-8")
        with self.assertRaisesRegex(InvalidProject, "duplicate JSON key"):
            inspect(path)

    def test_reject_duplicate_native_json_keys(self):
        path = self.project()
        with zipfile.ZipFile(path, "r") as archive:
            records = [(info.filename, archive.read(info), info.compress_type)
                       for info in archive.infolist()]
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload, compression in records:
                if name == "manifest.json":
                    self.assertTrue(payload.startswith(b"{"))
                    payload = b'{"format":"OpenSchematix Project",' + payload[1:]
                archive.writestr(name, payload, compress_type=compression)
        with self.assertRaisesRegex(InvalidProject, "duplicate JSON key"):
            inspect(path)

    def test_unknown_extension(self):
        path = self.root / "example.step"
        path.write_text("{}")
        with self.assertRaises(UnsupportedFormat):
            inspect(path)


if __name__ == "__main__":
    unittest.main()
