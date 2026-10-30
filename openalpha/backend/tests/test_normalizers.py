"""EDGAR and yfinance normalisation, derived fields, YoY and the failover service."""
import unittest

import numpy as np
import pandas as pd

from app.cache import SqliteCache
from app.errors import DataError, FetchError
from app.models import BALANCE_FIELDS, CASHFLOW_FIELDS, INCOME_FIELDS
from app.normalizers import (
    StatementService,
    YFinanceNormalizer,
    normalize_company_facts,
    normalize_frames,
    yoy_growth,
)
from app.normalizers.base import derive


def fact(end: str, val: float, start: str | None, filed: str, fp: str = "FY",
         form: str = "10-K") -> dict[str, object]:
    d: dict[str, object] = {"end": end, "val": val, "filed": filed, "fp": fp, "form": form}
    if start:
        d["start"] = start
    return d


def facts_payload() -> dict[str, object]:
    return {"facts": {"us-gaap": {
        # Tag switch: old tag for FY2019, new tag for FY2020 and FY2021.
        "SalesRevenueNet": {"units": {"USD": [
            fact("2019-12-31", 900.0, "2019-01-01", "2020-02-01")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            fact("2020-12-31", 1000.0, "2020-01-01", "2021-02-01"),
            fact("2021-12-31", 1200.0, "2021-01-01", "2022-02-01"),
            # Restatement of FY2021 filed later: the later filing must win.
            fact("2021-12-31", 1210.0, "2021-01-01", "2023-02-01"),
            # A quarter, a short duration and a 10-Q must all be ignored.
            fact("2021-09-30", 300.0, "2021-07-01", "2021-11-01", fp="Q3", form="10-Q"),
            fact("2021-12-31", 5.0, "2021-10-01", "2022-02-01"),
        ]}},
        "CostOfRevenue": {"units": {"USD": [
            fact("2020-12-31", 600.0, "2020-01-01", "2021-02-01"),
            fact("2021-12-31", 700.0, "2021-01-01", "2022-02-01")]}},
        "NetIncomeLoss": {"units": {"USD": [
            fact("2020-12-31", 100.0, "2020-01-01", "2021-02-01"),
            fact("2021-12-31", 150.0, "2021-01-01", "2022-02-01")]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            fact("2021-12-31", 1.5, "2021-01-01", "2022-02-01")]}},
        "Assets": {"units": {"USD": [
            fact("2020-12-31", 5000.0, None, "2021-02-01"),
            fact("2021-12-31", 6000.0, None, "2022-02-01")]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [
            fact("2021-12-31", 800.0, None, "2022-02-01")]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            fact("2020-12-31", 200.0, "2020-01-01", "2021-02-01"),
            fact("2021-12-31", 260.0, "2021-01-01", "2022-02-01")]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            fact("2020-12-31", 50.0, "2020-01-01", "2021-02-01"),
            fact("2021-12-31", 60.0, "2021-01-01", "2022-02-01")]}},
    }}}


class EdgarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = normalize_company_facts("TEST", facts_payload())

    def test_schema_is_exact(self):
        self.assertEqual(tuple(self.s.income.columns), INCOME_FIELDS)
        self.assertEqual(tuple(self.s.balance.columns), BALANCE_FIELDS)
        self.assertEqual(tuple(self.s.cashflow.columns), CASHFLOW_FIELDS)
        self.assertTrue(self.s.income.index.is_monotonic_increasing)
        self.assertEqual(self.s.source, "edgar")

    def test_tag_switch_is_continuous_and_restatement_wins(self):
        rev = self.s.income["revenue"]
        self.assertEqual(rev.loc["2019-12-31"], 900.0)     # old tag
        self.assertEqual(rev.loc["2020-12-31"], 1000.0)    # new tag
        self.assertEqual(rev.loc["2021-12-31"], 1210.0)    # later filing wins

    def test_quarters_and_short_durations_ignored(self):
        self.assertEqual(len(self.s.income), 3)
        self.assertNotIn(pd.Timestamp("2021-09-30"), self.s.income.index)

    def test_derived_fields(self):
        gp = self.s.income["gross_profit"]
        self.assertEqual(gp.loc["2021-12-31"], 1210.0 - 700.0)
        self.assertTrue(np.isnan(gp.loc["2019-12-31"]))    # no COGS that year: not guessed
        fcf = self.s.cashflow["free_cash_flow"]
        self.assertEqual(fcf.loc["2021-12-31"], 200.0)
        self.assertTrue(any("gross_profit derived" in n for n in self.s.notes))

    def test_missing_fields_are_nan(self):
        self.assertTrue(self.s.income["rnd_expenses"].isna().all())
        self.assertEqual(self.s.balance["long_term_debt"].loc["2021-12-31"], 800.0)
        self.assertEqual(self.s.income["diluted_eps"].loc["2021-12-31"], 1.5)

    def test_rejects_non_gaap_and_empty(self):
        with self.assertRaises(DataError):
            normalize_company_facts("X", {"facts": {"ifrs-full": {"Revenue": {}}}})
        with self.assertRaises(DataError):
            normalize_company_facts("X", {"facts": {"us-gaap": {"Assets": {"units": {"USD": []}}}}})
        with self.assertRaises(DataError):
            normalize_company_facts("X", {})


def yf_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cols = [pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]   # newest first, as yfinance
    income = pd.DataFrame(
        {cols[0]: [2000.0, 1100.0, 90.0, 300.0, 5.2], cols[1]: [1500.0, 900.0, 80.0, 250.0, 4.1]},
        index=["Total Revenue", "Cost Of Revenue", "Research And Development",
               "Net Income", "Diluted EPS"])
    balance = pd.DataFrame(
        {cols[0]: [9000.0, 3000.0], cols[1]: [8000.0, np.nan]},
        index=["Total Assets", "Long Term Debt"])
    cashflow = pd.DataFrame(
        {cols[0]: [500.0, -120.0], cols[1]: [400.0, -100.0]},
        index=["Operating Cash Flow", "Capital Expenditure"])
    return income, balance, cashflow


