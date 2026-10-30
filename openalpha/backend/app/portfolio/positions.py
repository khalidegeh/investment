"""Local multi-account positions: entry price, P/L and signals.

The config file stays on disk and is never sent anywhere. All maths is plain
NumPy/pandas. A holding with no price, or a USD holding with no FX rate, is
shown with a flag and left out of base-currency totals: nothing is assumed.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..errors import InvalidSymbol, PortfolioError
from ..symbols import normalize_symbol
from .signals import Signal, SignalConfig, compute_signal

_ACCOUNT = re.compile(r"[A-Z0-9_]{1,16}")


class Lot(BaseModel):
    symbol: str
    shares: float = Field(gt=0)
    entry_price: float = Field(gt=0)      # per share, in the lot's currency
    entry_date: date
    currency: Literal["CAD", "USD"]
    instrument: Literal["stock", "etf"] = "stock"
    sharia: Literal["compliant", "non_compliant", "questionable", "unchecked"] = "unchecked"

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        try:
            return normalize_symbol(v)
        except InvalidSymbol as exc:
            raise ValueError(str(exc)) from exc


class PortfolioConfig(BaseModel):
    base_currency: Literal["CAD"] = "CAD"
    accounts: dict[str, list[Lot]]

    @field_validator("accounts")
    @classmethod
    def _accounts(cls, v: dict[str, list[Lot]]) -> dict[str, list[Lot]]:
        if not v:
            raise ValueError("at least one account is required")
        for name in v:
            if not _ACCOUNT.fullmatch(name):
                raise ValueError(f"bad account name {name!r} (use A-Z, 0-9, underscore)")
        return v

    def symbols(self) -> list[str]:
        return sorted({lot.symbol for lots in self.accounts.values() for lot in lots})


def load_portfolio(path: str | Path) -> PortfolioConfig:
    p = Path(path)
    try:
        return PortfolioConfig.model_validate_json(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PortfolioError(f"portfolio config not found: {p}") from exc
    except (ValidationError, json.JSONDecodeError) as exc:
        raise PortfolioError(f"invalid portfolio config {p}: {exc}") from exc


@dataclass(frozen=True)
class PositionLine:
    account: str
    symbol: str
    currency: str
    instrument: str
    shares: float
    avg_entry: float
    price: float | None
    prev_close: float | None
    cost: float
    market_value: float | None
    total_pl: float | None
    total_pl_pct: float | None
    day_pl: float | None
    signal: Signal
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountSummary:
    account: str
    lines: tuple[PositionLine, ...]
    cost_base: float
    market_value_base: float
    total_pl_base: float
    day_pl_base: float
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioSnapshot:
    base_currency: str
    usdcad: float | None
    accounts: tuple[AccountSummary, ...]
    cost_base: float = 0.0
    market_value_base: float = 0.0
    total_pl_base: float = 0.0
    day_pl_base: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def lines(self) -> list[PositionLine]:
        return [ln for a in self.accounts for ln in a.lines]


def build_snapshot(
    portfolio: PortfolioConfig,
    closes: Mapping[str, pd.Series],
    usdcad: float | None,
    cfg: SignalConfig | None = None,
) -> PortfolioSnapshot:
    """Compute every line and the per-account and overall totals in CAD."""
    cfg = cfg or SignalConfig()
    summaries: list[AccountSummary] = []
    for account, lots in sorted(portfolio.accounts.items()):
        by_symbol: dict[str, list[Lot]] = {}
        for lot in lots:
            by_symbol.setdefault(lot.symbol, []).append(lot)
        lines: list[PositionLine] = []
        excluded: list[str] = []
        cost_b = mv_b = pl_b = day_b = 0.0
        for symbol, group in sorted(by_symbol.items()):
            if len({g.currency for g in group}) != 1 or len({g.instrument for g in group}) != 1:
                raise PortfolioError(f"{account}/{symbol}: lots mix currencies or instruments")
            shares = sum(g.shares for g in group)
            avg = sum(g.shares * g.entry_price for g in group) / shares
            cur, inst = group[0].currency, group[0].instrument
            sharia_ok = all(g.sharia == "compliant" for g in group)
            series = closes.get(symbol)
            flags: list[str] = []
            price = prev = None
            if series is not None and len(series) > 0:
                price = float(series.iloc[-1])
                prev = float(series.iloc[-2]) if len(series) > 1 else None
            else:
                flags.append("no price data")
            sig = (
                compute_signal(series, avg, cfg, sharia_ok=sharia_ok)
                if series is not None and len(series) > 0
                else Signal("N/A", ("no price data",))
            )
            cost = shares * avg
            mv = None if price is None else shares * price
            pl = None if mv is None else mv - cost
            day = None if price is None or prev is None else (price - prev) * shares
            rate = 1.0 if cur == "CAD" else usdcad
            if rate is None:
                flags.append("no USDCAD rate: excluded from CAD totals")
            if rate is not None and mv is not None and pl is not None:
                cost_b += cost * rate
                mv_b += mv * rate
                pl_b += pl * rate
                day_b += (day or 0.0) * rate
            else:
                excluded.append(symbol)
            lines.append(
                PositionLine(
                    account, symbol, cur, inst, shares, avg, price, prev, cost, mv, pl,
                    None if pl is None else pl / cost, day, sig, tuple(flags),
                )
            )
        summaries.append(
            AccountSummary(account, tuple(lines), cost_b, mv_b, pl_b, day_b, tuple(excluded))
        )
    return PortfolioSnapshot(
        portfolio.base_currency,
        usdcad,
        tuple(summaries),
        sum(a.cost_base for a in summaries),
        sum(a.market_value_base for a in summaries),
        sum(a.total_pl_base for a in summaries),
        sum(a.day_pl_base for a in summaries),
    )


def rolling_pl(close: pd.Series, shares: float, entry_price: float) -> pd.Series:
    """P/L over time for one line, in the line's currency: (close - entry) x shares."""
    if shares <= 0 or entry_price <= 0:
        raise ValueError("shares and entry_price must be positive")
    return (close - entry_price) * shares
