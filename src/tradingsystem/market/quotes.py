"""Shared live-quote helpers.

Finnhub (free tier, US-only) for US symbols; yfinance for TSX (.TO)
symbols and FX. Ledger symbols are never renamed here — QUOTE_ALIASES
only translates for the quote lookup so ACB pools stay intact.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

FINNHUB_HELPER = (
    Path.home() / "workspace/custom-connectors/custom.finnhub/bin/finnhub_api.py"
)

# ledger symbol -> quoting ticker
QUOTE_ALIASES = {
    "XQQ": "XQQ.TO",  # iShares NASDAQ 100 (CAD-hedged), TSX listing
}

FINNHUB_THROTTLE_S = 1.2  # free tier: 60 calls/min


def quote_symbol(symbol: str) -> str:
    return QUOTE_ALIASES.get(symbol, symbol)


def is_cad(symbol: str) -> bool:
    return quote_symbol(symbol).endswith(".TO")


def finnhub_quote(symbol: str) -> dict | None:
    """Current + previous close via the Finnhub connector helper."""
    try:
        out = subprocess.run(
            [sys.executable, str(FINNHUB_HELPER), "/quote", f"symbol={symbol}"],
            capture_output=True,
            text=True,
            timeout=40,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(f"  [warn] finnhub {symbol}: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"  [warn] finnhub {symbol}: {out.stderr.strip()[:100]}", file=sys.stderr)
        return None
    try:
        q = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if not q.get("c"):
        return None
    time.sleep(FINNHUB_THROTTLE_S)
    return {"price": float(q["c"]), "prev_close": float(q.get("pc") or 0.0)}


def yf_quote(symbol: str) -> dict | None:
    """Current + previous close via yfinance (TSX symbols, FX, futures)."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [warn] yfinance not installed", file=sys.stderr)
        return None
    try:
        fi = yf.Ticker(symbol).fast_info
        price = float(fi.last_price)
        prev = float(fi.previous_close)
    except Exception as e:  # noqa: BLE001 - quote failure is non-fatal
        print(f"  [warn] yfinance {symbol}: {e}", file=sys.stderr)
        return None
    if not price:
        return None
    return {"price": price, "prev_close": prev}


def get_quote(symbol: str) -> dict | None:
    """Dispatch to the right provider; returns {price, prev_close} or None."""
    qsym = quote_symbol(symbol)
    if qsym.endswith(".TO"):
        return yf_quote(qsym)
    return finnhub_quote(qsym)


def get_usdcad() -> float | None:
    fx = yf_quote("CAD=X")
    return fx["price"] if fx else None
