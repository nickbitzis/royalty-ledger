import json

import pytest

from royaltyledger.reports import ReportError, detect_format, parse

WAVELET = """Reporting Month,ISRC,Track Title,Artist Name,Country,Streams,Net Revenue,Currency
2025-07,ZZLHR2400001,Copper Lights,Oda Velmar,US,12000,48.12345678,USD
"""
TIDEPOOL = """period;territory;isrc;product_title;artist;sale_type;quantity;amount_eur
202508;DE;zz-lhr-24-00001;Copper Lights;Oda Velmar;STREAM;5000;16,5000
202508;FR;zz-lhr-24-00002;Winter Road;Oda Velmar;DOWNLOAD;3;1,9100
"""
KESTREL = (
    "SaleDate\tISRC\tTitle\tArtist\tCountryCode\tUnits\tRoyaltyLocal\tCurrencyCode\n"
    "30/09/2025\tZZLHR2400002\tWinter Road\tOda Velmar\tJP\t4\t410\tJPY\n"
)
PRESSING = """month,catalog_no,release_title,artist,format,transaction,units,dealer_price_usd,amount_usd
2025-10,LHR-0001,Glass River,Oda Velmar,CD,SALE,40,9.00,360.00
2025-11,LHR-0001,Glass River,Oda Velmar,CD,RETURN,4,9.00,36.00
"""
SOCIETY = """distribution_period,income_type,iswc,isrc,work_title,performer,territory,amount,currency
2025Q4,PERF,T-900.000.001-1,,Copper Lights,Oda Velmar,GB,12.40,GBP
2025Q4,MECH,T-900.000.001-1,,Copper Lights,Oda Velmar,GB,9.10,GBP
2025Q4,NEIGH,,ZZLHR2400001,Copper Lights,Oda Velmar,DE,3.05,GBP
"""


@pytest.mark.parametrize(
    "text,fmt",
    [
        (WAVELET, "wavelet_csv"),
        (TIDEPOOL, "tidepool_semicolon"),
        (KESTREL, "kestrel_tsv"),
        (PRESSING, "pressing_physical"),
        (SOCIETY, "society_csv"),
    ],
)
def test_detect_format(text, fmt):
    assert detect_format(text) == fmt


def test_parse_wavelet():
    [x] = parse(WAVELET, "w.csv")
    assert (x["store"], x["kind"], x["month"], x["territory"], x["units"]) == (
        "wavelet",
        "stream",
        "2025-07",
        "US",
        12000,
    )
    assert x["amount"] == "48.12345678" and x["currency"] == "USD"
    assert x["line_id"] == "w.csv:2" and x["raw"]["ISRC"] == "ZZLHR2400001"


def test_parse_tidepool_decimal_comma_and_sale_types():
    a, b = parse(TIDEPOOL, "t.csv")
    assert a["amount"] == "16.5000" and a["currency"] == "EUR" and a["month"] == "2025-08"
    assert a["kind"] == "stream" and b["kind"] == "download"
    assert a["isrc"] == "zz-lhr-24-00001"


def test_parse_kestrel_date_and_local_currency():
    [x] = parse(KESTREL, "k.tsv")
    assert x["month"] == "2025-09" and x["currency"] == "JPY" and x["kind"] == "download" and x["units"] == 4


def test_parse_pressing_returns_are_negative():
    sale, ret = parse(PRESSING, "p.csv")
    assert sale["units"] == 40 and sale["amount"] == "360.00"
    assert ret["units"] == -4 and ret["amount"] == "-36.00"
    assert ret["catalog_no"] == "LHR-0001" and ret["kind"] == "physical"


def test_parse_society_income_types():
    perf, mech, neigh = parse(SOCIETY, "s.csv")
    assert [perf["kind"], mech["kind"], neigh["kind"]] == ["performance", "mechanical", "neighbouring"]
    assert perf["month"] == "2025-12"  # FX at the last month of the distribution quarter
    assert neigh["isrc"] == "ZZLHR2400001" and perf["iswc"] == "T-900.000.001-1"


def test_unknown_header_rejected():
    with pytest.raises(ReportError):
        parse("a,b,c\n1,2,3\n", "x.csv")


def test_wrong_field_count_rejected_with_line_number():
    bad = WAVELET + "2025-07,ZZLHR2400001,Copper Lights,US,1,1.0,USD\n"
    with pytest.raises(ReportError, match="x.csv:3"):
        parse(bad, "x.csv")


def test_blank_lines_and_bom_tolerated():
    lines = parse("﻿" + WAVELET + "\n\n", "w.csv")
    assert len(lines) == 1


def test_generated_reports_cover_every_format(small_data, svc):
    formats = {f["format"] for f in svc.files}
    assert formats == {"wavelet_csv", "tidepool_semicolon", "kestrel_tsv", "pressing_physical", "society_csv"}
    truth = json.loads((small_data / "truth.json").read_text())
    assert len(truth) == len(svc.lines)
    assert all(line["line_id"] in truth for line in svc.lines)
