"""Orchestration: generate or load data, parse reports, convert FX, match, compute statements."""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path

from .catalog import Catalog, generate, validate
from .engine import SOURCE, Engine, Ledger
from .matching import Matcher, evaluate
from .reports import detect_format, generate_reports, parse
from .rules import Rules, add_quarters


def build_data(data_dir: Path, seed: int = 7, n_artists: int = 60, scale: float = 1.0) -> dict:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cat_dict = generate(seed, n_artists)
    errors = validate(cat_dict)
    if errors:
        raise ValueError(errors[:5])
    (data_dir / "catalog.json").write_text(json.dumps(cat_dict, indent=1))
    rules = Rules.load()
    summary = generate_reports(Catalog(cat_dict), rules, data_dir / "reports", seed=seed, scale=scale)
    return summary


class RoyaltyService:
    def __init__(self, data_dir: Path, rules: Rules | None = None):
        self.data_dir = Path(data_dir)
        self.rules = rules or Rules.load()
        self.cat = Catalog.load(self.data_dir / "catalog.json")
        truth_path = self.data_dir / "truth.json"
        self.truth = json.loads(truth_path.read_text()) if truth_path.exists() else {}
        self.decisions_path = self.data_dir / "decisions.json"
        self.decisions = json.loads(self.decisions_path.read_text()) if self.decisions_path.exists() else {}
        self.lines: list[dict] = []
        self.files: list[dict] = []
        self.matcher = Matcher(self.cat, self.decisions)
        self.engine = Engine(self.cat, self.rules)
        self.ledger: Ledger | None = None
        self.timings: dict[str, float] = {}
        t0 = time.perf_counter()
        for path in sorted((self.data_dir / "reports").iterdir()):
            if path.suffix in (".csv", ".tsv"):
                self._add_file(path.name, path.read_text())
        self.timings["parse_s"] = round(time.perf_counter() - t0, 3)
        self.recompute()

    def _add_file(self, name: str, text: str, fmt: str | None = None) -> list[dict]:
        fmt = fmt or detect_format(text)
        lines = parse(text, name, fmt)
        for line in lines:
            line["usd_micros"] = self.rules.to_usd_micros(line["amount"], line["currency"], line["month"])
        self.lines.extend(lines)
        self.files.append({"file": name, "format": fmt, "lines": len(lines)})
        return lines

    def ingest(self, name: str, text: str, fmt: str | None = None) -> dict:
        if any(f["file"] == name for f in self.files):
            raise ValueError(f"{name} was already ingested")
        lines = self._add_file(name, text, fmt)
        (self.data_dir / "reports" / name).write_text(text)
        self.recompute()
        status = defaultdict(int)
        for line in lines:
            status[line["match"]["status"]] += 1
        return {"file": name, "format": self.files[-1]["format"], "lines": len(lines), "status": dict(status)}

    def recompute(self) -> None:
        t0 = time.perf_counter()
        self.matcher = Matcher(self.cat, self.decisions)
        self.matcher.match_all(self.lines)
        t1 = time.perf_counter()
        self.ledger = self.engine.run(self.lines)
        t2 = time.perf_counter()
        self.timings.update(match_s=round(t1 - t0, 3), compute_s=round(t2 - t1, 3))
        self.line_index = {line["line_id"]: line for line in self.lines}

    def decide(self, key_id: str, action: str, target: str | None = None) -> dict:
        if action not in ("accept", "reject"):
            raise ValueError("action must be accept or reject")
        item = self.matcher.queue.get(key_id)
        if item is None:
            raise KeyError(key_id)
        if action == "accept":
            valid = {"track": self.cat.tracks, "release": self.cat.releases, "composition": self.cat.compositions}
            if target not in valid[item["kind"]]:
                raise ValueError(f"{target} is not a known {item['kind']}")
        self.decisions[key_id] = {"action": action, "target": target}
        self.decisions_path.write_text(json.dumps(self.decisions, indent=1, sort_keys=True))
        self.recompute()
        return {"key_id": key_id, "action": action, "target": target, "lines": item["lines"]}

    # views ------------------------------------------------------------------------------------------------------
    def match_eval(self) -> dict:
        return evaluate(self.lines, self.truth)

    def review_queue(self) -> list[dict]:
        out = []
        for q in sorted(self.matcher.queue.values(), key=lambda x: -x["micros"]):
            cands = []
            for c in q["candidates"]:
                tgt = c["target"]
                if q["kind"] == "release":
                    obj = self.cat.releases[tgt]
                    title, aid = obj["title"], obj["artist_id"]
                elif q["kind"] == "composition":
                    obj = self.cat.compositions[tgt]
                    title, aid = obj["title"], self.cat.tracks[self.cat.comp_tracks[tgt]]["artist_id"]
                else:
                    obj = self.cat.tracks[tgt]
                    title, aid = obj["title"], obj["artist_id"]
                ident = obj.get("isrc") or obj.get("catalog_no") or obj.get("iswc")
                cands.append({**c, "title": title, "artist": self.cat.artists[aid]["name"], "id": ident})
            out.append(
                {
                    **{k: v for k, v in q.items() if k not in ("stores", "candidates")},
                    "stores": sorted(q["stores"]),
                    "candidates": cands,
                }
            )
        return out

    def statement(self, payee: str, period: str) -> dict:
        return self.ledger.statements[(payee, period)]

    def payees(self, ptype: str | None = None) -> list[dict]:
        last = self.rules.periods[-1]
        out = []
        for (payee, period), st in self.ledger.statements.items():
            if period != last or (ptype and st["payee_type"] != ptype):
                continue
            out.append(
                {
                    "id": payee,
                    "name": st["payee_name"],
                    "type": st["payee_type"],
                    "closing_total": st["closing_total"],
                    "payable": st["payable"],
                }
            )
        return sorted(out, key=lambda x: x["id"])

    def audit(self, statement_line_id: str) -> dict:
        res = self.ledger.audit(statement_line_id)
        for c in res["contributions"]:
            line = self.line_index[c["report_line"]]
            c.update(
                file=line["file"],
                row=line["row"],
                store=line["store"],
                month=line["month"],
                raw=line["raw"],
                line_usd_micros=line["usd_micros"],
                match=line["match"]["method"],
            )
        res["contributions_micros_total"] = sum(c["micros"] for c in res["contributions"])
        return res

    def recoupment(self) -> list[dict]:
        out = []
        for a in self.ledger.accounts.values():
            if a["type"] != "artist":
                continue
            out.append(self.recoupment_for(a["id"]))
        return sorted(out, key=lambda x: x["account_id"])

    def recoupment_for(self, acct_id: str) -> dict:
        a = self.ledger.accounts[acct_id]
        hist = self.ledger.history[acct_id]
        start_charged = sum(c["recoupable_cents"] for c in a["charges"] if c["period"] < self.rules.periods[0])
        prior_earned = next(
            x["prior_earnings_cents"] for c in self.cat.data["contracts"] for x in c["accounts"] if x["id"] == acct_id
        )
        series = [
            {
                "period": "start",
                "charged_to_date": start_charged,
                "recouped_to_date": min(prior_earned, start_charged),
                "balance": a["opening_history"],
            }
        ]
        for h in hist:
            series.append(
                {
                    "period": h["period"],
                    "charged_to_date": h["charged_to_date"],
                    "recouped_to_date": min(h["earned_to_date"], h["charged_to_date"]),
                    "balance": h["closing"],
                }
            )
        recent = [h["earnings_total"] - h["reserve_held"] + h["reserve_released"] for h in hist[-2:]]
        run_rate = sum(recent) // len(recent) if recent else 0
        bal = hist[-1]["carried_forward"] if hist else a["balance"]
        projection = None
        if bal < 0 and run_rate > 0:
            n = math.ceil(-bal / run_rate)
            projection = {"periods": n, "period": add_quarters(self.rules.periods[-1], n), "beyond_10_years": n > 40}
        artist = self.cat.artists[a["payee"]]
        contract = self.cat.contracts[a["contract"]]
        return {
            "account_id": acct_id,
            "artist_id": a["payee"],
            "artist": artist["name"],
            "label": a["label"],
            "deal": contract["type"],
            "cross_collat": contract["cross_collat"],
            "balance": bal,
            "recouped": bal >= 0,
            "charged_to_date": a["charged_total"],
            "run_rate_per_period": run_rate,
            "projection": projection,
            "series": series,
            "charges": [
                {k: c[k] for k in ("desc", "kind", "amount_cents", "recoupable_cents", "period")} for c in a["charges"]
            ],
        }

    def analytics(self, top: int = 15) -> dict:
        by_track: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        by_terr: dict[str, int] = defaultdict(int)
        rate: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        by_source: dict[str, int] = defaultdict(int)
        for line in self.lines:
            m = line["match"]
            by_terr[line["territory"]] += line["usd_micros"]
            by_source[SOURCE.get(line["kind"], line["kind"])] += line["usd_micros"]
            if m["status"] != "matched":
                continue
            if line["kind"] in ("stream", "download") and m["target"] in self.cat.tracks:
                by_track[m["target"]][0] += line["usd_micros"]
                by_track[m["target"]][1] += line["units"] if line["kind"] == "stream" else 0
            if line["kind"] == "stream":
                r = rate[(line["store"], line["territory"])]
                r[0] += line["usd_micros"]
                r[1] += line["units"]
        tracks = sorted(by_track.items(), key=lambda kv: -kv[1][0])[:top]
        return {
            "top_tracks": [
                {
                    "track_id": tid,
                    "title": self.cat.tracks[tid]["title"],
                    "isrc": self.cat.tracks[tid]["isrc"],
                    "artist": self.cat.artists[self.cat.tracks[tid]["artist_id"]]["name"],
                    "gross_micros": v[0],
                    "streams": v[1],
                }
                for tid, v in tracks
            ],
            "territories": [
                {"territory": k, "gross_micros": v} for k, v in sorted(by_terr.items(), key=lambda kv: -kv[1])
            ],
            "sources": [{"source": k, "gross_micros": v} for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])],
            "per_stream": [
                {
                    "store": s,
                    "territory": t,
                    "streams": v[1],
                    "gross_micros": v[0],
                    "usd_per_1000": round(v[0] / v[1] / 1000, 4) if v[1] else 0.0,
                }
                for (s, t), v in sorted(rate.items())
            ],
            "recoup_projection": [
                {
                    k: r[k]
                    for k in (
                        "account_id",
                        "artist",
                        "label",
                        "balance",
                        "run_rate_per_period",
                        "projection",
                        "recouped",
                    )
                }
                for r in self.recoupment()
            ],
        }

    def summary(self) -> dict:
        periods = []
        for p in self.rules.periods:
            tie = self.ledger.tie_out(p)
            periods.append(tie)
        return {
            "label": self.rules.data["label"],
            "publisher": self.rules.data["publisher"],
            "artists": len(self.cat.artists),
            "releases": len(self.cat.releases),
            "tracks": len(self.cat.tracks),
            "compositions": len(self.cat.compositions),
            "files": len(self.files),
            "lines": len(self.lines),
            "periods": periods,
            "matching": self.match_eval(),
            "review_queue": len(self.matcher.queue),
            "timings": self.timings,
            "statements": len(self.ledger.statements),
        }
