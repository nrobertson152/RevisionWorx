"""Executable golden invariants for proposed ADR-017 request identity."""
import unittest

from prototype.request_identity_research import canonical_request, request_digest


def put(rid="prj_a", data=None, expected=0):
    return {"type": "put", "kind": "project", "id": rid,
            "data": {"name": "A"} if data is None else data, "expect_rev": expected}


def check(rid="prj_a", expected=0):
    return {"type": "check", "kind": "project", "id": rid, "expect_rev": expected}


def delete(rid="prj_a", expected=None):
    return {"type": "delete", "kind": "project", "id": rid, "expect_rev": expected}


class FullCommandIdentityResearch(unittest.TestCase):
    def test_identical_request_has_identical_digest(self):
        ops = [check("prj_old", 5), put()]
        self.assertEqual(request_digest("publish", ops), request_digest("publish", ops))

    def test_dictionary_key_insertion_order_is_irrelevant(self):
        one = put(data={"b": {"z": 1, "a": 2}, "a": 5})
        two = put(data={"a": 5, "b": {"a": 2, "z": 1}})
        self.assertEqual(request_digest("write", [one]), request_digest("write", [two]))

    def test_changed_check_expectation_changes_txid_identity(self):
        ops = [check("prj_other", 0), put()]
        changed = [check("prj_other", 99), put()]
        self.assertNotEqual(request_digest("write", ops), request_digest("write", changed))

    def test_reordered_checks_change_identity(self):
        a, b = check("prj_x", 1), check("prj_y", 2)
        self.assertNotEqual(request_digest("write", [a, b, put()]),
                            request_digest("write", [b, a, put()]))

    def test_removed_check_changes_identity(self):
        self.assertNotEqual(request_digest("write", [check("other", 0), put()]),
                            request_digest("write", [put()]))

    def test_check_only_commands_are_distinguishable(self):
        self.assertNotEqual(request_digest("preflight", [check("prj_x", 0)]),
                            request_digest("preflight", [check("prj_x", 10)]))

    def test_writes_have_ordered_identity(self):
        a, b = put("prj_a"), put("prj_b")
        self.assertNotEqual(request_digest("write", [a, b]),
                            request_digest("write", [b, a]))

    def test_write_data_and_kind_are_included(self):
        self.assertNotEqual(request_digest("write", [put(data={"a": 1})]),
                            request_digest("write", [put(data={"a": 2})]))
        changed = put()
        changed["kind"] = "document"
        self.assertNotEqual(request_digest("write", [put()]),
                            request_digest("write", [changed]))

    def test_none_expectation_is_not_absent_record(self):
        self.assertNotEqual(request_digest("write", [put(expected=None)]),
                            request_digest("write", [put(expected=0)]))

    def test_derived_rev_is_not_part_of_put_request(self):
        first = put(data={"name": "A", "_rev": 999})
        second = put(data={"name": "A"})
        self.assertEqual(request_digest("write", [first]),
                         request_digest("write", [second]))

    def test_blob_ref_order_and_empty_default(self):
        ops = [put()]
        self.assertEqual(request_digest("write", ops),
                         request_digest("write", ops, []))
        self.assertNotEqual(request_digest("write", ops, ["shaA", "shaB"]),
                            request_digest("write", ops, ["shaB", "shaA"]))

    def test_changed_op_label_is_not_identical(self):
        self.assertNotEqual(request_digest("document.create", [put()]),
                            request_digest("document.revise", [put()]))

    def test_canonical_utf8_and_unchanged_unicode(self):
        encoded = canonical_request("更新", [put(data={"name": "é 中文"})])
        self.assertIn("更新".encode("utf-8"), encoded)
        self.assertIn("é 中文".encode("utf-8"), encoded)

    def test_reject_invalid_preconditions_and_nonfinite_json(self):
        for invalid in (True, -1, 1.5, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                request_digest("write", [put(expected=invalid)])
        with self.assertRaises(ValueError):
            request_digest("write", [put(data={"reading": float("nan")})])
        with self.assertRaises(ValueError):
            request_digest("write", [check(expected=None)])
        with self.assertRaises(ValueError):
            request_digest("write", [put(data={1: "not-json-key"})])

    def test_reject_unsupported_and_overlarge_payload(self):
        with self.assertRaises(ValueError):
            request_digest("write", [])
        with self.assertRaises(ValueError):
            request_digest("write", [put(data={"payload": b"non-json"})])
        with self.assertRaises(ValueError):
            request_digest("write", [put(data={"payload": "a" * 1_048_577})])

    def test_delete_precondition_is_part_of_identity(self):
        self.assertNotEqual(request_digest("delete", [delete(expected=3)]),
                            request_digest("delete", [delete(expected=4)]))


if __name__ == "__main__":
    unittest.main()
