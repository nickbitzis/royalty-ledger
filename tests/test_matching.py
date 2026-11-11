import pytest
from helpers import mini_catalog

from royaltyledger.matching import Matcher, norm_artist, norm_isrc, norm_text


def mk(kind="stream", isrc="", title="", artist="", catalog_no="", iswc="", store="wavelet"):
    return {
        "line_id": "x:2",
        "kind": kind,
        "isrc": isrc,
        "title": title,
        "artist": artist,
        "catalog_no": catalog_no,
        "iswc": iswc,
        "store": store,
        "usd_micros": 1000,
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Copper Lights (Remastered)", "copper lights"),
        ("Copper Lights - Radio Edit", "copper lights"),
        ("COPPER  LIGHTS ", "copper lights"),
        ("Copper Lights feat. Someone", "copper lights"),
        ("Cöpper Lights [2025 Mix]", "copper lights"),
    ],
)
def test_norm_text(raw, expected):
    assert norm_text(raw) == expected


def test_norm_artist_and_isrc():
    assert norm_artist("Copper Hours, The") == "the copper hours"
    assert norm_isrc("zz-lhr-24-00001") == "ZZLHR2400001"


def test_exact_isrc_match_with_store_formatting():
    m = Matcher(mini_catalog())
    res = m.match(mk(isrc="zz-lhr-24-00001", title="Copper Lights", artist="Oda Velmar"))
    assert res["status"] == "matched" and res["target"] == "T00001" and res["method"] == "isrc"


def test_fuzzy_match_without_isrc():
    m = Matcher(mini_catalog())
    res = m.match(mk(title="Coper Lights", artist="Oda Velmar"))
    assert res["status"] == "matched" and res["target"] == "T00001" and res["method"] == "fuzzy"


def test_fuzzy_match_with_artist_variant():
    m = Matcher(mini_catalog())
    res = m.match(mk(title="Marble Heart (Live)", artist="Copper Hours, The"))
    assert res["target"] == "T00003"


def test_borderline_goes_to_review():
    m = Matcher(mini_catalog())
    res = m.match(mk(title="Marbel Hearts Forever", artist="The Copper Hours"))
    assert res["status"] == "review"
    assert res["candidates"][0]["target"] == "T00003"
    assert len(m.queue) == 1


def test_unknown_artist_stays_unmatched():
    m = Matcher(mini_catalog())
    res = m.match(mk(isrc="ZZOTH2500001", title="Brass Anthem 1", artist="Unsigned Act 1"))
    assert res["status"] == "unmatched"


def test_isrc_with_conflicting_title_goes_to_review():
    m = Matcher(mini_catalog())
    res = m.match(mk(isrc="ZZLHR2400002", title="Copper Lights", artist="Oda Velmar"))
    assert res["status"] == "review" and res["method"] == "isrc_conflict"
    assert res["candidates"][0]["target"] == "T00001"  # the title points at the right track


def test_physical_matches_by_catalog_number_then_title():
    m = Matcher(mini_catalog())
    assert m.match(mk(kind="physical", catalog_no="LHR-0001"))["target"] == "R0001"
    assert m.match(mk(kind="physical", title="Glass River", artist="Oda Velmar"))["target"] == "R0001"


def test_society_matches_by_iswc_then_title():
    m = Matcher(mini_catalog())
    assert m.match(mk(kind="performance", iswc="T-900.000.003-3"))["target"] == "WK00003"
    res = m.match(mk(kind="mechanical", title="Winter Road", artist="Oda Velmar"))
    assert res["target"] == "WK00002"


def test_review_decisions_apply_to_every_line_with_same_metadata():
    cat = mini_catalog()
    line = mk(title="Marbel Hearts Forever", artist="The Copper Hours")
    key_id = Matcher.key_id(Matcher(cat).key(line))
    m = Matcher(cat, {key_id: {"action": "accept", "target": "T00003"}})
    for store in ("wavelet", "tidepool"):
        res = m.match({**line, "store": store})
        assert res["status"] == "matched" and res["method"] == "review" and res["target"] == "T00003"
    m2 = Matcher(cat, {key_id: {"action": "reject", "target": None}})
    assert m2.match(line)["status"] == "unmatched"


def test_match_rate_and_correctness_on_generated_reports(svc):
    ev = svc.match_eval()
    assert ev["lines"] == len(svc.lines)
    assert ev["match_rate"] > 0.97
    assert ev["precision"] > 0.999
    assert ev["unmatched_truly_not_ours"] >= ev["unmatched_lines"] * 0.9
    assert ev["by_method"]["isrc"] > ev["by_method"].get("fuzzy", 0)
