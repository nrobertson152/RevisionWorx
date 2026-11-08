# SPDX-License-Identifier: GPL-3.0-or-later
"""Phones enrolled to accounts, and the field signatures they make (ADR-028; FieldWorx's PROTOCOL.md section 12).

A FieldWorx phone signs each record it makes with its own Ed25519 key. An administrator of a team workspace enrols the
phone to the person's account, after comparing the fingerprint the phone shows; the enrolment lives in the account record
(``devices``), which only an administrator may change under the rules every computer already applies (``shared.judge``),
so this adds no rule and no format. A record signed by an enrolled phone can then be accepted as *verified* in that
person's name: :func:`signature_evidence` is what decides, and ``unit verify`` checks every such acceptance again.

A device enrolled to two accounts counts as enrolled to neither. A revoked device stays revoked (its key is kept, so what
it signed before can still be checked); a phone that changes hands starts again as a new device. Revoking a device, or
disabling its account, refuses its signatures on anything new; a retry of an acceptance already recorded still answers
what was recorded.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from . import ed25519
from .shared import SharedWorkspace
from .workspace import RecordPut, now_iso, strip_rev

#: What a FieldWorx phone signs: these bytes, then the record's id as 64 ASCII hex digits (its PROTOCOL.md section 12.2).
SIGNATURE_DOMAIN = b"fieldworx/event-signature/v1\0"
FINGERPRINT_DOMAIN = b"fieldworx/key-fingerprint/v1\0"
#: The evidence kinds an accepted signed record carries on a unit event (docs/UNITS.md section 8), and the one that names
#: the field record itself, which the signature must be over.
SIGNATURE_EVIDENCE, SIGNER_EVIDENCE, RECORD_EVIDENCE = "field-signature", "field-signer", "fieldworx-event"
_DEVICE = re.compile(r"^dev_[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX128 = re.compile(r"^[0-9a-f]{128}$")
_FINGERPRINT = re.compile(r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
ENTRY_FIELDS = {"key", "name", "added", "added_by", "revoked", "revoked_by", "reason"}
NAME_LIMIT, REASON_LIMIT = 100, 2000


class DeviceError(ValueError):
    """Nothing was changed: what was asked can't be done, or a signature can't be trusted. The message says why."""


# ---------------------------------------------------------------------------------------------------- keys
def fingerprint(key_hex: str) -> str:
    """What the phone shows and a person compares: the first 8 bytes of SHA-256 of the domain and the public key, in four
    groups of four uppercase hex digits."""
    digest = hashlib.sha256(FINGERPRINT_DOMAIN + bytes.fromhex(key_hex)).hexdigest()[:16].upper()
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))


def check_key(key_hex: str) -> str:
    key = str(key_hex or "").strip().lower()
    if not _HEX64.match(key) or ed25519._decode(bytes.fromhex(key)) is None:
        raise DeviceError("that isn't a phone's public key (64 hex digits of an Ed25519 key)")
    return key


def check_device_id(device: str) -> str:
    device = str(device or "").strip().lower()
    if not _DEVICE.match(device):
        raise DeviceError(f"{device!r} isn't a FieldWorx device id (dev_ and 32 hex digits)")
    return device


def _text(value: str, what: str, limit: int, required: bool = False) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if required and not text:
        raise DeviceError(f"give a {what}")
    if _CTRL.search(text) or len(text) > limit:
        raise DeviceError(f"the {what} must be at most {limit} characters, with no control characters")
    return text


# ---------------------------------------------------------------------------------------------------- reading
def entries(account: dict) -> tuple[dict[str, dict], list[str]]:
    """An account record's devices, each exactly the documented shape, and what was wrong with any that isn't (those are
    left out, so a damaged entry can never vouch for anything)."""
    raw = account.get("devices", {})
    if not isinstance(raw, dict):
        return {}, ["its devices aren't a list of devices"]
    good, problems = {}, []
    for device, e in raw.items():
        ok = (isinstance(device, str) and _DEVICE.match(device) and isinstance(e, dict) and set(e) == ENTRY_FIELDS
              and all(isinstance(e[k], str) for k in ENTRY_FIELDS) and _HEX64.match(e["key"])
              and (e["revoked"] == "") == (e["revoked_by"] == ""))
        if ok:
            good[device] = e
        else:
            problems.append(f"device {str(device)[:40]!r} isn't recorded the way it should be")
    return good, problems


def accounts_of(ws) -> dict[str, dict]:
    """Account id -> record in a team workspace; empty in one that isn't set up for a team."""
    return {rid: strip_rev(rec) for rid, rec in ws.list("account").items()} if isinstance(ws, SharedWorkspace) else {}


