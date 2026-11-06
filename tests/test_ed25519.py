# SPDX-License-Identifier: GPL-3.0-or-later
"""Signatures have to be exactly right. These are the test vectors from RFC 8032, section 7.1."""
import hashlib

import pytest

from revisionworx import ed25519

VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd"
     "454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"),
]


@pytest.mark.parametrize("seed, public, message, signature", VECTORS)
def test_rfc_8032_vectors(seed, public, message, signature):
    seed, public, message, signature = map(bytes.fromhex, (seed, public, message, signature))
    assert ed25519.public_key(seed) == public
    assert ed25519.sign(seed, message) == signature
    assert ed25519.verify(public, message, signature)


def _signed(n=7):
    seed = hashlib.sha256(f"seed {n}".encode()).digest()
    return ed25519.public_key(seed), b"a journal entry", ed25519.sign(seed, b"a journal entry")


def test_many_keys_sign_and_verify():
    for n in range(40):
        public, message, signature = _signed(n)
        assert ed25519.verify(public, message, signature)


def test_changing_any_byte_is_refused():
    public, message, signature = _signed()
    for i in range(64):
        bad = bytearray(signature)
        bad[i] ^= 1
        assert not ed25519.verify(public, message, bytes(bad))
    for i in range(32):
        bad = bytearray(public)
        bad[i] ^= 1
        assert not ed25519.verify(bytes(bad), message, signature)
    assert not ed25519.verify(public, message + b"!", signature)


def test_a_signature_with_s_past_the_group_order_is_refused():
    """Adding the group order to s makes a different signature for the same message, unless it's refused."""
    public, message, signature = _signed()
    s = int.from_bytes(signature[32:], "little") + ed25519._L
    if s < 2 ** 256:
        assert not ed25519.verify(public, message, signature[:32] + s.to_bytes(32, "little"))


@pytest.mark.parametrize("public, signature", [(b"", b""), (b"\xff" * 32, b"\xff" * 64), (b"\x00" * 32, b"\x00" * 64),
                                               (b"x" * 31, b"y" * 64), (b"x" * 32, b"y" * 63)])
def test_damaged_keys_and_signatures_are_false_and_never_an_exception(public, signature):
    assert ed25519.verify(public, b"m", signature) is False


def test_a_seed_has_to_be_32_bytes():
    with pytest.raises(ValueError):
        ed25519.public_key(b"short")
