# SPDX-License-Identifier: GPL-3.0-or-later
"""Signing handover packs (ADR-030): optional, and off unless an administrator turns it on with the company's public key. Then
every pack is signed with ssh-keygen (SHA256SUMS.sig), the key's fingerprint is recorded with the pack, and checking a pack
checks the signature against the pack, a key the customer gives, and the workspace's register. Real signing uses ssh-keygen
with software keys; a security key (YubiKey) is simulated, because there is no hardware here."""
import base64
import contextlib
import io
import json
import shutil
import subprocess

import pytest

from conftest import tree_snapshot
from revisionworx import sshsig
from revisionworx.cli import main as rwx_main
from revisionworx.units import pack
from revisionworx.units.service import service_for
from revisionworx.workspace import RecordPut, Workspace
from test_sshsig import SecurityKey, keygen
from test_unit_pack import plant, reseal  # noqa: F401 - the fixture, and making a changed pack whole again

needs_ssh_keygen = pytest.mark.skipif(sshsig.ssh_keygen() is None, reason="ssh-keygen (OpenSSH) isn't installed here")


@pytest.fixture
def company(tmp_path):
    """The company's key pair, made by ssh-keygen: ``company`` (private) and ``company.pub``."""
    if sshsig.ssh_keygen() is None:
        pytest.skip("ssh-keygen (OpenSSH) isn't installed here")
    keys = tmp_path / "keys"
    keys.mkdir()
    return keygen(keys)


def run(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = rwx_main(list(args))
    return code, buf.getvalue()


def test_packs_arent_signed_unless_someone_turns_it_on(ws, plant, tmp_path):
    assert pack.signing(ws)["enabled"] is False
    made = pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    assert made["signed"] is False and not (tmp_path / "pack" / pack.SIGNATURE).exists()
    assert json.loads((tmp_path / "pack" / "unit.json").read_text("utf-8"))["signed"] is False
    assert plant["svc"].packs(plant["unit"]["id"])[0]["key"] == ""
    assert "The pack is not signed." in (tmp_path / "pack" / "index.html").read_text("utf-8")


def test_a_key_given_where_packs_arent_signed_is_refused(ws, plant, tmp_path):
    (tmp_path / "some-key").write_text("whatever")
    head = ws.head_seq()
    with pytest.raises(pack.PackError, match="doesn't sign handover packs"):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack", sign_with=tmp_path / "some-key")
    assert ws.head_seq() == head and not (tmp_path / "pack").exists()


def test_signing_is_turned_on_with_an_ed25519_key_only(ws, tmp_path):
    def s(data):
        return len(data).to_bytes(4, "big") + data
    rsa = base64.b64encode(s(b"ssh-rsa") + s(b"\x01\x00\x01") + s(b"\x00" + b"\x01" * 256)).decode()
    head = ws.head_seq()
    with pytest.raises(pack.PackError, match="Ed25519 keys only"):
        pack.set_signing(ws, f"ssh-rsa {rsa} boss@office")
    with pytest.raises(pack.PackError, match="can't sign packs"):
        pack.set_signing(ws, "not a key at all")
    assert ws.head_seq() == head and pack.signing(ws)["enabled"] is False
    yubikey = SecurityKey().key
    state = pack.set_signing(ws, yubikey.openssh() + " packs@company")
    assert state["enabled"] and state["fingerprint"] == yubikey.fingerprint and state["key"].security_key
    assert pack.allowed_signers(state["key"]) == f'supplier namespaces="{pack.NAMESPACE}" {yubikey.openssh()}'
    state = pack.set_signing(ws, None)
    assert (state["enabled"], state["key"], state["fingerprint"]) == (False, None, "")


def test_once_on_every_pack_is_signed_by_the_workspaces_key(share, ws, plant, tmp_path, company):
    svc, uid = plant["svc"], plant["unit"]["id"]
    on = pack.set_signing(ws, (tmp_path / "keys" / "company.pub").read_text())
    with pytest.raises(pack.PackError, match="signs every handover pack"):
        pack.make(svc, "SN-1", tmp_path / "unsigned")
    assert svc.packs(uid) == [] and not (tmp_path / "unsigned").exists()
    made = pack.make(svc, "SN-1", tmp_path / "pack", sign_with=company)
    out = tmp_path / "pack"
    assert made["signed"] and made["key"] == on["fingerprint"] and made["files"] == 6
    assert (out / pack.SIGNATURE).is_file() and pack.SIGNATURE not in (out / pack.SUMS).read_text("utf-8")
    assert json.loads((out / "unit.json").read_text("utf-8"))["signed"] == {
        "key": on["fingerprint"], "type": sshsig.ED25519, "namespace": pack.NAMESPACE}
    assert svc.packs(uid)[0]["key"] == on["fingerprint"]
    assert svc.events(uid)[-1]["body"]["key"] == on["fingerprint"]
    report = pack.check(out)
    assert report["ok"] and report["files"] == 6 and report["signed"] == {
        "key": on["fingerprint"], "type": sshsig.ED25519, "security_key": False, "touched": None}
    assert pack.check(out, key=on["fingerprint"])["ok"]
    reg = pack.check(out, svc)
    assert reg["ok"] and reg["register"]["state"] == "issued" and reg["register"]["key"] == on["fingerprint"]
    page = (out / "index.html").read_text("utf-8")
    assert on["fingerprint"] in page and "ssh-keygen -Y check-novalidate" in page and "anyone can write a page" in page
    # the customer's side, with OpenSSH alone
    (tmp_path / "allowed_signers").write_text(pack.allowed_signers(on["key"]) + "\n")
    done = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(tmp_path / "allowed_signers"), "-I", "supplier", "-n",
                           pack.NAMESPACE, "-s", str(out / pack.SIGNATURE)], input=(out / pack.SUMS).read_bytes(),
                          capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    if shutil.which("sha256sum"):
        assert subprocess.run(["sha256sum", "-c", pack.SUMS], cwd=out, capture_output=True, timeout=60).returncode == 0