def _holders(accounts: dict[str, dict], device: str) -> list[tuple[str, dict, dict]]:
    """``(account id, account, entry)`` for every account the device is enrolled to, revoked or not."""
    return [(acc_id, acc, e) for acc_id, acc in sorted(accounts.items()) for d, e in entries(acc)[0].items() if d == device]


def listing(ws) -> list[dict]:
    """Every device enrolled in the workspace, with its account and whether its signatures count now."""
    accounts = accounts_of(ws)
    rows = []
    for acc_id, acc in sorted(accounts.items()):
        for device, e in sorted(entries(acc)[0].items()):
            current = [h for h in _holders(accounts, device) if not h[2]["revoked"]]
            problem = ("" if e["revoked"] else
                       "enrolled to more than one account, so it counts for neither" if len(current) > 1 else
                       "the account is disabled" if acc.get("active") is not True else "")
            rows.append({"device": device, "account": acc_id, "account_name": acc.get("name", acc_id), "name": e["name"],
                         "fingerprint": fingerprint(e["key"]), "key": e["key"], "added": e["added"], "added_by": e["added_by"],
                         "revoked": e["revoked"], "revoked_by": e["revoked_by"], "reason": e["reason"],
                         "active": not e["revoked"] and not problem, "problem": problem})
    return rows


def signature_message(event_id: str) -> bytes:
    return SIGNATURE_DOMAIN + event_id.encode("ascii")


def _holds(entry: dict, event: str, sig: str) -> bool:
    return ed25519.verify(bytes.fromhex(entry["key"]), signature_message(event), bytes.fromhex(sig))


def parse_signature(text: str) -> tuple[str, str, str]:
    """``DEVICE:EVENT:SIG`` as given on a command line."""
    parts = str(text or "").strip().lower().split(":")
    if len(parts) != 3 or not _DEVICE.match(parts[0]) or not _HEX64.match(parts[1]) or not _HEX128.match(parts[2]):
        raise DeviceError("a field signature is DEVICE:EVENT:SIG: dev_ and 32 hex digits, the record's 64-hex id, and the "
                          "128-hex signature")
    return parts[0], parts[1], parts[2]


def signature_evidence(ws, text: str) -> tuple[list[dict], str]:
    """``(evidence, refusal)`` for a signed field record. The evidence is what a unit event carries once the signature is
    shown to hold, over the record's id, with the key enrolled for the device: ``field-signature`` (``DEVICE:EVENT``,
    labelled with the signature) and ``field-signer`` (the account, labelled with the device's name). ``refusal`` is why
    the device's signatures can't be used for anything *new* (revoked, or its account disabled), or ``""``: a retry of an
    acceptance already recorded still needs the same evidence to answer what was recorded.

    A DeviceError, and nothing to record, when this isn't a team workspace, the device isn't enrolled, it is enrolled to
    more than one account, or the signature doesn't hold (fail closed)."""
    device, event, sig = parse_signature(text)
    if not isinstance(ws, SharedWorkspace):
        raise DeviceError("this workspace isn't set up for a team, so it has no accounts to enrol phones to; accept the "
                          "record as reported")
    holders = _holders(accounts_of(ws), device)
    if not holders:
        raise DeviceError(f"phone {device} isn't enrolled to anyone in this workspace; an administrator can enrol it")
    current = [h for h in holders if not h[2]["revoked"]]
    if len(current) > 1:
        raise DeviceError(f"phone {device} is enrolled to more than one account ({', '.join(h[0] for h in current)}), so "
                          "its signatures count for neither until an administrator revokes the wrong one")
    if not current and len(holders) > 1:
        raise DeviceError(f"phone {device} was enrolled to more than one account and is revoked from all of them")
    acc_id, acc, e = current[0] if current else holders[0]
    if not _holds(e, event, sig):
        raise DeviceError(f"the signature on field record {event[:12]} doesn't hold with the key enrolled for phone {device}: "
                          "the record wasn't made on that phone, or it was changed")
    refusal = (f"phone {device} was revoked ({e['reason'] or 'no reason given'}), so its signatures no longer count"
               if e["revoked"] else
               f"phone {device} is enrolled to {acc_id}, whose account is disabled" if acc.get("active") is not True else "")
    return ([{"kind": SIGNATURE_EVIDENCE, "ref": f"{device}:{event}", "label": sig},
             {"kind": SIGNER_EVIDENCE, "ref": acc_id, "label": e["name"]}], refusal)


