# SPDX-License-Identifier: GPL-3.0-or-later
"""Revision label schemes (ADR-011)."""
from __future__ import annotations

import re
import string

SCHEMES = ("alpha", "numeric", "numeric2", "manual")
ALPHA_EXCLUDED = set("IOQSXZ")  # ASME Y14.35 recommends not using these letters
ALPHABET = [c for c in string.ascii_uppercase if c not in ALPHA_EXCLUDED]
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")


def alpha_label(n: int) -> str:
    """1 -> A, 20 -> Y, 21 -> AA (bijective base-20 over the allowed letters)."""
    if n < 1:
        raise ValueError("revision numbers start at 1")
    out = []
    while n:
        n, r = divmod(n - 1, len(ALPHABET))
        out.append(ALPHABET[r])
    return "".join(reversed(out))


def label_for(scheme: str, number: int) -> str | None:
    if scheme == "alpha":
        return alpha_label(number)
    if scheme == "numeric":
        return str(number)
    if scheme == "numeric2":
        return f"{number:02d}"
    if scheme == "manual":
        return None
    raise ValueError(f"unknown revision scheme {scheme!r}; use one of {SCHEMES}")


def validate_label(label: str) -> str:
    label = label.strip()
    if not _LABEL_RE.match(label):
        raise ValueError(f"revision label {label!r} must be 1-16 letters, digits, '.', '_' or '-'")
    return label
