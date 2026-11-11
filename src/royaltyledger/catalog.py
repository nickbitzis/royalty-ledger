"""Synthetic catalog for Lantern Hill Records and Lantern Hill Music (both made up).

Seeded and deterministic. Identifiers are fictional on purpose: ISRCs use the user-assigned country code ZZ and
registrant LHR (for example ZZLHR2500001), ISWC-like work ids use the T-900 range, catalog numbers are LHR-0001.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SYL = [
    "ka",
    "lo",
    "mi",
    "ren",
    "sa",
    "vel",
    "dor",
    "an",
    "is",
    "ta",
    "nor",
    "bel",
    "cor",
    "li",
    "ma",
    "zen",
    "ul",
    "ra",
    "fen",
    "ost",
    "ery",
    "jun",
    "pa",
    "quin",
    "tor",
    "wyn",
    "ash",
    "ev",
    "oda",
    "rik",
]
WORDS = [
    "Glass",
    "River",
    "Night",
    "Paper",
    "Static",
    "Copper",
    "Lantern",
    "Harbor",
    "Velvet",
    "Signal",
    "Winter",
    "Ember",
    "Satellite",
    "Orchard",
    "Neon",
    "Quiet",
    "Hollow",
    "Silver",
    "Tide",
    "Meadow",
    "Arcade",
    "Comet",
    "Driftwood",
    "Echo",
    "Fever",
    "Golden",
    "Highway",
    "Island",
    "Juniper",
    "Kettle",
    "Low",
    "Marble",
    "North",
    "Ocean",
    "Pilot",
    "Rain",
    "Salt",
    "Thunder",
    "Undertow",
    "Violet",
    "Wire",
    "Yellow",
    "Avenue",
    "Blue",
    "Cinder",
    "Daylight",
    "Fable",
    "Garden",
    "Heart",
    "Ivory",
    "Mirror",
    "Motel",
    "Radio",
    "Shadow",
    "Sugar",
]
TAILS = [
    "Song",
    "Lights",
    "Dreams",
    "Lines",
    "Hearts",
    "Days",
    "Waves",
    "Room",
    "Season",
    "Fire",
    "Road",
    "Blues",
    "Sky",
    "Machine",
    "Letters",
    "Garden",
    "Hours",
    "Summer",
    "Static",
    "Parade",
    "Ghosts",
    "Runner",
]
OUTSIDE_PUBLISHERS = [
    "Brightwater Songs",
    "Coldframe Publishing",
    "Saltmarsh Music Group",
    "Quarry Lane Songs",
    "Pinecrest Editions",
]


def _name(r: random.Random, parts: int = 2) -> str:
    def word() -> str:
        return "".join(r.choice(SYL) for _ in range(r.choice([2, 2, 3]))).capitalize()

    return " ".join(word() for _ in range(parts))


def _title(r: random.Random) -> str:
    form = r.random()
    if form < 0.4:
        return f"{r.choice(WORDS)} {r.choice(TAILS)}"
    if form < 0.65:
        return r.choice(WORDS)
    if form < 0.85:
        return f"{r.choice(WORDS)} {r.choice(WORDS)}"
    return f"The {r.choice(WORDS)} {r.choice(TAILS)}"


def _splits(r: random.Random, n: int) -> list[int]:
    """Writer shares in basis points that sum to exactly 10000."""
    if n == 1:
        return [10000]
    raw = [r.randint(2, 6) for _ in range(n)]
    total = sum(raw)
    parts = [10000 * x // total for x in raw]
    parts[0] += 10000 - sum(parts)
    return parts


def generate(seed: int = 7, n_artists: int = 60) -> dict:
    r = random.Random(seed)
    artists, releases, tracks, comps, contracts = [], [], [], [], []
    publishers = [{"id": "PUB-LH", "name": "Lantern Hill Music", "ours": True}]
    publishers += [{"id": f"PUB-{i + 1:02d}", "name": n, "ours": False} for i, n in enumerate(OUTSIDE_PUBLISHERS)]
    producers = [{"id": f"PR{i + 1:03d}", "name": _name(r)} for i in range(max(4, n_artists * 2 // 3))]
    writers: list[dict] = []
    deal_types = ["traditional", "copub", "copub", "admin"]

    def new_writer(name: str, ours: bool) -> dict:
        w = {
            "id": f"W{len(writers) + 1:04d}",
            "name": name,
            "publisher_id": "PUB-LH" if ours else r.choice(publishers[1:])["id"],
            "deal": None,
        }
        if ours:
            w["deal"] = {
                "type": r.choice(deal_types),
                "advance_cents": r.choice([0, 0, 150000, 300000, 750000]),
                "signed_month": r.choice(["2024-03", "2024-11", "2025-05", "2026-02"]),
            }
        writers.append(w)
        return w

    co_writers = [new_writer(_name(r), r.random() < 0.55) for _ in range(max(6, n_artists * 2))]
    isrc_seq, work_seq, cat_seq = 0, 0, 0
    for a in range(n_artists):
        aid = f"A{a + 1:03d}"
        solo = r.random() < 0.6
        name = _name(r) if solo else f"The {r.choice(WORDS)} {r.choice(TAILS)}" if r.random() < 0.5 else _name(r, 1)
        while any(x["name"] == name for x in artists):
            name = _name(r)
        pop = 1.0 / (a + 1) ** 0.85 * r.uniform(0.6, 1.4)
        artist_writer = new_writer(name, r.random() < 0.8) if r.random() < 0.85 else None
        n_albums = r.randint(2, 4)
        deal = "royalty" if r.random() < 0.7 else "net_profit"
        cross = r.random() < 0.6
        cid = f"C{a + 1:03d}"
        groups = [f"{cid}:all"] if cross else [f"{cid}:LP{i + 1}" for i in range(n_albums)]
        cycle_month = ["2023-04", "2024-06", "2025-03", "2026-02"]
        accounts = []
        charges = []
        for i in range(n_albums):
            acct = groups[0] if cross else groups[i]
            label = "All albums (cross-collateralized)" if cross else f"Album {i + 1}"
            if not any(x["id"] == acct for x in accounts):
                accounts.append({"id": acct, "label": label, "prior_earnings_cents": 0})
            month = cycle_month[i] if n_albums < 4 or i < 3 else cycle_month[3]
            scale = 0.3 + 4 * pop
            adv = round(r.uniform(4000, 22000) * scale / 500) * 500 * 100
            rec = round(r.uniform(2500, 14000) * scale) * 100
            charges.append(
                {
                    "account_id": acct,
                    "kind": "advance",
                    "amount_cents": adv,
                    "month": month,
                    "desc": f"Album {i + 1} advance",
                }
            )
            charges.append(
                {
                    "account_id": acct,
                    "kind": "recording",
                    "amount_cents": rec,
                    "month": month,
                    "desc": f"Album {i + 1} recording costs",
                }
            )
            if r.random() < 0.5:
                charges.append(
                    {
                        "account_id": acct,
                        "kind": "video",
                        "amount_cents": round(r.uniform(1500, 8000) * scale) * 100,
                        "month": month,
                        "desc": f"Album {i + 1} video",
                    }
                )
            if r.random() < 0.25:
                charges.append(
                    {
                        "account_id": acct,
                        "kind": "tour_support",
                        "amount_cents": round(r.uniform(1000, 6000) * scale) * 100,
                        "month": "2025-10" if month < "2025-10" else month,
                        "desc": "Tour support",
                    }
                )
        # earnings before the ledger starts (statements were migrated from an older system)
        for acc in accounts:
            before = sum(c["amount_cents"] for c in charges if c["account_id"] == acc["id"] and c["month"] < "2025-07")
            acc["prior_earnings_cents"] = int(before * r.uniform(0.1, 1.15)) if before else 0
        contracts.append(
            {
                "id": cid,
                "artist_id": aid,
                "type": deal,
                "rates_bps": {
                    "stream": r.choice([1600, 1800, 2000, 2200]),
                    "download": r.choice([1600, 1800, 2000]),
                    "physical": r.choice([1400, 1500, 1600]),
                    "neighbouring": 5000,
                },
                "profit_share_bps": 5000,
                "cross_collat": cross,
                "reserve_bps": r.choice([2000, 2500, 3000]),
                "reserve_periods": 2,
                "accounts": accounts,
                "charges": charges,
            }
        )
        artists.append(
            {
                "id": aid,
                "name": name,
                "contract_id": cid,
                "popularity": round(pop, 6),
                "writer_id": artist_writer["id"] if artist_writer else None,
            }
        )
        # releases: albums, EPs and singles; EPs and singles belong to one album cycle's account
        plan = [("album", i) for i in range(n_albums)]
        plan += [("ep", r.randrange(n_albums)) for _ in range(r.randint(0, 2))]
        plan += [("single", r.randrange(n_albums)) for _ in range(r.randint(2, 4))]
        used_titles: set[str] = set()
        for kind, cycle in plan:
            cat_seq += 1
            rid = f"R{cat_seq:04d}"
            rtitle = _title(r)
            year = int(cycle_month[min(cycle, 3)][:4])
            n_tracks = {"album": r.randint(10, 14), "ep": r.randint(4, 6), "single": r.randint(1, 2)}[kind]
            releases.append(
                {
                    "id": rid,
                    "artist_id": aid,
                    "title": rtitle,
                    "type": kind,
                    "catalog_no": f"LHR-{cat_seq:04d}",
                    "year": year,
                    "account_id": groups[0 if cross else cycle],
                    "physical": kind == "album" and r.random() < 0.55,
                }
            )
            for _ in range(n_tracks):
                t = _title(r)
                while t in used_titles:
                    t = _title(r)
                used_titles.add(t)
                isrc_seq += 1
                work_seq += 1
                n_w = r.choice([1, 2, 2, 3])
                ws = [artist_writer] if artist_writer else []
                while len(ws) < n_w:
                    c = r.choice(co_writers)
                    if c not in ws:
                        ws.append(c)
                shares = [
                    {"writer_id": w["id"], "share_bps": s, "publisher_id": w["publisher_id"]}
                    for w, s in zip(ws, _splits(r, len(ws)), strict=True)
                ]
                wid = f"WK{work_seq:05d}"
                comps.append(
                    {
                        "id": wid,
                        "iswc": f"T-900.{work_seq // 1000:03d}.{work_seq % 1000:03d}-{work_seq % 10}",
                        "title": t,
                        "shares": shares,
                    }
                )
                prods = []
                if r.random() < 0.8:
                    p = producers[(a + r.randrange(3)) % len(producers)]
                    prods.append(
                        {
                            "producer_id": p["id"],
                            "points_bps": r.choice([200, 300, 300, 400]),
                            "mode": "record_one" if r.random() < 0.7 else "first_sale",
                        }
                    )
                tracks.append(
                    {
                        "id": f"T{isrc_seq:05d}",
                        "isrc": f"ZZLHR{year % 100:02d}{isrc_seq:05d}",
                        "title": t,
                        "artist_id": aid,
                        "release_id": rid,
                        "composition_id": wid,
                        "producers": prods,
                        "popularity": round(pop * r.paretovariate(1.6) * (1.6 if year >= 2025 else 1.0), 6),
                    }
                )
    return {
        "seed": seed,
        "label": "Lantern Hill Records",
        "publisher": "Lantern Hill Music",
        "artists": artists,
        "releases": releases,
        "tracks": tracks,
        "compositions": comps,
        "writers": writers,
        "publishers": publishers,
        "producers": producers,
        "contracts": contracts,
    }


def validate(cat: dict) -> list[str]:
    """Structural checks. Every composition's writer shares must sum to exactly 100.00%."""
    errors = []
    for c in cat["compositions"]:
        total = sum(s["share_bps"] for s in c["shares"])
        if total != 10000:
            errors.append(f"{c['id']} writer shares sum to {total} bps, not 10000")
    track_ids = {t["id"] for t in cat["tracks"]}
    if len(track_ids) != len(cat["tracks"]):
        errors.append("duplicate track ids")
    if len({t["isrc"] for t in cat["tracks"]}) != len(cat["tracks"]):
        errors.append("duplicate ISRCs")
    for t in cat["tracks"]:
        if sum(p["points_bps"] for p in t["producers"]) > 1000:
            errors.append(f"{t['id']} producer points exceed 10")
    return errors


