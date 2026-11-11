"""Command line: generate data, serve the API, run the CI gate, print a statement."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from .catalog import validate
from .money import fmt_cents
from .service import RoyaltyService, build_data

GATE_DIR = Path(__file__).resolve().parents[2] / "gate"


def _ensure_data(data: Path, seed: int, artists: int, fresh: bool = False) -> None:
    if fresh or not (data / "catalog.json").exists():
        if fresh and data.exists():
            import shutil

            shutil.rmtree(data)
        summary = build_data(data, seed=seed, n_artists=artists)
        print(f"generated {summary['lines']:,} report lines in {len(summary['files'])} files under {data}")


def cmd_gate(a: argparse.Namespace) -> int:
    """Recompute a sample period and compare every payee's statement with the committed expected file."""
    data = Path(a.data)
    _ensure_data(data, a.seed, a.artists, fresh=a.fresh)
    svc = RoyaltyService(data)
    failures: list[str] = []
    errors = validate(svc.cat.data)
    failures += [f"split: {e}" for e in errors]
    for p in svc.rules.periods:
        tie = svc.ledger.tie_out(p)
        if not tie["ties_out"]:
            failures.append(f"{p}: allocated {tie['allocated_cents']} != received {tie['received_cents']}")
    period = a.period
    current = {
        payee: {"closing_total": st["closing_total"], "payable": st["payable"], "digest": st["digest"]}
        for (payee, p), st in sorted(svc.ledger.statements.items())
        if p == period
    }
    tie = svc.ledger.tie_out(period)
    snapshot = {
        "period": period,
        "seed": a.seed,
        "artists": a.artists,
        "received_cents": tie["received_cents"],
        "by_kind": tie["by_kind"],
        "statements": current,
    }
    expected_path = Path(a.expected)
    if a.update:
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
        print(f"wrote {expected_path} with {len(current)} statements")
        return 0
    expected = json.loads(expected_path.read_text())
    if expected["received_cents"] != snapshot["received_cents"]:
        failures.append(f"received changed: {expected['received_cents']} -> {snapshot['received_cents']}")
    for payee in sorted(set(expected["statements"]) | set(current)):
        e, c = expected["statements"].get(payee), current.get(payee)
        if e != c:
            failures.append(f"statement changed for {payee}: expected {e}, got {c}")
    lines = [
        f"Gate {period}: {len(current)} statements, received {fmt_cents(tie['received_cents'])}, "
        f"allocated {fmt_cents(tie['allocated_cents'])}, difference {tie['difference_cents']} cents"
    ]
    lines += [f"FAIL {f}" for f in failures[:50]]
    lines.append("PASS" if not failures else f"{len(failures)} failures")
    print("\n".join(lines))
    return 1 if failures else 0


def cmd_statement(a: argparse.Namespace) -> int:
    svc = RoyaltyService(Path(a.data))
    st = svc.statement(a.payee, a.period)
    print(f"{st['payee_name']} ({st['payee_id']}), {st['payee_type']}, period {st['period']}")
    for acct in st["accounts"]:
        print(f"\n  {acct['label']} [{acct['account_id']}]")
        print(f"    {'Opening balance':<52}{fmt_cents(acct['opening']):>16}")
        for e in acct["earnings"]:
            print(f"    {e['source'] + ', ' + e['territory']:<52}{fmt_cents(e['cents']):>16}")
        for c in acct["charges"]:
            print(f"    {c['desc'] + ' (recoupable)':<52}{fmt_cents(-c['recoupable_cents']):>16}")
        for k, text, sign in (
            ("reserve_held", "Reserve held against returns", -1),
            ("reserve_released", "Reserve released", 1),
            ("producer_held", "Held until recording costs recouped", -1),
            ("producer_released", "Released, retroactive to record one", 1),
        ):
            if acct[k]:
                print(f"    {text:<52}{fmt_cents(sign * acct[k]):>16}")
        print(f"    {'Closing balance':<52}{fmt_cents(acct['closing']):>16}")
    print(f"\n  Payable {fmt_cents(st['payable'])} (minimum payment {fmt_cents(st['min_payment'])})")
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    svc = RoyaltyService(Path(a.data))
    print(json.dumps(svc.summary(), indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="royaltyledger")
    ap.add_argument("--data", default="data", help="data directory (catalog, reports, decisions)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write the synthetic catalog and usage reports")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--artists", type=int, default=60)
    s = sub.add_parser("serve", help="run the API and web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8882)
    gate = sub.add_parser("gate", help="CI gate: recompute a period and compare with the expected statements")
    gate.add_argument("--period", default="2026Q2")
    gate.add_argument("--expected", default=str(GATE_DIR / "expected_2026Q2.json"))
    gate.add_argument("--seed", type=int, default=7)
    gate.add_argument("--artists", type=int, default=60)
    gate.add_argument("--fresh", action="store_true", help="regenerate the data first")
    gate.add_argument("--update", action="store_true", help="rewrite the expected file")
    st = sub.add_parser("statement", help="print one statement")
    st.add_argument("payee")
    st.add_argument("--period", default="2026Q2")
    sub.add_parser("eval", help="print the summary: tie-out per period and matching scores")
    h = sub.add_parser("healthcheck")
    h.add_argument("--url", default="http://127.0.0.1:8882/healthz")
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        _ensure_data(Path(a.data), a.seed, a.artists, fresh=True)
        return 0
    if a.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(Path(a.data)), host=a.host, port=a.port, log_level="warning")
        return 0
    if a.cmd == "gate":
        return cmd_gate(a)
    if a.cmd == "statement":
        return cmd_statement(a)
    if a.cmd == "eval":
        return cmd_eval(a)
    if a.cmd == "healthcheck":
        try:
            with urllib.request.urlopen(a.url, timeout=3) as r:
                return 0 if r.status == 200 else 1
        except OSError:
            return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