class YFinanceTests(unittest.IsolatedAsyncioTestCase):
    def test_label_mapping_sign_and_derivation(self):
        s = normalize_frames("SHOP.TO", *yf_frames())
        self.assertEqual(s.source, "yfinance")
        self.assertTrue(s.income.index.is_monotonic_increasing)      # oldest first
        self.assertEqual(s.income["revenue"].loc["2024-12-31"], 2000.0)
        self.assertEqual(s.income["gross_profit"].loc["2024-12-31"], 900.0)
        self.assertEqual(s.cashflow["capex"].loc["2024-12-31"], 120.0)   # outflow made positive
        self.assertEqual(s.cashflow["free_cash_flow"].loc["2024-12-31"], 380.0)
        self.assertTrue(np.isnan(s.balance["long_term_debt"].loc["2023-12-31"]))
        self.assertTrue(s.income["sga_expenses"].isna().all())

    def test_label_matching_ignores_case_and_punctuation(self):
        inc = pd.DataFrame({pd.Timestamp("2024-12-31"): [1.0]}, index=["total  revenue"])
        s = normalize_frames("X", inc, pd.DataFrame(), pd.DataFrame())
        self.assertEqual(s.income["revenue"].iloc[0], 1.0)

    def test_empty_frames_rejected(self):
        with self.assertRaises(DataError):
            normalize_frames("X", pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    async def test_loader_result_is_cached(self):
        calls: list[str] = []

        def loader(sym: str) -> dict[str, pd.DataFrame]:
            calls.append(sym)
            i, b, c = yf_frames()
            return {"income": i, "balance": b, "cashflow": c}

        cache = SqliteCache(":memory:")
        yf = YFinanceNormalizer(cache, loader)
        first = await yf.fetch("shop.to")
        second = await yf.fetch("SHOP.TO")
        self.assertEqual(calls, ["SHOP.TO"])
        self.assertEqual(float(second.income["revenue"].loc["2024-12-31"]), 2000.0)
        self.assertTrue(first.cashflow["free_cash_flow"].equals(second.cashflow["free_cash_flow"]))
        cache.close()

    async def test_empty_result_not_cached(self):
        n = [0]

        def loader(sym: str) -> dict[str, pd.DataFrame]:
            n[0] += 1
            e = pd.DataFrame()
            return {"income": e, "balance": e, "cashflow": e}

        cache = SqliteCache(":memory:")
        yf = YFinanceNormalizer(cache, loader)
        for _ in range(2):
            with self.assertRaises(DataError):
                await yf.fetch("ZZZ")
        self.assertEqual(n[0], 2)
        cache.close()


class FakeEdgar:
    def __init__(self, exc: Exception | None = None):
        self.exc, self.calls = exc, 0

    async def fetch(self, symbol: str):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.exc:
            raise self.exc
        return normalize_company_facts(symbol, facts_payload())


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cache = SqliteCache(":memory:")
        self.yf = YFinanceNormalizer(self.cache, lambda s: dict(zip(
            ("income", "balance", "cashflow"), yf_frames(), strict=True)))

    def tearDown(self) -> None:
        self.cache.close()

    async def test_us_symbol_uses_edgar(self):
        edgar = FakeEdgar()
        s = await StatementService(edgar, self.yf).get("AAPL")  # type: ignore[arg-type]
        self.assertEqual((s.source, edgar.calls), ("edgar", 1))

    async def test_canadian_symbol_skips_edgar(self):
        edgar = FakeEdgar()
        s = await StatementService(edgar, self.yf).get("SHOP.TO")  # type: ignore[arg-type]
        self.assertEqual((s.source, edgar.calls), ("yfinance", 0))
        self.assertTrue(any("non-US listing" in n for n in s.notes))

    async def test_edgar_data_error_falls_back_with_reason(self):
        edgar = FakeEdgar(DataError("no us-gaap facts"))
        s = await StatementService(edgar, self.yf).get("ON")  # type: ignore[arg-type]
        self.assertEqual(s.source, "yfinance")
        self.assertTrue(any("no us-gaap facts" in n for n in s.notes))

    async def test_edgar_outage_falls_back(self):
        edgar = FakeEdgar(FetchError("HTTP 503"))
        s = await StatementService(edgar, self.yf).get("NVDA")  # type: ignore[arg-type]
        self.assertEqual(s.source, "yfinance")

    async def test_no_edgar_configured(self):
        s = await StatementService(None, self.yf).get("AAPL")
        self.assertEqual(s.source, "yfinance")
        self.assertTrue(any("OPENALPHA_CONTACT" in n for n in s.notes))


class YoyTests(unittest.TestCase):
    def test_growth_and_guardrails(self):
        df = pd.DataFrame({"a": [100.0, 110.0, -5.0, 10.0, np.nan, 7.0],
                           "b": [0.0, 5.0, 6.0, 3.0, 2.0, 1.0]})
        g = yoy_growth(df)
        self.assertTrue(np.isnan(g["a"].iloc[0]))
        self.assertAlmostEqual(g["a"].iloc[1], 0.10)
        self.assertAlmostEqual(g["a"].iloc[2], -5.0 / 110.0 - 1.0)
        self.assertTrue(np.isnan(g["a"].iloc[3]))      # prior value negative
        self.assertTrue(np.isnan(g["a"].iloc[4]))      # missing
        self.assertTrue(np.isnan(g["a"].iloc[5]))      # prior missing
        self.assertTrue(np.isnan(g["b"].iloc[1]))      # prior zero
        self.assertAlmostEqual(g["b"].iloc[2], 0.2)

    def test_derive_keeps_reported_gross_profit(self):
        inc = pd.DataFrame({"revenue": [10.0], "cost_of_goods_sold": [4.0], "gross_profit": [7.0]})
        cf = pd.DataFrame(
            {"operating_cash_flow": [5.0], "capex": [2.0], "free_cash_flow": [np.nan]}
        )
        i2, c2 = derive(inc, cf, [])
        self.assertEqual(i2["gross_profit"].iloc[0], 7.0)
        self.assertEqual(c2["free_cash_flow"].iloc[0], 3.0)


if __name__ == "__main__":
    unittest.main()
