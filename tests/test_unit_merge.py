# SPDX-License-Identifier: GPL-3.0-or-later
"""Merged identities (docs/UNITS.md section 15): two records turn out to be one physical unit. One entry moves the duplicate's
identifiers, and their claims, to the unit that stays; each keeps its own history; the duplicate takes no more actions, and its
earlier id still finds the unit. Voiding the merge undoes it, in one entry."""
import contextlib
import io
import json

import pytest

from revisionworx import integration
from revisionworx.cli import main as rwx_main
from revisionworx.gui.viewmodels import unit_sections
from revisionworx.units import deviation, history as H, pack, query
from revisionworx.units.model import MERGED, ValidationError, key_id, load_key
from revisionworx.units.service import KIND, _Tx, service_for

AST = "ast_" + "a" * 32


@pytest.fixture
def pair(ws):
    """P-101, imported from an asset register (a customer's tag and a FieldWorx id), and SN-500, which a work order registered:
    the same pump."""
    svc = service_for(ws)
    imported = svc.register(identifiers=[{"scheme": "tag", "issuer": "Acme", "value": "P-101"},
                                         {"scheme": "fieldworx", "issuer": "", "value": AST}],
                            name="Feed pump", customer="Acme", status="in-service")
    built = svc.register(identifiers=[{"scheme": "serial", "issuer": "", "value": "SN-500"}], name="Pump",
                         status="in-service", model={"part": "", "number": "PMP-11"})
    svc.note(imported["id"], "inspected 2026-10-01")
    return {"svc": svc, "dup": imported["id"], "keep": built["id"]}


def claim_owner(svc, scheme, issuer, value):
    rec = svc.ws.get(KIND["key"], key_id(scheme, issuer, value))
    return load_key(rec)["owner"] if rec else None


