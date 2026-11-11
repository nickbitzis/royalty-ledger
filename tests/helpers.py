"""A tiny hand-built catalog where every number can be checked by hand."""

from __future__ import annotations

import copy

from royaltyledger.catalog import Catalog
from royaltyledger.engine import Engine
from royaltyledger.rules import Rules

MINI = {
    "seed": 0,
    "label": "Lantern Hill Records",
    "publisher": "Lantern Hill Music",
    "artists": [
        {"id": "A001", "name": "Oda Velmar", "contract_id": "C001", "popularity": 1.0, "writer_id": "W0001"},
        {"id": "A002", "name": "The Copper Hours", "contract_id": "C002", "popularity": 0.5, "writer_id": None},
    ],
    "releases": [
        {
            "id": "R0001",
            "artist_id": "A001",
            "title": "Glass River",
            "type": "album",
            "catalog_no": "LHR-0001",
            "year": 2024,
            "account_id": "C001:all",
            "physical": True,
        },
        {
            "id": "R0002",
            "artist_id": "A002",
            "title": "Night Static",
            "type": "album",
            "catalog_no": "LHR-0002",
            "year": 2024,
            "account_id": "C002:LP1",
            "physical": False,
        },
        {
            "id": "R0003",
            "artist_id": "A002",
            "title": "Salt Garden",
            "type": "album",
            "catalog_no": "LHR-0003",
            "year": 2026,
            "account_id": "C002:LP2",
            "physical": False,
        },
    ],
    "tracks": [
        {
            "id": "T00001",
            "isrc": "ZZLHR2400001",
            "title": "Copper Lights",
            "artist_id": "A001",
            "release_id": "R0001",
            "composition_id": "WK00001",
            "producers": [{"producer_id": "PR001", "points_bps": 300, "mode": "record_one"}],
            "popularity": 1.0,
        },
        {
            "id": "T00002",
            "isrc": "ZZLHR2400002",
            "title": "Winter Road",
            "artist_id": "A001",
            "release_id": "R0001",
            "composition_id": "WK00002",
            "producers": [{"producer_id": "PR002", "points_bps": 400, "mode": "first_sale"}],
            "popularity": 1.0,
        },
        {
            "id": "T00003",
            "isrc": "ZZLHR2400003",
            "title": "Marble Heart",
            "artist_id": "A002",
            "release_id": "R0002",
            "composition_id": "WK00003",
            "producers": [],
            "popularity": 1.0,
        },
        {
            "id": "T00004",
            "isrc": "ZZLHR2600004",
            "title": "Pilot Season",
            "artist_id": "A002",
            "release_id": "R0003",
            "composition_id": "WK00004",
            "producers": [],
            "popularity": 1.0,
        },
    ],
    "compositions": [
        {
            "id": "WK00001",
            "iswc": "T-900.000.001-1",
            "title": "Copper Lights",
            "shares": [
                {"writer_id": "W0001", "share_bps": 5000, "publisher_id": "PUB-LH"},
                {"writer_id": "W0002", "share_bps": 5000, "publisher_id": "PUB-02"},
            ],
        },
        {
            "id": "WK00002",
            "iswc": "T-900.000.002-2",
            "title": "Winter Road",
            "shares": [{"writer_id": "W0001", "share_bps": 10000, "publisher_id": "PUB-LH"}],
        },
        {
            "id": "WK00003",
            "iswc": "T-900.000.003-3",
            "title": "Marble Heart",
            "shares": [
                {"writer_id": "W0003", "share_bps": 6000, "publisher_id": "PUB-LH"},
                {"writer_id": "W0004", "share_bps": 4000, "publisher_id": "PUB-LH"},
            ],
        },
        {
            "id": "WK00004",
            "iswc": "T-900.000.004-4",
            "title": "Pilot Season",
            "shares": [{"writer_id": "W0002", "share_bps": 10000, "publisher_id": "PUB-02"}],
        },
    ],
    "writers": [
        {
            "id": "W0001",
            "name": "Oda Velmar",
            "publisher_id": "PUB-LH",
            "deal": {"type": "copub", "advance_cents": 0, "signed_month": "2024-01"},
        },
        {"id": "W0002", "name": "Ren Kalo", "publisher_id": "PUB-02", "deal": None},
        {
            "id": "W0003",
            "name": "Sa Mirel",
            "publisher_id": "PUB-LH",
            "deal": {"type": "traditional", "advance_cents": 100000, "signed_month": "2024-01"},
        },
        {
            "id": "W0004",
            "name": "Isa Torn",
            "publisher_id": "PUB-LH",
            "deal": {"type": "admin", "advance_cents": 0, "signed_month": "2024-01"},
        },
    ],
    "publishers": [
        {"id": "PUB-LH", "name": "Lantern Hill Music", "ours": True},
        {"id": "PUB-02", "name": "Coldframe Publishing", "ours": False},
    ],
    "producers": [{"id": "PR001", "name": "Kel Ostra"}, {"id": "PR002", "name": "Juno Fenn"}],
    "contracts": [
        {
            "id": "C001",
            "artist_id": "A001",
            "type": "royalty",
            "rates_bps": {"stream": 2000, "download": 2000, "physical": 1500, "neighbouring": 5000},
            "profit_share_bps": 5000,
            "cross_collat": True,
            "reserve_bps": 2500,
            "reserve_periods": 2,
            "accounts": [{"id": "C001:all", "label": "All albums (cross-collateralized)", "prior_earnings_cents": 0}],
            "charges": [
                {
                    "account_id": "C001:all",
                    "kind": "advance",
                    "amount_cents": 100000,
                    "month": "2024-01",
                    "desc": "Album 1 advance",
                },
                {
                    "account_id": "C001:all",
                    "kind": "recording",
                    "amount_cents": 50000,
                    "month": "2024-01",
                    "desc": "Album 1 recording costs",
                },
            ],
        },
        {
            "id": "C002",
            "artist_id": "A002",
            "type": "net_profit",
            "rates_bps": {"stream": 1800, "download": 1800, "physical": 1500, "neighbouring": 5000},
            "profit_share_bps": 5000,
            "cross_collat": False,
            "reserve_bps": 2500,
            "reserve_periods": 2,
            "accounts": [
                {"id": "C002:LP1", "label": "Album 1", "prior_earnings_cents": 0},
                {"id": "C002:LP2", "label": "Album 2", "prior_earnings_cents": 0},
            ],
            "charges": [
                {
                    "account_id": "C002:LP1",
                    "kind": "advance",
                    "amount_cents": 20000,
                    "month": "2024-01",
                    "desc": "Album 1 advance",
                },
                {
                    "account_id": "C002:LP1",
                    "kind": "recording",
                    "amount_cents": 10000,
                    "month": "2024-01",
                    "desc": "Album 1 recording costs",
                },
                {
                    "account_id": "C002:LP2",
                    "kind": "advance",
                    "amount_cents": 50000,
                    "month": "2026-02",
                    "desc": "Album 2 advance",
                },
            ],
        },
    ],
}

_seq = [0]


def mini_catalog(mutate=None) -> Catalog:
    data = copy.deepcopy(MINI)
    if mutate:
        mutate(data)
    return Catalog(data)


def line(
    kind: str,
    target: str,
    micros: int,
    month: str = "2025-08",
    territory: str = "US",
    store: str = "wavelet",
    units: int = 1000,
    status: str = "matched",
) -> dict:
    _seq[0] += 1
    return {
        "line_id": f"test.csv:{_seq[0]}",
        "file": "test.csv",
        "row": _seq[0],
        "kind": kind,
        "store": store,
        "month": month,
        "territory": territory,
        "units": units,
        "usd_micros": micros,
        "raw": {},
        "match": {"status": status, "target": target if status == "matched" else None, "method": "isrc"},
    }


def run(lines: list[dict], cat: Catalog | None = None):
    cat = cat or mini_catalog()
    return Engine(cat, Rules.load()).run(lines)


def alloc(line_: dict, cat: Catalog | None = None) -> dict:
    cat = cat or mini_catalog()
    out: dict = {}
    for k, v in Engine(cat, Rules.load()).allocate(line_):
        out[k] = out.get(k, 0) + v
    return out
