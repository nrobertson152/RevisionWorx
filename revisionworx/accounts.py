# SPDX-License-Identifier: GPL-3.0-or-later
"""Accounts for a team working from a shared folder (ADR-024).

A password never leaves the computer it's typed on and isn't stored anywhere. It's stretched with scrypt into the seed of an
Ed25519 key, and only the public key goes in the journal. Logging in means deriving the key again and comparing. Anyone who
can read the folder can therefore try guessing passwords on their own computer, which is why they're stretched hard, and why an
administrator's key also needs a secret that only exists on their computer.

There's no server to refuse anyone, so what makes these rules real is that every computer checks every entry's signature
(``shared.judge``). A regular user can't make themselves an administrator or act in someone else's name.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import unicodedata
from pathlib import Path

from . import atomicio, clientstate, ed25519, projects, sharecheck
from .shared import (ENABLE_OP, PASSWORD_OP, RECOVER_OP, RECOVERY, ROLES, RULES, UPGRADE_OP, NotPermitted, Session, SharedWorkspace,
                     forget_session, next_rolling, save_session, saved_session, signing_message, team_dir)
from .workspace import (FORMAT_VERSION, REQUEST_IDENTITY_VERSION, SHARED_FORMAT_VERSION, RecordPut, Workspace, WorkspaceError,
                        current_actor, entry_digest, is_shared_info, now_iso, request_identity, strip_rev, default_settings)

KDF = {"name": "scrypt", "n": 2 ** 17, "r": 8, "p": 1}
MIN_PASSWORD = 8
MAX_PASSWORD = 1024
_ACCOUNT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_WRONG = "that name and password don't match"


class AccountError(WorkspaceError):
    """Nothing was changed: the request itself can't be done."""


class LoginFailed(WorkspaceError):
    """The name, password or recovery code doesn't match. Nothing was changed."""


class ComputerNotSetUp(LoginFailed):
    """An administrator's key also needs a secret kept on the computer they use, and it isn't on this one."""


