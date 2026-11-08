# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenSSH signatures checked in pure Python (revisionworx/sshsig.py): Ed25519 keys in software and on security keys such as a
YubiKey. Signatures made by ssh-keygen itself are checked here, and security-key signatures made here (no hardware in CI) are
checked by ssh-keygen too, so both sides agree with OpenSSH. The tests that run ssh-keygen skip where it isn't installed."""
import hashlib
import subprocess

import pytest

from revisionworx import ed25519, sshsig

NS = "revisionworx-handover-pack"
needs_ssh_keygen = pytest.mark.skipif(sshsig.ssh_keygen() is None, reason="ssh-keygen (OpenSSH) isn't installed here")


def s(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


class SecurityKey:
    """What a FIDO security key does when ssh-keygen asks it to sign (OpenSSH's PROTOCOL.u2f), done in software."""

    def __init__(self, seed=b"k" * 32, application="ssh:"):
        self.seed, self.application = seed, application
        self.blob = s(sshsig.SK_ED25519.encode()) + s(ed25519.public_key(seed)) + s(application.encode())
        self.key = sshsig.key_from_blob(self.blob)

    def sign(self, message: bytes, *, flags=sshsig.USER_PRESENT, counter=7, hash_name="sha512", namespace=NS,
             claim: bytes | None = None) -> str:
        """``claim``: the key the signature says it is by, if not this one (a forger's)."""
        data = sshsig.signed_data(namespace, hash_name, message)
        signed = (hashlib.sha256(self.application.encode()).digest() + bytes([flags]) + counter.to_bytes(4, "big")
                  + hashlib.sha256(data).digest())
        sig = s(sshsig.SK_ED25519.encode()) + s(ed25519.sign(self.seed, signed)) + bytes([flags]) + counter.to_bytes(4, "big")
        return sshsig.armor(sshsig.MAGIC + (1).to_bytes(4, "big") + s(claim or self.blob) + s(namespace.encode()) + s(b"")
                            + s(hash_name.encode()) + s(sig))


def software_signature(seed: bytes, message: bytes, namespace=NS, hash_name="sha512", version=1, extra=b"") -> str:
    """An ssh-ed25519 SSHSIG signature, built field by field from PROTOCOL.sshsig (not from the module under test)."""
    blob = s(sshsig.ED25519.encode()) + s(ed25519.public_key(seed))
    data = b"SSHSIG" + s(namespace.encode()) + s(b"") + s(hash_name.encode()) + s(hashlib.new(hash_name, message).digest())
    sig = s(sshsig.ED25519.encode()) + s(ed25519.sign(seed, data))
    return sshsig.armor(sshsig.MAGIC + version.to_bytes(4, "big") + s(blob) + s(namespace.encode()) + s(b"")
                        + s(hash_name.encode()) + s(sig) + extra)


def keygen(tmp_path, name="company", kind="ed25519"):
    path = tmp_path / name
    subprocess.run(["ssh-keygen", "-q", "-t", kind, "-N", "", "-C", name, "-f", str(path)], check=True, timeout=60)
    return path


@needs_ssh_keygen
def test_a_signature_ssh_keygen_made_holds_and_names_the_key_ssh_keygen_names(tmp_path):
    key = keygen(tmp_path)
    message = tmp_path / "SHA256SUMS"
    message.write_bytes(b"0" * 64 + b"  index.html\n")
    signature = sshsig.sign_file(message, key, NS)
    assert signature == (tmp_path / "SHA256SUMS.sig").read_bytes()
    info = sshsig.verify(signature, message.read_bytes(), NS)
    printed = subprocess.run(["ssh-keygen", "-l", "-f", str(key) + ".pub"], capture_output=True, text=True, timeout=60)
    assert info["fingerprint"] == printed.stdout.split()[1] == sshsig.parse_public_key((tmp_path / "company.pub")
                                                                                         .read_text()).fingerprint
    assert info["type"] == sshsig.ED25519 and info["flags"] is None and info["hash"] == "sha512"
    with pytest.raises(sshsig.SignatureError, match="doesn't hold"):
        sshsig.verify(signature, message.read_bytes() + b"x", NS)
    with pytest.raises(sshsig.SignatureError, match="made for something else"):
        sshsig.verify(signature, message.read_bytes(), "file")


@pytest.mark.parametrize("hash_name", ["sha512", "sha256"])
def test_a_security_key_signature_holds_only_if_the_key_was_touched(tmp_path, hash_name):
    yubikey = SecurityKey()
    message = b"0" * 64 + b"  index.html\n"
    info = sshsig.verify(yubikey.sign(message, hash_name=hash_name), message, NS)
    assert info["type"] == sshsig.SK_ED25519 and info["touched"] is True and info["pin"] is False and info["counter"] == 7
    assert info["fingerprint"] == yubikey.key.fingerprint
    assert sshsig.verify(yubikey.sign(message, flags=0x05), message, NS)["pin"] is True
    with pytest.raises(sshsig.SignatureError, match="wasn't touched"):
        sshsig.verify(yubikey.sign(message, flags=0), message, NS)
    assert sshsig.verify(yubikey.sign(message, flags=0), message, NS, require_touch=False)["touched"] is False
    with pytest.raises(sshsig.SignatureError, match="doesn't hold"):
        sshsig.verify(yubikey.sign(message), message + b"x", NS)
    mallory = SecurityKey(seed=b"m" * 32)
    with pytest.raises(sshsig.SignatureError, match="doesn't hold"):        # signed by one key, claiming to be by another
        sshsig.verify(mallory.sign(message, claim=yubikey.blob), message, NS)
    same_key_other_app = SecurityKey(application="ssh:other")              # the application is part of what is signed
    with pytest.raises(sshsig.SignatureError, match="doesn't hold"):
        sshsig.verify(same_key_other_app.sign(message, claim=yubikey.blob), message, NS)


@needs_ssh_keygen
def test_openssh_accepts_the_security_key_signatures_this_accepts(tmp_path):
    yubikey = SecurityKey()
    message = b"0" * 64 + b"  index.html\n"
    (tmp_path / "allowed_signers").write_text(f'supplier namespaces="{NS}" {yubikey.key.openssh()}\n')
    for hash_name in ("sha512", "sha256"):
        (tmp_path / "SHA256SUMS.sig").write_text(yubikey.sign(message, hash_name=hash_name))
        done = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(tmp_path / "allowed_signers"), "-I", "supplier", "-n", NS,
                               "-s", str(tmp_path / "SHA256SUMS.sig")], input=message, capture_output=True, timeout=60)
        assert done.returncode == 0 and yubikey.key.fingerprint.encode() in done.stdout, done.stderr
        done = subprocess.run(["ssh-keygen", "-Y", "check-novalidate", "-n", NS, "-s", str(tmp_path / "SHA256SUMS.sig")],
                              input=message, capture_output=True, timeout=60)
        assert done.returncode == 0 and yubikey.key.fingerprint.encode() in done.stdout, done.stderr
    done = subprocess.run(["ssh-keygen", "-Y", "check-novalidate", "-n", NS, "-s", str(tmp_path / "SHA256SUMS.sig")],
                          input=message + b"x", capture_output=True, timeout=60)
    assert done.returncode != 0


