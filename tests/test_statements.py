"""Statements on the generated data: tie-out, audit trail, exports, determinism."""

import csv
import io

from royaltyledger.exports import statement_csv, statement_html
from royaltyledger.service import RoyaltyService


def test_every_period_ties_out_to_the_cent(svc):
    for p in svc.rules.periods:
        tie = svc.ledger.tie_out(p)
        assert tie["ties_out"] and tie["difference_cents"] == 0
        assert tie["by_kind"]["payees"] > 0 and tie["by_kind"]["fees"] > 0


def test_statement_closing_equals_components(svc):
    for st in svc.ledger.statements.values():
        for a in st["accounts"]:
            assert a["closing"] == (
                a["opening"]
                + a["earnings_total"]
                - a["charges_total"]
                - a["reserve_held"]
                + a["reserve_released"]
                - a["producer_held"]
                + a["producer_released"]
            )
            assert a["earnings_total"] == sum(e["cents"] for e in a["earnings"])
            assert a["paid"] >= 0 and a["carried_forward"] == a["closing"] - a["paid"]


def test_payable_respects_minimum(svc):
    for st in svc.ledger.statements.values():
        assert st["payable"] == 0 or st["payable"] >= st["min_payment"]


def test_audit_trail_is_complete_for_every_statement_line(svc):
    checked = 0
    files: dict[str, list[str]] = {}
    for st in list(svc.ledger.statements.values())[:60]:
        for a in st["accounts"]:
            for e in a["earnings"]:
                au = svc.audit(e["line_id"])
                assert au["contributions_micros_total"] == e["micros"]
                assert au["cents"] == e["cents"]
                for c in au["contributions"]:
                    if c["file"] not in files:
                        files[c["file"]] = (svc.data_dir / "reports" / c["file"]).read_text().splitlines()
                    assert files[c["file"]][c["row"] - 1]  # the row exists in the original file
                checked += 1
    assert checked > 50


def test_audit_raw_row_matches_source_file(svc):
    st = next(s for s in svc.ledger.statements.values() if s["payee_type"] == "artist" and s["earnings_total"])
    e = next(e for a in st["accounts"] for e in a["earnings"])
    c = svc.audit(e["line_id"])["contributions"][0]
    src = (svc.data_dir / "reports" / c["file"]).read_text().splitlines()
    header = src[0]
    delim = "\t" if "\t" in header else ";" if ";" in header else ","
    row = next(csv.reader([src[c["row"] - 1]], delimiter=delim))
    assert row == list(c["raw"].values())


def test_csv_export_rows_add_up(svc):
    st = next(s for s in svc.ledger.statements.values() if s["payee_type"] == "artist" and s["earnings_total"])
    rows = list(csv.DictReader(io.StringIO(statement_csv(st))))
    earnings = sum(int(r["amount_cents"]) for r in rows if r["line_type"] == "earnings")
    assert earnings == st["earnings_total"]
    assert all(r["audit_id"] for r in rows if r["line_type"] == "earnings")


def test_html_export_is_printable_and_escaped(svc):
    st = next(iter(svc.ledger.statements.values()))
    html = statement_html({**st, "payee_name": "<b>x</b>"}, "Lantern Hill Records")
    assert "&lt;b&gt;" in html and "@media print" in html and "Not legal or accounting advice" in html


def test_statements_are_deterministic(small_data, svc):
    again = RoyaltyService(small_data)
    for key, st in svc.ledger.statements.items():
        assert again.ledger.statements[key]["digest"] == st["digest"]


def test_recoupment_series_and_projection(svc):
    rows = svc.recoupment()
    assert rows
    for r in rows:
        assert r["series"][0]["period"] == "start" and len(r["series"]) == 1 + len(svc.rules.periods)
        for pt in r["series"]:
            assert 0 <= pt["recouped_to_date"] <= pt["charged_to_date"]
        if r["projection"]:
            assert r["balance"] < 0 and r["run_rate_per_period"] > 0


def test_analytics(svc):
    an = svc.analytics(5)
    assert len(an["top_tracks"]) == 5
    assert an["top_tracks"][0]["gross_micros"] >= an["top_tracks"][-1]["gross_micros"]
    rates = {(r["store"], r["territory"]): r["usd_per_1000"] for r in an["per_stream"]}
    assert rates[("wavelet", "US")] > rates[("wavelet", "BR")]
    assert rates[("tidepool", "US")] > rates[("wavelet", "US")]