def test_a_pack_signed_with_another_key_isnt_issued(share, ws, plant, tmp_path, company):
    pack.set_signing(ws, (tmp_path / "keys" / "company.pub").read_text())
    other = keygen(tmp_path / "keys", "someone-else")
    head, before = ws.head_seq(), tree_snapshot(share / ".revisionworx")
    with pytest.raises(pack.PackError, match="but this workspace signs its packs with"):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack", sign_with=other)
    assert ws.head_seq() == head and tree_snapshot(share / ".revisionworx") == before
    assert not (tmp_path / "pack").exists() and not list(tmp_path.glob(".pack.*.tmp"))


def test_a_signature_that_fails_leaves_nothing(share, ws, plant, tmp_path, company, monkeypatch):
    pack.set_signing(ws, (tmp_path / "keys" / "company.pub").read_text())
    monkeypatch.setattr(sshsig, "ssh_keygen", lambda: None)
    head = ws.head_seq()
    with pytest.raises(pack.PackError, match="isn't installed"):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack", sign_with=company)
    assert ws.head_seq() == head and not (tmp_path / "pack").exists() and not list(tmp_path.glob(".pack.*.tmp"))


def test_a_security_key_signature_is_recorded_as_one_and_must_have_been_touched(ws, plant, tmp_path, monkeypatch):
    """ssh-keygen with a YubiKey, simulated: what it writes is a security-key signature (PROTOCOL.u2f)."""
    yubikey = SecurityKey()
    pack.set_signing(ws, yubikey.key.openssh())
    touched = {"flags": sshsig.USER_PRESENT}

    def ssh_keygen_with_a_yubikey(path, key_file, namespace, **_kw):
        sig = yubikey.sign(path.read_bytes(), flags=touched["flags"], namespace=namespace).encode()
        path.with_name(path.name + ".sig").write_bytes(sig)
        return sig
    monkeypatch.setattr(sshsig, "sign_file", ssh_keygen_with_a_yubikey)
    (tmp_path / "id_ed25519_sk").write_text("a key handle")
    made = pack.make(plant["svc"], "SN-1", tmp_path / "pack", sign_with=tmp_path / "id_ed25519_sk")
    assert made["key"] == yubikey.key.fingerprint
    assert pack.check(tmp_path / "pack")["signed"] == {"key": yubikey.key.fingerprint, "type": sshsig.SK_ED25519,
                                                       "security_key": True, "touched": True}
    touched["flags"] = 0
    with pytest.raises(pack.PackError, match="wasn't touched"):
        pack.make(plant["svc"], "SN-1", tmp_path / "untouched", sign_with=tmp_path / "id_ed25519_sk")
    assert [p["pack"] for p in plant["svc"].packs(plant["unit"]["id"])] == [1]


