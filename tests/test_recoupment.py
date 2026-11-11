"""Period close: recoupment, cross-collateralization, reserves, record one, rolling balances, minimum payment."""

from helpers import line, mini_catalog, run

from royaltyledger.money import portion

USD = 1_000_000


def acct(led, period, payee, account):
    st = led.statements[(payee, period)]
    return next(a for a in st["accounts"] if a["account_id"] == account)


def test_opening_balance_is_minus_recoupable_charges():
    led = run([])
    a = acct(led, "2025Q3", "A001", "C001:all")
    assert a["opening"] == -150_000  # $1,000 advance + $500 recording


def test_net_profit_costs_charged_at_artist_profit_share():
    led = run([])
    a = acct(led, "2025Q3", "A002", "C002:LP1")
    assert a["opening"] == -(20_000 + 5_000)  # advance in full, recording costs at 50%


def test_video_costs_are_half_recoupable_on_royalty_deals():
    def add_video(d):
        d["contracts"][0]["charges"].append(
            {"account_id": "C001:all", "kind": "video", "amount_cents": 40_000, "month": "2025-08", "desc": "Video"}
        )

    led = run([], mini_catalog(add_video))
    a = acct(led, "2025Q3", "A001", "C001:all")
    assert a["charges_total"] == 20_000 and a["closing"] == -170_000


def test_royalties_recoup_then_pay():
    # each $1,000 stream line credits A001 with $149.60 (see test_engine)
    lines = [line("stream", "T00001", 1000 * USD, month="2025-08") for _ in range(12)]
    led = run(lines)
    a = acct(led, "2025Q3", "A001", "C001:all")
    assert a["earnings_total"] == 12 * 14_960
    assert a["closing"] == -150_000 + 12 * 14_960
    assert a["paid"] == a["closing"] == 29_520
    assert led.statements[("A001", "2025Q3")]["payable"] == 29_520


def test_balances_roll_forward_between_periods():
    lines = [line("stream", "T00001", 300 * USD, month=m) for m in ("2025-08", "2025-11", "2026-02", "2026-05")]
    led = run(lines)
    periods = led.periods
    for p, nxt in zip(periods, periods[1:], strict=False):
        for (payee, period), st in led.statements.items():
            if period != p or (payee, nxt) not in led.statements:
                continue
            for a in st["accounts"]:
                b = next(x for x in led.statements[(payee, nxt)]["accounts"] if x["account_id"] == a["account_id"])
                assert b["opening"] == a["closing"] - a["paid"] == a["carried_forward"]


def test_cross_collateralized_new_advance_absorbs_earnings():
    def second_album(d):
        d["contracts"][0]["charges"].append(
            {
                "account_id": "C001:all",
                "kind": "advance",
                "amount_cents": 200_000,
                "month": "2026-01",
                "desc": "Album 2 advance",
            }
        )

    lines = [
        line("stream", "T00001", 1500 * USD, month="2025-08"),
        line("stream", "T00001", 1500 * USD, month="2026-02"),
    ]
    led = run(lines, mini_catalog(second_album))
    q1 = acct(led, "2026Q1", "A001", "C001:all")
    # album 1 earnings now recoup album 2's advance, so nothing is paid
    assert q1["charges_total"] == 200_000 and q1["closing"] < 0 and q1["paid"] == 0


def test_not_cross_collateralized_accounts_are_separate():
    lines = [line("stream", "T00003", 1000 * USD, month="2026-02")]  # album 1 earns 440,000 micros x 1000
    led = run(lines)
    lp1 = acct(led, "2026Q1", "A002", "C002:LP1")
    lp2 = acct(led, "2026Q1", "A002", "C002:LP2")
    assert lp1["closing"] == -25_000 + 44_000
    assert lp2["closing"] == -50_000  # album 2's new advance is untouched by album 1's earnings
    st = led.statements[("A002", "2026Q1")]
    assert st["payable"] == 19_000  # positive account is paid, the negative one does not offset it


def test_cross_collat_flag_would_offset():
    def cross(d):
        for r in d["releases"]:
            if r["artist_id"] == "A002":
                r["account_id"] = "C002:LP1"
        d["contracts"][1]["charges"][2]["account_id"] = "C002:LP1"

    led = run([line("stream", "T00003", 1000 * USD, month="2026-02")], mini_catalog(cross))
    assert led.statements[("A002", "2026Q1")]["payable"] == 0


