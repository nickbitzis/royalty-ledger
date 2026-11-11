from decimal import Decimal

import pytest

from royaltyledger.money import buckets_to_cents, fmt_cents, micros_to_cents, portion, round_div, split, to_micros
from royaltyledger.rules import Rules, period_of


@pytest.mark.parametrize(
    "num,den,expected",
    [
        (5, 2, 2),
        (7, 2, 4),
        (-5, 2, -2),
        (-7, 2, -4),
        (10, 3, 3),
        (11, 3, 4),
        (-11, 3, -4),
        (15000, 10000, 2),
        (25000, 10000, 2),
        (35000, 10000, 4),
    ],
)
def test_round_half_even(num, den, expected):
    assert round_div(num, den) == expected


@pytest.mark.parametrize(
    "amount,weights",
    [(100, [1, 1, 1]), (1, [3333, 3333, 3334]), (999_999, [6000, 4000]), (-1001, [1, 1, 1]), (7, [5000, 2500, 2500])],
)
def test_split_sums_exactly(amount, weights):
    parts = split(amount, weights)
    assert sum(parts) == amount
    assert max(parts) - min(parts) <= amount if amount > 0 else True


def test_split_largest_remainder_is_deterministic():
    assert split(100, [1, 1, 1]) == [34, 33, 33]
    assert split(2, [1, 1, 1]) == [1, 1, 0]


def test_portion_rounds_half_even():
    assert portion(25, 1, 10) == 2  # 2.5 rounds to 2
    assert portion(35, 1, 10) == 4  # 3.5 rounds to 4


def test_buckets_to_cents_ties_to_total():
    buckets = {("a",): 5000, ("b",): 5000, ("c",): 5000}  # 0.5 cents each, total 1.5 cents
    cents = buckets_to_cents(buckets)
    assert sum(cents.values()) == micros_to_cents(15000) == 2
    assert sorted(cents.values()) == [0, 1, 1]


def test_buckets_to_cents_with_negative_buckets():
    buckets = {("sale",): 1_234_567, ("return",): -234_560, ("fee",): 3}
    cents = buckets_to_cents(buckets)
    assert sum(cents.values()) == micros_to_cents(sum(buckets.values()))


def test_to_micros_exact_decimal():
    assert to_micros("0.00412345", "1") == 4123  # 0.00412345 USD = 4123.45 micros, rounds to 4123
    assert to_micros("1,5".replace(",", "."), "1.0850") == 1_627_500
    assert to_micros(Decimal("-10.00"), "1.27") == -12_700_000


def test_fmt_cents():
    assert fmt_cents(123456) == "$1,234.56"
    assert fmt_cents(-5) == "-$0.05"


def test_fx_uses_rate_of_usage_month():
    rules = Rules.load()
    jul = rules.to_usd_micros("100", "EUR", "2025-07")
    aug = rules.to_usd_micros("100", "EUR", "2025-08")
    assert jul == to_micros("100", rules.fx["2025-07"]["EUR"])
    assert jul != aug


def test_fx_jpy_six_decimal_rate():
    rules = Rules.load()
    assert rules.to_usd_micros("1000", "JPY", "2026-01") == to_micros("1000", rules.fx["2026-01"]["JPY"])
    assert rules.fx["2026-01"]["JPY"].startswith("0.00")


def test_fx_missing_rate_is_an_error():
    with pytest.raises(KeyError):
        Rules.load().to_usd_micros("1", "EUR", "2031-01")
    with pytest.raises(KeyError):
        Rules.load().to_usd_micros("1", "CHF", "2025-07")


def test_usd_is_identity():
    assert Rules.load().to_usd_micros("2.5", "USD", "2025-09") == 2_500_000


@pytest.mark.parametrize(
    "month,period", [("2025-07", "2025Q3"), ("2025-12", "2025Q4"), ("2026-01", "2026Q1"), ("2026-06", "2026Q2")]
)
def test_period_of(month, period):
    assert period_of(month) == period
