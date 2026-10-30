"""yfinance statement frames to the unified schema (fallback for non-US filers).

yfinance returns frames with line items as rows and fiscal period ends as
columns. Row labels vary across versions, so labels are matched after
normalising case and punctuation, with explicit alternates. yfinance manages
its own HTTP, so results are cached here in the shared SQLite cache.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import re
from collections.abc import Callable
from datetime import date
from io import StringIO
from typing import Protocol, cast

import pandas as pd

from ..cache import SqliteCache, make_key
from ..errors import DataError
from ..models import BALANCE_FIELDS, CASHFLOW_FIELDS, INCOME_FIELDS, StatementSet
from ..symbols import normalize_symbol
from .base import assemble, derive

DAY = 86_400.0

LABELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": ("income", ("Total Revenue", "Operating Revenue", "Revenue")),
    "cost_of_goods_sold": ("income", ("Cost Of Revenue", "Reconciled Cost Of Revenue")),
    "gross_profit": ("income", ("Gross Profit",)),
    "rnd_expenses": ("income", ("Research And Development", "Research Development")),
    "sga_expenses": ("income", (
        "Selling General And Administration", "Selling General Administrative",
    )),
    "operating_income": ("income", ("Operating Income", "Total Operating Income As Reported")),
    "net_income": ("income", (
        "Net Income", "Net Income Common Stockholders",
        "Net Income From Continuing Operation Net Minority Interest",
    )),
    "diluted_eps": ("income", ("Diluted EPS",)),
    "cash_and_equivalents": ("balance", (
        "Cash And Cash Equivalents", "Cash Cash Equivalents And Federal Funds Sold",
    )),
    "short_term_investments": ("balance", (
        "Other Short Term Investments", "Short Term Investments", "Available For Sale Securities",
    )),
    "total_assets": ("balance", ("Total Assets",)),
    "short_term_debt": ("balance", ("Current Debt", "Current Debt And Capital Lease Obligation")),
    "long_term_debt": ("balance", ("Long Term Debt", "Long Term Debt And Capital Lease Obligation")),
    "total_liabilities": ("balance", ("Total Liabilities Net Minority Interest", "Total Liabilities")),
    "shareholder_equity": ("balance", (
        "Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest",
    )),
    "operating_cash_flow": ("cashflow", (
        "Operating Cash Flow", "Cash Flow From Continuing Operating Activities",
    )),
    "capex": ("cashflow", ("Capital Expenditure", "Purchase Of PPE")),
}

FrameLoader = Callable[[str], dict[str, pd.DataFrame]]


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _pick(frame: pd.DataFrame, alternates: tuple[str, ...]) -> pd.Series | None:
    by_label = {_key(str(i)): i for i in frame.index}
    for alt in alternates:
        hit = by_label.get(_key(alt))
        if hit is not None:
            row = frame.loc[hit]
            return row.iloc[0] if isinstance(row, pd.DataFrame) else row
    return None


def normalize_frames(
    symbol: str, income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame
) -> StatementSet:
    raw = {"income": income, "balance": balance, "cashflow": cashflow}
    if all(f.empty for f in raw.values()):
        raise DataError(f"yfinance: no statements for {symbol}")
    rows: dict[str, dict[date, dict[str, float]]] = {"income": {}, "balance": {}, "cashflow": {}}
    for field, (kind, alternates) in LABELS.items():
        series = _pick(raw[kind], alternates)
        if series is None:
            continue
        for col, val in series.items():
            num = pd.to_numeric(val, errors="coerce")
            if pd.isna(num):
                continue
            num = abs(float(num)) if field == "capex" else float(num)
            rows[kind].setdefault(pd.Timestamp(str(col)).date(), {})[field] = num
    notes = ["yfinance fallback: typically only the last 4 fiscal years"]
    inc = assemble(rows["income"], INCOME_FIELDS)
    bal = assemble(rows["balance"], BALANCE_FIELDS)
    cfl = assemble(rows["cashflow"], CASHFLOW_FIELDS)
    inc, cfl = derive(inc, cfl, notes)
    return StatementSet(symbol, "yfinance", inc, bal, cfl, tuple(notes))


class _TickerLike(Protocol):
    income_stmt: pd.DataFrame
    balance_sheet: pd.DataFrame
    cashflow: pd.DataFrame


def load_with_yfinance(symbol: str) -> dict[str, pd.DataFrame]:
    """Blocking loader; run in a worker thread. yfinance is imported lazily."""
    try:
        module = importlib.import_module("yfinance")
    except ImportError as exc:
        raise DataError("yfinance is not installed (pip install 'openalpha[data]')") from exc
    t = cast(_TickerLike, module.Ticker(symbol))
    return {"income": t.income_stmt, "balance": t.balance_sheet, "cashflow": t.cashflow}


def _encode(frames: dict[str, pd.DataFrame]) -> bytes:
    return json.dumps({k: v.to_json(orient="split", date_format="iso") for k, v in frames.items()}).encode()


def _decode(body: bytes) -> dict[str, pd.DataFrame]:
    blobs = json.loads(body)
    return {k: pd.read_json(StringIO(v), orient="split", convert_axes=False) for k, v in blobs.items()}


class YFinanceNormalizer:
    def __init__(
        self, cache: SqliteCache, loader: FrameLoader = load_with_yfinance, ttl: float = DAY
    ):
        self._cache, self._loader, self._ttl = cache, loader, ttl

    async def fetch(self, symbol: str) -> StatementSet:
        sym = normalize_symbol(symbol)
        key = make_key("YF", f"yfinance://{sym}/annual", {})
        hit = await asyncio.to_thread(self._cache.get, key, self._ttl)
        if hit is not None:
            frames = _decode(hit.body)
        else:
            frames = await asyncio.to_thread(self._loader, sym)
            if all(f.empty for f in frames.values()):
                raise DataError(f"yfinance: no statements for {sym}")   # empty is never cached
            await asyncio.to_thread(self._cache.put, key, 200, _encode(frames))
        return normalize_frames(sym, frames["income"], frames["balance"], frames["cashflow"])
