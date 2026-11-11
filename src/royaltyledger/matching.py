"""Match report lines to the catalog.

Order: exact identifier (ISRC, catalog number, ISWC) with a title sanity check, then fuzzy title and artist
matching inside the matched artist's catalog. Confident fuzzy matches are accepted, borderline ones go to a
review queue, the rest stay unmatched. Money on lines that are not matched is held in suspense, never guessed.

Review decisions are keyed by the normalized identifiers of the line, so one decision resolves every line with
the same metadata (every month, territory and store).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from .catalog import Catalog

AUTO = 0.93
REVIEW = 0.75
ISRC_TITLE_MIN = 0.6
TRACK_KINDS = {"stream", "download", "neighbouring"}
COMP_KINDS = {"performance", "mechanical"}

_paren = re.compile(r"[\(\[].*?[\)\]]")
_feat = re.compile(r"\b(feat\.?|ft\.?|featuring)\b.*$")
_dash = re.compile(r"\s-\s.*$")
_nonalnum = re.compile(r"[^a-z0-9]+")


@lru_cache(maxsize=200_000)
def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = _paren.sub(" ", s)
    s = _dash.sub(" ", s)
    s = _feat.sub(" ", s)
    s = _nonalnum.sub(" ", s).strip()
    return " ".join(s.split())


@lru_cache(maxsize=200_000)
def norm_artist(s: str) -> str:
    n = norm_text(s)
    if n.endswith(" the"):
        n = "the " + n[:-4]
    return n


@lru_cache(maxsize=200_000)
def norm_isrc(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def ratio(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def target_kind(kind: str) -> str:
    if kind == "physical":
        return "release"
    if kind in COMP_KINDS:
        return "composition"
    return "track"


class Matcher:
    def __init__(self, cat: Catalog, decisions: dict | None = None):
        self.cat = cat
        self.decisions = decisions if decisions is not None else {}
        self.artist_norm = {norm_artist(a["name"]): a["id"] for a in cat.data["artists"]}
        self.artist_tracks: dict[str, list[tuple[str, str]]] = {}
        for t in cat.data["tracks"]:
            self.artist_tracks.setdefault(t["artist_id"], []).append((t["id"], norm_text(t["title"])))
        self.artist_releases: dict[str, list[tuple[str, str]]] = {}
        for rel in cat.data["releases"]:
            self.artist_releases.setdefault(rel["artist_id"], []).append((rel["id"], norm_text(rel["title"])))
        self.isrc = {norm_isrc(k): v for k, v in cat.by_isrc.items()}
        self._cache: dict[str, dict] = {}
        self._artist_cache: dict[str, tuple[str | None, float]] = {}
        self.queue: dict[str, dict] = {}

    # ------------------------------------------------------------------------------------------------------------
    def key(self, line: dict) -> str:
        tk = target_kind(line["kind"])
        ident = line["catalog_no"] if tk == "release" else line["iswc"] if tk == "composition" else line["isrc"]
        return "|".join([tk, norm_isrc(ident), norm_text(line["title"]), norm_artist(line["artist"])])

    @staticmethod
    def key_id(key: str) -> str:
        return hashlib.sha1(key.encode()).hexdigest()[:10]

    def _artist(self, name: str) -> tuple[str | None, float]:
        n = norm_artist(name)
        if n in self._artist_cache:
            return self._artist_cache[n]
        if n in self.artist_norm:
            res = (self.artist_norm[n], 1.0)
        else:
            best, score = None, 0.0
            for cand, aid in self.artist_norm.items():
                s = ratio(n, cand)
                if s > score:
                    best, score = aid, s
            res = (best, score) if score >= 0.8 else (None, score)
        self._artist_cache[n] = res
        return res

    def _fuzzy(self, title: str, artist: str, tk: str) -> list[dict]:
        aid, a_score = self._artist(artist)
        if not aid:
            return []
        nt = norm_text(title)
        pool = self.artist_releases[aid] if tk == "release" else self.artist_tracks.get(aid, [])
        scored = []
        for tid, cand in pool:
            s = 0.75 * ratio(nt, cand) + 0.25 * a_score
            scored.append({"target": tid, "score": round(s, 4)})
        scored.sort(key=lambda x: (-x["score"], x["target"]))
        return scored[:3]

    def _resolve(self, line: dict) -> dict:
        tk = target_kind(line["kind"])
        key = self.key(line)
        kid = self.key_id(key)
        dec = self.decisions.get(kid)
        if dec:
            if dec["action"] == "accept":
                return {"status": "matched", "target": dec["target"], "method": "review", "score": None, "key": kid}
            return {"status": "unmatched", "target": None, "method": "rejected", "score": None, "key": kid}
        if tk == "release":
            rid = self.cat.by_catno.get(line["catalog_no"].strip().upper())
            if rid:
                return {"status": "matched", "target": rid, "method": "catalog_no", "score": 1.0, "key": kid}
        elif tk == "composition":
            cid = self.cat.by_iswc.get(line["iswc"].strip())
            if cid:
                return {"status": "matched", "target": cid, "method": "iswc", "score": 1.0, "key": kid}
        else:
            tid = self.isrc.get(norm_isrc(line["isrc"]))
            if tid:
                t_score = ratio(norm_text(line["title"]), norm_text(self.cat.tracks[tid]["title"]))
                fuzzy = [] if t_score == 1.0 else self._fuzzy(line["title"], line["artist"], "track")
                # the ISRC wins unless the title is far off, or another track by the artist fits the title better
                better = [c for c in fuzzy if c["target"] != tid and c["score"] >= AUTO and c["score"] > t_score]
                if t_score >= ISRC_TITLE_MIN and not better:
                    return {"status": "matched", "target": tid, "method": "isrc", "score": 1.0, "key": kid}
                cands = [{"target": tid, "score": round(t_score, 4), "why": "ISRC match, title disagrees"}]
                cands += [c for c in fuzzy if c["target"] != tid][:2]
                cands.sort(key=lambda c: -c["score"])
                return {
                    "status": "review",
                    "target": None,
                    "method": "isrc_conflict",
                    "score": t_score,
                    "key": kid,
                    "candidates": cands,
                }
        cands = self._fuzzy(line["title"], line["artist"], "release" if tk == "release" else "track")
        if tk == "composition":
            cands = [{"target": self.cat.tracks[c["target"]]["composition_id"], "score": c["score"]} for c in cands]
        if not cands or cands[0]["score"] < REVIEW:
            return {
                "status": "unmatched",
                "target": None,
                "method": "none",
                "score": cands[0]["score"] if cands else 0.0,
                "key": kid,
                "candidates": cands,
            }
        best = cands[0]
        ambiguous = len(cands) > 1 and best["score"] - cands[1]["score"] < 0.03 and best["score"] < 1.0
        if best["score"] >= AUTO and not ambiguous:
            return {
                "status": "matched",
                "target": best["target"],
                "method": "fuzzy",
                "score": best["score"],
                "key": kid,
            }
        return {
            "status": "review",
            "target": None,
            "method": "fuzzy_review",
            "score": best["score"],
            "key": kid,
            "candidates": cands,
        }

    def match(self, line: dict) -> dict:
        key = self.key(line)
        res = self._cache.get(key)
        if res is None:
            res = self._resolve(line)
            self._cache[key] = res
        line["match"] = res
        if res["status"] == "review":
            q = self.queue.setdefault(
                res["key"],
                {
                    "key_id": res["key"],
                    "kind": target_kind(line["kind"]),
                    "method": res["method"],
                    "isrc": line["isrc"],
                    "catalog_no": line["catalog_no"],
                    "iswc": line["iswc"],
                    "title": line["title"],
                    "artist": line["artist"],
                    "candidates": res["candidates"],
                    "lines": 0,
                    "micros": 0,
                    "stores": set(),
                    "line_ids": [],
                },
            )
            q["lines"] += 1
            q["micros"] += line.get("usd_micros", 0)
            q["stores"].add(line["store"])
            if len(q["line_ids"]) < 20:
                q["line_ids"].append(line["line_id"])
        return res

    def match_all(self, lines: list[dict]) -> None:
        self.queue = {}
        self._cache = {}
        for line in lines:
            self.match(line)


def evaluate(lines: list[dict], truth: dict) -> dict:
    """Score matching against the generator's ground truth."""
    n = auto = auto_ok = review = review_top_ok = unmatched = unmatched_ok = 0
    by_method: dict[str, int] = {}
    for line in lines:
        if line["line_id"] not in truth:
            continue
        n += 1
        real = truth[line["line_id"]]
        m = line["match"]
        if m["status"] == "matched":
            auto += 1
            by_method[m["method"]] = by_method.get(m["method"], 0) + 1
            auto_ok += m["target"] == real
        elif m["status"] == "review":
            review += 1
            review_top_ok += bool(m.get("candidates")) and m["candidates"][0]["target"] == real
        else:
            unmatched += 1
            unmatched_ok += real is None
    return {
        "lines": n,
        "matched": auto,
        "match_rate": round(auto / n, 4) if n else 0.0,
        "matched_correct": auto_ok,
        "precision": round(auto_ok / auto, 5) if auto else 0.0,
        "by_method": dict(sorted(by_method.items())),
        "review_lines": review,
        "review_top_candidate_correct": round(review_top_ok / review, 4) if review else 0.0,
        "unmatched_lines": unmatched,
        "unmatched_truly_not_ours": unmatched_ok,
    }