def test_a_merge_moves_identifiers_and_claims_in_one_entry_and_keeps_both_histories(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    before = svc.events(dup)
    head = ws.head_seq()
    svc.merge(dup, keep, "the same pump: the tag is on the casting of SN-500")
    assert ws.head_seq() == head + 1
    d, k = svc.unit(dup), svc.unit(keep)
    assert d["status"] == MERGED and d["identifiers"] == []
    assert [(i["scheme"], i["value"], i["active"]) for i in k["identifiers"]] == [
        ("serial", "SN-500", True), ("tag", "P-101", True), ("fieldworx", AST, True)]
    assert claim_owner(svc, "tag", "Acme", "P-101") == keep and claim_owner(svc, "fieldworx", "", AST) == keep
    assert svc.events(dup)[:len(before)] == before                      # nothing rewritten
    assert svc.events(dup)[-1]["type"] == "unit.merged" and svc.events(keep)[-1]["type"] == "unit.absorbed"
    assert svc.resolve("P-101")["id"] == keep and svc.resolve(AST)["id"] == keep
    assert svc.resolve(dup)["id"] == dup and svc.resolve(dup, follow=True)["id"] == keep
    assert query.unit_detail(svc, svc.unit(dup))["history"][-1]["summary"] == \
        "merged into SN-500 (moved: tag P-101, fieldworx " + AST + ")"
    assert query.unit_detail(svc, svc.unit(keep))["history"][-1]["summary"] == \
        f"{dup[:12]} merged into this unit (took over: tag P-101, fieldworx {AST})"
    assert H.merged_into(svc.events(dup))["unit"] == keep
    assert query.verify(svc) == []


def test_the_duplicate_takes_no_more_actions(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.merge(dup, keep, "same pump")
    for act in (lambda: svc.note(dup, "x"), lambda: svc.milestone(dup, "shipped", "2026-11-01"),
                lambda: svc.set_status(dup, "retired", reason="x"), lambda: svc.install(dup, "M1", number="MOT-1"),
                lambda: svc.update(dup, name="x"), lambda: svc.merge(keep, dup, "back the other way")):
        with pytest.raises(ValidationError, match=r"was merged into unit SN-500 \(unt_[0-9a-f]{32}\): record it there"):
            act()
    with pytest.raises(ValidationError, match="merged into unit SN-500"):
        svc.merge(svc.register(identifiers=[{"scheme": "serial", "issuer": "", "value": "SN-9"}])["id"], dup, "into it")


def test_a_merged_unit_drops_out_of_lists_and_queries_but_its_past_doesnt(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.install(dup, "M1", number="MOT-7", serial="M-1")
    svc.remove(dup, "M1", "returned")
    svc.merge(dup, keep, "same pump")
    assert [u["id"] for u in query.search(svc, "")] == [keep]
    assert dup not in {u["id"] for u in svc.units()} and dup in {u["id"] for u in svc.scan()[0]}
    rows = query.units_with_component(svc, number="MOT-7", history=True)
    assert [(r["unit"]["id"], r["unit"]["status"]) for r in rows] == [(dup, MERGED)]   # where it was fitted then
    assert query.units_with_component(svc, number="MOT-7") == []


def test_the_detail_shows_the_merge_and_the_earlier_id_is_an_alias(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.merge(dup, keep, "same pump")
    k, d = query.unit_detail(svc, svc.unit(keep)), query.unit_detail(svc, svc.unit(dup))
    assert [m["unit"] for m in k["merged_from"]] == [dup] and k["merged_into"] is None
    assert k["aliases"] == sorted([AST, dup])
    assert d["merged_into"]["unit"] == keep and d["merged_into"]["label"] == "SN-500" and d["aliases"] == []
    lines = [line for _h, body in unit_sections(k) for line in body]
    assert any(line.startswith(f"Merged into this unit: {dup[:12]} ({dup})") for line in lines)
    assert any("Merged into SN-500" in line for _h, body in unit_sections(d) for line in body)


@pytest.mark.parametrize("side", ["survivor", "duplicate"])
def test_voiding_the_merge_undoes_it_in_one_entry(ws, pair, side):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    before = {dup: svc.unit(dup), keep: svc.unit(keep)}
    svc.merge(dup, keep, "same pump")
    target = keep if side == "survivor" else dup
    n = svc.unit(target)["events"]["count"]
    head = ws.head_seq()
    svc.void(target, [n], "they are two pumps after all")
    assert ws.head_seq() == head + 1
    for uid in (dup, keep):
        now = svc.unit(uid)
        assert {k: v for k, v in now.items() if k not in ("events", "updated", "_rev")} == \
            {k: v for k, v in before[uid].items() if k not in ("events", "updated", "_rev")}
    assert claim_owner(svc, "tag", "Acme", "P-101") == dup and claim_owner(svc, "fieldworx", "", AST) == dup
    assert svc.resolve("P-101")["id"] == dup and query.verify(svc) == []
    svc.note(dup, "takes actions again")


def test_a_merge_isnt_undone_over_identifier_changes_made_since(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.merge(dup, keep, "same pump")
    n = svc.unit(keep)["events"]["count"]
    svc.retire_identifier(keep, "tag", "P-101", issuer="Acme", reason="tag replaced")
    with pytest.raises(ValidationError, match="identifier changes or merges since"):
        svc.void(keep, [n], "two pumps")


def test_refused_until_a_person_has_settled_it(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    with pytest.raises(ValidationError, match="a reason is required"):
        svc.merge(dup, keep, "")
    with pytest.raises(ValidationError, match="into itself"):
        svc.merge(dup, dup, "x")
    svc.install(dup, "M1", number="MOT-7")
    with pytest.raises(ValidationError, match="has something fitted"):
        svc.merge(dup, keep, "same pump")
    svc.remove(dup, "M1", "stock")
    frame = svc.register(identifiers=[{"scheme": "serial", "issuer": "", "value": "FR-1"}])
    svc.install(frame["id"], "P1", component=dup)
    with pytest.raises(ValidationError, match="is fitted in unit FR-1"):
        svc.merge(dup, keep, "same pump")
    svc.remove(frame["id"], "P1", "stock")
    deviation.approve(svc, "concession", title="Weld", departure="weld out of tolerance", reason="passed load test",
                      units=[dup])
    with pytest.raises(ValidationError, match="covered by a deviation or concession"):
        svc.merge(dup, keep, "same pump")
    other = svc.register(identifiers=[{"scheme": "serial", "issuer": "", "value": "SN-1"}])
    second = svc.register(identifiers=[{"scheme": "serial", "issuer": "", "value": "SN-2"}])
    with pytest.raises(ValidationError, match="SN-1 has serial SN-1 and SN-2 has serial SN-2: retire the wrong one first"):
        svc.merge(other["id"], second["id"], "same")
    assert svc.unit(dup)["status"] != MERGED and query.verify(svc) == []


def test_unit_verify_finds_a_merge_recorded_on_one_side_only(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.merge(dup, keep, "same pump")
    n = svc.unit(keep)["events"]["count"]
    tx = _Tx(svc, None, "", svc._meta(reason="a modified program"))   # what the service refuses to write alone
    tx.unit(keep)
    tx.emit(keep, "void", {"events": [n]})
    ws.commit("t", tx.ops())
    assert "merge-mismatch" in {p["code"] for p in query.verify(svc)}


def test_other_programs_naming_the_earlier_id_reach_the_unit_that_stayed(share, ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.merge(dup, keep, "same pump")
    ws.close()
    answer = integration.unit(dup, workspace=share)
    assert answer["unit"]["id"] == keep and answer["named"] == {"unit": dup, "merged_into": keep}
    assert dup in answer["unit"]["aliases"]
    done = integration.unit_note(dup, "seal replaced on site", txid="0" * 31 + "1", workspace=share,
                                 evidence=["fieldworx-event:fev_" + "b" * 32])
    assert done["unit"]["id"] == keep
    h = done["unit"]["history"][-1]
    assert h["summary"] == "seal replaced on site"
    assert {"kind": "merged-unit", "ref": dup, "label": "recorded against this earlier id; merged into SN-500"} in h["evidence"]
    listed = integration.list_units(workspace=share, detail=True)["units"]
    assert [u["id"] for u in listed] == [keep] and listed[0]["aliases"] == sorted([AST, dup])


def test_a_merged_unit_gets_no_handover_pack(ws, pair, tmp_path):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    svc.merge(dup, keep, "same pump")
    with pytest.raises(ValidationError, match="record it there"):
        pack.make(svc, dup, tmp_path / "pack")
    assert not (tmp_path / "pack").exists() and pack.make(svc, "P-101", tmp_path / "keep")["unit"] == "SN-500"


def test_a_merge_retried_with_its_txid_isnt_written_twice(ws, pair):
    svc, dup, keep = pair["svc"], pair["dup"], pair["keep"]
    txid = "c" * 32
    svc.merge(dup, keep, "same pump", txid=txid)
    head = ws.head_seq()
    svc.merge(dup, keep, "same pump", txid=txid)
    assert svc.last_replayed and ws.head_seq() == head


def test_the_command_line_merges_shows_and_refuses(share, ws, pair):
    dup, keep = pair["dup"], pair["keep"]
    ws.close()

    def run(*args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rwx_main(["-w", str(share), "unit", *args])
        return code, buf.getvalue()
    code, text = run("merge", "P-101", "--into", "SN-500", "--reason", "same pump")
    assert code == 0 and f"merged P-101 ({dup}) into SN-500" in text
    code, text = run("show", dup)
    assert code == 0 and "MERGED into SN-500" in text
    code, text = run("show", "P-101")
    assert code == 0 and text.startswith("SN-500") and f"({dup}) was merged into this unit" in text
    code, _ = run("note", dup, "too late")
    assert code == 2
    code, text = run("history", dup, "--json")
    assert code == 0 and json.loads(text)[-1]["type"] == "unit.merged"