@pytest.mark.parametrize("what", ["not a signature", "not base64", "cut short", "something after", "version", "hash", "bytes",
                                  "too big"])
def test_damaged_and_foreign_signatures_are_refused_with_a_reason(what):
    seed, message = b"s" * 32, b"listing\n"
    good = software_signature(seed, message)
    assert sshsig.verify(good, message, NS)["fingerprint"].startswith("SHA256:")
    cases = {
        "not a signature": "hello",
        "not base64": f"{sshsig.BEGIN}\n!!!!\n{sshsig.END}\n",
        "cut short": sshsig.armor(sshsig.unarmor(good)[:-5]),
        "something after": software_signature(seed, message, extra=b"\x00"),
        "version": software_signature(seed, message, version=2),
        "hash": software_signature(seed, message, hash_name="md5"),
        "bytes": b"\xff" * 10,
        "too big": b"x" * (sshsig.MAX_SIGNATURE + 1),
    }
    with pytest.raises(sshsig.SignatureError):
        sshsig.verify(cases[what], message, NS)


def test_a_signature_with_one_bit_changed_doesnt_hold():
    seed, message = b"s" * 32, b"listing\n"
    flipped = bytearray(sshsig.unarmor(software_signature(seed, message)))
    flipped[-3] ^= 0x01                                       # a bit of the signature itself
    with pytest.raises(sshsig.SignatureError, match="doesn't hold"):
        sshsig.verify(sshsig.armor(bytes(flipped)), message, NS)


