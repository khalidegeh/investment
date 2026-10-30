#!/usr/bin/env python3
"""Daily P/L tracker — no broker access needed.

Equation, per position lot:
    unrealized P/L = (current_price - entry_acb) * shares
    day P/L        = (current_price - prev_close) * shares

Entries (ACB/share) come from ledger.pkl — the authoritative record.
Prices: Finnhub /quote for US symbols, yfinance for TSX (.TO) symbols.
USD lots are converted to CAD at the live USDCAD rate (yfinance CAD=X).

Usage:
    .venv/bin/python scripts/pnl_tracker.py [--ledger ledger.pkl]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradingsystem.market.quotes import get_quote, get_usdcad, is_cad  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily P/L from ledger entries + live quotes")
    ap.add_argument("--ledger", default=str(ROOT / "ledger.pkl"))
    args = ap.parse_args()

    with open(args.ledger, "rb") as f:
        ledger = pickle.load(f)

    lots = sorted(ledger.positions().items())  # [((account, symbol), shares)]
    if not lots:
        print("No open positions in ledger.")
        return 0

    # --- quotes ---------------------------------------------------------
    quotes: dict[str, dict | None] = {}
    for (account, symbol), _ in lots:
        if symbol in quotes:
            continue
        quotes[symbol] = get_quote(symbol)

    usdcad = get_usdcad()
    if usdcad is None:
        print("[warn] no USDCAD rate; USD lots shown in USD only", file=sys.stderr)

    # --- P/L ------------------------------------------------------------
    rows = []
    tot_unreal_cad = 0.0
    tot_day_cad = 0.0
    for (account, symbol), shares in lots:
        entry = ledger.get_acb_per_share(symbol, account)
        q = quotes.get(symbol)
        ccy = "CAD" if is_cad(symbol) else "USD"
        if q is None:
            rows.append((account, symbol, shares, entry, None, None, None, None, ccy))
            continue
        price, prev = q["price"], q["prev_close"]
        unreal = (price - entry) * shares
        day = (price - prev) * shares if prev else 0.0
        rate = 1.0 if ccy == "CAD" else (usdcad or 1.0)
        unreal_cad, day_cad = unreal * rate, day * rate
        tot_unreal_cad += unreal_cad
        tot_day_cad += day_cad
        rows.append((account, symbol, shares, entry, price, unreal, unreal_cad, day_cad, ccy))

    # --- report ----------------------------------------------------------
    print(f"{'Acct':<6} {'Sym':<8} {'Shares':>9} {'Entry':>10} {'Price':>10} "
          f"{'Unreal':>11} {'Unreal(C$)':>11} {'Day(C$)':>10}")
    print("-" * 82)
    for account, symbol, shares, entry, price, unreal, unreal_cad, day_cad, ccy in rows:
        if price is None:
            print(f"{account:<6} {symbol:<8} {shares:>9.4f} {entry:>10.2f} "
                  f"{'n/a':>10} {'n/a':>11} {'n/a':>11} {'n/a':>10}")
            continue
        flag = "" if ccy == "CAD" else " *"
        print(f"{account:<6} {symbol:<8} {shares:>9.4f} {entry:>10.2f} "
              f"{price:>10.2f} {unreal:>+11.2f} {unreal_cad:>+11.2f} {day_cad:>+10.2f}{flag}")
    print("-" * 82)
    fx_note = f"USDCAD {usdcad:.4f}" if usdcad else "USDCAD n/a"
    print(f"TOTAL unrealized: C${tot_unreal_cad:+.2f}   |   Day P/L: C${tot_day_cad:+.2f}   ({fx_note})")
    print("* USD lot, converted at live rate. Entries = ledger ACB/share.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
