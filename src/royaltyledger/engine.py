"""Allocation, recoupment, reserves and statements.

Step 1, per report line (micros): the whole line is split into buckets keyed (payee, account, source, territory).
Fees, withholding tax, the label's share, the publisher's share and suspense are buckets too, so every line's
parts add back to the line exactly. This is checked for every line.

Step 2, per statement period: buckets are converted to cents with the largest remainder method, so the period's
buckets add up to the money received, to the cent.

Step 3, per account, period by period: opening balance, earnings, recoupable charges, reserves held and released,
closing balance, and payment when the payee's positive balances reach the minimum payment.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from .catalog import Catalog
from .money import buckets_to_cents, portion, split
from .rules import Rules, add_quarters, period_of

SOURCE = {
    "stream": "Streaming",
    "download": "Downloads",
    "physical": "Physical",
    "neighbouring": "Neighbouring rights",
    "label_mech": "Mechanicals (label paid)",
    "mechanical": "Mechanicals (society)",
    "performance": "Performance (publisher share)",
}
LABEL, PUBLISHER, TAX, SUSPENSE = "_label", "_publisher", "_tax", "_suspense"


def fee_payee(store: str) -> str:
    return f"_fee:{store}"


class AllocationError(AssertionError):
    pass


class Engine:
    def __init__(self, cat: Catalog, rules: Rules):
        self.cat = cat
        self.rules = rules
        self.track_account = {t["id"]: cat.releases[t["release_id"]]["account_id"] for t in cat.data["tracks"]}

    # ------------------------------------------------------------------------------------------------------------
    # step 1: one line into buckets

    def _publishing(
        self, out: list, comp_id: str, amount: int, source: str, terr: str, controlled_only: bool, deal_key: str
    ) -> None:
        comp = self.cat.compositions[comp_id]
        shares = [s for s in comp["shares"] if not controlled_only or s["publisher_id"] == "PUB-LH"]
        if not shares:
            out.append(((SUSPENSE, "no controlled share", source, terr), amount))
            return
        parts = split(amount, [s["share_bps"] for s in shares])
        for s, part in zip(shares, parts, strict=True):
            if s["publisher_id"] != "PUB-LH":
                out.append(((s["publisher_id"], s["publisher_id"], source, terr), part))
                continue
            w = self.cat.writers[s["writer_id"]]
            pct = self.rules.writer_deals[w["deal"]["type"]][deal_key]
            to_writer = portion(part, pct, 10000)
            out.append(((w["id"], f"{w['id']}:pub", source, terr), to_writer))
            out.append(((PUBLISHER, PUBLISHER, source, terr), part - to_writer))

    def _master(
        self,
        out: list,
        track_id: str,
        gross: int,
        kind: str,
        store: str,
        terr: str,
        units: int,
        fee_applies: bool = True,
    ) -> None:
        t = self.cat.tracks[track_id]
        contract = self.cat.contract_for_artist(t["artist_id"])
        acct = self.track_account[track_id]
        fee = self.rules.fee_micros(store, gross) if fee_applies else 0
        if fee:
            out.append(((fee_payee(store), store, "Distribution fee", terr), fee))
        net = gross - fee
        mech = 0
        if kind == "download":
            mech = self.rules.download_mechanical(terr, units, gross)
        elif kind == "physical":
            mech = units * self.rules.physical_mech
        if mech:
            self._publishing(out, t["composition_id"], mech, SOURCE["label_mech"], terr, False, "mech_bps")
        source = SOURCE[kind]
        prod_parts = []
        if contract["type"] == "royalty":
            factor = self.rules.territory_factor(terr)
            all_in = portion(net, contract["rates_bps"][kind] * factor, 10000 * 10000)
            for p in t["producers"]:
                prod_parts.append((p, portion(net, p["points_bps"] * factor, 10000 * 10000)))
            label = net - mech - all_in
        else:
            pool = net - mech
            all_in = portion(pool, contract["profit_share_bps"], 10000)
            for p in t["producers"]:
                prod_parts.append((p, portion(pool, p["points_bps"], 10000)))
            label = pool - all_in
        artist_net = all_in - sum(x for _, x in prod_parts)
        out.append(((t["artist_id"], acct, source, terr), artist_net))
        for p, amt in prod_parts:
            pid = p["producer_id"]
            out.append(((pid, f"{pid}@{acct}#{p['mode']}", source, terr), amt))
        out.append(((LABEL, LABEL, source, terr), label))

    def allocate(self, line: dict) -> list[tuple[tuple, int]]:
        gross = line["usd_micros"]
        terr = line["territory"]
        kind = line["kind"]
        m = line["match"]
        out: list[tuple[tuple, int]] = []
        if m["status"] != "matched":
            why = "In review" if m["status"] == "review" else "Unmatched"
            out.append(((SUSPENSE, line["store"], why, terr), gross))
        elif kind in ("performance", "mechanical"):
            tax = self.rules.withholding(terr, gross)
            if tax:
                out.append(((TAX, TAX, "Withholding tax", terr), tax))
            self._publishing(
                out,
                m["target"],
                gross - tax,
                SOURCE[kind],
                terr,
                True,
                "perf_bps" if kind == "performance" else "mech_bps",
            )
        elif kind == "neighbouring":
            tax = self.rules.withholding(terr, gross)
            if tax:
                out.append(((TAX, TAX, "Withholding tax", terr), tax))
            self._master(out, m["target"], gross - tax, kind, line["store"], terr, 0, fee_applies=False)
        elif kind == "physical":
            tids = self.cat.release_tracks[m["target"]]
            fee = self.rules.fee_micros(line["store"], gross)
            out.append(((fee_payee(line["store"]), line["store"], "Distribution fee", terr), fee))
            for tid, part in zip(tids, split(gross - fee, [1] * len(tids)), strict=True):
                self._master(out, tid, part, kind, line["store"], terr, line["units"], fee_applies=False)
        else:
            self._master(out, m["target"], gross, kind, line["store"], terr, line["units"])
        if sum(x for _, x in out) != gross:
            raise AllocationError(f"{line['line_id']}: parts {sum(x for _, x in out)} != line {gross}")
        return out

    # ------------------------------------------------------------------------------------------------------------
    # step 2 and 3

    def run(self, lines: list[dict]) -> Ledger:
        led = Ledger(self.cat, self.rules)
        by_period: dict[str, list[dict]] = defaultdict(list)
        for line in lines:
            by_period[period_of(line["month"])].append(line)
        for period in self.rules.periods:
            micros: dict[tuple, int] = defaultdict(int)
            contrib: dict[tuple, list] = defaultdict(list)
            received = 0
            for line in by_period.get(period, []):
                received += line["usd_micros"]
                merged: dict[tuple, int] = defaultdict(int)
                for key, amt in self.allocate(line):
                    merged[key] += amt
                for key, amt in merged.items():
                    micros[key] += amt
                    contrib[key].append((line["line_id"], amt))
            cents = buckets_to_cents(dict(micros), received)
            led.close_period(period, received, dict(micros), cents, contrib)
        return led


class Ledger:
    """Account state across periods, plus the statements produced."""

    def __init__(self, cat: Catalog, rules: Rules):
        self.cat = cat
        self.rules = rules
        self.periods: list[str] = []
        self.received_micros: dict[str, int] = {}
        self.bucket_cents: dict[str, dict[tuple, int]] = {}
        self.bucket_micros: dict[str, dict[tuple, int]] = {}
        self.contrib: dict[str, dict[tuple, list]] = {}
        self.statements: dict[tuple[str, str], dict] = {}
        self.accounts: dict[str, dict] = {}
        self.history: dict[str, list[dict]] = defaultdict(list)
        self._init_accounts()

    # account setup ----------------------------------------------------------------------------------------------
    def _recoupable(self, contract: dict | None, charge: dict) -> int:
        pct = self.rules.recoupable[charge["kind"]]
        amt = portion(charge["amount_cents"], pct, 10000)
        if contract and contract["type"] == "net_profit" and charge["kind"] != "advance":
            # costs come off the top before the 50/50 split, so the artist bears their profit share of them
            amt = portion(charge["amount_cents"], 10000 - contract["profit_share_bps"], 10000)
        return amt

    def _new_account(self, acct_id: str, payee: str, ptype: str, label: str, **kw) -> dict:
        a = {
            "id": acct_id,
            "payee": payee,
            "type": ptype,
            "label": label,
            "balance": 0,
            "charges": [],
            "charged_total": 0,
            "recording_charged": 0,
            "earned_total": 0,
            "reserve_schedule": defaultdict(int),
            "reserve_bps": 0,
            "reserve_periods": 0,
            "held": 0,
            "mode": None,
            "artist_account": None,
            "triggered": True,
            "opening_history": 0,
        }
        a.update(kw)
        self.accounts[acct_id] = a
        return a

    def _init_accounts(self) -> None:
        start = self.rules.periods[0]
        for c in self.cat.data["contracts"]:
            for acc in c["accounts"]:
                a = self._new_account(
                    acc["id"],
                    c["artist_id"],
                    "artist",
                    acc["label"],
                    contract=c["id"],
                    reserve_bps=c["reserve_bps"],
                    reserve_periods=c["reserve_periods"],
                )
                a["earned_total"] = acc["prior_earnings_cents"]
                a["balance"] = acc["prior_earnings_cents"]
            for ch in c["charges"]:
                a = self.accounts[ch["account_id"]]
                rec = self._recoupable(c, ch)
                item = {**ch, "recoupable_cents": rec, "period": period_of(ch["month"])}
                a["charges"].append(item)
                if item["period"] < start:
                    a["balance"] -= rec
                    a["charged_total"] += rec
                    if ch["kind"] == "recording":
                        a["recording_charged"] += rec
        for w in self.cat.data["writers"]:
            if not w["deal"]:
                continue
            a = self._new_account(f"{w['id']}:pub", w["id"], "writer", f"Songwriter ({w['deal']['type']} deal)")
            if w["deal"]["advance_cents"]:
                ch = {
                    "kind": "advance",
                    "amount_cents": w["deal"]["advance_cents"],
                    "month": w["deal"]["signed_month"],
                    "desc": "Publishing advance",
                    "recoupable_cents": w["deal"]["advance_cents"],
                    "period": period_of(w["deal"]["signed_month"]),
                }
                a["charges"].append(ch)
                if ch["period"] < start:
                    a["balance"] -= ch["recoupable_cents"]
                    a["charged_total"] += ch["recoupable_cents"]
        for p in self.cat.data["publishers"]:
            if not p["ours"]:
                self._new_account(p["id"], p["id"], "publisher", "Co-publisher share of mechanicals")
        for a in self.accounts.values():
            a["opening_history"] = a["balance"]

    def _producer_account(self, acct_id: str, payee: str) -> dict:
        if acct_id not in self.accounts:
            artist_acct, mode = acct_id.split("@", 1)[1].split("#")
            label = (
                "Producer royalty, payable from record one once recording costs are recouped"
                if mode == "record_one"
                else "Producer royalty, payable from the first sale"
            )
            self._new_account(
                acct_id, payee, "producer", label, mode=mode, artist_account=artist_acct, triggered=mode != "record_one"
            )
        return self.accounts[acct_id]

    # period close -----------------------------------------------------------------------------------------------
    def close_period(self, period: str, received: int, micros: dict, cents: dict, contrib: dict) -> None:
        self.periods.append(period)
        self.received_micros[period] = received
        self.bucket_micros[period] = micros
        self.bucket_cents[period] = cents
        self.contrib[period] = contrib
        earnings: dict[str, list] = defaultdict(list)
        for key, c in cents.items():
            payee, acct = key[0], key[1]
            if payee.startswith("_") or (c == 0 and micros[key] == 0):
                continue
            if acct not in self.accounts:
                self._producer_account(acct, payee)
            earnings[acct].append(
                {
                    "line_id": "|".join([period, *key]),
                    "source": key[2],
                    "territory": key[3],
                    "cents": c,
                    "micros": micros[key],
                }
            )
        rows: dict[str, dict] = {}
        # artists and writers first, producers after (record-one depends on the artist account)
        order = sorted(self.accounts.values(), key=lambda a: (a["type"] == "producer", a["id"]))
        for a in order:
            lines = sorted(earnings.get(a["id"], []), key=lambda x: (x["source"], x["territory"]))
            rows[a["id"]] = self._close_account(a, period, lines)
        # payment per payee: positive balances are paid when they reach the minimum; negatives never offset
        by_payee: dict[str, list[str]] = defaultdict(list)
        for acct_id in rows:
            by_payee[self.accounts[acct_id]["payee"]].append(acct_id)
        for payee, acct_ids in by_payee.items():
            ptype = self.accounts[acct_ids[0]]["type"]
            minimum = self.rules.min_payment[ptype]
            positive = sum(max(0, rows[x]["closing"]) for x in acct_ids)
            pay = positive >= minimum and positive > 0
            for x in acct_ids:
                paid = max(0, rows[x]["closing"]) if pay else 0
                rows[x]["paid"] = paid
                self.accounts[x]["balance"] = rows[x]["closing"] - paid
                rows[x]["carried_forward"] = self.accounts[x]["balance"]
                self.history[x].append(
                    {
                        "period": period,
                        **{
                            k: rows[x][k]
                            for k in (
                                "opening",
                                "earnings_total",
                                "charges_total",
                                "reserve_held",
                                "reserve_released",
                                "closing",
                                "paid",
                                "carried_forward",
                                "producer_held",
                                "producer_released",
                            )
                        },
                        "charged_to_date": self.accounts[x]["charged_total"],
                        "earned_to_date": self.accounts[x]["earned_total"],
                    }
                )
            st = self._statement(payee, ptype, period, [rows[x] for x in sorted(acct_ids)], minimum, pay)
            self.statements[(payee, period)] = st

    def _close_account(self, a: dict, period: str, lines: list[dict]) -> dict:
        opening = a["balance"]
        charges = [c for c in a["charges"] if c["period"] == period]
        charges_total = sum(c["recoupable_cents"] for c in charges)
        a["charged_total"] += charges_total
        a["recording_charged"] += sum(c["recoupable_cents"] for c in charges if c["kind"] == "recording")
        earned = sum(x["cents"] for x in lines)
        held = released = 0
        prod_held = prod_released = 0
        if a["type"] == "artist":
            physical = sum(x["cents"] for x in lines if x["source"] == SOURCE["physical"])
            if physical > 0 and a["reserve_bps"]:
                held = portion(physical, a["reserve_bps"], 10000)
                # liquidated in equal parts over the next N quarters, even past the last period computed here
                later = [add_quarters(period, k + 1) for k in range(a["reserve_periods"])]
                for p, part in zip(later, split(held, [1] * len(later)), strict=True):
                    a["reserve_schedule"][p] += part
            released = a["reserve_schedule"].pop(period, 0)
            a["earned_total"] += earned - held + released
        elif a["type"] == "producer":
            artist = self.accounts[a["artist_account"]]
            if not a["triggered"]:
                if artist["earned_total"] >= artist["recording_charged"]:
                    a["triggered"] = True
                    prod_released = a["held"]
                    a["held"] = 0
                else:
                    prod_held = earned
                    a["held"] += earned
            a["earned_total"] += earned - prod_held + prod_released
        else:
            a["earned_total"] += earned
        closing = opening + earned - charges_total - held + released - prod_held + prod_released
        return {
            "account_id": a["id"],
            "label": a["label"],
            "opening": opening,
            "earnings": lines,
            "earnings_total": earned,
            "charges": [
                {
                    "desc": c["desc"],
                    "kind": c["kind"],
                    "amount_cents": c["amount_cents"],
                    "recoupable_cents": c["recoupable_cents"],
                }
                for c in charges
            ],
            "charges_total": charges_total,
            "reserve_held": held,
            "reserve_released": released,
            "producer_held": prod_held,
            "producer_released": prod_released,
            "producer_held_to_date": a["held"],
            "reserve_outstanding": sum(a["reserve_schedule"].values()),
            "closing": closing,
            "recouped": closing >= 0,
            "unrecouped": max(0, -closing),
        }

    def _statement(self, payee: str, ptype: str, period: str, accounts: list[dict], minimum: int, pay: bool) -> dict:
        st = {
            "payee_id": payee,
            "payee_name": self.cat.payee_name(payee),
            "payee_type": ptype,
            "period": period,
            "currency": "USD",
            "accounts": accounts,
            "opening_total": sum(a["opening"] for a in accounts),
            "earnings_total": sum(a["earnings_total"] for a in accounts),
            "closing_total": sum(a["closing"] for a in accounts),
            "payable": sum(a["paid"] for a in accounts),
            "min_payment": minimum,
            "below_minimum": not pay and sum(max(0, a["closing"]) for a in accounts) > 0,
            "carried_forward_total": sum(a["carried_forward"] for a in accounts),
        }
        st["digest"] = statement_digest(st)
        return st

    # queries ----------------------------------------------------------------------------------------------------
    def tie_out(self, period: str) -> dict:
        """The CI gate check: every cent received is in exactly one bucket."""
        from .money import micros_to_cents

        cents = self.bucket_cents[period]
        received = micros_to_cents(self.received_micros[period])
        by_kind: dict[str, int] = defaultdict(int)
        for key, c in cents.items():
            p = key[0]
            kind = (
                "fees"
                if p.startswith("_fee")
                else "label"
                if p == LABEL
                else "publisher"
                if p == PUBLISHER
                else "tax"
                if p == TAX
                else "suspense"
                if p == SUSPENSE
                else "payees"
            )
            by_kind[kind] += c
        total = sum(cents.values())
        return {
            "period": period,
            "received_cents": received,
            "allocated_cents": total,
            "difference_cents": total - received,
            "by_kind": dict(sorted(by_kind.items())),
            "ties_out": total == received,
        }

    def audit(self, line_id: str) -> dict:
        parts = line_id.split("|")
        period, key = parts[0], tuple(parts[1:])
        if period not in self.contrib or key not in self.contrib[period]:
            raise KeyError(line_id)
        rows = self.contrib[period][key]
        return {
            "line_id": line_id,
            "period": period,
            "payee": key[0],
            "account": key[1],
            "source": key[2],
            "territory": key[3],
            "cents": self.bucket_cents[period][key],
            "micros": self.bucket_micros[period][key],
            "contributions": [{"report_line": lid, "micros": m} for lid, m in rows],
        }


def statement_digest(st: dict) -> str:
    core = {k: v for k, v in st.items() if k != "digest"}
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]
