#!/usr/bin/env python3
"""Wednesday allocator: where should the next contribution go?

Deterministic rule:
  1. Value every position at live prices, in CAD.
  2. gap(name) = target_value - current_value  (CAD)
  3. Eligible = gap > 0 AND halal == "compliant" (the hard gate).
  4. The full contribution goes to the eligible name with the largest
     CAD gap; ties broken by config `priority` (lower first).

This never recommends a buy in a name whose halal status is unchecked —
those are valued but excluded, listed at the bottom.

Usage:
    .venv/bin/python scripts/allocate.py [--amount 250]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradingsystem.market.quotes import get_quote, get_usdcad, is_cad  # noqa: E402


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Allocate the next contribution")
    ap.add_argument("--amount", type=float, default=None,
                    help="contribution in CAD (default: cash_default in allocation.yaml)")
    ap.add_argument("--ledger", default=str(ROOT / "ledger.pkl"))
    ap.add_argument("--config", default=str(ROOT / "config/allocation.yaml"))
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    amount = args.amount if args.amount is not None else float(cfg.get("cash_default", 250))

    with open(args.ledger, "rb") as f:
        ledger = pickle.load(f)
    positions = ledger.positions()  # {(account, symbol): shares}

    # aggregate shares per symbol across accounts
    shares_by_symbol: dict[str, float] = {}
    for (account, symbol), sh in positions.items():
        shares_by_symbol[symbol] = shares_by_symbol.get(symbol, 0.0) + sh

    names = [str(n["symbol"]) for n in cfg["handful"]] + \
            [str(n["symbol"]) for n in cfg.get("legacy", [])]
    meta = {str(n["symbol"]): n for n in cfg["handful"]}
    meta.update({str(n["symbol"]): n for n in cfg.get("legacy", [])})
    # normalize targets to sum to 100
    total_target = sum(n.get("target_pct", 0) for n in cfg["handful"]) + \
        sum(n.get("target_pct", 0) for n in cfg.get("legacy", []))
    targets = {s: meta[s].get("target_pct", 0) / total_target * 100 for s in names}

    # --- live valuation -------------------------------------------------
    usdcad = get_usdcad()
    if usdcad is None:
        print("[warn] no USDCAD rate; USD values shown unconverted", file=sys.stderr)
        usdcad = 1.0

    value_cad: dict[str, float] = {}
    price_of: dict[str, float | None] = {}
    for symbol in names:
        sh = shares_by_symbol.get(symbol, 0.0)
        q = get_quote(symbol)  # quote every name; handful needs prices even at zero weight
        price = q["price"] if q else None
        price_of[symbol] = price
        if price is None:
            value_cad[symbol] = 0.0
            continue
        rate = 1.0 if is_cad(symbol) else usdcad
        value_cad[symbol] = sh * price * rate

    total_value = sum(value_cad.values())
    if total_value <= 0:
        print("No valued positions; cannot allocate.")
        return 1

    # --- gaps ------------------------------------------------------------
    rows = []
    for symbol in names:
        cur_w = value_cad[symbol] / total_value * 100
        tgt_w = targets[symbol]
        gap_cad = tgt_w / 100 * total_value - value_cad[symbol]
        rows.append({
            "symbol": symbol,
            "shares": shares_by_symbol.get(symbol, 0.0),
            "price": price_of[symbol],
            "value": value_cad[symbol],
            "cur_w": cur_w,
            "tgt_w": tgt_w,
            "gap": gap_cad,
            "halal": meta[symbol].get("halal", "unchecked"),
            "priority": meta[symbol].get("priority", 99),
            "note": meta[symbol].get("note", ""),
        })

    print(f"Portfolio value: C${total_value:,.2f}   (USDCAD {usdcad:.4f})")
    print(f"{'Sym':<8} {'Shares':>9} {'Price':>10} {'Value C$':>11} "
          f"{'Now %':>7} {'Tgt %':>7} {'Gap C$':>10}  Halal")
    print("-" * 84)
    for r in sorted(rows, key=lambda r: -r["gap"]):
        p = f"{r['price']:.2f}" if r["price"] else "n/a"
        print(f"{r['symbol']:<8} {r['shares']:>9.4f} {p:>10} {r['value']:>11.2f} "
              f"{r['cur_w']:>6.2f}% {r['tgt_w']:>6.2f}% {r['gap']:>+10.2f}  {r['halal']}")

    # --- recommendation --------------------------------------------------
    eligible = [r for r in rows if r["gap"] > 0 and r["halal"] == "compliant"]
    excluded = [r for r in rows if r["gap"] > 0 and r["halal"] != "compliant"]

    print("-" * 84)
    if not eligible:
        print("No eligible buy: every compliant name is at or above target.")
        return 0
    eligible.sort(key=lambda r: (-r["gap"], r["priority"]))
    pick = eligible[0]
    px = pick["price"]
    est_shares = amount / (px * (usdcad if not is_cad(pick["symbol"]) else 1.0)) if px else 0
    ccy = "CAD" if is_cad(pick["symbol"]) else "USD"

    print(f"RECOMMENDED BUY: C${amount:,.2f} -> {pick['symbol']}")
    if px:
        print(f"  ~{est_shares:.4f} shares @ {px:.2f} {ccy} (live price; final fill may differ)")
    print(f"  Halal: {pick['halal']} (per allocation.yaml — scholar decides finally)")
    if pick["note"]:
        print(f"  Note: {pick['note']}")
    if len(eligible) > 1:
        print(f"  Runner-up: {eligible[1]['symbol']} (gap C${eligible[1]['gap']:,.2f})")
    if excluded:
        excl = ", ".join(f"{r['symbol']} (halal {r['halal']})" for r in excluded)
        print(f"  Excluded by halal gate: {excl}")
    print("  Assumes fractional shares (Wealthsimple). Re-run Wednesday morning for fresh prices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