def test_minimum_payment_threshold_carries_small_balances():
    lines = [line("stream", "T00001", 1025 * USD, month="2025-08") for _ in range(10)]  # just above recoup
    led = run(lines)
    a = acct(led, "2025Q3", "A001", "C001:all")
    assert 0 < a["closing"] < 10_000
    assert a["paid"] == 0 and led.statements[("A001", "2025Q3")]["below_minimum"]
    nxt = acct(led, "2025Q4", "A001", "C001:all")
    assert nxt["opening"] == a["closing"]


def test_reserve_held_on_physical_and_liquidated_over_two_periods():
    led = run([line("physical", "R0001", 1000 * USD, month="2025-08", store="pressing", units=100)])
    q3 = acct(led, "2025Q3", "A001", "C001:all")
    physical = sum(e["cents"] for e in q3["earnings"] if e["source"] == "Physical")
    assert q3["reserve_held"] == portion(physical, 2500, 10000)
    q4 = acct(led, "2025Q4", "A001", "C001:all")
    q1 = acct(led, "2026Q1", "A001", "C001:all")
    q2 = acct(led, "2026Q2", "A001", "C001:all")
    assert q4["reserve_released"] + q1["reserve_released"] == q3["reserve_held"]
    assert abs(q4["reserve_released"] - q1["reserve_released"]) <= 1
    assert q2["reserve_released"] == 0


def test_returns_reduce_earnings_after_reserve():
    lines = [
        line("physical", "R0001", 1000 * USD, month="2025-08", store="pressing", units=100),
        line("physical", "R0001", -200 * USD, month="2025-11", store="pressing", units=-20),
    ]
    led = run(lines)
    q4 = acct(led, "2025Q4", "A001", "C001:all")
    assert q4["earnings_total"] < 0 and q4["reserve_held"] == 0 and q4["reserve_released"] > 0


def test_producer_record_one_held_until_recording_costs_recouped_then_retroactive():
    lines = [
        line("stream", "T00001", 1000 * USD, month="2025-08"),  # artist earns $149.60 < $500 recording
        line("stream", "T00001", 3000 * USD, month="2025-11"),
    ]  # cumulative $598.40 >= $500
    led = run(lines)
    q3 = acct(led, "2025Q3", "PR001", "PR001@C001:all#record_one")
    assert q3["earnings_total"] == 2_640 and q3["producer_held"] == 2_640 and q3["closing"] == 0
    q4 = acct(led, "2025Q4", "PR001", "PR001@C001:all#record_one")
    assert q4["producer_released"] == 2_640  # paid back to record one
    assert q4["closing"] == 2_640 + 7_920 and q4["paid"] == q4["closing"]


def test_producer_first_sale_paid_without_waiting():
    led = run([line("stream", "T00002", 1000 * USD, month="2025-08")])
    a = acct(led, "2025Q3", "PR002", "PR002@C001:all#first_sale")
    assert a["producer_held"] == 0 and a["closing"] == 3_520 and a["paid"] == 3_520


def test_writer_advance_recoups_from_publishing():
    lines = [line("mechanical", "WK00003", 1000 * USD, month="2025-08", store="northbridge")]
    led = run(lines)
    a = acct(led, "2025Q3", "W0003", "W0003:pub")
    assert a["opening"] == -100_000
    assert a["closing"] == -100_000 + 30_000


def test_outside_publisher_paid_above_threshold():
    led = run([line("download", "T00004", 10 * USD, month="2025-08", store="kestrel", units=300)])
    st = led.statements[("PUB-02", "2025Q3")]
    assert st["payable"] == 300 * 12  # 300 downloads x 12 cents


def test_reserve_in_last_period_is_held_and_scheduled_past_the_ledger():
    led = run([line("physical", "R0001", 1000 * USD, month="2026-05", store="pressing", units=100)])
    q2 = acct(led, "2026Q2", "A001", "C001:all")
    assert q2["reserve_held"] > 0 and q2["reserve_outstanding"] == q2["reserve_held"]
