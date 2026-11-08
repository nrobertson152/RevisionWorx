# SPDX-License-Identifier: GPL-3.0-or-later
"""Phones enrolled to accounts, and field records they sign (ADR-028; FieldWorx's PROTOCOL.md section 12).

An administrator enrols a phone after comparing its fingerprint; a record that phone signed is then accepted through
``link ... --field-signature`` as verified in its owner's name, and refused, with nothing written, whenever that can't be
shown. ``unit verify`` checks every such acceptance again."""
import io
import json

import pytest

from revisionworx import accounts, cli, devices, ed25519
from revisionworx.shared import NotPermitted
from revisionworx.units import query, service_for
from revisionworx.units.model import ValidationError, check_evidence
from revisionworx.workspace import RecordPut, Workspace, strip_rev
from team_helpers import PASSWORD, add_user, log_in, new_team

# RFC 8032 section 7.1, test 1: the seed and its public key. The fingerprint and the signature below are the family's
# vectors (FieldWorx spec/vectors/signatures.json), and the signature is the same from OpenSSL.
SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
KEY = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
FINGERPRINT = "F9A2-C52A-0A32-73C7"
SIGNATURE_OVER_AB = ("c33ed023945a591172ea9ca287b67122633e9c0c5dcddfb47b561f993bdb19dd"
                     "0fd58f65f72717fdc53e11a3d1f860ff5d6d962140f762a7d2b87fb873e9b406")
DEVICE = "dev_" + "1" * 32
OTHER_SEED = bytes(range(32))
OTHER_DEVICE = "dev_" + "2" * 32
RECORD = "e" * 64


def signed(event=RECORD, seed=SEED, device=DEVICE) -> str:
    return f"{device}:{event}:{ed25519.sign(seed, devices.signature_message(event)).hex()}"


def cited(event=RECORD) -> str:
    return f"{devices.RECORD_EVIDENCE}:{event}=reported by riley on 2026-11-07"


def link(capsys, *args):
    capsys.readouterr()
    code = cli.main(list(args))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def entries_in(folder) -> int:
    with Workspace.open(folder) as ws:
        return len(ws.journal_seqs())


@pytest.fixture
def team(tmp_path, capsys, monkeypatch):
    """A team workspace with boss, its administrator, logged in on this computer; riley has an account; unit NH-1."""
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"), ("APPDATA", "cfg"),
                     ("LOCALAPPDATA", "state")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
    folder = tmp_path / "ws"
    assert cli.main(["init", str(folder), "--name", "Team"]) == 0
    w = ["-w", str(folder)]
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main([*w, "team", "enable", "--admin", "boss", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "!\n"))
    assert cli.main([*w, "user", "add", "riley", "--display", "Riley Smith", "--password-stdin"]) == 0
    code, made = link(capsys, *w, "link", "unit-register", "--txid", "a1" * 16, "--serial", "NH-1", "--model", "HPU-75")
    assert code == 0, made
    return folder, w


def enrol(capsys, w, account="riley", device=DEVICE, key=KEY, fingerprint=FINGERPRINT, name="Riley's phone"):
    return link(capsys, *w, "link", "device-enrol", "--account", account, "--device", device, "--key", key,
                "--fingerprint", fingerprint, "--name", name)


def accept(capsys, w, txid="b2" * 16, signature=None, basis="verified", evidence=None):
    return link(capsys, *w, "link", "unit-fit", "NH-1", "P1", "--part-number", "PMP-10", "--basis", basis, "--txid", txid,
                "--evidence", evidence or cited(), "--field-signature", signature or signed())


# ------------------------------------------------------------------------------------------------ the vectors
def test_the_fingerprint_and_signature_are_the_familys_vectors():
    assert ed25519.public_key(SEED).hex() == KEY
    assert devices.fingerprint(KEY) == FINGERPRINT
    assert ed25519.sign(SEED, b"fieldworx/event-signature/v1\0" + b"ab" * 32).hex() == SIGNATURE_OVER_AB
    assert devices.signature_message("ab" * 32) == b"fieldworx/event-signature/v1\x00" + b"ab" * 32


