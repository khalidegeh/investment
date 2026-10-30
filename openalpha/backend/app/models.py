"""Unified statement schema shared by every normalizer.

Statements are DataFrames indexed by fiscal period end (ascending) with exactly
the columns below. A value the source does not provide is NaN: nothing is
guessed, padded or interpolated. Capex is stored as a positive outflow.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

INCOME_FIELDS: tuple[str, ...] = (
    "revenue",
    "cost_of_goods_sold",
    "gross_profit",
    "rnd_expenses",
    "sga_expenses",
    "operating_income",
    "net_income",
    "diluted_eps",
)
BALANCE_FIELDS: tuple[str, ...] = (
    "cash_and_equivalents",
    "short_term_investments",
    "total_assets",
    "short_term_debt",
    "long_term_debt",
    "total_liabilities",
    "shareholder_equity",
)
CASHFLOW_FIELDS: tuple[str, ...] = (
    "operating_cash_flow",
    "capex",
    "free_cash_flow",
)
FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "income": INCOME_FIELDS,
    "balance": BALANCE_FIELDS,
    "cashflow": CASHFLOW_FIELDS,
}
STATEMENT_KINDS: tuple[str, ...] = tuple(FIELDS_BY_KIND)


@dataclass(frozen=True, eq=False)
class StatementSet:
    symbol: str
    source: str                         # "edgar" | "yfinance"
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    notes: tuple[str, ...] = ()

    def frame(self, kind: str) -> pd.DataFrame:
        if kind == "income":
            return self.income
        if kind == "balance":
            return self.balance
        if kind == "cashflow":
            return self.cashflow
        raise ValueError(f"unknown statement kind {kind!r}")
