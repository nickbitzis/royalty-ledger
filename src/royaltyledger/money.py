"""Money rules. Two integer units, no floats anywhere.

- Report lines are converted to USD **micros** (1 micro = 0.000001 USD = 0.0001 cent). Per-stream amounts
  are often a fraction of a cent, so line level math needs more precision than cents.
- Statements are in integer **cents**.

Rounding rules, applied the same way everywhere:
1. FX: local amount (exact Decimal from the report) x rate, rounded half-even to the micro.
2. A share of a line (fee, royalty rate, producer points) is rounded half-even to the micro. The residual party
   (the label on master income, the publisher on publishing income) takes whatever is left, so the parts of a
   line always add back to the line exactly.
3. Splits between several payees at once (writer splits, release to tracks, reserve installments) use the
   largest remainder method, ties broken by position, so the parts add up exactly.
4. At period close every bucket (payee, account, source, territory) is converted from micros to cents with the
   largest remainder method across all buckets of the period, so the cents add up to the period's money received
   rounded half-even to the cent.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

MICROS_PER_USD = 1_000_000
MICROS_PER_CENT = 10_000


def round_div(num: int, den: int) -> int:
    """num / den rounded half to even. Works for negative numerators."""
    q, r = divmod(num, den)
    twice = 2 * r
    if twice > den or (twice == den and q % 2 == 1):
        q += 1
    return q


def portion(amount: int, num: int, den: int) -> int:
    """amount x num / den, rounded half-even."""
    return round_div(amount * num, den)


def split(amount: int, weights: list[int]) -> list[int]:
    """Largest remainder split of an integer amount by integer weights. Parts sum to amount exactly."""
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    floors, rems = [], []
    for w in weights:
        q, r = divmod(amount * w, total)
        floors.append(q)
        rems.append(r)
    left = amount - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (-rems[i], i))
    for i in order[:left]:
        floors[i] += 1
    return floors


def to_micros(amount: str | Decimal, usd_per_unit: str | Decimal) -> int:
    value = Decimal(amount) * Decimal(usd_per_unit) * MICROS_PER_USD
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def micros_to_cents(micros: int) -> int:
    return round_div(micros, MICROS_PER_CENT)


def buckets_to_cents(buckets: dict, total_micros: int | None = None) -> dict:
    """Convert a dict of micros to cents with the largest remainder method.

    The result sums to the half-even rounding of the total, so a period's statements, fees, label share and
    suspense add up to the money received, to the cent."""
    keys = sorted(buckets)
    total = sum(buckets.values()) if total_micros is None else total_micros
    target = micros_to_cents(total)
    floors, rems = {}, {}
    for k in keys:
        q, r = divmod(buckets[k], MICROS_PER_CENT)
        floors[k] = q
        rems[k] = r
    left = target - sum(floors.values())
    order = sorted(keys, key=lambda k: (-rems[k], str(k)))
    for k in order[:left]:
        floors[k] += 1
    return floors


def fmt_cents(c: int) -> str:
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


def fmt_micros(m: int, places: int = 6) -> str:
    d = Decimal(m) / MICROS_PER_USD
    return f"${d:.{places}f}"