def test_only_ed25519_keys_are_taken_and_the_message_says_how_to_make_one():
    rsa_blob = s(b"ssh-rsa") + s(b"\x01\x00\x01") + s(b"\x00" + b"\x01" * 256)
    with pytest.raises(sshsig.SignatureError, match="ed25519-sk"):
        sshsig.key_from_blob(rsa_blob)
    ecdsa = s(b"sk-ecdsa-sha2-nistp256@openssh.com") + s(b"nistp256") + s(b"\x04" + b"\x01" * 64) + s(b"ssh:")
    with pytest.raises(sshsig.SignatureError, match="Ed25519 keys only"):
        sshsig.key_from_blob(ecdsa)
    key = SecurityKey().key
    assert sshsig.parse_public_key(key.openssh() + " boss@office\n").fingerprint == key.fingerprint
    for bad in ("", "ssh-ed25519", f"ssh-ed25519 {key.openssh().split()[1]}", "ssh-ed25519 !!!",
                key.openssh() + "\n" + key.openssh()):
        with pytest.raises(sshsig.SignatureError):
            sshsig.parse_public_key(bad)
    with pytest.raises(sshsig.SignatureError, match="isn't an Ed25519 key"):
        sshsig.key_from_blob(s(sshsig.ED25519.encode()) + s(b"\x00" * 31))


@needs_ssh_keygen
def test_rsa_and_ecdsa_keys_ssh_keygen_makes_are_refused(tmp_path):
    for kind in ("rsa", "ecdsa"):
        path = keygen(tmp_path, kind, kind)
        with pytest.raises(sshsig.SignatureError, match="Ed25519 keys only"):
            sshsig.parse_public_key((tmp_path / f"{kind}.pub").read_text())


def test_signing_says_what_is_missing(tmp_path, monkeypatch):
    (tmp_path / "SHA256SUMS").write_bytes(b"x\n")
    monkeypatch.setattr(sshsig.shutil, "which", lambda *_a, **_k: str(tmp_path / "ssh-keygen"))
    with pytest.raises(sshsig.SignatureError, match="no key file"):
        sshsig.sign_file(tmp_path / "SHA256SUMS", tmp_path / "missing", NS)
    monkeypatch.setattr(sshsig.shutil, "which", lambda *_a, **_k: None)
    with pytest.raises(sshsig.SignatureError, match="isn't installed"):
        sshsig.sign_file(tmp_path / "SHA256SUMS", tmp_path / "missing", NS)


@needs_ssh_keygen
def test_a_key_ssh_keygen_cant_sign_with_says_so(tmp_path):
    (tmp_path / "SHA256SUMS").write_bytes(b"x\n")
    (tmp_path / "not-a-key").write_text("this isn't a key\n")
    with pytest.raises(sshsig.SignatureError, match="ssh-keygen didn't sign"):
        sshsig.sign_file(tmp_path / "SHA256SUMS", tmp_path / "not-a-key", NS, timeout=60)
    assert not (tmp_path / "SHA256SUMS.sig").exists()
