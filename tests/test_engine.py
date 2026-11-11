"""Line level allocation: fees, contract types, producer points, mechanicals, publishing splits."""

import random

import pytest
from helpers import alloc, line, mini_catalog

from royaltyledger.catalog import generate, validate
from royaltyledger.engine import LABEL, PUBLISHER, SUSPENSE, TAX, Engine
from royaltyledger.rules import Rules

USD = 1_000_000


def test_royalty_deal_stream_home_territory():
    a = alloc(line("stream", "T00001", USD))
    assert a[("_fee:wavelet", "wavelet", "Distribution fee", "US")] == 120_000  # 12% distribution fee
    # net 880,000; all-in 20% = 176,000; producer 3 points = 26,400 out of the artist's share
    assert a[("A001", "C001:all", "Streaming", "US")] == 149_600
    assert a[("PR001", "PR001@C001:all#record_one", "Streaming", "US")] == 26_400
    assert a[(LABEL, LABEL, "Streaming", "US")] == 704_000
    assert sum(a.values()) == USD


def test_royalty_deal_foreign_rate_factor():
    a = alloc(line("stream", "T00001", USD, territory="GB"))
    # 85% of the base rate outside the home territory
    assert a[("A001", "C001:all", "Streaming", "GB")] == 880_000 * 2000 * 8500 // 10**8 - 880_000 * 300 * 8500 // 10**8
    assert sum(a.values()) == USD


def test_net_profit_deal_splits_50_50():
    a = alloc(line("stream", "T00003", USD))
    assert a[("A002", "C002:LP1", "Streaming", "US")] == 440_000
    assert a[(LABEL, LABEL, "Streaming", "US")] == 440_000


def test_net_profit_deal_deducts_mechanicals_before_split():
    a = alloc(line("download", "T00003", USD, store="kestrel", units=1))
    net = USD - 150_000  # 15% download store fee
    pool = net - 120_000  # US download mechanical per unit, paid by the label
    assert a[("A002", "C002:LP1", "Downloads", "US")] == pool // 2
    assert a[(LABEL, LABEL, "Downloads", "US")] == pool - pool // 2


def test_producer_points_come_out_of_artist_share():
    a = alloc(line("stream", "T00002", USD))
    all_in = 176_000
    producer = a[("PR002", "PR002@C001:all#first_sale", "Streaming", "US")]
    assert producer == 35_200  # 4 points of 880,000
    assert a[("A001", "C001:all", "Streaming", "US")] == all_in - producer
    assert a[(LABEL, LABEL, "Streaming", "US")] == 880_000 - all_in  # label share is unchanged by producer points


def test_download_mechanical_per_unit_split_by_writer_shares():
    a = alloc(line("download", "T00001", USD, store="kestrel", units=1))
    mech = 120_000
    # WK00001: 50% our writer W0001 on a co-pub deal (75% of mechanicals), 50% outside publisher PUB-02
    assert a[("W0001", "W0001:pub", "Mechanicals (label paid)", "US")] == 45_000
    assert a[(PUBLISHER, PUBLISHER, "Mechanicals (label paid)", "US")] == 15_000
    assert a[("PUB-02", "PUB-02", "Mechanicals (label paid)", "US")] == 60_000
    assert a[(LABEL, LABEL, "Downloads", "US")] == 850_000 - mech - 170_000


def test_download_mechanical_percent_outside_home():
    a = alloc(line("download", "T00004", USD, store="kestrel", territory="DE", units=3))
    assert a[("PUB-02", "PUB-02", "Mechanicals (label paid)", "DE")] == 90_000  # 9% of gross


def test_society_performance_uses_writer_deal_and_withholding():
    a = alloc(line("performance", "WK00003", USD, store="northbridge", territory="DE"))
    tax = a[(TAX, TAX, "Withholding tax", "DE")]
    assert tax == 150_000
    net = USD - tax
    w3 = net * 6000 // 10000  # traditional deal: 0% of the publisher share of performance income
    w4 = net - w3  # admin deal: 85%
    assert a[("W0003", "W0003:pub", "Performance (publisher share)", "DE")] == 0
    assert a[("W0004", "W0004:pub", "Performance (publisher share)", "DE")] == w4 * 8500 // 10000
    assert a[(PUBLISHER, PUBLISHER, "Performance (publisher share)", "DE")] == w3 + w4 - w4 * 8500 // 10000


def test_society_mechanical_traditional_deal_pays_half():
    a = alloc(line("mechanical", "WK00003", USD, store="northbridge", territory="US"))
    assert a[("W0003", "W0003:pub", "Mechanicals (society)", "US")] == 300_000
    assert a[("W0004", "W0004:pub", "Mechanicals (society)", "US")] == 340_000


def test_society_income_on_work_with_no_controlled_share_goes_to_suspense():
    a = alloc(line("performance", "WK00004", USD, store="northbridge"))
    assert sum(v for k, v in a.items() if k[0] == SUSPENSE) == USD


def test_neighbouring_rights_at_contract_rate():
    a = alloc(line("neighbouring", "T00003", USD, store="northbridge", territory="GB"))
    assert a[("A002", "C002:LP1", "Neighbouring rights", "GB")] == 500_000


def test_physical_line_expands_to_release_tracks():
    a = alloc(line("physical", "R0001", 100 * USD, store="pressing", units=10))
    assert a[("_fee:pressing", "pressing", "Distribution fee", "US")] == 25 * USD
    assert ("PR001", "PR001@C001:all#record_one", "Physical", "US") in a
    assert ("PR002", "PR002@C001:all#first_sale", "Physical", "US") in a
    assert sum(a.values()) == 100 * USD


def test_physical_return_is_negative_and_still_exact():
    a = alloc(line("physical", "R0001", -9 * USD - 3, store="pressing", units=-1))
    assert a[("A001", "C001:all", "Physical", "US")] < 0
    assert sum(a.values()) == -9 * USD - 3


@pytest.mark.parametrize("status", ["review", "unmatched"])
def test_unmatched_money_goes_to_suspense(status):
    a = alloc(line("stream", "T00001", USD, status=status))
    assert list(a.values()) == [USD] and next(iter(a))[0] == SUSPENSE


def test_publishing_splits_sum_to_100_percent_in_generated_catalog():
    cat = generate(seed=5, n_artists=20)
    assert validate(cat) == []
    for comp in cat["compositions"]:
        assert sum(s["share_bps"] for s in comp["shares"]) == 10000


def test_validate_catches_bad_split():
    cat = generate(seed=5, n_artists=3)
    cat["compositions"][0]["shares"][0]["share_bps"] -= 1
    assert any("10000" in e for e in validate(cat))


def test_every_line_allocates_exactly_random_amounts():
    eng = Engine(mini_catalog(), Rules.load())
    r = random.Random(1)
    kinds = [
        ("stream", "T00001", "wavelet"),
        ("download", "T00002", "kestrel"),
        ("physical", "R0001", "pressing"),
        ("performance", "WK00001", "northbridge"),
        ("mechanical", "WK00003", "northbridge"),
        ("neighbouring", "T00004", "northbridge"),
        ("stream", "T00004", "tidepool"),
    ]
    for _ in range(500):
        kind, target, store = r.choice(kinds)
        amt = r.randint(-5_000_000, 50_000_000)
        terr = r.choice(["US", "GB", "DE", "JP", "BR"])
        parts = eng.allocate(line(kind, target, amt, territory=terr, store=store, units=r.randint(1, 50)))
        assert sum(v for _, v in parts) == amt