class Catalog:
    """Indexes over the catalog dict."""

    def __init__(self, data: dict):
        self.data = data
        self.artists = {a["id"]: a for a in data["artists"]}
        self.releases = {x["id"]: x for x in data["releases"]}
        self.tracks = {t["id"]: t for t in data["tracks"]}
        self.compositions = {c["id"]: c for c in data["compositions"]}
        self.writers = {w["id"]: w for w in data["writers"]}
        self.publishers = {p["id"]: p for p in data["publishers"]}
        self.producers = {p["id"]: p for p in data["producers"]}
        self.contracts = {c["id"]: c for c in data["contracts"]}
        self.by_isrc = {t["isrc"]: t["id"] for t in data["tracks"]}
        self.by_iswc = {c["iswc"]: c["id"] for c in data["compositions"]}
        self.by_catno = {x["catalog_no"]: x["id"] for x in data["releases"]}
        self.release_tracks: dict[str, list[str]] = {}
        for t in data["tracks"]:
            self.release_tracks.setdefault(t["release_id"], []).append(t["id"])
        self.comp_tracks = {t["composition_id"]: t["id"] for t in data["tracks"]}

    def contract_for_artist(self, artist_id: str) -> dict:
        return self.contracts[self.artists[artist_id]["contract_id"]]

    def payee_name(self, payee_id: str) -> str:
        for table in (self.artists, self.producers, self.writers, self.publishers):
            if payee_id in table:
                return table[payee_id]["name"]
        return payee_id

    @staticmethod
    def load(path: Path) -> Catalog:
        return Catalog(json.loads(Path(path).read_text()))
