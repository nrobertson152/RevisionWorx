# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact quantities, units of measure and safety limits.

Quantities are :class:`decimal.Decimal`, never ``float``: ``0.1 + 0.2`` must be ``0.3`` in a BOM. All
arithmetic runs in :data:`EXACT`, a context that *traps* ``Inexact``: a result that would have to be
rounded raises :class:`QuantityError` instead of silently changing a quantity.

Text is parsed strictly (``"2"``, ``"2.5"``, ``".5"`` is refused): no signs, exponents, thousands
separators or decimal commas, so the same CSV means the same thing in every locale.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import (Context, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow, Rounded,
                     localcontext)
from typing import Iterable

from .errors import LimitExceeded, QuantityError, UnitError

#: Arithmetic context for every quantity operation. 80 significant digits is far beyond any real BOM;
#: anything that would need rounding raises instead.
EXACT = Context(prec=80, Emax=10_000, Emin=-10_000,
                traps=[InvalidOperation, Overflow, DivisionByZero, Inexact, Rounded])

_QTY_RE = re.compile(r"[0-9]+(\.[0-9]+)?")


@dataclass(frozen=True)
class Limits:
    """Safety limits. Exceeding one raises :class:`LimitExceeded` or :class:`QuantityError`; nothing is
    truncated or rounded silently."""

    max_line_quantity: Decimal = Decimal(10) ** 9      # per BOM line
    max_scale: int = 6                                  # decimal places on a BOM line
    max_total_quantity: Decimal = Decimal(10) ** 15     # any exploded or rolled-up total
    max_depth: int = 64                                 # assembly levels below a root
    max_explosion_rows: int = 200_000                   # rows of an indented (path) explosion
    max_lines: int = 1_000_000                          # lines in one structure


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Unit:
    """A unit of measure. ``factor`` converts one of this unit into the dimension's base unit."""

    code: str
    name: str
    dimension: str
    factor: Decimal
    integral: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.factor, Decimal) or not self.factor.is_finite() or self.factor <= 0:
            raise UnitError(f"unit {self.code!r}: factor must be a positive finite Decimal")


class UnitRegistry:
    """Unit codes, case-insensitive, with aliases. Lookups return the canonical :class:`Unit`."""

    def __init__(self, units: Iterable[Unit] = (), aliases: dict[str, str] | None = None):
        self._units: dict[str, Unit] = {}
        self._aliases: dict[str, str] = {}
        for u in units:
            self.register(u)
        for alias, code in (aliases or {}).items():
            self.alias(alias, code)

    @staticmethod
    def _key(code: str) -> str:
        return unicodedata.normalize("NFC", code).strip().casefold()

    def register(self, unit: Unit) -> None:
        key = self._key(unit.code)
        if not key:
            raise UnitError("unit code must not be empty")
        if key in self._units or key in self._aliases:
            raise UnitError(f"unit {unit.code!r} is already defined")
        self._units[key] = unit

    def alias(self, alias: str, code: str) -> None:
        target = self.get(code)
        key = self._key(alias)
        if key in self._units or key in self._aliases:
            raise UnitError(f"unit alias {alias!r} is already defined")
        self._aliases[key] = self._key(target.code)

    def get(self, code: str) -> Unit:
        key = self._key(code)
        key = self._aliases.get(key, key)
        try:
            return self._units[key]
        except KeyError:
            raise UnitError(f"unknown unit {code!r}; known units: {', '.join(self.codes())}") from None

    def known(self, code: str) -> bool:
        key = self._key(code)
        return self._aliases.get(key, key) in self._units

    def codes(self) -> list[str]:
        return sorted((u.code for u in self._units.values()), key=str.casefold)

    def canonical(self, code: str) -> str:
        return self.get(code).code

    def convert(self, quantity: Decimal, from_code: str, to_code: str) -> Decimal:
        """Convert exactly between units of the same dimension. Raises :class:`UnitError` across
        dimensions and :class:`QuantityError` if the result cannot be represented exactly."""
        src, dst = self.get(from_code), self.get(to_code)
        if src.dimension != dst.dimension:
            raise UnitError(f"cannot convert {src.code} ({src.dimension}) to {dst.code} ({dst.dimension})")
        if src is dst:
            return quantity
        try:
            with localcontext(EXACT):
                return (quantity * src.factor) / dst.factor
        except (Inexact, Rounded, InvalidOperation):
            raise QuantityError(f"{quantity} {src.code} has no exact value in {dst.code}") from None


def _d(text: str) -> Decimal:
    return Decimal(text)


DEFAULT_UNITS = UnitRegistry(
    [
        Unit("EA", "each", "count", _d("1"), integral=True),
        Unit("mm", "millimetre", "length", _d("0.001")),
        Unit("cm", "centimetre", "length", _d("0.01")),
        Unit("m", "metre", "length", _d("1")),
        Unit("in", "inch", "length", _d("0.0254")),
        Unit("ft", "foot", "length", _d("0.3048")),
        Unit("g", "gram", "mass", _d("0.001")),
        Unit("kg", "kilogram", "mass", _d("1")),
        Unit("lb", "pound", "mass", _d("0.45359237")),
        Unit("oz", "ounce", "mass", _d("0.028349523125")),
        Unit("mL", "millilitre", "volume", _d("0.001")),
        Unit("L", "litre", "volume", _d("1")),
        Unit("gal", "US gallon", "volume", _d("3.785411784")),
        Unit("mm2", "square millimetre", "area", _d("0.000001")),
        Unit("m2", "square metre", "area", _d("1")),
        Unit("ft2", "square foot", "area", _d("0.09290304")),
    ],
    aliases={"each": "EA", "pc": "EA", "pcs": "EA", "pce": "EA", "qty": "EA", "unit": "EA",
             "inch": "in", "feet": "ft", "foot": "ft", "metre": "m", "meter": "m",
             "litre": "L", "liter": "L", "kgs": "kg", "lbs": "lb", "sqm": "m2", "sqft": "ft2"},
)