def recheck(accounts: dict[str, dict], event: dict, cache: dict | None = None) -> str | None:
    """For ``unit verify``: what is wrong with a unit event's field signature, or None (also when it has none). It must be
    one signature and one signer; the event's basis ``verified``; any ``fieldworx-event`` it cites the record signed; and the
    signature must hold with the key the signer's account enrolled for the device, revoked or not (a revocation doesn't undo
    what was accepted before). ``accounts``: account id -> record; ``cache`` saves checking one signature twice."""
    evidence = event.get("evidence") or []
    sigs = [x for x in evidence if isinstance(x, dict) and x.get("kind") == SIGNATURE_EVIDENCE]
    signers = [x for x in evidence if isinstance(x, dict) and x.get("kind") == SIGNER_EVIDENCE]
    if not sigs and not signers:
        return None
    if len(sigs) != 1 or len(signers) != 1:
        return "a field signature needs exactly one signature and one signer"
    device, _, record = str(sigs[0].get("ref", "")).partition(":")
    sig, account = str(sigs[0].get("label", "")), str(signers[0].get("ref", ""))
    if not _DEVICE.match(device) or not _HEX64.match(record) or not _HEX128.match(sig):
        return "the field signature is malformed"
    if event.get("basis") != "verified":
        return f"it carries a field signature, but its basis is {event.get('basis')}, not verified"
    cited = {str(x.get("ref", "")) for x in evidence if isinstance(x, dict) and x.get("kind") == RECORD_EVIDENCE}
    if cited and cited != {record}:
        return f"the field signature is over record {record[:12]}, not the record it cites"
    holders = _holders(accounts, device)
    if not holders:
        return f"the field signature names phone {device}, which isn't enrolled to anyone"
    cache = {} if cache is None else cache

    def holds(e: dict) -> bool:
        key = (e["key"], record, sig)
        if key not in cache:
            cache[key] = _holds(e, record, sig)
        return cache[key]

    if any(acc_id == account and holds(e) for acc_id, _acc, e in holders):
        return None
    others = [acc_id for acc_id, _acc, e in holders if holds(e)]
    if others:
        return f"the field signature is phone {device}'s, enrolled to {others[0]}, not to {account}"
    return f"the field signature on record {record[:12]} doesn't hold with the key enrolled for phone {device}"


# ---------------------------------------------------------------------------------------------------- changes (admin)
def _raw(record: dict, account: str) -> dict:
    raw = record.get("devices", {})
    if not isinstance(raw, dict):
        raise DeviceError(f"{account}'s phones aren't recorded the way they should be, so nothing was changed")
    return dict(raw)


def _commit(ws, op: str, account: str, record: dict, devices: dict) -> int:
    """Written at the revision read, so a change made meanwhile on another computer is never overwritten: that's a
    ConflictError, and nothing is written."""
    new = {**strip_rev(record), "devices": devices, "modified": now_iso()}
    return ws.commit(op, [RecordPut("account", account, new, expect_rev=record["_rev"])])


def _row(ws, account: str, device: str) -> dict:
    return next(r for r in listing(ws) if r["account"] == account and r["device"] == device)