def test_check_finds_a_signature_that_doesnt_hold_is_missing_or_was_added(ws, plant, tmp_path, company):
    unsigned = tmp_path / "unsigned"
    pack.make(plant["svc"], "SN-1", unsigned)
    pack.set_signing(ws, (tmp_path / "keys" / "company.pub").read_text())
    signed = tmp_path / "signed"
    pack.make(plant["svc"], "SN-1", signed, sign_with=company)
    fp = pack.signing(ws)["fingerprint"]
    assert pack.check(signed, key="SHA256:" + "A" * 43)["problems"] == [
        f"the pack is signed with the key {fp}, not with the key you gave (SHA256:{'A' * 43})"]
    assert pack.check(unsigned, key=fp)["problems"] == [f"the pack isn't signed, so it isn't signed with the key {fp}"]
    copy = tmp_path / "copy"
    shutil.copytree(signed, copy)
    damaged = bytearray(sshsig.unarmor((copy / pack.SIGNATURE).read_bytes()))
    damaged[-10] ^= 0x01                                                          # a bit of the signature itself
    (copy / pack.SIGNATURE).write_text(sshsig.armor(bytes(damaged)))
    report = pack.check(copy)
    assert not report["ok"] and report["signed"] is False and report["problems"][0].startswith(f"{pack.SIGNATURE} doesn't hold")
    (copy / pack.SIGNATURE).unlink()
    assert pack.check(copy)["problems"] == [f"the pack says it is signed with the key {fp}, but {pack.SIGNATURE} is missing"]
    shutil.copy(signed / pack.SIGNATURE, unsigned / pack.SIGNATURE)                # a signature of another pack's list
    assert any("doesn't hold" in p for p in pack.check(unsigned)["problems"])
    (unsigned / pack.SIGNATURE).unlink()
    sshsig.sign_file(unsigned / pack.SUMS, company, pack.NAMESPACE)               # signed afterwards by the right key
    report = pack.check(unsigned, plant["svc"])
    assert report["problems"] == [f"{pack.SIGNATURE} isn't part of the pack: the pack says it isn't signed",
                                  f"Test workspace issued this pack unsigned, but here it is signed with the key {fp}"]


def test_a_changed_pack_signed_again_by_another_key_is_not_issued(ws, plant, tmp_path, company):
    pack.set_signing(ws, (tmp_path / "keys" / "company.pub").read_text())
    out = tmp_path / "pack"
    pack.make(plant["svc"], "SN-1", out, sign_with=company)
    mallory = keygen(tmp_path / "keys", "mallory")
    page = out / "index.html"
    page.write_text(page.read_text("utf-8").replace("Plant 2", "Plant 9"), "utf-8")
    record = json.loads((out / "unit.json").read_text("utf-8"))
    mallorys = sshsig.parse_public_key((tmp_path / "keys" / "mallory.pub").read_text()).fingerprint
    record["signed"]["key"] = mallorys
    (out / "unit.json").write_text(json.dumps(record), "utf-8")
    (out / pack.SIGNATURE).unlink()
    reseal(out)
    sshsig.sign_file(out / pack.SUMS, mallory, pack.NAMESPACE)
    assert pack.check(out)["ok"] and pack.check(out)["signed"]["key"] == mallorys    # whole and signed, by someone
    assert not pack.check(out, key=pack.signing(ws)["fingerprint"])["ok"]           # but not by the company's key
    assert pack.check(out, plant["svc"])["register"]["state"] == "not-issued"        # and not issued


def test_turning_signing_off_keeps_the_packs_already_signed_checkable(ws, plant, tmp_path, company):
    pack.set_signing(ws, (tmp_path / "keys" / "company.pub").read_text())
    pack.make(plant["svc"], "SN-1", tmp_path / "signed", sign_with=company)
    pack.set_signing(ws, None)
    report = pack.check(tmp_path / "signed", plant["svc"])
    assert report["ok"] and report["signed"] and report["register"]["key"] == report["signed"]["key"]
    assert pack.make(plant["svc"], "SN-1", tmp_path / "now")["signed"] is False