def canonical_text(q: Decimal) -> str:
    """Canonical text for a quantity: plain notation, no exponent, no trailing zeros (``2.50`` -> ``2.5``)."""
    if not isinstance(q, Decimal) or not q.is_finite():
        raise QuantityError(f"not a finite decimal quantity: {q!r}")
    with localcontext(EXACT):
        n = q.normalize()
    text = format(n, "f")
    return "0" if text in ("-0", "") else text


def parse_quantity(text: str, *, what: str = "quantity") -> Decimal:
    """Parse strict decimal text (``"3"``, ``"0.25"``). Explains why anything else is refused."""
    if not isinstance(text, str):
        raise QuantityError(f"{what} must be text, got {type(text).__name__}")
    s = unicodedata.normalize("NFKC", text).strip()
    if not s:
        raise QuantityError(f"{what} is empty")
    if _QTY_RE.fullmatch(s):
        return Decimal(s)
    if s.startswith("-"):
        raise QuantityError(f"{what} {text!r} is negative; quantities must be positive")
    if s.startswith("+"):
        raise QuantityError(f"{what} {text!r}: write the number without a sign")
    if "," in s:
        raise QuantityError(f"{what} {text!r}: use '.' as the decimal separator and no thousands separators")
    if re.fullmatch(r"[0-9.]*[eE][+-]?[0-9]+", s):
        raise QuantityError(f"{what} {text!r}: exponent notation is not accepted; write the number in full")
    if s.casefold() in ("nan", "inf", "infinity", "-inf"):
        raise QuantityError(f"{what} {text!r} is not a finite number")
    if s.startswith(".") or s.endswith("."):
        raise QuantityError(f"{what} {text!r}: write a digit on both sides of the decimal point")
    raise QuantityError(f"{what} {text!r} is not a decimal number")


def to_quantity(value: object, *, what: str = "quantity") -> Decimal:
    """Accept ``Decimal``, ``int`` or strict text. ``float`` is refused: it cannot hold 0.1 exactly."""
    if isinstance(value, bool):
        raise QuantityError(f"{what} must be a number, got a boolean")
    if isinstance(value, Decimal):
        q = value
    elif isinstance(value, int):
        q = Decimal(value)
    elif isinstance(value, str):
        q = parse_quantity(value, what=what)
    elif isinstance(value, float):
        raise QuantityError(f"{what} {value!r} is a float; pass a Decimal or text such as '0.1'")
    else:
        raise QuantityError(f"{what} must be a Decimal, int or text, got {type(value).__name__}")
    if not q.is_finite():
        raise QuantityError(f"{what} {value!r} is not a finite number")
    return q


def check_line_quantity(q: Decimal, unit: Unit | None, limits: Limits = DEFAULT_LIMITS,
                        *, what: str = "quantity") -> None:
    """Raise :class:`QuantityError` unless ``q`` is a valid quantity for one BOM line."""
    if not q.is_finite():
        raise QuantityError(f"{what} {q} is not finite")
    if q <= 0:
        raise QuantityError(f"{what} {canonical_text(q) if q.is_finite() else q} must be greater than zero")
    if q > limits.max_line_quantity:
        raise QuantityError(f"{what} {canonical_text(q)} exceeds the per-line limit "
                            f"{canonical_text(limits.max_line_quantity)}")
    exponent = q.normalize(EXACT).as_tuple().exponent
    if isinstance(exponent, int) and -exponent > limits.max_scale:
        raise QuantityError(f"{what} {canonical_text(q)} has more than {limits.max_scale} decimal places")
    if unit is not None and unit.integral and q != q.to_integral_value():
        raise QuantityError(f"{what} {canonical_text(q)} must be a whole number for unit {unit.code}")


def multiply(a: Decimal, b: Decimal, limits: Limits = DEFAULT_LIMITS, *, what: str = "quantity") -> Decimal:
    """Exact product, bounded by ``limits.max_total_quantity``."""
    try:
        with localcontext(EXACT):
            r = a * b
    except (Inexact, Rounded, Overflow, InvalidOperation):
        raise QuantityError(f"{what}: {a} x {b} cannot be represented exactly") from None
    if r > limits.max_total_quantity:
        raise LimitExceeded(f"{what} {canonical_text(r)} exceeds the total-quantity limit "
                            f"{canonical_text(limits.max_total_quantity)}")
    return r


def add(a: Decimal, b: Decimal, limits: Limits = DEFAULT_LIMITS, *, what: str = "quantity") -> Decimal:
    """Exact sum, bounded by ``limits.max_total_quantity``."""
    try:
        with localcontext(EXACT):
            r = a + b
    except (Inexact, Rounded, Overflow, InvalidOperation):
        raise QuantityError(f"{what}: {a} + {b} cannot be represented exactly") from None
    if r > limits.max_total_quantity:
        raise LimitExceeded(f"{what} {canonical_text(r)} exceeds the total-quantity limit "
                            f"{canonical_text(limits.max_total_quantity)}")
    return r


def subtract(a: Decimal, b: Decimal) -> Decimal:
    try:
        with localcontext(EXACT):
            return a - b
    except (Inexact, Rounded, Overflow, InvalidOperation):
        raise QuantityError(f"{a} - {b} cannot be represented exactly") from None
