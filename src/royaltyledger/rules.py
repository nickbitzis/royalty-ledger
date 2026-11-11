"""Business rules as data: stores and fees, territories, FX by month, writer deals, thresholds.

The defaults ship in data/rules.json. Every number there is illustrative."""

from __future__ import annotations

import json
from pathlib import Path

from .money import portion, to_micros

DEFAULT_RULES = Path(__file__).parent / "data" / "rules.json"


class Rules:
    def __init__(self, data: dict):
        self.data = data
        self.fx: dict[str, dict[str, str]] = data["fx_usd_per_unit"]
        self.stores = data["stores"]
        self.territories = data["territories"]
        self.periods: list[str] = data["statement_periods"]
        self.writer_deals = data["writer_deals"]
        self.recoupable = data["recoupable_pct_bps"]
        self.min_payment = data["min_payment_cents"]
        self.foreign_factor = data["foreign_rate_factor_bps"]
        self.physical_mech = data["physical_mech_per_track_micros"]

    @staticmethod
    def load(path: Path | None = None) -> Rules:
        return Rules(json.loads(Path(path or DEFAULT_RULES).read_text()))

    def months(self) -> list[str]:
        return sorted(self.fx)

    def to_usd_micros(self, amount: str, currency: str, month: str) -> int:
        """FX at the rate of the usage month. Unknown month or currency is an error, never a guess."""
        try:
            rate = self.fx[month][currency]
        except KeyError as e:
            raise KeyError(f"no FX rate for {currency} in {month}") from e
        return to_micros(amount, rate)

    def fee_micros(self, store: str, gross: int) -> int:
        return portion(gross, self.stores[store]["distribution_fee_bps"], 10000)

    def territory_factor(self, territory: str) -> int:
        """Royalty rate factor in bps: 100% at home, a reduced rate abroad."""
        t = self.territories.get(territory)
        return 10000 if t and t["home"] else self.foreign_factor

    def download_mechanical(self, territory: str, units: int, gross: int) -> int:
        rule = self.territories[territory]["download_mech"]
        if "per_unit_micros" in rule:
            return units * rule["per_unit_micros"]
        return portion(gross, rule["pct_bps"], 10000)

    def withholding(self, territory: str, amount: int) -> int:
        return portion(amount, self.territories.get(territory, {}).get("withholding_bps", 0), 10000)


def period_of(month: str) -> str:
    return f"{month[:4]}Q{(int(month[5:7]) - 1) // 3 + 1}"


def add_quarters(period: str, n: int) -> str:
    y, q = int(period[:4]), int(period[5])
    idx = y * 4 + (q - 1) + n
    return f"{idx // 4}Q{idx % 4 + 1}"