# ------------------------------------------------------------------------------------------------ enrolling
def test_an_administrator_enrols_a_phone_and_a_record_it_signed_is_accepted_as_verified(team, capsys):
    folder, w = team
    code, made = enrol(capsys, w)
    assert code == 0 and made["result"] == "enrolled", made
    row = made["device"]
    assert (row["device"], row["account"], row["fingerprint"], row["name"], row["active"], row["added_by"]) == \
        (DEVICE, "riley", FINGERPRINT, "Riley's phone", True, "boss")
    code, fitted = accept(capsys, w)
    assert code == 0 and fitted["result"] == "recorded", fitted
    last = fitted["unit"]["history"][-1]
    assert last["basis"] == "verified" and last["actor"]["account"] == "boss"
    sig = signed().split(":")[2]
    assert {"kind": "field-signature", "ref": f"{DEVICE}:{RECORD}", "label": sig} in last["evidence"]
    assert {"kind": "field-signer", "ref": "riley", "label": "Riley's phone"} in last["evidence"]
    assert fitted["unit"]["components"][0]["basis"] == "verified"
    with Workspace.open(folder) as ws:
        assert query.verify(service_for(ws)) == []
    code, version = link(capsys, "link", "version")
    assert "field-signatures" in version["features"]


def test_enrolling_again_is_answered_and_writes_nothing(team, capsys):
    folder, w = team
    assert enrol(capsys, w)[0] == 0
    n = entries_in(folder)
    code, again = enrol(capsys, w)
    assert code == 0 and again["result"] == "already-enrolled" and again["device"]["device"] == DEVICE
    assert entries_in(folder) == n


@pytest.mark.parametrize("change, kind, says", [
    ({"fingerprint": "F9A2-C52A-0A32-73C8"}, "refused", "isn't this key's"),
    ({"fingerprint": "F9A2C52A0A3273C7"}, "refused", "four groups of four"),
    ({"key": "00" * 32 + "1"}, "refused", "isn't a phone's public key"),
    ({"device": "phone-1"}, "refused", "isn't a FieldWorx device id"),
    ({"account": "nobody"}, "not-found", "no account called nobody"),
])
def test_a_phone_that_cant_be_shown_to_be_the_one_in_hand_isnt_enrolled(team, capsys, change, kind, says):
    folder, w = team
    n = entries_in(folder)
    code, refused = enrol(capsys, w, **change)
    assert code == 2 and refused["kind"] == kind and says in refused["error"], refused
    assert entries_in(folder) == n


def test_a_device_or_key_is_enrolled_once_and_a_revoked_one_never_again(team, capsys, monkeypatch):
    folder, w = team
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "!\n"))
    assert cli.main([*w, "user", "add", "sam", "--password-stdin"]) == 0
    assert enrol(capsys, w)[0] == 0
    code, refused = enrol(capsys, w, account="sam")
    assert code == 2 and "already enrolled to riley" in refused["error"]
    other_key = ed25519.public_key(OTHER_SEED).hex()
    code, refused = enrol(capsys, w, account="sam", device=OTHER_DEVICE, key=KEY)
    assert code == 2 and f"already enrolled as phone {DEVICE}" in refused["error"]
    assert cli.main([*w, "user", "device", "revoke", "riley", DEVICE, "--reason", "lost on site"]) == 0
    code, refused = enrol(capsys, w)
    assert code == 2 and "starts again as a new device" in refused["error"]
    code, made = enrol(capsys, w, account="sam", device=OTHER_DEVICE, key=other_key,
                       fingerprint=devices.fingerprint(other_key))
    assert code == 0 and made["result"] == "enrolled"


