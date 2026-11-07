# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial numbering (docs/UNITS.md section 11): patterns, runs per period, numbers taken in the registration's own entry, claimed
serials skipped, races, retries and the command line."""
import contextlib
import io

import pytest

from revisionworx import units
from revisionworx.units import query
from revisionworx.units.__main__ import main as unit_main
from revisionworx.units.model import (MalformedUnitRecord, NotFound, ReplayRefused, ValidationError, check_pattern,
                                      load_numbering, numbering_id, pattern_period, render_pattern)
from revisionworx.workspace import ConflictError, RecordPut

TX1, TX2 = "d4" * 16, "e5" * 16


@pytest.fixture
def svc(ws):
    return units.service_for(ws, clock=lambda: "2026-11-07T09:00:00Z")


def entries(ws):
    return len(ws.journal_seqs())


def serials(u):
    return [i["value"] for i in u["identifiers"] if i["scheme"] == "serial"]


@pytest.mark.parametrize("pattern,n,day,value,period", [
    ("NH-{yy}-{seq:4}", 7, "2026-11-07", "NH-26-0007", "2026"),
    ("SN{yyyy}{mm}{seq:3}", 12, "2026-03-02", "SN202603012", "2026-03"),
    ("PUMP-{seq:5}", 3, "2026-01-01", "PUMP-00003", ""),
    ("X{seq}", 12345, "2026-01-01", "X12345", ""),
    ("A-{seq:2}", 100, "2026-01-01", "A-100", ""),                       # padding is a minimum, never a cut
])
def test_patterns_fill_and_run_per_period(pattern, n, day, value, period):
    assert render_pattern(check_pattern(pattern), n, day) == value and pattern_period(pattern, day) == period


@pytest.mark.parametrize("bad", ["NH-{yy}", "A{seq}{seq}", "A{seq:13}", "A{seq:0}", "A{seq}{x}", "A}{seq}", "",
                                 "X" * 95 + "{seq:12}"])
def test_bad_patterns_are_refused(bad):
    with pytest.raises(ValidationError):
        check_pattern(bad)


def test_units_take_the_next_serial_in_their_own_entry(ws, svc):
    num = svc.create_numbering("NH", "NH-{yy}-{seq:4}")
    assert num["id"] == numbering_id("nh") and svc.next_serial_preview("NH") == "NH-26-0001"
    n = entries(ws)
    first = svc.register(serial_from="NH", model={"part": "", "number": "HPU-75"})
    assert entries(ws) == n + 1 and serials(first) == ["NH-26-0001"]
    entry_ops = {(r["kind"], r["id"]) for r in ws.read_entry(ws.head_seq())["records"]}
    assert ("unitseq", num["id"]) in entry_ops                     # the counter moved in the registration's entry
    second = svc.register(serial_from="nh", milestones={"built": "2027-01-04"})
    assert serials(second) == ["NH-27-0001"]                       # the built date picks the year's run
    assert serials(svc.register(serial_from="NH")) == ["NH-26-0002"]
    assert svc.numbering("NH")["counters"] == {"2026": 2, "2027": 1}
    assert query.verify(svc) == []


def test_a_serial_already_claimed_is_skipped_and_never_reused(svc):
    svc.create_numbering("NH", "NH-{yy}-{seq:4}")
    svc.register(identifiers=[{"scheme": "serial", "value": "NH-26-0001", "issuer": ""}])     # an imported unit
    assert serials(svc.register(serial_from="NH")) == ["NH-26-0002"]
    made = svc.register_many([{"serial_from": "NH"}, {"serial_from": "NH"}])                  # one entry, two numbers
    assert [serials(u) for u in made] == [["NH-26-0003"], ["NH-26-0004"]]


def test_a_numbering_with_an_issuer_issues_that_issuers_serials(svc):
    svc.create_numbering("Parker", "P{seq:6}", issuer="Parker", start=500)
    u = svc.register(serial_from="Parker")
    assert [(i["value"], i["issuer"]) for i in u["identifiers"]] == [("P000500", "Parker")]
    with pytest.raises(ValidationError, match="not both"):
        svc.register(serial_from="Parker", identifiers=[{"scheme": "serial", "value": "X1", "issuer": "Parker"}])


def test_names_are_unique_and_unknown_ones_refused(ws, svc):
    svc.create_numbering("NH", "NH-{seq:4}")
    n = entries(ws)
    with pytest.raises(ValidationError, match="already"):
        svc.create_numbering("nh", "OTHER-{seq}")
    with pytest.raises(NotFound):
        svc.register(serial_from="ZZ")
    assert entries(ws) == n


def test_two_registrations_at_once_never_share_a_number(ws, svc, monkeypatch):
    """The numbering is written at the revision read: a number issued between this registration's read and its commit makes
    this one a conflict, and nothing of it is written."""
    svc.create_numbering("NH", "NH-{yy}-{seq:4}")
    real_commit = ws.commit
    other = units.service_for(ws, clock=lambda: "2026-11-07T09:00:00Z")
    state = {"raced": False}

    def racing_commit(op, ops, *args, **kwargs):
        if not state["raced"]:
            state["raced"] = True
            monkeypatch.setattr(ws, "commit", real_commit)
            other.register(serial_from="NH")
        return real_commit(op, ops, *args, **kwargs)

    monkeypatch.setattr(ws, "commit", racing_commit)
    with pytest.raises(ConflictError):
        svc.register(serial_from="NH")
    assert [serials(u) for u in svc.units()] == [["NH-26-0001"]]
    assert serials(svc.register(serial_from="NH")) == ["NH-26-0002"]          # trying again takes the next one


def test_a_retry_takes_no_second_number(ws, svc):
    svc.create_numbering("NH", "NH-{seq:4}", txid=TX1)
    n = entries(ws)
    assert svc.create_numbering("NH", "NH-{seq:4}", txid=TX1)["name"] == "NH" and entries(ws) == n
    with pytest.raises(ReplayRefused):
        svc.create_numbering("NH", "NH-{seq:5}", txid=TX1)
    u = svc.register(serial_from="NH", txid=TX2)
    again = svc.register(serial_from="NH", txid=TX2)
    assert again["id"] == u["id"] and serials(again) == ["NH-0001"] and svc.numbering("NH")["counters"] == {"": 1}


def test_a_damaged_numbering_is_refused_whole(ws, svc):
    num = svc.create_numbering("NH", "NH-{seq:4}")
    rec = ws.get("unitseq", num["id"])
    ws.commit("tamper", [RecordPut("unitseq", num["id"], {**{k: v for k, v in rec.items() if k != "_rev"},
                                                          "counters": {"soon": 3}}, expect_rev=rec["_rev"])])
    with pytest.raises(MalformedUnitRecord):
        svc.register(serial_from="NH")
    assert [p["code"] for p in query.verify(svc)] == ["malformed"]
    with pytest.raises(MalformedUnitRecord):
        load_numbering({**{k: v for k, v in rec.items() if k != "_rev"}, "id": numbering_id("other")})


def test_the_command_line_makes_numberings_and_issues_serials(share, ws):
    ws.close()

    def run(*args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = unit_main([str(share), *args])
        return code, out.getvalue(), err.getvalue()

    code, out, err = run("numbering", "new", "NH", "--pattern", "NH-{yyyy}-{seq:3}")
    assert code == 0, err
    assert "next serial NH-" in out
    code, out, _ = run("new", "--serial-from", "NH", "--model", "HPU-75", "--built", "2026-05-01")
    assert code == 0 and "registered NH-2026-001" in out
    code, out, _ = run("numbering", "next", "NH", "--on", "2026-06-01")
    assert out.strip() == "NH-2026-002"
    code, out, _ = run("numbering", "list")
    assert "NH-{yyyy}-{seq:3}" in out
    code, _, err = run("numbering", "new", "BAD", "--pattern", "NH-{yy}")
    assert code == 2 and "exactly one {seq}" in err
