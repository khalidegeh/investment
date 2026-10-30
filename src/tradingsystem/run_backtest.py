"""CLI: PYTHONPATH=src python3 -m tradingsystem.run_backtest --config config.yaml
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingsystem.backtest.engine import run_backtest
from tradingsystem.config import Config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="override universe, e.g. --symbols AAPL NVDA")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    result = run_backtest(
        cfg, args.symbols,
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
    )
    Path(args.out).write_text(result.to_json())
    print(f"symbols={result.symbols} trades={result.num_trades} "
          f"return={result.total_return_pct}% maxDD={result.max_drawdown_pct}% "
          f"realized={result.realized_pnl} -> {args.out}")


if __name__ == "__main__":
    main()
