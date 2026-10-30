"""Shared normalizer helpers: assembling frames, derived fields, YoY growth."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

import pandas as pd

from ..models import StatementSet


class StatementNormalizer(Protocol):
    async def fetch(self, symbol: str) -> StatementSet: ...


def assemble(rows: Mapping[date, Mapping[str, float]], fields: tuple[str, ...]) -> pd.DataFrame:
    """Build an ascending period-end frame with exactly ``fields`` as columns."""
    df = pd.DataFrame(
        [dict(r) for r in rows.values()],
        index=pd.DatetimeIndex(pd.to_datetime(list(rows.keys())), name="period_end"),
        columns=list(fields),
        dtype="float64",
    )
    out: pd.DataFrame = df.sort_index()
    return out


def derive(
    income: pd.DataFrame, cashflow: pd.DataFrame, notes: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fill gross profit when absent and always define FCF as OCF minus capex."""
    inc = income.copy()
    gp = inc["revenue"] - inc["cost_of_goods_sold"]
    missing = inc["gross_profit"].isna() & gp.notna()
    if bool(missing.any()):
        inc.loc[missing, "gross_profit"] = gp[missing]
        notes.append("gross_profit derived as revenue - cost_of_goods_sold where not reported")
    cf = cashflow.copy()
    cf["free_cash_flow"] = cf["operating_cash_flow"] - cf["capex"]
    return inc, cf


def yoy_growth(df: pd.DataFrame) -> pd.DataFrame:
    """Year-over-year growth as a fraction (0.10 = +10%).

    NaN when the prior value is missing or not positive: a percentage change
    off a zero or negative base is meaningless and would mislead.
    """
    prev = df.shift(1)
    growth = df / prev - 1.0
    out: pd.DataFrame = growth.where(prev > 0)
    return out
