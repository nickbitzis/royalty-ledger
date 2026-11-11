"""Usage reports: a synthetic generator and one parser per store format.

Five fictional senders, five formats:
- Wavelet (streaming): comma CSV, USD, month as 2025-07.
- Tidepool Music (streams and downloads): semicolon CSV, decimal comma, EUR, period 202507, ISRCs lower case
  with hyphens.
- Kestrel Downloads: tab separated, local currency, sale date 31/07/2025.
- Pressing Plant Distribution (physical): sales and returns per catalog number, USD.
- Northbridge Rights Society (quarterly): performance and mechanical income per work (ISWC), and neighbouring
  rights income per recording (ISRC), GBP.

The generator uses floats only to invent amounts. It writes them as decimal strings and the ledger never sees a
float. It also writes truth.json: the real track behind every line, used to score matching.
"""

from __future__ import annotations

import csv
import io
import json
import random
from pathlib import Path

from .catalog import Catalog
from .rules import Rules

TERR_WEIGHT = {
    "US": 0.36,
    "GB": 0.11,
    "DE": 0.09,
    "FR": 0.06,
    "NL": 0.03,
    "SE": 0.04,
    "JP": 0.06,
    "BR": 0.08,
    "MX": 0.06,
    "CA": 0.06,
    "AU": 0.05,
}
PER_STREAM_USD = {
    "US": 0.0040,
    "GB": 0.0038,
    "DE": 0.0036,
    "FR": 0.0033,
    "NL": 0.0039,
    "SE": 0.0035,
    "JP": 0.0042,
    "BR": 0.0011,
    "MX": 0.0010,
    "CA": 0.0034,
    "AU": 0.0035,
}
STORE_RATE = {"wavelet": 1.0, "tidepool": 1.22}
DOWNLOAD_USD = 0.69
STREAMS_K = 40000

HEADERS = {
    "wavelet_csv": [
        "Reporting Month",
        "ISRC",
        "Track Title",
        "Artist Name",
        "Country",
        "Streams",
        "Net Revenue",
        "Currency",
    ],
    "tidepool_semicolon": [
        "period",
        "territory",
        "isrc",
        "product_title",
        "artist",
        "sale_type",
        "quantity",
        "amount_eur",
    ],
    "kestrel_tsv": ["SaleDate", "ISRC", "Title", "Artist", "CountryCode", "Units", "RoyaltyLocal", "CurrencyCode"],
    "pressing_physical": [
        "month",
        "catalog_no",
        "release_title",
        "artist",
        "format",
        "transaction",
        "units",
        "dealer_price_usd",
        "amount_usd",
    ],
    "society_csv": [
        "distribution_period",
        "income_type",
        "iswc",
        "isrc",
        "work_title",
        "performer",
        "territory",
        "amount",
        "currency",
    ],
}
DELIMS = {
    "wavelet_csv": ",",
    "tidepool_semicolon": ";",
    "kestrel_tsv": "\t",
    "pressing_physical": ",",
    "society_csv": ",",
}
MONTH_END = {
    "01": 31,
    "02": 28,
    "03": 31,
    "04": 30,
    "05": 31,
    "06": 30,
    "07": 31,
    "08": 31,
    "09": 30,
    "10": 31,
    "11": 30,
    "12": 31,
}


class ReportError(ValueError):
    pass


# ----------------------------------------------------------------------------------------------------------------
# parsing


def detect_format(text: str) -> str:
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    for fmt, header in HEADERS.items():
        if [h.strip() for h in first.split(DELIMS[fmt])] == header:
            return fmt
    raise ReportError(f"unknown report header: {first[:120]!r}")


def _month_of_quarter_end(q: str) -> str:
    return f"{q[:4]}-{int(q[5]) * 3:02d}"


