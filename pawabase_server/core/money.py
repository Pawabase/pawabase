"""Money, as integers.

Every amount in this application is an integer count of the currency's minor
unit — cents for USD, kobo for NGN — paired with a currency code. There is no
float anywhere in the money path, and no `Decimal` in the database: a column
holding `1999` and a column holding `19.99` differ in that only one of them can
be summed a million times and still be right.

The minor-unit *scale* is a property of the currency, not of the amount. JPY
has none; most currencies have two. `format_money` is the only place that
knows, and it is the only place a number becomes a string for a human.

Rounding appears exactly twice — splitting a discount across lines, and
computing a percentage — and both use `ROUND_HALF_UP` on a `Decimal`, then
return an int. Bankers' rounding is Python's default and is wrong here: a
merchant expects a 5% discount on 1.05 to be 5 cents, not 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: Currencies whose minor unit is not 1/100. Anything not listed has two
#: decimal places, which covers every currency this platform supports today.
_SCALES: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "CLP": 0,
    "ISK": 0,
    "XOF": 0,
    "XAF": 0,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "TND": 3,
}

#: The currencies a store can be created in. Paystack is the only payment
#: provider, so this is exactly what Paystack settles: naira, cedi, rand and
#: shilling. A currency it cannot charge would give a store that can list
#: products and never take a payment.
SUPPORTED_CURRENCIES = ("NGN", "GHS", "ZAR", "KES")

#: What to put in front of the number. Currencies with no entry are rendered
#: with the code instead, which is correct and unambiguous if less pretty.
_SYMBOLS: dict[str, str] = {
    "NGN": "₦",
    "GHS": "₵",
    "KES": "KSh ",
    "ZAR": "R",
}


def scale(currency: str) -> int:
    """How many decimal places this currency's minor unit implies."""
    return _SCALES.get(currency.upper(), 2)


def to_minor(amount: str | int | float | Decimal, currency: str) -> int:
    """Parse a human-entered amount into minor units.

    Accepts what a form field actually contains — `"19.99"`, `"1,299.00"`,
    `19.99` — and returns `1999`. A float is accepted because form parsing
    sometimes produces one, and is converted through its string form so
    `0.1 + 0.2` never reaches the arithmetic.
    """
    if isinstance(amount, int) and not isinstance(amount, bool):
        value = Decimal(amount)
    elif isinstance(amount, Decimal):
        value = amount
    else:
        text = str(amount).strip().replace(",", "").replace(" ", "")
        if not text:
            return 0
        value = Decimal(text)
    factor = Decimal(10) ** scale(currency)
    return int((value * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_major(minor: int, currency: str) -> Decimal:
    """Minor units back to a decimal amount, for display or export."""
    return (Decimal(minor) / (Decimal(10) ** scale(currency))).quantize(
        Decimal(1).scaleb(-scale(currency))
    )


def format_money(minor: int, currency: str) -> str:
    """Render an amount the way a merchant reads it: `$1,299.00`.

    Negative amounts get the sign in front of the symbol (`-$5.00`), which is
    what a refund line should look like; accounting parentheses are a display
    choice the front end can make from the sign.
    """
    currency = currency.upper()
    places = scale(currency)
    negative = minor < 0
    quantised = to_major(abs(minor), currency)
    if places:
        whole, _, fraction = f"{quantised:f}".partition(".")
        body = f"{int(whole):,}.{fraction.ljust(places, '0')[:places]}"
    else:
        body = f"{int(quantised):,}"
    symbol = _SYMBOLS.get(currency)
    rendered = f"{symbol}{body}" if symbol else f"{body} {currency}"
    return f"-{rendered}" if negative else rendered


def percent_of(minor: int, percent: str | int | float | Decimal) -> int:
    """`percent`% of `minor`, rounded half-up to whole minor units.

    Used by percentage discounts and by provider fee estimates. Half-up rather
    than Python's default half-even, because a merchant reading "5% of $1.05"
    expects 5c and half-even gives 4c.
    """
    rate = Decimal(str(percent)) / Decimal(100)
    return int((Decimal(minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split `total` across `weights` so the parts sum to exactly `total`.

    An order-level discount has to land on individual lines — for refunds, for
    tax, for the ledger — and the obvious `round(total * w / sum)` per line
    loses or invents a cent whenever the division is not clean. This gives each
    line its floor share and then hands the remainder out one unit at a time,
    largest fractional part first, so the sum is exact by construction.

    A zero total, or weights summing to zero, splits into zeros rather than
    dividing by zero — an order of entirely free items still has to allocate.
    """
    if not weights:
        return []
    basis = sum(weights)
    if basis == 0 or total == 0:
        return [0] * len(weights)

    shares: list[int] = []
    remainders: list[tuple[Decimal, int]] = []
    for index, weight in enumerate(weights):
        exact = Decimal(total) * Decimal(weight) / Decimal(basis)
        floor = int(exact.to_integral_value(rounding="ROUND_FLOOR"))
        shares.append(floor)
        remainders.append((exact - floor, index))

    leftover = total - sum(shares)
    # Ties break on the earlier line, so the allocation is deterministic and a
    # refund computed twice matches itself.
    for _, index in sorted(remainders, key=lambda pair: (-pair[0], pair[1]))[:leftover]:
        shares[index] += 1
    return shares


@dataclass(frozen=True, slots=True)
class Money:
    """An amount and its currency, for arithmetic that must not mix the two.

    The database stores the pair as two columns; this is what services pass
    around when both halves have to travel together.
    """

    minor: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def parse(cls, amount: str | int | float | Decimal, currency: str = "USD") -> Money:
        return cls(to_minor(amount, currency), currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        return cls(0, currency)

    def _same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot combine {self.currency} and {other.currency}. Convert "
                f"one of them first — this platform never guesses a rate."
            )

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def __mul__(self, quantity: int) -> Money:
        return Money(self.minor * quantity, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.minor < other.minor

    def __bool__(self) -> bool:
        return self.minor != 0

    @property
    def is_negative(self) -> bool:
        return self.minor < 0

    def format(self) -> str:
        return format_money(self.minor, self.currency)

    def as_prop(self) -> dict[str, object]:
        """The shape money takes in an Inertia prop.

        Both halves plus the rendered string: the front end formats nothing
        itself, so a currency's placement and symbol are decided once, on the
        server, and every screen agrees.
        """
        return {
            "minor": self.minor,
            "currency": self.currency,
            "formatted": self.format(),
        }

    def __str__(self) -> str:
        return self.format()


def money_prop(minor: int | None, currency: str) -> dict[str, object]:
    """`Money.as_prop` for the two loose columns a model actually stores."""
    return Money(minor or 0, currency).as_prop()
