"""Seed the ledger with opening balances derived from broker screenshots.

Book cost per position = market value - unrealized P/L (both from the
screenshots dated 2026-10-08). TFSA/FHSA split derived by diffing the
TFSA-only screenshot against the combined FHSA+TFSA screenshot.

DO NOT RUN until the account split is confirmed by the user.
Verify against Wealthsimple monthly statements afterwards.
"""
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingsystem.ledger.trade_ledger import TradeLedger

# (symbol, account, shares, book_cost) — book_cost in the position's currency
OPENING = [
    # TFSA (from the TFSA-only screenshot)
    ("BTE.TO",  "TFSA", 75.4969, 502.81),    # 511.11 - 8.30
    ("CP.TO",   "TFSA", 1.3093,  163.91),    # 157.12 + 6.79
    ("FRSH",    "TFSA", 25.0,    342.25),    # 339.50 + 2.75 USD
    ("NVDA",    "TFSA", 1.0,     222.51),    # 230.97 - 8.46 USD
    ("SHOP.TO", "TFSA", 2.6551,  513.91),    # 621.80 - 107.89
    # FHSA (combined screenshot minus TFSA)
    ("NVDA",    "FHSA", 3.0,     720.80),    # 694.43 + 26.37 USD
    ("SHOP.TO", "FHSA", 5.0,     1168.65),   # 1170.95 - 2.30
    ("VFV",     "FHSA", 7.702,   1518.99),   # 1507.90 + 11.09
    ("XQQ",     "FHSA", 26.3435, 2000.00),   # 1965.23 + 34.77
]

CONFIRMED = True  # user confirmed the TFSA/FHSA split on 2026-10-08


def main() -> None:
    if not CONFIRMED:
        print("Split not confirmed — refusing to seed. "
              "Set CONFIRMED=True after user verification.")
        sys.exit(1)
    ledger = TradeLedger()
    asof = datetime(2026, 10, 8, tzinfo=timezone.utc)
    for symbol, account, shares, cost in OPENING:
        t = ledger.record_opening_balance(symbol, account, shares, cost, asof,
                                          notes="seeded 2026-10-08 from screenshots")
        print(f"{account:5s} {symbol:8s} {shares:10.4f} sh @ "
              f"{ledger.get_acb_per_share(symbol, account):9.4f}  "
              f"(book {cost:.2f})")
    out = Path(__file__).resolve().parents[1] / "ledger.pkl"
    with open(out, "wb") as f:
        pickle.dump(ledger, f)
    print(f"saved -> {out}")


if __name__ == "__main__":
    main()