def enrol(ws, account: str, device: str, key: str, fingerprint_seen: str, name: str = "") -> dict:
    """An administrator enrols a phone to an account, having compared the fingerprint the phone shows: it must match the
    key's. Refused, with nothing changed, if the device or its key is enrolled anywhere else (revoked ones included: a phone
    that changes hands starts again as a new device), or the account is disabled. Enrolling the same phone to the same
    account again answers ``already-enrolled``, so a retry is safe. Returns ``{"result", "device": row}``."""
    from .accounts import AccountError, _as_admin, normal_id
    _as_admin(ws)
    account = normal_id(account)
    device, key = check_device_id(device), check_key(key)
    seen = str(fingerprint_seen or "").strip().upper()
    if not _FINGERPRINT.match(seen):
        raise DeviceError("type the fingerprint the phone shows, four groups of four: 3F2A-91C0-77B1-04DE")
    if seen != fingerprint(key):
        raise DeviceError(f"the fingerprint you typed, {seen}, isn't this key's ({fingerprint(key)}), so the phone wasn't "
                          "enrolled. Check you have the right phone's field log")
    name = _text(name, "name", NAME_LIMIT)
    record = ws.get("account", account)
    if record is None:
        raise AccountError(f"there is no account called {account}")
    mine = entries(strip_rev(record))[0].get(device)
    if mine is not None and mine["key"] == key and not mine["revoked"]:
        return {"result": "already-enrolled", "device": _row(ws, account, device)}
    if record.get("active") is not True:
        raise DeviceError(f"{account}'s account is disabled")
    for acc_id, acc in accounts_of(ws).items():
        raw = acc.get("devices", {})
        for d, e in (raw.items() if isinstance(raw, dict) else ()):
            if d == device:
                revoked = isinstance(e, dict) and e.get("revoked")
                raise DeviceError(f"phone {device} is already enrolled to {acc_id}" + (
                    " and was revoked: a phone that's been revoked starts again as a new device" if revoked else ""))
            if isinstance(e, dict) and e.get("key") == key:
                raise DeviceError(f"that key is already enrolled as phone {d}, to {acc_id}")
    devices = _raw(strip_rev(record), account)
    devices[device] = {"key": key, "name": name, "added": now_iso(), "added_by": ws.session.account, "revoked": "",
                       "revoked_by": "", "reason": ""}
    _commit(ws, "account.device.enrol", account, record, devices)
    return {"result": "enrolled", "device": _row(ws, account, device)}


def revoke(ws, account: str, device: str, reason: str) -> dict:
    """From now on the phone's signatures are refused on anything new. What was accepted before stays, and can still be
    checked. Revoking it again answers ``already-revoked``. Returns ``{"result", "device": row}``."""
    from .accounts import AccountError, _as_admin, normal_id
    _as_admin(ws)
    account, device = normal_id(account), check_device_id(device)
    reason = _text(reason, "reason", REASON_LIMIT, required=True)
    record = ws.get("account", account)
    if record is None:
        raise AccountError(f"there is no account called {account}")
    devices = _raw(strip_rev(record), account)
    e = entries(strip_rev(record))[0].get(device)
    if e is None:
        raise DeviceError(f"phone {device} isn't enrolled to {account}" if device not in devices else
                          f"phone {device} isn't recorded the way it should be in {account}'s account, so nothing was changed")
    if e["revoked"]:
        return {"result": "already-revoked", "device": _row(ws, account, device)}
    devices[device] = {**e, "revoked": now_iso(), "revoked_by": ws.session.account, "reason": reason}
    _commit(ws, "account.device.revoke", account, record, devices)
    return {"result": "revoked", "device": _row(ws, account, device)}


__all__ = ["DeviceError", "RECORD_EVIDENCE", "SIGNATURE_EVIDENCE", "SIGNER_EVIDENCE", "accounts_of", "check_device_id",
           "check_key", "enrol", "entries", "fingerprint", "listing", "parse_signature", "recheck", "revoke",
           "signature_evidence", "signature_message"]