def test_only_an_administrator_changes_whose_phone_is_whose(tmp_path):
    """No new rule: an account record is an administrator's to change (shared.judge), and a phone is part of one."""
    folder, boss_pc, _code, boss = new_team(tmp_path)
    add_user(folder, boss_pc, boss, "riley")
    riley_pc = tmp_path / "computers" / "riley"
    riley = log_in(folder, riley_pc, "riley")
    with Workspace.open(folder, writable=True, session=riley, state_dir=riley_pc) as ws:
        with pytest.raises(NotPermitted):
            devices.enrol(ws, "riley", DEVICE, KEY, FINGERPRINT)
        record = ws.get("account", "riley")
        mine = {**strip_rev(record), "devices": {DEVICE: {"key": KEY, "name": "", "added": "2026-11-07T00:00:00Z",
                                                          "added_by": "riley", "revoked": "", "revoked_by": "", "reason": ""}}}
        with pytest.raises(NotPermitted, match="only an administrator can change accounts"):
            ws.commit("account.device.enrol", [RecordPut("account", "riley", mine, expect_rev=record["_rev"])])
        assert devices.listing(ws) == []


def test_changing_an_account_keeps_its_phones(tmp_path):
    folder, boss_pc, code, boss = new_team(tmp_path)
    add_user(folder, boss_pc, boss, "riley")
    with Workspace.open(folder, writable=True, session=boss, state_dir=boss_pc) as ws:
        devices.enrol(ws, "riley", DEVICE, KEY, FINGERPRINT, "Riley's phone")
        accounts.set_role(ws, "riley", "admin")
        accounts.set_name(ws, "riley", "Riley Smith")
    riley_pc = tmp_path / "computers" / "riley"
    log_in(folder, riley_pc, "riley")                       # chooses a new password: the account record is rewritten
    accounts.recover_admin(folder, code, "riley", PASSWORD + "#", state_dir=riley_pc)
    with Workspace.open(folder) as ws:
        assert [(r["device"], r["account"], r["active"]) for r in devices.listing(ws)] == [(DEVICE, "riley", True)]


# ------------------------------------------------------------------------------------------------ accepting
@pytest.mark.parametrize("how, kind, says", [
    ("unenrolled", "signature", "isn't enrolled to anyone"),
    ("bad-signature", "signature", "doesn't hold"),
    ("another-record", "bad-input", "cite that record, and only it"),
    ("not-verified", "bad-input", "give --basis verified"),
    ("revoked", "signature", "was revoked (lost on site)"),
    ("disabled", "signature", "whose account is disabled"),
    ("malformed", "signature", "DEVICE:EVENT:SIG"),
])
def test_a_signature_that_cant_be_trusted_records_nothing(team, capsys, how, kind, says):
    folder, w = team
    assert enrol(capsys, w)[0] == 0
    signature, basis, evidence = signed(), "verified", cited()
    if how == "unenrolled":
        signature = signed(seed=OTHER_SEED, device=OTHER_DEVICE)
    elif how == "bad-signature":
        signature = signature[:-1] + ("0" if signature[-1] != "0" else "1")
    elif how == "another-record":
        evidence = cited("f" * 64)
    elif how == "not-verified":
        basis = "reported"
    elif how == "revoked":
        assert cli.main([*w, "user", "device", "revoke", "riley", DEVICE, "--reason", "lost on site"]) == 0
    elif how == "disabled":
        assert cli.main([*w, "user", "disable", "riley"]) == 0
    elif how == "malformed":
        signature = f"{DEVICE}:{RECORD}"
    n = entries_in(folder)
    code, refused = accept(capsys, w, signature=signature, basis=basis, evidence=evidence)
    assert code == 2 and refused["kind"] == kind and says in refused["error"], refused
    assert entries_in(folder) == n
    code, read = link(capsys, *w, "link", "unit", "NH-1")
    assert read["unit"]["components"] == []


