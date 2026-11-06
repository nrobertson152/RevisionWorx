# SPDX-License-Identifier: GPL-3.0-or-later
"""Ed25519 signatures (RFC 8032), in pure Python because the core uses the standard library only.

Used to sign journal entries in a shared workspace (ADR-024). Python big integers do not run in constant time, so
this would be the wrong tool where an attacker can measure the signer. It is the right one here: signing happens on
the user's own computer and nothing answers requests from anyone else. Pinned to the RFC's test vectors by
tests/test_ed25519.py.
"""
from __future__ import annotations

import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)
_WINDOW = 4                                    # bits per step of a fixed-base multiplication


def _add(a: tuple, b: tuple) -> tuple:
    """Points in extended coordinates (X, Y, Z, T): the unified formula, correct for doubling too."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    e = (y1 - x1) * (y2 - x2) % _P
    f = (y1 + x1) * (y2 + x2) % _P
    g = 2 * t1 * t2 * _D % _P
    h = 2 * z1 * z2 % _P
    i, j, k, m = f - e, h - g, h + g, f + e
    return (i * j % _P, k * m % _P, j * k % _P, i * m % _P)


def _double(a: tuple) -> tuple:
    x, y, z, _t = a
    e = x * x % _P
    f = y * y % _P
    g = 2 * z * z % _P
    h = e + f
    i = h - (x + y) * (x + y) % _P
    j = e - f
    k = g + j
    return (i * k % _P, j * h % _P, k * j % _P, i * h % _P)


_ZERO = (0, 1, 1, 0)


def _x_from_y(y: int, sign: int) -> int | None:
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    if x & 1 != sign:
        x = _P - x
    return x


_BASE_Y = 4 * pow(5, _P - 2, _P) % _P
_BASE_X = _x_from_y(_BASE_Y, 0)
_BASE = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _P)


def _base_table() -> list[list[tuple]]:
    """Multiples of the base point for each 4-bit position of a scalar, so a fixed-base multiplication is additions only."""
    table, start = [], _BASE
    for _ in range(0, 256, _WINDOW):
        row, point = [_ZERO], _ZERO
        for _ in range((1 << _WINDOW) - 1):
            point = _add(point, start)
            row.append(point)
        table.append(row)
        for _ in range(_WINDOW):
            start = _double(start)
    return table


_TABLE: list[list[tuple]] | None = None


def _times_base(scalar: int) -> tuple:
    global _TABLE
    if _TABLE is None:
        _TABLE = _base_table()
    point, position, mask = _ZERO, 0, (1 << _WINDOW) - 1
    while scalar:
        digit = scalar & mask
        if digit:
            point = _add(point, _TABLE[position][digit])
        scalar >>= _WINDOW
        position += 1
    return point


def _times(scalar: int, point: tuple) -> tuple:
    """Window method: four doublings for each add, against a table of 16 multiples of this point."""
    row, step = [_ZERO], _ZERO
    for _ in range(15):
        step = _add(step, point)
        row.append(step)
    result = _ZERO
    for shift in range((scalar.bit_length() + 3) // 4 * 4 - 4, -4, -4):
        result = _double(_double(_double(_double(result))))
        digit = (scalar >> shift) & 15
        if digit:
            result = _add(result, row[digit])
    return result


def _encode(point: tuple) -> bytes:
    x, y, z, _t = point
    inverse = pow(z, _P - 2, _P)
    x, y = x * inverse % _P, y * inverse % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(data: bytes) -> tuple | None:
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= _P:
        return None
    x = _x_from_y(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("an Ed25519 seed is 32 bytes")
    h = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(h[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    return scalar, h[32:]


def public_key(seed: bytes) -> bytes:
    return _encode(_times_base(_expand(seed)[0]))


def sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _expand(seed)
    public = _encode(_times_base(scalar))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    big_r = _encode(_times_base(r))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % _L
    return big_r + ((r + k * scalar) % _L).to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a valid signature. Damaged keys and signatures are False, never an exception."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a, big_r = _decode(public), _decode(signature[:32])
    if a is None or big_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False                           # the RFC's check that stops a signature being altered and still passing
    k = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    # s*B = R + k*A, compared projectively so that no inversion is needed
    left = _times_base(s)
    right = _add(big_r, _times(k, a))
    return (left[0] * right[2] - right[0] * left[2]) % _P == 0 and (left[1] * right[2] - right[1] * left[2]) % _P == 0
