"""Pinned OpenSchematix 1.1.0 native BOM oracle against real source fixtures.

This test is deliberately separate from the standalone adapter suite: its
upstream checkout is supplied by CI, never bundled into RevisionWorx runtime.
The comparison verifies observed native BOM lines rather than claiming that
the adapter's unresolved manufacturer candidates form a purchasing BOM.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

from revisionworx_integrations.openschematix import inspect_bytes


UPSTREAM = os.environ.get("OPENSCHEMATIX_ORACLE_SOURCE")
EXAMPLES = Path(__file__).parent / "fixtures" / "openschematix"
BASELINES = (
    ("demo_project.schematix", 3, 26, 16, 5),
    ("start_stop_latch.osch", 1, 7, 5, 0),
    ("hydraulic_cylinder_circuit.osch", 1, 9, 7, 0),
)


@unittest.skipUnless(UPSTREAM, "native oracle requires pinned OpenSchematix source checkout")
class NativeBOMOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(UPSTREAM).resolve()
        if not (source / "openschematix" / "bom.py").is_file():
            raise AssertionError(f"missing pinned native BOM source at {source}")
        sys.path.insert(0, str(source))
        from openschematix import VERSION, bom, projectio
        if VERSION != "1.1.0":
            raise AssertionError(f"unexpected oracle version: {VERSION}")
        cls.bom = bom
        cls.projectio = projectio

    def test_upstream_native_bom_against_all_real_fixtures(self):
        for filename, sheet_count, instance_count, native_lines, candidate_count in BASELINES:
            with self.subTest(filename=filename):
                source = EXAMPLES / filename
                original = source.read_bytes()
                report = inspect_bytes(original, source.suffix)
                opened = self.projectio.open_any(str(source), activate=False)
                project = opened.project
                lines = self.bom.generate(project)

                self.assertEqual(len(project.sheets), sheet_count)
                self.assertEqual(sum(len(s.components) for s in project.sheets), instance_count)
                self.assertEqual(len(report["sheets"]), sheet_count)
                self.assertEqual(len(report["components"]), instance_count)
                self.assertEqual(len(lines), native_lines)
                self.assertEqual(len(report["bom_candidates"]), candidate_count)
                self.assertEqual(report["bom_status"], "candidate_only_not_equivalent_to_native_bom")
                self.assertEqual(report["file_sha256"], hashlib.sha256(original).hexdigest())
                self.assertEqual(source.read_bytes(), original, "oracle must never modify source")

                adapter_refs = {(c["sheet_uid"], c["object_id"])
                                for c in report["components"]}
                native_refs = {(uid, cid) for line in lines for uid, cid in line.refs}
                self.assertTrue(native_refs.issubset(adapter_refs))
                self.assertTrue(all(type(line.quantity) is int and line.quantity > 0
                                    for line in lines))
                self.assertTrue(all(line.refs for line in lines))

                evidence = {
                    "file": filename, "sha256": report["file_sha256"],
                    "native_bom_line_count": len(lines),
                    "native_total_physical_line_quantities": sum(line.quantity for line in lines),
                    "native_referenced_instances": len(native_refs),
                    "adapter_candidates": len(report["bom_candidates"]),
                    "native_bom": [
                        {"quantity": line.quantity, "manufacturer": line.manufacturer,
                         "part_number": line.part_number, "designators": line.designators,
                         "refs": line.refs}
                        for line in lines
                    ],
                }
                print("NATIVE_BOM_EVIDENCE " + json.dumps(evidence, sort_keys=True))

    def test_native_sheet_filtering_preserves_source_identity(self):
        source = EXAMPLES / "demo_project.schematix"
        original = source.read_bytes()
        project = self.projectio.open_any(str(source), activate=False).project
        if len(project.sheets) < 2:
            self.fail("fixture no longer provides a multi-sheet comparison")
        all_lines = self.bom.generate(project)
        first = project.sheets[0].uid
        selected = self.bom.generate(project, sheet_uids={first})
        self.assertLessEqual(len(selected), len(all_lines))
        self.assertTrue(selected)
        self.assertTrue(all(any(uid == first for uid, _id in line.refs) for line in selected))
        self.assertEqual(original, source.read_bytes())
        print("NATIVE_SHEET_FILTER_EVIDENCE " + json.dumps({
            "fixture": source.name, "sheet_uid": first,
            "all_lines": len(all_lines), "selected_lines": len(selected),
            "selected_refs": sum(len(line.refs) for line in selected),
        }, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