def test_a_phone_enrolled_to_two_accounts_counts_for_neither_until_one_is_revoked(tmp_path):
    """Two administrators on two computers can enrol one phone to two people at the same moment: neither is chosen."""
    folder, boss_pc, _code, boss = new_team(tmp_path)
    add_user(folder, boss_pc, boss, "riley")
    add_user(folder, boss_pc, boss, "sam")
    entry = {"key": KEY, "name": "", "added": "2026-11-07T00:00:00Z", "added_by": "boss", "revoked": "", "revoked_by": "",
             "reason": ""}
    with Workspace.open(folder, writable=True, session=boss, state_dir=boss_pc) as ws:
        for who in ("riley", "sam"):                         # what two computers' enrolments add up to
            record = ws.get("account", who)
            ws.commit("account.device.enrol", [RecordPut("account", who, {**strip_rev(record), "devices": {DEVICE: entry}},
                                                         expect_rev=record["_rev"])])
        assert [r["problem"] for r in devices.listing(ws)] == ["enrolled to more than one account, so it counts for neither"] * 2
        with pytest.raises(devices.DeviceError, match="more than one account"):
            devices.signature_evidence(ws, signed())
        devices.revoke(ws, "sam", DEVICE, "enrolled to the wrong person")
        evidence, refusal = devices.signature_evidence(ws, signed())
        assert refusal == "" and evidence[1] == {"kind": "field-signer", "ref": "riley", "label": ""}


def test_signature_evidence_is_added_only_after_it_is_checked(team, capsys):
    """Typing the evidence by hand, through link or the unit command, is refused: only --field-signature adds it."""
    folder, w = team
    assert enrol(capsys, w)[0] == 0
    sig = signed().split(":")[2]
    n = entries_in(folder)
    code, refused = link(capsys, *w, "link", "unit-note", "NH-1", "seen on site", "--txid", "c3" * 16, "--basis", "verified",
                         "--evidence", cited(), "--evidence", f"field-signature:{DEVICE}:{RECORD}={sig}",
                         "--evidence", "field-signer:riley=Riley's phone")
    assert code == 2 and refused["kind"] == "bad-input" and "--field-signature" in refused["error"], refused
    capsys.readouterr()
    assert cli.main([*w, "unit", "note", "NH-1", "seen on site", "--basis", "verified",
                     "--evidence", "field-signer:riley"]) != 0
    assert "--field-signature" in capsys.readouterr().err
    assert entries_in(folder) == n
    with Workspace.open(folder) as ws:
        with pytest.raises(ValidationError, match="--field-signature"):
            service_for(ws)._meta(evidence=[{"kind": "field-signature", "ref": f"{DEVICE}:{RECORD}", "label": sig}])


def test_every_record_command_takes_a_field_signature(team, capsys):
    folder, w = team
    assert enrol(capsys, w)[0] == 0
    common = ["--basis", "verified", "--evidence", cited(), "--field-signature", signed()]
    for i, args in enumerate([["unit-note", "NH-1", "seen on site"], ["unit-milestone", "NH-1", "installed", "--on", "2026-11-07"],
                              ["unit-register", "--serial", "NH-2"]]):
        code, out = link(capsys, *w, "link", *args, "--txid", f"{i:02d}" * 16, *common)
        assert code == 0 and out["result"] == "recorded", out
    with Workspace.open(folder) as ws:
        assert query.verify(service_for(ws)) == [{"code": "signature-reused", "subject": RECORD, "message":
                                                  f"signed field record {DEVICE}:{RECORD} is cited by 3 different actions: "
                                                  "a field record is accepted once"}]


def test_a_retry_after_the_phone_is_revoked_answers_what_was_recorded(team, capsys):
    folder, w = team
    assert enrol(capsys, w)[0] == 0
    code, first = accept(capsys, w)
    assert code == 0 and first["result"] == "recorded"
    assert cli.main([*w, "user", "device", "revoke", "riley", DEVICE, "--reason", "lost on site"]) == 0
    n = entries_in(folder)
    code, again = accept(capsys, w)
    assert code == 0 and again["result"] == "already-recorded" and again["events"] == first["events"], again
    code, refused = link(capsys, *w, "link", "unit-note", "NH-1", "seen on site", "--txid", "c3" * 16, "--basis", "verified",
                         "--evidence", cited(), "--field-signature", signed())
    assert code == 2 and refused["kind"] == "signature" and "was revoked" in refused["error"]
    assert entries_in(folder) == n
    with Workspace.open(folder) as ws:
        assert query.verify(service_for(ws)) == []           # a revocation doesn't undo what was accepted before


