# SPDX-License-Identifier: GPL-3.0-or-later
"""Change control: data model validation, strict loading and policy (issue #19)."""
import pytest

from revisionworx.changes import fixtures
from revisionworx.changes.model import (AffectedItem, ChangeOrder, ChangeRequest, ItemType, MalformedRecord,
                                        ValidationError, clean_text, load_change, number_key)
from revisionworx.changes.workflow import Policy


# ---------------------------------------------------------------- text
def test_text_is_nfc_normalised_and_trimmed():
    assert clean_text("  Café  ", "title") == "Café"


@pytest.mark.parametrize("bad", ["a\x00b", "tab\there", "bell\x07", "del\x7f"])
def test_single_line_text_refuses_control_characters(bad):
    with pytest.raises(ValidationError, match="control character"):
        clean_text(bad, "title")


def test_multiline_text_allows_newlines_and_tabs_but_normalises_crlf():
    assert clean_text("line 1\r\nline 2\tx\r", "description", multiline=True) == "line 1\nline 2\tx"
    with pytest.raises(ValidationError):
        clean_text("a\x1bb", "description", multiline=True)


def test_unpaired_surrogate_is_refused_before_anything_is_written():
    with pytest.raises(ValidationError, match="invalid Unicode"):
        clean_text("bad \udc80", "title")


def test_length_limits_and_required_and_type():
    with pytest.raises(ValidationError, match="too long"):
        clean_text("x" * 201, "title")
    with pytest.raises(ValidationError, match="required"):
        clean_text("   ", "reason", required=True)
    with pytest.raises(ValidationError, match="must be text"):
        clean_text(42, "title")


def test_number_key_folds_case_width_and_dashes():
    assert number_key("ECR-0001") == number_key("ecr-0001") == number_key("ＥＣＲ－０００１")
    assert number_key("ECR‐0001") == number_key("ECR-0001")
    assert number_key("ECR-0001") != number_key("ECR-0010")


# ---------------------------------------------------------------- items
def test_affected_item_validation():
    with pytest.raises(ValidationError, match="unknown affected item type"):
        AffectedItem.create("widget", "x")
    with pytest.raises(ValidationError, match="needs the document's id"):
        AffectedItem.create("document", "DWG-1")
    with pytest.raises(ValidationError, match="documents only"):
        AffectedItem.create("part", "P-1", baseline_rev_id="rev_" + "0" * 32)
    with pytest.raises(ValidationError, match="not a revision id"):
        AffectedItem.create("document", "", "doc_" + "1" * 32, baseline_rev_id="../../etc")
    with pytest.raises(ValidationError, match="not a valid identifier"):
        AffectedItem.create("part", "P", "bad id with spaces")
    with pytest.raises(ValidationError, match="unknown proposed action"):
        AffectedItem.create("part", "P-1", action="explode")


def test_declared_item_keys_are_case_insensitive():
    a = AffectedItem.create("part", "p-100")
    b = AffectedItem.create(ItemType.PART, "P-100")
    assert a.key == b.key


# ---------------------------------------------------------------- strict loading
def _sample_records():
    svc, _, _, ids = fixtures.sample_memory_service()
    return svc.store.get("ecr", ids["draft"]), svc.store.get("eco", ids["order"])


def test_round_trip_is_exact():
    ecr, eco = _sample_records()
    for rec in (ecr, eco):
        loaded = load_change(rec)
        again = {**loaded.to_dict(), "_rev": rec["_rev"]}
        assert again == rec


@pytest.mark.parametrize("mutate, match", [
    (lambda r: r.update(schema=99), "schema"),
    (lambda r: r.update(kind="eco"), "kind"),
    (lambda r: r.update(status="approvedish"), "status"),
    (lambda r: r.update(id="ecr_XYZ"), "invalid ecr id"),
    (lambda r: r.update(reason=""), "reason is required"),
    (lambda r: r.update(title="t\x00"), "control character"),
    (lambda r: r.update(created="yesterday"), "ISO 8601"),
    (lambda r: r.update(affected="not a list"), "must be a list"),
    (lambda r: r.update(affected=[r["affected"][0], r["affected"][0]]), "duplicate affected item"),
    (lambda r: r.update(event_count=-1), "event_count"),
    (lambda r: r.update(event_count=True), "event_count"),
    (lambda r: r.update(priority="whenever"), "priority"),
    (lambda r: r.update(created_by="alice"), "actor"),
    (lambda r: r.update(dependencies=[{"change_id": "nope"}]), "dependency"),
    (lambda r: r.update(signoffs=[{"id": "s"}]), "signoff stage"),
])
def test_malformed_change_records_are_refused_not_half_loaded(mutate, match):
    ecr, _ = _sample_records()
    mutate(ecr)
    with pytest.raises(MalformedRecord, match=match):
        ChangeRequest.from_dict(ecr)


def test_malformed_order_records_are_refused():
    _, eco = _sample_records()
    bad = dict(eco, actions=[eco["actions"][0], eco["actions"][0]])
    with pytest.raises(MalformedRecord, match="duplicate action"):
        ChangeOrder.from_dict(bad)
    with pytest.raises(MalformedRecord, match="request ids"):
        ChangeOrder.from_dict(dict(eco, request_ids=["../x"]))
    with pytest.raises(MalformedRecord, match="required"):
        ChangeOrder.from_dict(dict(eco, actions=[dict(eco["actions"][0], required="yes")]))
    with pytest.raises(MalformedRecord, match="unknown change kind"):
        load_change({"kind": "memo"})
    with pytest.raises(MalformedRecord):
        load_change(["not", "a", "dict"])


# ---------------------------------------------------------------- policy
def test_default_policy_round_trips():
    assert Policy.from_dict(Policy().to_dict()) == Policy()


@pytest.mark.parametrize("settings, match", [
    ({"surprise": 1}, "unknown policy"),
    ({"ecr_approvals_required": -1}, "0 to 10"),
    ({"ecr_approvals_required": 1.5}, "whole number"),
    ({"ecr_approvals_required": True}, "whole number"),
    ({"require_distinct_approver": "yes"}, "true or false"),
    ({"ecr_prefix": "ECO-"}, "must differ"),
    ({"number_width": 0}, "1 to 9"),
    ({"close_requires_revision_state": "wip"}, "approved"),
    ({"categories": []}, "non-empty"),
    ({"categories": ["a", "A"]}, "unique"),
    ({"ecr_prefix": "x" * 17}, "16"),
])
def test_invalid_policies_are_refused(settings, match):
    with pytest.raises(ValidationError, match=match):
        Policy.from_dict(settings)


def test_policy_formats_numbers_and_matches_categories_case_insensitively():
    p = Policy.from_dict({"number_width": 6, "ecr_prefix": "CR "})
    assert p.format_number("ecr", 12) == "CR 000012"
    assert p.category("DOCUMENTATION") == "documentation"
    with pytest.raises(ValidationError, match="unknown category"):
        p.category("marketing")