class ShareCheckFailed(WorkspaceError):
    """This folder doesn't behave the way team use needs, so it wasn't turned on."""

    def __init__(self, report: dict):
        self.report = report
        failed = "; ".join(f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"])
        super().__init__(f"this folder can't safely be shared this way, so nothing was changed. {failed}")


# ---------------------------------------------------------------------------- names and passwords
def normal_id(account: str) -> str:
    text = str(account).strip().casefold()
    if not _ACCOUNT_ID.match(text):
        raise AccountError("a user name starts with a letter or digit, and has only letters, digits, '.', '_' and '-'")
    return text


def check_password(password: str, account: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AccountError(f"a password needs at least {MIN_PASSWORD} characters. Anyone who can read the shared folder can try "
                           "to guess it on their own computer, so longer is better")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"a password can have at most {MAX_PASSWORD} characters")
    if password.casefold() == account.casefold():
        raise AccountError("a password can't be the same as the user name")


def _stretch(password: str, key: dict, workspace_id: str, account: str) -> bytes:
    kdf = key.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("name") != "scrypt":
        raise LoginFailed("this account uses a kind of password this version of RevisionWorx doesn't know; update it")
    n, r, p = kdf.get("n"), kdf.get("r"), kdf.get("p")
    # What's in the folder isn't trusted: bounds, so a doctored account can't make logging in eat all the memory.
    if not all(type(v) is int for v in (n, r, p)) or not (2 <= n <= 2 ** 20 and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
        raise LoginFailed("this account's password settings are out of range, so it can't be used")
    try:
        salt = bytes.fromhex(key.get("salt", ""))
    except (TypeError, ValueError):
        raise LoginFailed("this account's password settings are damaged, so it can't be used") from None
    context = f"revisionworx/login/v1\x00{workspace_id}\x00{account}\x00".encode("utf-8")
    text = unicodedata.normalize("NFKC", password).encode("utf-8", "surrogatepass")
    if not hasattr(hashlib, "scrypt"):
        raise LoginFailed("this Python has no scrypt, which accounts need; use the RevisionWorx build, or a Python built with OpenSSL")
    return hashlib.scrypt(text, salt=context + salt, n=n, r=r, p=p, dklen=32, maxmem=128 * n * r * p + 2 ** 26)


def _seed_for(password: str, key: dict, workspace_id: str, account: str, device_secret: bytes | None) -> bytes:
    stretched = _stretch(password, key, workspace_id, account)
    if not key.get("device"):
        return stretched
    if device_secret is None:
        raise ComputerNotSetUp("this administrator account only works on the computer it was set up on. Log in there, or use the "
                               "recovery code on this one")
    return hmac.new(device_secret, stretched, "sha512").digest()[:32]


def _new_key(password: str, workspace_id: str, account: str, device_secret: bytes | None = None) -> tuple[bytes, dict]:
    key = {"salt": secrets.token_hex(16), "kdf": dict(KDF), "device": device_secret is not None}
    seed = _seed_for(password, key, workspace_id, account, device_secret)
    key["public"] = ed25519.public_key(seed).hex()
    return seed, key


def device_secret(workspace_id: str, account: str, state_dir=None, create: bool = False) -> bytes | None:
    """The part of an administrator's key that only exists on their computer."""
    path = team_dir(workspace_id, state_dir) / f"device-{clientstate.safe_name(account)}.json"
    try:
        return bytes.fromhex(atomicio.read_json_object(path)["secret"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if not create:
        return None
    secret = secrets.token_bytes(32)
    clientstate.write_private(path, {"secret": secret.hex()})
    return secret


# ---------------------------------------------------------------------------- recovery code
def _new_recovery_code() -> str:
    text = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def _recovery_seed(code: str, workspace_id: str) -> bytes:
    letters = re.sub(r"[^A-Za-z2-7]", "", str(code)).upper()
    try:
        raw = base64.b32decode(letters)
    except (binascii.Error, ValueError):
        raise LoginFailed("that isn't a recovery code. It's 32 letters and digits, in groups of four") from None
    return hashlib.sha256(b"revisionworx/recovery/v1\x00" + workspace_id.encode("utf-8") + b"\x00" + raw).digest()


# ---------------------------------------------------------------------------- turning team use on
# A team set up now: workspace.json format 3, on team rules 2. Tests set these to (2, 1) to make a team workspace exactly as one
# set up before 2026-11-08 was, with no rules in its signed settings.
NEW_TEAM_FORMAT = SHARED_FORMAT_VERSION
NEW_TEAM_RULES = RULES


def is_team_workspace(location: Path) -> bool:
    return Workspace._is_shared_folder(Workspace.meta_dir_for(location))


def enable_team_use(location: Path, admin: str, name: str, password: str, state_dir=None) -> dict:
    """Set a workspace up for a team, with its first administrator on this computer. Returns the recovery code, which is shown
    this once and never kept, the login, and the folder check it passed.

    Can't be undone. Everyone else must have closed RevisionWorx first, as a copy that opened the workspace before this would
    keep writing the old way, and its changes would be ignored."""
    meta = Workspace.meta_dir_for(location)
    account = normal_id(admin)
    check_password(password, account)
    info_path = meta / "workspace.json"
    with Workspace.open(meta, state_dir=state_dir) as probe:            # checks the format, and refuses what isn't a workspace
        info = dict(probe.info)
        if isinstance(probe, SharedWorkspace) and probe._replica.enabled_at() is not None:
            raise AccountError("this workspace is already set up for a team")
    report = sharecheck.check_folder(meta)
    if not report["ok"]:
        raise ShareCheckFailed(report)
    was_plain = not is_shared_info(info)
    token = Workspace._acquire_writer_lock(meta)
    ws = Workspace(meta, info, True, token)
    try:
        ws._check_journal()
        ws.recover()
        code = _new_recovery_code()
        recovery_public = ed25519.public_key(_recovery_seed(code, ws.id)).hex()
        seed, key = _new_key(password, ws.id, account, device_secret(ws.id, account, state_dir, create=True))
        record = {"id": account, "name": name.strip() or account, "role": "admin", "active": True, "key": key,
                  "must_change": False, "created": now_iso(), "created_by": account}
        settings = {**default_settings(), **strip_rev(ws.get("settings", "workspace") or {}), "collaboration_mode": "shared-folder"}
        ops = [RecordPut("account", account, record, expect_rev=0),
               RecordPut("settings", "sharing", {"id": "sharing", "recovery_public": recovery_public, "enabled": now_iso(),
                                                 "first_admin": account,
                                                 **({"rules": NEW_TEAM_RULES} if NEW_TEAM_RULES > 1 else {})}, expect_rev=0),
               RecordPut("settings", "workspace", settings), *projects.roots_inside_workspace(ws)]
        rolling, previous = "", ""
        head = ws.head_seq()
        for seq in range(1, head + 1):
            entry = ws.read_entry(seq)
            previous = entry.get("digest") or entry_digest(entry)
            rolling = next_rolling(rolling, previous)
        entry = {"seq": head + 1, "txid": secrets.token_hex(16), "time": now_iso(),
                 "actor": {**current_actor(), "user": account, "name": record["name"], "account": account}, "op": ENABLE_OP,
                 "records": [op.as_json() for op in ops], "blobs": [], "checks": [],
                 "request": {"version": REQUEST_IDENTITY_VERSION, "digest": request_identity(ENABLE_OP, ops, None)},
                 "writer_token": token, "prev": previous, "history": rolling, "signer": account}
        entry["sig"] = ed25519.sign(seed, signing_message(entry)).hex()
        entry["digest"] = entry_digest(entry)
        # workspace.json first: a crash between the two leaves nobody able to write until this is run again, which is safe. The
        # other order would leave a journal that older code keeps writing to without signing anything.
        atomicio.atomic_write_json(info_path, {**info, "format_version": NEW_TEAM_FORMAT, "collaboration": "shared"})
        try:
            atomicio.publish_exclusive(ws._journal_dir() / f"{head + 1:020d}.json", atomicio.dumps_json(entry))
        except BaseException:
            if was_plain:
                atomicio.atomic_write_json(info_path, {**info, "format_version": FORMAT_VERSION})
            raise
    finally:
        ws._close_keeping_the_real_error()
    session = Session(info["workspace_id"], account, record["name"], key["public"], seed)
    return {"recovery_code": code, "session": session, "check": report}


# ---------------------------------------------------------------------------- logging in
def _password_fits(record: dict, password: str, workspace_id: str, account: str, state_dir) -> bool:
    key = record.get("key") or {}
    device = device_secret(workspace_id, account, state_dir) if key.get("device") else None
    return ed25519.public_key(_seed_for(password, key, workspace_id, account, device)).hex() == key.get("public")


def login(location: Path, account: str, password: str, state_dir=None, remember_hours: float | None = None) -> dict:
    """Check a password. Returns the session to act with, whether the password has to be changed first, and the role. With
    ``remember_hours`` the login is also kept on this computer for that long, which lets the command line and other programs
    use it."""
    account = normal_id(account)
    with Workspace.open(location, state_dir=state_dir) as ws:
        if not isinstance(ws, SharedWorkspace):
            raise AccountError("this workspace isn't set up for a team, so there's nothing to log in to")
        record = ws.get("account", account)
        if record is None or not _password_fits(record, password, ws.id, account, state_dir):
            # The account may have just been made or changed, and a network client can take seconds to hear of it. Look properly once.
            ws.sync(fresh=True, patient=True)
            record = ws.get("account", account)
            if record is None or not _password_fits(record, password, ws.id, account, state_dir):
                raise LoginFailed(_WRONG)
        key = record.get("key") or {}
        seed = _seed_for(password, key, ws.id, account, device_secret(ws.id, account, state_dir) if key.get("device") else None)
        if record.get("active") is not True:
            raise LoginFailed(f"account {account} has been disabled. Ask an administrator")
        session = Session(ws.id, account, str(record.get("name") or account), key["public"], seed)
        if remember_hours:
            save_session(session, state_dir, remember_hours)
        return {"session": session, "must_change": bool(record.get("must_change")), "role": record.get("role")}


def logout(workspace_id: str, state_dir=None) -> None:
    forget_session(workspace_id, state_dir)


def change_password(location: Path, old_password: str, new_password: str, session: Session | None = None, state_dir=None) -> Session:
    """Your own password. For an administrator this also ties the key to this computer."""
    with Workspace.open(location, writable=True, session=session, state_dir=state_dir) as ws:
        me = ws._signer()
        if me.account == RECOVERY:
            raise AccountError("the recovery code can't have a password")
        check_password(new_password, me.account)
        record = ws.get("account", me.account)
        key = record["key"]
        old_seed = _seed_for(old_password, key, ws.id, me.account, device_secret(ws.id, me.account, state_dir) if key.get("device") else None)
        if ed25519.public_key(old_seed).hex() != key["public"]:
            raise LoginFailed("the current password isn't right")
        device = device_secret(ws.id, me.account, state_dir, create=True) if record["role"] == "admin" else None
        seed, new_key = _new_key(new_password, ws.id, me.account, device)
        ws.commit(PASSWORD_OP, [RecordPut("account", me.account,
                                          {**strip_rev(record), "key": new_key, "must_change": False, "modified": now_iso()},
                                          expect_rev=record["_rev"])])
        new_session = Session(ws.id, me.account, me.name, new_key["public"], seed)
    if saved_session(new_session.workspace_id, state_dir) is not None:
        save_session(new_session, state_dir)
    return new_session


def recover_admin(location: Path, code: str, account: str, new_password: str, name: str = "", state_dir=None) -> Session:
    """With the recovery code: an administrator account on this computer with a new password, when the old one is forgotten or
    the computer it was on is gone. Any earlier key for that account stops working."""
    account = normal_id(account)
    check_password(new_password, account)
    with Workspace.open(location, state_dir=state_dir) as ws:
        if not isinstance(ws, SharedWorkspace):
            raise AccountError("this workspace isn't set up for a team")
        workspace_id = ws.id
        expected = (ws.get("settings", "sharing") or {}).get("recovery_public")
    seed = _recovery_seed(code, workspace_id)
    public = ed25519.public_key(seed).hex()
    if public != expected:
        raise LoginFailed("that recovery code doesn't belong to this workspace")
    device = device_secret(workspace_id, account, state_dir, create=True)
    new_seed, key = _new_key(new_password, workspace_id, account, device)
    recovery = Session(workspace_id, RECOVERY, "Recovery code", public, seed)
    with Workspace.open(location, writable=True, session=recovery, state_dir=state_dir) as ws:
        old = ws.get("account", account)
        record = {**strip_rev(old or {}), "id": account, "name": name.strip() or (old or {}).get("name") or account, "role": "admin",
                  "active": True, "key": key, "must_change": False, "modified": now_iso()}
        ws.commit(RECOVER_OP, [RecordPut("account", account, record, expect_rev=old["_rev"] if old else 0)])
    return Session(workspace_id, account, record["name"], key["public"], new_seed)


# ---------------------------------------------------------------------------- what an administrator does
def _as_admin(ws) -> None:
    if not isinstance(ws, SharedWorkspace):
        raise AccountError("this workspace isn't set up for a team, so it has no accounts")
    ws._signer()
    if not ws.is_admin:
        raise NotPermitted("only an administrator can do that")


def upgrade_rules(ws) -> dict:
    """Move a team workspace set up before 2026-11-08 to team rules 2 (ADR-024): from the entry this writes on, every computer
    refuses an entry that changes or removes a record the unit register writes once. Entries before keep the verdicts they
    had. Only an administrator, on a writable handle.

    workspace.json goes to format 3 first, so a RevisionWorx that knows only rules 1 refuses the workspace before anything is
    judged by rules 2. A crash between the two leaves a format-3 workspace on rules 1, which this version reads as it is: run
    this again."""
    _as_admin(ws)
    before = ws.rules
    info_path = ws.meta_dir / "workspace.json"
    info = atomicio.read_json_object(info_path)
    if info.get("format_version") != SHARED_FORMAT_VERSION:
        atomicio.atomic_write_json(info_path, {**info, "format_version": SHARED_FORMAT_VERSION})
    if before >= RULES:
        return {"rules": before, "changed": False}
    sharing = ws.get("settings", "sharing")
    if sharing is None:
        raise AccountError("this workspace's team settings are missing; run 'revisionworx doctor'")
    ws.commit(UPGRADE_OP, [RecordPut("settings", "sharing", {**strip_rev(sharing), "rules": RULES},
                                     expect_rev=int(sharing.get("_rev", 0)))])
    return {"rules": ws.rules, "changed": True}


def list_accounts(ws) -> list[dict]:
    if not isinstance(ws, SharedWorkspace):
        return []
    rows = [{k: v for k, v in strip_rev(r).items() if k != "key"} for r in ws.list("account").values()]
    return sorted(rows, key=lambda r: r["id"])


def create_account(ws, account: str, password: str, name: str = "", role: str = "user") -> dict:
    """The user chooses their own password the first time they log in, so this one only has to reach them."""
    _as_admin(ws)
    account = normal_id(account)
    check_password(password, account)
    if role not in ROLES:
        raise AccountError(f"a role is one of {', '.join(ROLES)}")
    if ws.get("account", account) is not None:
        raise AccountError(f"there is already an account called {account}")
    _seed, key = _new_key(password, ws.id, account)
    record = {"id": account, "name": name.strip() or account, "role": role, "active": True, "key": key, "must_change": True,
              "created": now_iso(), "created_by": ws.session.account}
    seq = ws.commit("account.create", [RecordPut("account", account, record, expect_rev=0)])
    return {k: v for k, v in record.items() if k != "key"} | {"_rev": seq}


def _change(ws, op: str, account: str, **changes) -> dict:
    _as_admin(ws)
    account = normal_id(account)
    record = ws.get("account", account)
    if record is None:
        raise AccountError(f"there is no account called {account}")
    new = {**strip_rev(record), **changes, "modified": now_iso()}
    seq = ws.commit(op, [RecordPut("account", account, new, expect_rev=record["_rev"])])
    return {k: v for k, v in new.items() if k != "key"} | {"_rev": seq}


def set_active_flag(ws, account: str, active: bool) -> dict:
    """A disabled account's changes are ignored from then on, by every computer. What it did before stands."""
    return _change(ws, "account.enable" if active else "account.disable", account, active=bool(active))


def set_role(ws, account: str, role: str) -> dict:
    if role not in ROLES:
        raise AccountError(f"a role is one of {', '.join(ROLES)}")
    return _change(ws, "account.role", account, role=role)


def set_name(ws, account: str, name: str) -> dict:
    return _change(ws, "account.rename", account, name=name.strip() or normal_id(account))


def reset_password(ws, account: str, password: str) -> dict:
    """For someone who's forgotten theirs. They choose a new one at their next login."""
    _as_admin(ws)
    account = normal_id(account)
    check_password(password, account)
    _seed, key = _new_key(password, ws.id, account)
    return _change(ws, "account.reset", account, key=key, must_change=True)


__all__ = ["AccountError", "ComputerNotSetUp", "LoginFailed", "ShareCheckFailed", "change_password", "check_password", "create_account",
           "enable_team_use", "is_team_workspace", "list_accounts", "login", "logout", "normal_id", "recover_admin", "reset_password",
           "set_active_flag", "set_name", "set_role"]
