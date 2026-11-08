# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenSSH signatures (the SSHSIG format ``ssh-keygen -Y sign`` writes, OpenSSH's PROTOCOL.sshsig), checked in pure Python
because the core uses the standard library only.

Ed25519 keys only: in software (``ssh-ed25519``) or on a FIDO security key such as a YubiKey (``sk-ssh-ed25519@openssh.com``,
OpenSSH's PROTOCOL.u2f), where the key must also say that someone touched it. Signing is done by ``ssh-keygen`` itself, which
asks for the touch, the PIN or the passphrase; nothing here ever holds a private key.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import ed25519

MAGIC = b"SSHSIG"
VERSION = 1
ED25519 = "ssh-ed25519"
SK_ED25519 = "sk-ssh-ed25519@openssh.com"
TYPES = (ED25519, SK_ED25519)
USER_PRESENT = 0x01                     # the security key was touched
USER_VERIFIED = 0x04                    # and its PIN (or fingerprint) was given
HASHES = {"sha256": hashlib.sha256, "sha512": hashlib.sha512}
BEGIN = "-----BEGIN SSH SIGNATURE-----"
END = "-----END SSH SIGNATURE-----"
MAX_SIGNATURE = 16 * 1024               # a real one is under 1 KB


class SignatureError(ValueError):
    """A signature or key that doesn't hold, can't be read, or isn't a kind this checks. The message says which."""


@dataclass(frozen=True)
class PublicKey:
    type: str
    blob: bytes                          # the key as OpenSSH writes it on the wire
    point: bytes                         # the Ed25519 public key, 32 bytes
    application: str = ""                # a security key's application, such as "ssh:"

    @property
    def fingerprint(self) -> str:
        """As ``ssh-keygen -l`` prints it: ``SHA256:`` and the unpadded base64 of the SHA-256 of the key."""
        return "SHA256:" + base64.b64encode(hashlib.sha256(self.blob).digest()).decode("ascii").rstrip("=")

    @property
    def security_key(self) -> bool:
        return self.type == SK_ED25519

    def openssh(self) -> str:
        """One line, as in a ``.pub`` file or after the principal in an ``allowed_signers`` file."""
        return f"{self.type} {base64.b64encode(self.blob).decode('ascii')}"


# ------------------------------------------------------------------------------------------------- reading
class _Reader:
    def __init__(self, data: bytes, what: str):
        self.data, self.pos, self.what = data, 0, what

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise SignatureError(f"{self.what} is cut short or damaged")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def string(self) -> bytes:
        return self.take(self.u32())

    def text(self) -> str:
        try:
            return self.string().decode("utf-8")
        except UnicodeDecodeError:
            raise SignatureError(f"{self.what} is damaged") from None

    def end(self) -> None:
        if self.pos != len(self.data):
            raise SignatureError(f"{self.what} has something after its end")


def _string(data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + data


def key_from_blob(blob: bytes) -> PublicKey:
    r = _Reader(blob, "the public key")
    kind = r.text()
    if kind not in TYPES:
        raise SignatureError(f"the key is {kind}: RevisionWorx checks Ed25519 keys only ({ED25519}, or {SK_ED25519} on a "
                             "security key such as a YubiKey, made with ssh-keygen -t ed25519-sk)")
    point = r.string()
    if len(point) != 32 or ed25519._decode(point) is None:
        raise SignatureError("the public key isn't an Ed25519 key")
    application = r.text() if kind == SK_ED25519 else ""
    r.end()
    return PublicKey(kind, bytes(blob), point, application)


def parse_public_key(text: str) -> PublicKey:
    """The key in one line of a ``.pub`` file: ``TYPE BASE64 [comment]``."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(lines) != 1:
        raise SignatureError("a public key file holds one key on one line (TYPE BASE64 comment), as ssh-keygen writes it")
    parts = lines[0].split()
    if len(parts) < 2:
        raise SignatureError("that isn't an OpenSSH public key (TYPE BASE64 comment)")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("that isn't an OpenSSH public key: its key isn't base64") from None
    key = key_from_blob(blob)
    if key.type != parts[0]:
        raise SignatureError(f"the line says {parts[0]} but holds a {key.type} key")
    return key


def unarmor(text: bytes | str) -> bytes:
    """The SSHSIG blob of an armored signature (``-----BEGIN SSH SIGNATURE-----``)."""
    if isinstance(text, bytes):
        if len(text) > MAX_SIGNATURE:
            raise SignatureError("that is far too big to be a signature")
        try:
            text = text.decode("ascii")
        except UnicodeDecodeError:
            raise SignatureError("that isn't an OpenSSH signature") from None
    lines = [line.strip() for line in text.strip().splitlines()]
    if len(lines) < 3 or lines[0] != BEGIN or lines[-1] != END:
        raise SignatureError("that isn't an OpenSSH signature (-----BEGIN SSH SIGNATURE-----)")
    try:
        return base64.b64decode("".join(lines[1:-1]), validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("the signature is damaged: it isn't base64") from None


def armor(blob: bytes) -> str:
    body = base64.b64encode(blob).decode("ascii")
    return "\n".join([BEGIN, *(body[i:i + 70] for i in range(0, len(body), 70)), END]) + "\n"


def signed_data(namespace: str, hash_name: str, message: bytes) -> bytes:
    """What an SSHSIG signature signs (PROTOCOL.sshsig): the preamble, the namespace, the hash's name and the message's hash."""
    return (MAGIC + _string(namespace.encode("utf-8")) + _string(b"") + _string(hash_name.encode("ascii"))
            + _string(HASHES[hash_name](message).digest()))


# ------------------------------------------------------------------------------------------------ checking
def verify(signature: bytes | str, message: bytes, namespace: str, *, require_touch: bool = True) -> dict:
    """Check an armored SSHSIG signature of ``message`` in ``namespace``. Returns the signer's key and, for a security key,
    its flags and counter; raises ``SignatureError`` saying why it doesn't hold. A security key's signature must say that the
    key was touched unless ``require_touch`` is false, as ``ssh-keygen -Y verify`` does by default."""
    r = _Reader(unarmor(signature), "the signature")
    if r.take(6) != MAGIC:
        raise SignatureError("that isn't an OpenSSH signature")
    version = r.u32()
    if version != VERSION:
        raise SignatureError(f"the signature is SSHSIG version {version}; this checks version {VERSION}")
    key = key_from_blob(r.string())
    ns = r.text()
    r.string()                                   # reserved
    hash_name = r.text()
    sig_blob = r.string()
    r.end()
    if ns != namespace:
        raise SignatureError(f"the signature is for {ns!r}, not {namespace!r}: it was made for something else")
    if hash_name not in HASHES:
        raise SignatureError(f"the signature uses {hash_name!r}; only sha256 and sha512 are checked")
    s = _Reader(sig_blob, "the signature")
    if s.text() != key.type:
        raise SignatureError("the signature's kind doesn't match its key")
    raw = s.string()
    flags = counter = None
    if key.security_key:
        flags = s.take(1)[0]
        counter = s.u32()
    s.end()
    if len(raw) != 64:
        raise SignatureError("the signature is damaged")
    data = signed_data(namespace, hash_name, message)
    if key.security_key:                         # PROTOCOL.u2f: what the security key signs
        data = (hashlib.sha256(key.application.encode("utf-8")).digest() + bytes([flags])
                + counter.to_bytes(4, "big") + hashlib.sha256(data).digest())
    if not ed25519.verify(key.point, data, raw):
        raise SignatureError("the signature doesn't hold: the file was changed after it was signed, or it was signed with "
                             "another key")
    if key.security_key and require_touch and not flags & USER_PRESENT:
        raise SignatureError("the security key that signed this wasn't touched")
    return {"key": key, "fingerprint": key.fingerprint, "type": key.type, "hash": hash_name, "flags": flags,
            "counter": counter, "touched": bool(flags & USER_PRESENT) if flags is not None else None,
            "pin": bool(flags & USER_VERIFIED) if flags is not None else None}


# ------------------------------------------------------------------------------------------------- signing
def ssh_keygen() -> str | None:
    """OpenSSH's ``ssh-keygen``, if it is installed (Windows 10 and 11 have it as an optional feature, Linux in openssh-client)."""
    return shutil.which("ssh-keygen")


def sign_file(path: Path, key_file: Path, namespace: str, *, timeout: float = 300.0) -> bytes:
    """Sign ``path`` with ``ssh-keygen -Y sign``, which writes ``path.sig`` beside it, and return that signature. ssh-keygen
    asks, on this terminal, for whatever the key needs: a touch of the security key, its PIN, a passphrase. Its own messages
    go to standard error."""
    exe = ssh_keygen()
    if exe is None:
        raise SignatureError("ssh-keygen (OpenSSH) isn't installed here, and it is what signs: install the OpenSSH client")
    key_file, path = Path(key_file), Path(path)
    if not key_file.is_file():
        raise SignatureError(f"there's no key file {key_file}")
    out = path.with_name(path.name + ".sig")
    try:
        done = subprocess.run([exe, "-Y", "sign", "-f", str(key_file), "-n", namespace, str(path)],
                              stdout=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise SignatureError(f"ssh-keygen didn't sign within {timeout:.0f} seconds (was the security key touched?)") from None
    except OSError as exc:
        raise SignatureError(f"ssh-keygen couldn't be run: {exc}") from None
    if done.returncode != 0 or not out.is_file():
        raise SignatureError(f"ssh-keygen didn't sign (exit {done.returncode}): its message says why")
    return out.read_bytes()
