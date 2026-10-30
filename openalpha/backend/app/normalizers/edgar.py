"""SEC XBRL company facts to the unified schema (US-GAAP filers).

Concepts are tried in priority order **per period**, so a company that
switched tags mid-history (for example Revenues to
RevenueFromContractWithCustomerExcludingAssessedTax) still yields a continuous
series. Components are never summed: if the aggregate tag is absent the field
stays NaN. Coverage is whatever the filer's XBRL history contains.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..errors import DataError
from ..models import BALANCE_FIELDS, CASHFLOW_FIELDS, INCOME_FIELDS, StatementSet
from ..sources.edgar import EdgarClient
from .base import assemble, derive

ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT", "20-F", "40-F")

# field -> (statement, is_duration, unit, us-gaap tags in priority order)
_Spec = tuple[str, bool, str, tuple[str, ...]]
MAPPING: dict[str, _Spec] = {
    "revenue": ("income", True, "USD", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    )),
    "cost_of_goods_sold": ("income", True, "USD", (
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold", "CostOfServices",
    )),
    "gross_profit": ("income", True, "USD", ("GrossProfit",)),
    "rnd_expenses": ("income", True, "USD", (
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    )),
    "sga_expenses": ("income", True, "USD", ("SellingGeneralAndAdministrativeExpense",)),
    "operating_income": ("income", True, "USD", ("OperatingIncomeLoss",)),
    "net_income": ("income", True, "USD", ("NetIncomeLoss", "ProfitLoss")),
    "diluted_eps": ("income", True, "USD/shares", ("EarningsPerShareDiluted",)),
    "cash_and_equivalents": ("balance", False, "USD", (
        "CashAndCashEquivalentsAtCarryingValue", "Cash",
    )),
    "short_term_investments": ("balance", False, "USD", (
        "ShortTermInvestments", "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    )),
    "total_assets": ("balance", False, "USD", ("Assets",)),
    "short_term_debt": ("balance", False, "USD", (
        "DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings",
    )),
    "long_term_debt": ("balance", False, "USD", (
        "LongTermDebtNoncurrent", "LongTermNotesPayable",
    )),
    "total_liabilities": ("balance", False, "USD", ("Liabilities",)),
    "shareholder_equity": ("balance", False, "USD", (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    )),
    "operating_cash_flow": ("cashflow", True, "USD", (
        "NetCashProvidedByUsedInOperatingActivities",
    )),
    "capex": ("cashflow", True, "USD", (
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    )),
}


def _annual_values(node: object, duration: bool, unit: str) -> dict[date, float]:
    """Annual values for one tag, keyed by period end; latest filing wins."""
    if not isinstance(node, dict):
        return {}
    units = node.get("units")
    entries = units.get(unit) if isinstance(units, dict) else None
    if not isinstance(entries, list):
        return {}
    best: dict[date, tuple[str, float]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        val, end, filed = e.get("val"), e.get("end"), str(e.get("filed", ""))
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not isinstance(end, str):
            continue
        if e.get("fp") != "FY" or e.get("form") not in ANNUAL_FORMS:
            continue
        try:
            end_d = date.fromisoformat(end)
            if duration:
                start = e.get("start")
                if not isinstance(start, str):
                    continue
                if not 340 <= (end_d - date.fromisoformat(start)).days <= 380:
                    continue
        except ValueError:
            continue
        if end_d not in best or filed >= best[end_d][0]:
            best[end_d] = (filed, float(val))
    return {d: v for d, (_, v) in best.items()}


def normalize_company_facts(symbol: str, facts_payload: dict[str, object]) -> StatementSet:
    facts = facts_payload.get("facts")
    gaap = facts.get("us-gaap") if isinstance(facts, dict) else None
    if not isinstance(gaap, dict) or not gaap:
        raise DataError(f"edgar: {symbol} has no us-gaap facts (IFRS or foreign filer?)")

    rows: dict[str, dict[date, dict[str, float]]] = {"income": {}, "balance": {}, "cashflow": {}}
    for field, (kind, duration, unit, tags) in MAPPING.items():
        for tag in tags:
            for end, val in _annual_values(gaap.get(tag), duration, unit).items():
                rows[kind].setdefault(end, {}).setdefault(field, val)

    if not any(rows.values()):
        raise DataError(f"edgar: no annual statement data for {symbol}")
    notes: list[str] = []
    income = assemble(rows["income"], INCOME_FIELDS)
    balance = assemble(rows["balance"], BALANCE_FIELDS)
    cashflow = assemble(rows["cashflow"], CASHFLOW_FIELDS)
    income, cashflow = derive(income, cashflow, notes)
    first = min(df.index.min() for df in (income, balance, cashflow) if not df.empty)
    notes.append(f"edgar coverage starts {pd.Timestamp(first).date()} (XBRL era)")
    return StatementSet(symbol, "edgar", income, balance, cashflow, tuple(notes))


class EdgarNormalizer:
    def __init__(self, client: EdgarClient):
        self._client = client

    async def fetch(self, symbol: str) -> StatementSet:
        facts = await self._client.company_facts(symbol)
        return normalize_company_facts(symbol, facts)
