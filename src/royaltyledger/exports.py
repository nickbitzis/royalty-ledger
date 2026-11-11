"""Statement exports: CSV (one row per statement line) and a standalone printable HTML page."""

from __future__ import annotations

import csv
import html
import io

from .money import fmt_cents


def statement_rows(st: dict) -> list[list[str]]:
    rows = []
    base = [st["payee_id"], st["payee_name"], st["period"]]
    for a in st["accounts"]:
        acct = [a["account_id"], a["label"]]
        rows.append(base + acct + ["opening_balance", "", "", str(a["opening"]), ""])
        for e in a["earnings"]:
            rows.append(base + acct + ["earnings", e["source"], e["territory"], str(e["cents"]), e["line_id"]])
        for c in a["charges"]:
            rows.append(base + acct + ["charge", c["desc"], "", str(-c["recoupable_cents"]), ""])
        if a["reserve_held"]:
            rows.append(base + acct + ["reserve_held", "Physical returns reserve", "", str(-a["reserve_held"]), ""])
        if a["reserve_released"]:
            rows.append(
                base + acct + ["reserve_released", "Physical returns reserve", "", str(a["reserve_released"]), ""]
            )
        if a["producer_held"]:
            rows.append(
                base + acct + ["producer_held", "Held until record-one trigger", "", str(-a["producer_held"]), ""]
            )
        if a["producer_released"]:
            rows.append(
                base + acct + ["producer_released", "Retroactive to record one", "", str(a["producer_released"]), ""]
            )
        rows.append(base + acct + ["closing_balance", "", "", str(a["closing"]), ""])
        rows.append(base + acct + ["paid", "", "", str(a["paid"]), ""])
    return rows


def statement_csv(st: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(
        [
            "payee_id",
            "payee_name",
            "period",
            "account_id",
            "account",
            "line_type",
            "source",
            "territory",
            "amount_cents",
            "audit_id",
        ]
    )
    w.writerows(statement_rows(st))
    return buf.getvalue()


def statement_html(st: dict, label: str) -> str:
    e = html.escape
    parts = [
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Statement {e(st["payee_name"])} {e(st["period"])}</title>
<style>
:root {{ color-scheme: light dark; --bg:#fff; --text:#111; --muted:#555; --border:#ddd; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --text:#eee; --muted:#aaa; --border:#333; }} }}
body {{ font: 13px/1.45 system-ui, sans-serif; margin: 0 auto; padding: 24px 16px; max-width: 860px;
  background: var(--bg); color: var(--text); }}
table {{ width: 100%; border-collapse: collapse; margin: 8px 0 18px; }}
td, th {{ padding: 4px 6px; border-top: 1px solid var(--border); text-align: left; }}
.n {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.m {{ color: var(--muted); }} h1 {{ font-size: 18px; margin: 0; }} h2 {{ font-size: 14px; margin: 18px 0 4px; }}
.tot td {{ font-weight: 600; }}
@media print {{ body {{ padding: 0; }} :root {{ --bg:#fff; --text:#000; }} }}
</style></head><body>
<h1>{e(label)} royalty statement</h1>
<p class="m">{e(st["payee_name"])} ({e(st["payee_id"])}, {e(st["payee_type"])}). Period {e(st["period"])}.
All amounts in USD. Statement digest {e(st["digest"])}.</p>"""
    ]
    for a in st["accounts"]:
        parts.append(f"<h2>{e(a['label'])} <span class='m'>{e(a['account_id'])}</span></h2><table>")
        parts.append(f"<tr><td>Opening balance</td><td></td><td class='n'>{fmt_cents(a['opening'])}</td></tr>")
        for x in a["earnings"]:
            parts.append(
                f"<tr><td>{e(x['source'])}</td><td>{e(x['territory'])}</td>"
                f"<td class='n'>{fmt_cents(x['cents'])}</td></tr>"
            )
        for c in a["charges"]:
            parts.append(
                f"<tr><td>{e(c['desc'])} (recoupable)</td><td></td>"
                f"<td class='n'>{fmt_cents(-c['recoupable_cents'])}</td></tr>"
            )
        for key, text, sign in (
            ("reserve_held", "Reserve held against physical returns", -1),
            ("reserve_released", "Reserve released", 1),
            ("producer_held", "Held until recording costs are recouped", -1),
            ("producer_released", "Released, retroactive to record one", 1),
        ):
            if a[key]:
                parts.append(f"<tr><td>{text}</td><td></td><td class='n'>{fmt_cents(sign * a[key])}</td></tr>")
        parts.append(
            f"<tr class='tot'><td>Closing balance</td><td></td><td class='n'>{fmt_cents(a['closing'])}</td>"
            f"</tr><tr><td>Paid this period</td><td></td><td class='n'>{fmt_cents(a['paid'])}</td></tr>"
            "</table>"
        )
    note = (
        f"Below the minimum payment of {fmt_cents(st['min_payment'])}, carried forward." if st["below_minimum"] else ""
    )
    parts.append(f"<p><b>Payable: {fmt_cents(st['payable'])}</b>. {note}</p>")
    parts.append(
        "<p class='m'>Illustrative statement from synthetic data. Not legal or accounting advice.</p></body></html>"
    )
    return "\n".join(parts)
