"""Money as integer minor units (kobo, cents) plus an ISO 4217 currency.

Floats never touch money. Every amount is a whole number of the currency's smallest unit, and
arithmetic between different currencies is refused rather than silently converted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.shared.errors import ValidationFailed

_CURRENCY = re.compile(r"^[A-Z]{3}$")

#: Largest single amount we accept: 10 billion major units. Far above any school fee, far below
#: the bigint ceiling, so sums of many amounts can never overflow.
MAX_MINOR = 10**12

BASIS_POINTS = 10_000

#: Currencies whose smallest unit is the major unit (no decimals).
_ZERO_DECIMAL = frozenset({"JPY", "KRW", "UGX", "RWF", "XAF", "XOF", "VND", "CLP", "ISK"})

_COUNTRY_CURRENCY = {
    "NG": "NGN",
    "GH": "GHS",
    "KE": "KES",
    "ZA": "ZAR",
    "UG": "UGX",
    "RW": "RWF",
    "TZ": "TZS",
    "CM": "XAF",
    "SN": "XOF",
    "CI": "XOF",
    "EG": "EGP",
    "GB": "GBP",
    "US": "USD",
    "CA": "CAD",
}


def currency_code(value: str) -> str:
    code = value.strip().upper()
    if not _CURRENCY.match(code):
        raise ValidationFailed(
            "Currency must be a three-letter ISO 4217 code.", details={"field": "currency"}
        )
    return code


def default_currency(country_code: str) -> str:
    return _COUNTRY_CURRENCY.get(country_code.upper(), "USD")


def minor_digits(currency: str) -> int:
    return 0 if currency in _ZERO_DECIMAL else 2


def _round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounding halves away from zero (for non-negative inputs)."""
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder * 2 >= denominator else 0)


@dataclass(frozen=True, slots=True, order=False)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise ValidationFailed(
                "Amounts must be whole minor units.", details={"field": "amount"}
            )
        if abs(self.amount_minor) > MAX_MINOR:
            raise ValidationFailed("That amount is too large.", details={"field": "amount"})
        object.__setattr__(self, "currency", currency_code(self.currency))

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    @classmethod
    def positive(cls, amount_minor: int, currency: str, field: str = "amount") -> Money:
        money = cls(amount_minor, currency)
        if money.amount_minor <= 0:
            raise ValidationFailed("Enter an amount greater than zero.", details={"field": field})
        return money

    def _same(self, other: Money) -> None:
        if other.currency != self.currency:
            raise ValidationFailed(
                "Amounts are in different currencies.", details={"field": "currency"}
            )

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: Money) -> bool:
        self._same(other)
        return self.amount_minor >= other.amount_minor

    @property
    def is_zero(self) -> bool:
        return self.amount_minor == 0

    @property
    def is_negative(self) -> bool:
        return self.amount_minor < 0

    def min(self, other: Money) -> Money:
        return self if self <= other else other

    def share(self, basis_points: int) -> Money:
        """``basis_points`` / 10 000 of this amount, rounded half up. 2 500 bp is 25 %."""
        if not 0 <= basis_points <= BASIS_POINTS:
            raise ValidationFailed(
                "A percentage must be between 0 and 100.", details={"field": "percent"}
            )
        if self.amount_minor < 0:
            return -(-self).share(basis_points)
        return Money(_round_half_up(self.amount_minor * basis_points, BASIS_POINTS), self.currency)

    def __str__(self) -> str:
        digits = minor_digits(self.currency)
        if digits == 0:
            return f"{self.currency} {self.amount_minor:,}"
        sign = "-" if self.amount_minor < 0 else ""
        major, minor = divmod(abs(self.amount_minor), 10**digits)
        return f"{self.currency} {sign}{major:,}.{minor:0{digits}d}"


def total(amounts: list[Money], currency: str) -> Money:
    result = Money.zero(currency)
    for amount in amounts:
        result = result + amount
    return result