def test_a_damaged_signing_setting_makes_no_pack(ws, plant, tmp_path):
    ws.commit("t", [RecordPut("settings", pack.SETTING, {"id": pack.SETTING, "enabled": "yes"})])
    with pytest.raises(pack.PackError, match="pack-signing setting is damaged"):
        pack.make(plant["svc"], "SN-1", tmp_path / "pack")
    assert not (tmp_path / "pack").exists() and plant["svc"].packs(plant["unit"]["id"]) == []


def test_only_an_administrator_changes_pack_signing_in_a_team_workspace(tmp_path):
    from team_helpers import add_user, computer, log_in, new_team
    folder, boss_pc, _code, boss = new_team(tmp_path)
    add_user(folder, boss_pc, boss, "alice")
    alice = log_in(folder, computer(tmp_path, "alice"), "alice")
    key = SecurityKey().key.openssh()
    with Workspace.open(folder, writable=True, session=alice, state_dir=computer(tmp_path, "alice")) as ws:
        with pytest.raises(pack.PackError, match="only an administrator"):
            pack.set_signing(ws, key)
    with Workspace.open(folder, writable=True, session=boss, state_dir=boss_pc) as ws:
        assert pack.set_signing(ws, key)["enabled"]
    with Workspace.open(folder, state_dir=computer(tmp_path, "alice")) as ws:
        assert pack.signing(ws)["fingerprint"] == SecurityKey().key.fingerprint


def test_the_command_line_turns_signing_on_signs_and_checks_with_the_customers_key(share, ws, plant, tmp_path, company):
    ws.close()
    code, text = run("-w", str(share), "unit", "pack-signing")
    assert code == 0 and "aren't signed" in text
    code, text = run("-w", str(share), "unit", "pack-signing", "on", "--key", str(tmp_path / "keys" / "company.pub"))
    fp = sshsig.parse_public_key((tmp_path / "keys" / "company.pub").read_text()).fingerprint
    assert code == 0 and f"every handover pack is signed with the key {fp}" in text and 'supplier namespaces=' in text
    code, text = run("-w", str(share), "unit", "pack-signing", "--json")
    assert code == 0 and json.loads(text)["fingerprint"] == fp
    code, _ = run("-w", str(share), "unit", "pack", "SN-1", str(tmp_path / "pack"))
    assert code == 2 and not (tmp_path / "pack").exists()
    code, text = run("-w", str(share), "unit", "pack", "SN-1", str(tmp_path / "pack"), "--sign-with", str(company))
    assert code == 0 and f"signed with the key {fp}" in text
    code, text = run("unit", "check-pack", str(tmp_path / "pack"))
    assert code == 0 and f"whole; signed with the key {fp}" in text and "compare that one with the key your supplier" in text
    code, text = run("unit", "check-pack", str(tmp_path / "pack"), "--key", str(tmp_path / "keys" / "company.pub"))
    assert code == 0 and "compare" not in text
    other = keygen(tmp_path / "keys", "other")
    code, text = run("unit", "check-pack", str(tmp_path / "pack"), "--key", str(other) + ".pub")
    assert code == 1 and "not with the key you gave" in text
    code, text = run("-w", str(share), "unit", "check-pack", str(tmp_path / "pack"))
    assert code == 0 and "issued: SN-1 handover pack 1" in text
    code, text = run("-w", str(share), "unit", "pack-signing", "off")
    assert code == 0 and "aren't signed" in text
    code, _ = run("-w", str(share), "unit", "pack-signing", "on")
    assert code == 2                                                   # on needs the key


@pytest.mark.qt
def test_the_units_window_sends_signed_packs_to_the_command_line(share, ws, plant, tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from revisionworx.gui.controller import Controller
    from revisionworx.gui.qt import units as units_window
    pack.set_signing(ws, SecurityKey().key.openssh())
    ws.close()
    ctl = Controller(cfg_dir=tmp_path / "cfg", cache_dir=tmp_path / "cache")
    ctl.open_workspace(share)
    asked = []
    monkeypatch.setattr(units_window.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: asked.append(a) or ""))
    win = units_window.UnitsWindow(ctl)
    assert win.select_unit("SN-1")
    win.make_pack()
    assert asked == [] and "--sign-with KEYFILE" in win.status.text() and SecurityKey().key.fingerprint in win.status.text()
    assert ctl.unit_pack_signing() == {"enabled": True, "fingerprint": SecurityKey().key.fingerprint}
    win.close()