def parse(text: str, file_name: str, fmt: str | None = None) -> list[dict]:
    """Parse one report into normalized usage lines. line_id is file:line_number (the header is line 1)."""
    fmt = fmt or detect_format(text)
    if fmt not in HEADERS:
        raise ReportError(f"unknown format {fmt}")
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=DELIMS[fmt])
    header = [h.strip() for h in next(reader)]
    if header != HEADERS[fmt]:
        raise ReportError(f"{file_name}: header does not match format {fmt}")
    out = []
    for n, row in enumerate(reader, start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(header):
            raise ReportError(f"{file_name}:{n}: expected {len(header)} fields, got {len(row)}")
        raw = dict(zip(header, row, strict=True))
        line = {
            "line_id": f"{file_name}:{n}",
            "file": file_name,
            "row": n,
            "raw": raw,
            "isrc": "",
            "iswc": "",
            "catalog_no": "",
            "title": "",
            "artist": "",
        }
        if fmt == "wavelet_csv":
            line.update(
                store="wavelet",
                kind="stream",
                month=raw["Reporting Month"],
                isrc=raw["ISRC"],
                title=raw["Track Title"],
                artist=raw["Artist Name"],
                territory=raw["Country"],
                units=int(raw["Streams"]),
                amount=raw["Net Revenue"],
                currency=raw["Currency"],
            )
        elif fmt == "tidepool_semicolon":
            p = raw["period"]
            line.update(
                store="tidepool",
                kind={"STREAM": "stream", "DOWNLOAD": "download"}[raw["sale_type"]],
                month=f"{p[:4]}-{p[4:]}",
                isrc=raw["isrc"],
                title=raw["product_title"],
                artist=raw["artist"],
                territory=raw["territory"],
                units=int(raw["quantity"]),
                amount=raw["amount_eur"].replace(".", "").replace(",", "."),
                currency="EUR",
            )
        elif fmt == "kestrel_tsv":
            d, m, y = raw["SaleDate"].split("/")
            line.update(
                store="kestrel",
                kind="download",
                month=f"{y}-{m}",
                isrc=raw["ISRC"],
                title=raw["Title"],
                artist=raw["Artist"],
                territory=raw["CountryCode"],
                units=int(raw["Units"]),
                amount=raw["RoyaltyLocal"],
                currency=raw["CurrencyCode"],
            )
        elif fmt == "pressing_physical":
            sign = -1 if raw["transaction"] == "RETURN" else 1
            units = int(raw["units"])
            amount = raw["amount_usd"]
            if sign < 0 and not amount.startswith("-"):
                amount = "-" + amount
            line.update(
                store="pressing",
                kind="physical",
                month=raw["month"],
                catalog_no=raw["catalog_no"],
                title=raw["release_title"],
                artist=raw["artist"],
                territory="US",
                units=sign * units,
                amount=amount,
                currency="USD",
            )
        else:
            kind = {"PERF": "performance", "MECH": "mechanical", "NEIGH": "neighbouring"}[raw["income_type"]]
            line.update(
                store="northbridge",
                kind=kind,
                month=_month_of_quarter_end(raw["distribution_period"]),
                iswc=raw["iswc"],
                isrc=raw["isrc"],
                title=raw["work_title"],
                artist=raw["performer"],
                territory=raw["territory"],
                units=0,
                amount=raw["amount"],
                currency=raw["currency"],
            )
        out.append(line)
    return out


# ----------------------------------------------------------------------------------------------------------------
# generation


def _misspell(r: random.Random, s: str) -> str:
    choice = r.random()
    if choice < 0.3 and len(s) > 4:
        i = r.randrange(1, len(s) - 1)
        return s[:i] + s[i + 1 :]
    if choice < 0.5 and len(s) > 4:
        i = r.randrange(1, len(s) - 2)
        return s[:i] + s[i + 1] + s[i] + s[i + 2 :]
    if choice < 0.7:
        return s + r.choice([" (Remastered)", " - Radio Edit", " (Live)", " [2025 Mix]"])
    if choice < 0.85:
        return s.upper()
    return s.replace(" ", "  ") + " "


def _artist_variant(r: random.Random, s: str) -> str:
    choice = r.random()
    if choice < 0.4:
        return s + " feat. " + r.choice(["Oda Ren", "Kalo Mi", "Sa Vel"])
    if choice < 0.7:
        return s.lower()
    if s.startswith("The "):
        return s[4:] + ", The"
    return s.replace("a", "e", 1) if "a" in s else s + "s"


def _tidepool_isrc(isrc: str) -> str:
    return f"{isrc[:2]}-{isrc[2:5]}-{isrc[5:7]}-{isrc[7:]}".lower() if len(isrc) == 12 else isrc


def generate_reports(cat: Catalog, rules: Rules, out_dir: Path, seed: int = 7, scale: float = 1.0) -> dict:
    """Write every report for every month into out_dir and return a summary."""
    r = random.Random(seed * 1009 + 17)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth: dict[str, str | None] = {}
    tracks = list(cat.data["tracks"])
    months = rules.months()
    # per (store, track) metadata defects: a store's copy of a track's metadata is consistently wrong
    defects: dict[tuple[str, str], dict] = {}
    for store, p_missing in (("wavelet", 0.04), ("tidepool", 0.05), ("kestrel", 0.10)):
        for t in tracks:
            artist = cat.artists[t["artist_id"]]["name"]
            d = {"isrc": t["isrc"], "title": t["title"], "artist": artist}
            x = r.random()
            if x < p_missing:
                d["isrc"] = ""
            elif x < p_missing + 0.008:
                other = r.choice(tracks)
                d["isrc"] = other["isrc"]  # wrong ISRC that exists: a keying error at the store
            if r.random() < 0.07:
                d["title"] = _misspell(r, d["title"])
            if r.random() < 0.05:
                d["artist"] = _artist_variant(r, d["artist"])
            if store == "tidepool" and d["isrc"]:
                d["isrc"] = _tidepool_isrc(d["isrc"])
            defects[(store, t["id"])] = d
    phantoms = [
        {
            "id": None,
            "isrc": f"ZZOTH25{i:05d}",
            "title": f"{r.choice(['Moth', 'Kite', 'Brass'])} {r.choice(['Anthem', 'Waltz', 'Signal', 'Choir'])} {i}",
            "artist": f"Unsigned Act {i}",
            "popularity": 0.05,
        }
        for i in range(12)
    ]

    def streams_for(t: dict, m_idx: int) -> float:
        return t["popularity"] * STREAMS_K * scale * (1 + 0.04 * m_idx) * r.uniform(0.8, 1.2)

    files = []

    def write(name: str, fmt: str, rows: list[list[str]], line_truth: list[str | None]) -> None:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=DELIMS[fmt], lineterminator="\n")
        w.writerow(HEADERS[fmt])
        w.writerows(rows)
        (out_dir / name).write_text(buf.getvalue())
        for i, tid in enumerate(line_truth, start=2):
            truth[f"{name}:{i}"] = tid
        files.append({"file": name, "format": fmt, "rows": len(rows)})

    society_acc: dict[str, dict] = {}
    for mi, month in enumerate(months):
        fx = rules.fx[month]
        wav, wav_t, tide, tide_t, kes, kes_t = [], [], [], [], [], []
        for t in tracks + phantoms:
            total = streams_for(t, mi)
            for terr, w in TERR_WEIGHT.items():
                for store, share in (("wavelet", 0.62), ("tidepool", 0.38)):
                    units = int(total * w * share * r.uniform(0.7, 1.3))
                    if units < 1500:
                        continue
                    usd = units * PER_STREAM_USD[terr] * STORE_RATE[store] * r.uniform(0.9, 1.1)
                    d = defects.get((store, t["id"])) or {"isrc": t["isrc"], "title": t["title"], "artist": t["artist"]}
                    if store == "wavelet":
                        wav.append([month, d["isrc"], d["title"], d["artist"], terr, str(units), f"{usd:.8f}", "USD"])
                        wav_t.append(t["id"])
                    else:
                        eur = f"{usd / float(fx['EUR']):.4f}".replace(".", ",")
                        tide.append(
                            [
                                month.replace("-", ""),
                                terr,
                                d["isrc"],
                                d["title"],
                                d["artist"],
                                "STREAM",
                                str(units),
                                eur,
                            ]
                        )
                        tide_t.append(t["id"])
                dl_units = int(total * w * 0.0011 * r.uniform(0.5, 1.5))
                if dl_units >= 1:
                    store = "kestrel" if r.random() < 0.7 else "tidepool"
                    d = defects.get((store, t["id"])) or {"isrc": t["isrc"], "title": t["title"], "artist": t["artist"]}
                    usd = dl_units * DOWNLOAD_USD * r.uniform(0.95, 1.05)
                    if store == "kestrel":
                        cur = rules.territories[terr]["currency"]
                        local = usd / float(fx[cur])
                        amount = f"{local:.0f}" if cur == "JPY" else f"{local:.2f}"
                        kes.append(
                            [
                                f"{MONTH_END[month[5:]]}/{month[5:]}/{month[:4]}",
                                d["isrc"],
                                d["title"],
                                d["artist"],
                                terr,
                                str(dl_units),
                                amount,
                                cur,
                            ]
                        )
                        kes_t.append(t["id"])
                    else:
                        eur = f"{usd / float(fx['EUR']):.4f}".replace(".", ",")
                        tide.append(
                            [
                                month.replace("-", ""),
                                terr,
                                d["isrc"],
                                d["title"],
                                d["artist"],
                                "DOWNLOAD",
                                str(dl_units),
                                eur,
                            ]
                        )
                        tide_t.append(t["id"])
                if t["id"] and terr in ("GB", "DE", "FR", "NL", "SE", "US", "JP", "CA"):
                    acc = society_acc.setdefault(t["id"], {})
                    acc[terr] = acc.get(terr, 0.0) + total * w
        write(f"wavelet_{month}.csv", "wavelet_csv", wav, wav_t)
        write(f"tidepool_{month.replace('-', '')}.csv", "tidepool_semicolon", tide, tide_t)
        write(f"kestrel_{month}.tsv", "kestrel_tsv", kes, kes_t)

        # physical
        phys, phys_t = [], []
        for rel in cat.data["releases"]:
            if not rel["physical"]:
                continue
            pop = sum(cat.tracks[x]["popularity"] for x in cat.release_tracks[rel["id"]])
            price = 8.5 + (int(rel["id"][1:]) % 6) * 0.5
            shipped = int(pop * 9 * scale * r.uniform(0.3, 1.7))
            artist = cat.artists[rel["artist_id"]]["name"]
            catno = "" if r.random() < 0.03 else rel["catalog_no"]
            if shipped > 0:
                phys.append(
                    [
                        month,
                        catno,
                        rel["title"],
                        artist,
                        "CD" if r.random() < 0.6 else "LP",
                        "SALE",
                        str(shipped),
                        f"{price:.2f}",
                        f"{shipped * price:.2f}",
                    ]
                )
                phys_t.append(rel["id"])
            if mi >= 1 and shipped > 8 and r.random() < 0.5:
                ret = max(1, int(shipped * r.uniform(0.05, 0.25)))
                phys.append(
                    [month, catno, rel["title"], artist, "CD", "RETURN", str(ret), f"{price:.2f}", f"{ret * price:.2f}"]
                )
                phys_t.append(rel["id"])
        write(f"pressing_{month}.csv", "pressing_physical", phys, phys_t)

        # society, quarterly
        if month[5:] in ("09", "12", "03", "06"):
            quarter = f"{month[:4]}Q{(int(month[5:]) - 1) // 3 + 1}"
            soc, soc_t = [], []
            gbp = float(fx["GBP"])
            for tid in sorted(society_acc):
                t = cat.tracks[tid]
                comp = cat.compositions[t["composition_id"]]
                controlled = sum(s["share_bps"] for s in comp["shares"] if s["publisher_id"] == "PUB-LH") / 10000
                artist = cat.artists[t["artist_id"]]["name"]
                iswc = "" if r.random() < 0.03 else comp["iswc"]
                title = _misspell(r, comp["title"]) if not iswc and r.random() < 0.5 else comp["title"]
                for terr, plays in sorted(society_acc[tid].items()):
                    if controlled > 0 and plays > 4000:
                        perf = plays * 0.00055 * controlled * r.uniform(0.8, 1.2) / gbp
                        mech = plays * 0.00045 * controlled * r.uniform(0.8, 1.2) / gbp
                        soc.append([quarter, "PERF", iswc, "", title, artist, terr, f"{perf:.2f}", "GBP"])
                        soc.append([quarter, "MECH", iswc, "", title, artist, terr, f"{mech:.2f}", "GBP"])
                        soc_t += [comp["id"], comp["id"]]
                    if terr != "US" and plays > 20000 and r.random() < 0.6:
                        neigh = plays * 0.0002 * r.uniform(0.7, 1.3) / gbp
                        soc.append([quarter, "NEIGH", "", t["isrc"], t["title"], artist, terr, f"{neigh:.2f}", "GBP"])
                        soc_t.append(tid)
            write(f"northbridge_{quarter}.csv", "society_csv", soc, soc_t)
            society_acc = {}
    (out_dir.parent / "truth.json").write_text(json.dumps(truth, separators=(",", ":")))
    return {"files": files, "lines": len(truth)}