def test_a_workspace_without_accounts_has_no_phones_to_trust(tmp_path, capsys, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "cfg"), ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"), ("APPDATA", "cfg"),
                     ("LOCALAPPDATA", "state")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    folder = tmp_path / "ws"
    assert cli.main(["init", str(folder), "--name", "Solo"]) == 0
    w = ["-w", str(folder)]
    assert link(capsys, *w, "link", "unit-register", "--txid", "a1" * 16, "--serial", "NH-1")[0] == 0
    code, refused = accept(capsys, w)
    assert code == 2 and refused["kind"] == "signature" and "isn't set up for a team" in refused["error"]
    code, listed = link(capsys, *w, "link", "devices")
    assert code == 0 and listed["team"] is False and listed["devices"] == []
    code, refused = enrol(capsys, w)
    assert code == 2 and "isn't set up for a team" in refused["error"]


def test_the_reference_client_enrols_and_accepts(team, capsys):
    """The client other programs copy, running `python -m revisionworx` in a real separate process."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "contrib"))
    import rwx_client
    folder, _w = team
    rwx = rwx_client.RevisionWorx(executable=None, workspace=str(folder))
    rwx.command = [sys.executable, "-m", "revisionworx"]
    assert rwx.has("field-signatures")
    made = rwx.device_enrol("riley", DEVICE, KEY, FINGERPRINT, name="Riley's phone")
    assert made["result"] == "enrolled" and rwx.devices()[0]["fingerprint"] == FINGERPRINT
    with pytest.raises(rwx_client.RwxError) as exc:
        rwx.unit_note("NH-1", "seen", "c3" * 16, basis="verified", evidence=[cited()],
                      field_signature=signed(seed=OTHER_SEED))
    assert exc.value.kind == "signature"
    noted = rwx.unit_note("NH-1", "seen", "c3" * 16, basis="verified", evidence=[cited()], field_signature=signed())
    assert noted["result"] == "recorded" and noted["unit"]["history"][-1]["basis"] == "verified"


# ------------------------------------------------------------------------------------------------ listing and revoking
def test_listing_and_revoking_phones(team, capsys):
    folder, w = team
    assert enrol(capsys, w)[0] == 0
    capsys.readouterr()
    assert cli.main([*w, "user", "device", "list"]) == 0
    text = capsys.readouterr().out
    assert DEVICE in text and FINGERPRINT in text and "Riley's phone" in text and "enrolled" in text
    assert cli.main([*w, "user", "device", "revoke", "riley", DEVICE, "--reason", "lost on site"]) == 0
    assert "Its signatures are refused from now on" in capsys.readouterr().out
    n = entries_in(folder)
    assert cli.main([*w, "user", "device", "revoke", "riley", DEVICE, "--reason", "lost on site"]) == 0
    assert "was already revoked" in capsys.readouterr().out and entries_in(folder) == n
    code, listed = link(capsys, *w, "link", "devices")
    row = listed["devices"][0]
    assert code == 0 and listed["team"] is True and row["active"] is False and row["reason"] == "lost on site"
    assert row["revoked_by"] == "boss" and row["revoked"]
    assert cli.main([*w, "user", "device", "list", "boss"]) == 0
    assert "No phones are enrolled to boss" in capsys.readouterr().out


def test_a_damaged_device_entry_vouches_for_nothing_and_is_kept(tmp_path):
    folder, boss_pc, _code, boss = new_team(tmp_path)
    add_user(folder, boss_pc, boss, "riley")
    with Workspace.open(folder, writable=True, session=boss, state_dir=boss_pc) as ws:
        record = ws.get("account", "riley")
        damaged = {DEVICE: {"key": KEY, "name": ""}}                 # missing fields
        ws.commit("account.rename", [RecordPut("account", "riley", {**strip_rev(record), "devices": damaged},
                                               expect_rev=record["_rev"])])
        assert devices.listing(ws) == []
        with pytest.raises(devices.DeviceError, match="isn't enrolled"):
            devices.signature_evidence(ws, signed())
        with pytest.raises(devices.DeviceError, match="already enrolled to riley"):
            devices.enrol(ws, "riley", DEVICE, KEY, FINGERPRINT)
        other_key = ed25519.public_key(OTHER_SEED).hex()
        devices.enrol(ws, "riley", OTHER_DEVICE, other_key, devices.fingerprint(other_key))
        assert ws.get("account", "riley")["devices"][DEVICE] == {"key": KEY, "name": ""}


# ------------------------------------------------------------------------------------------------ unit verify
@pytest.fixture
def signed_register(tmp_path):
    """A team workspace with riley's phone enrolled and unit NH-1, and a way to write signature evidence as a client that
    skips the checks would: what ``unit verify`` is for."""
    folder, boss_pc, _code, boss = new_team(tmp_path)
    add_user(folder, boss_pc, boss, "riley")
    add_user(folder, boss_pc, boss, "sam")
    with Workspace.open(folder, writable=True, session=boss, state_dir=boss_pc) as ws:
        devices.enrol(ws, "riley", DEVICE, KEY, FINGERPRINT, "Riley's phone")
        other_key = ed25519.public_key(OTHER_SEED).hex()
        devices.enrol(ws, "sam", OTHER_DEVICE, other_key, devices.fingerprint(other_key))
        unit = service_for(ws).register(identifiers=[{"scheme": "serial", "value": "NH-1"}])

    def write(evidence, basis="verified", txid=None):
        with Workspace.open(folder, writable=True, session=boss, state_dir=boss_pc) as ws:
            svc = service_for(ws)
            svc.signed_evidence = check_evidence(evidence)          # skipping link's checks
            svc.note(unit["id"], "seen on site", txid=txid, basis=basis, evidence=evidence)

    def problems():
        """``(code, what's wrong)``, without the unit and event a signature-invalid problem starts with."""
        with Workspace.open(folder) as ws:
            return [(p["code"], p["message"].split(": ", 2)[-1] if p["code"] == "signature-invalid" else p["message"])
                    for p in query.verify(service_for(ws))]

    return write, problems


def _evidence(record=RECORD, signature=None, signer="riley", cite=None):
    """Signature evidence as an accepted record carries it; ``cite`` is the record it cites (the signed one by default)."""
    device, event, sig = (signature or signed(record)).split(":")
    return [{"kind": "field-signature", "ref": f"{device}:{event}", "label": sig},
            {"kind": "field-signer", "ref": signer, "label": ""},
            {"kind": devices.RECORD_EVIDENCE, "ref": cite or record, "label": ""}]


def test_unit_verify_finds_a_signature_accepted_without_its_checks(signed_register):
    write, problems = signed_register
    write(_evidence())
    assert problems() == []
    write(_evidence(record="d" * 64, signature=signed("d" * 64)[:-2] + "00"))
    write(_evidence(record="c" * 64, signer="sam"))
    write(_evidence(record="b" * 64, cite="a" * 64))
    write(_evidence(record="9" * 64), basis="reported")
    write(_evidence(record="8" * 64)[::2])                       # the signature and the record, no signer
    write(_evidence(record="7" * 64, signature=signed("7" * 64, device="dev_" + "3" * 32)))
    assert problems() == [
        ("signature-invalid", f"the field signature on record {'d' * 12} doesn't hold with the key enrolled for phone {DEVICE}"),
        ("signature-invalid", f"the field signature is phone {DEVICE}'s, enrolled to riley, not to sam"),
        ("signature-invalid", f"the field signature is over record {'b' * 12}, not the record it cites"),
        ("signature-invalid", "it carries a field signature, but its basis is reported, not verified"),
        ("signature-invalid", "a field signature needs exactly one signature and one signer"),
        ("signature-invalid", f"the field signature names phone {'dev_' + '3' * 32}, which isn't enrolled to anyone"),
    ]


def test_unit_verify_finds_one_field_record_accepted_twice(signed_register):
    write, problems = signed_register
    write(_evidence(), txid="d4" * 16)
    write(_evidence(), txid="e5" * 16)
    assert problems() == [("signature-reused", f"signed field record {DEVICE}:{RECORD} is cited by 2 different actions: a "
                                               "field record is accepted once")]
