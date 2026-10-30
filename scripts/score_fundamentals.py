#!/usr/bin/env python3
"""Score tickers on fundamentals -> buy / hold / sell.

Usage:
    .venv/bin/python scripts/score_fundamentals.py AAPL NVDA
    .venv/bin/python scripts/score_fundamentals.py --verbose AAPL

With no symbols, scores the config universe. Requires FINNHUB_API_KEY in the
environment for the primary provider; falls back to yfinance, then to
data/fundamentals/<SYMBOL>.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tradingsystem.config import Config  # noqa: E402
from tradingsystem.fundamentals import advisor  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic fundamentals advisor")
    ap.add_argument("symbols", nargs="*", help="tickers to score (default: config universe)")
    ap.add_argument("--verbose", action="store_true", help="show per-metric breakdown")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = ap.parse_args()

    config = Config.load(args.config)
    symbols = args.symbols or config.get("universe", default=[])
    if not symbols:
        print("no symbols given and config universe is empty")
        return 1

    providers = advisor.build_providers(config)
    print(f"provider chain: {[p.name for p in providers] or ['(none configured)']}")
    print(f"{'SYMBOL':<8} {'VERDICT':<18} {'SCORE':<7} {'COVER':<7} {'SOURCE':<10} PRICE")
    print("-" * 70)
    for sym in symbols:
        a = advisor.advise(sym, config, providers)
        score = f"{a.score:.1f}" if a.score is not None else "-"
        price = f"{a.price:.2f} {a.currency}" if a.price else "-"
        print(f"{a.symbol:<8} {a.verdict:<18} {score:<7} {a.coverage:<7} {a.provider or '-':<10} {price}")
        for w in a.warnings:
            print(f"         ! {w}")
        if args.verbose:
            for name, b in a.breakdown.items():
                print(f"           {name:<20} value={b['value']} points={b['points']:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
