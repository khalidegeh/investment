"""Quarterly figures, checked against real SEC company facts for Apple.

The fixtures come from trader-skill (MIT, see NOTICE.md). That repo documents
the latest quarter as revenue +16.4%, net income +27.1% and EPS +28.7%; the
numbers asserted below were verified against that independent source.
"""
import json
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.normalizers import normalize_company_facts
from app.normalizers.base import yoy_growth_quarterly
from app.normalizers.quarterly import normalize_quarterly
from app.portfolio import assess_thesis_quarterly
from app.portfolio.thesis import BROKEN, INTACT, UNKNOWN, WATCH

FIX = Path(__file__).parent / "fixtures" / "trader_skill"
FACTS = json.loads((FIX / "companyfacts-CIK0000320193.json").read_text())


def quarterly() -> pd.DataFrame:
    return normalize_quarterly(FACTS["facts"]["us-gaap"])


class AppleQuarterlyTests(unittest.TestCase):
    def test_shape_and_ytd_values_are_ignored(self):
        q = quarterly()
        self.assertEqual(len(q), 11)
        self.assertTrue(q.index.is_monotonic_increasing)
        self.assertNotIn(296.105e9, q["revenue"].to_list())      # nine-month YTD figure
        self.assertNotIn(210.328e9, q["revenue"].to_list())      # six-month YTD figure

    def test_latest_quarter_matches_the_filing(self):
        last = quarterly().iloc[-1]
        self.assertEqual(quarterly().index[-1], pd.Timestamp("2026-06-27"))
        self.assertEqual(last["revenue"], 109_417_000_000)
        self.assertEqual(last["net_income"], 29_789_000_000)
        self.assertEqual(last["diluted_eps"], 2.02)
        self.assertFalse(bool(last["derived"]))

    def test_growth_matches_the_independent_source(self):
        g = yoy_growth_quarterly(quarterly()[["revenue", "net_income", "diluted_eps"]]).iloc[-1]
        self.assertAlmostEqual(g["revenue"] * 100, 16.4, delta=0.05)
        self.assertAlmostEqual(g["net_income"] * 100, 27.1, delta=0.05)
        self.assertAlmostEqual(g["diluted_eps"] * 100, 28.7, delta=0.05)

    def test_fourth_quarter_is_derived_and_flagged(self):
        q = quarterly()
        self.assertEqual(q.index[q["derived"]].tolist(),
                         [pd.Timestamp("2024-09-28"), pd.Timestamp("2025-09-27")])
        # FY2024 revenue 391.035 minus Q1..Q3 (119.575 + 90.753 + 85.777) = 94.930
        self.assertAlmostEqual(q.loc["2024-09-28", "revenue"] / 1e9, 94.930, places=3)
        self.assertAlmostEqual(q.loc["2025-09-27", "revenue"] / 1e9, 102.466, places=3)

    def test_one_off_quarter_distorts_next_years_growth(self):
        # Q4 FY2024 EPS was depressed by a one-time charge, so Q4 FY2025 growth is a base effect.
        q = quarterly()
        self.assertAlmostEqual(q.loc["2024-09-28", "diluted_eps"], 0.97, places=2)
        g = yoy_growth_quarterly(q[["diluted_eps"]])
        self.assertGreater(g.loc["2025-09-27", "diluted_eps"], 0.8)

    def test_thesis_on_real_quarters_is_intact_with_both_metrics(self):
        r = assess_thesis_quarterly(quarterly())
        self.assertEqual((r.status, r.basis, r.as_of), (INTACT, "quarterly", "2026-06-27"))
        self.assertAlmostEqual(r.revenue_growth[-1], 0.1637, places=3)

    def test_company_facts_attach_quarterly_to_the_statement_set(self):
        s = normalize_company_facts("AAPL", FACTS)
        self.assertIsNotNone(s.quarterly)
        self.assertEqual(len(s.quarterly), 11)             # type: ignore[arg-type]
        self.assertAlmostEqual(s.income.loc["2025-09-27", "revenue"] / 1e9, 416.161, places=3)


class MatchingTests(unittest.TestCase):
    def test_prior_year_matched_across_52_and_53_week_years(self):
        idx = pd.DatetimeIndex(["2024-12-28", "2025-12-27", "2027-01-02", "2028-03-31"])
        df = pd.DataFrame({"x": [100.0, 110.0, 99.0, 50.0]}, index=idx)
        g = yoy_growth_quarterly(df)["x"]
        self.assertTrue(np.isnan(g.iloc[0]))
        self.assertAlmostEqual(g.iloc[1], 0.10)            # 364 days apart
        self.assertAlmostEqual(g.iloc[2], -0.10)           # 371 days apart: a 53-week year
        self.assertTrue(np.isnan(g.iloc[3]))               # 454 days after the last: no prior year

    def test_zero_or_negative_prior_gives_nan(self):
        idx = pd.DatetimeIndex(["2024-12-28", "2025-12-27"])
        df = pd.DataFrame({"x": [-5.0, 10.0]}, index=idx)
        self.assertTrue(np.isnan(yoy_growth_quarterly(df)["x"].iloc[1]))

    def test_no_quarterly_data_raises(self):
        from app.errors import DataError

        with self.assertRaises(DataError):
            normalize_quarterly({"Assets": {"units": {"USD": []}}})


def series(rev: list[float], eps: list[float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        pd.date_range("2023-03-31", periods=len(rev), freq="QE"), name="period_end"
    )
    return pd.DataFrame({"revenue": rev, "diluted_eps": eps}, index=idx)


class QuarterlyThesisTests(unittest.TestCase):
    def test_both_falling_two_quarters_is_broken(self):
        rev = [100.0, 100.0, 100.0, 100.0, 95.0, 94.0, 93.0, 92.0]
        eps = [1.0, 1.0, 1.0, 1.0, 0.9, 0.9, 0.9, 0.9]
        self.assertEqual(assess_thesis_quarterly(series(rev, eps)).status, BROKEN)

    def test_only_revenue_falling_is_watch(self):
        rev = [100.0, 100.0, 100.0, 100.0, 95.0, 94.0, 93.0, 92.0]
        eps = [1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 1.2, 1.2]
        self.assertEqual(assess_thesis_quarterly(series(rev, eps)).status, WATCH)

    def test_too_few_quarters_is_unknown(self):
        self.assertEqual(assess_thesis_quarterly(series([1.0] * 6, [1.0] * 6)).status, UNKNOWN)

    def test_date_gap_in_quarters_does_not_fabricate_growth(self):
        q = series([100.0, 100.0, 100.0, 100.0, 110.0, 110.0, 110.0, 110.0], [1.0] * 8)
        q = q.drop(q.index[5])                              # a missing quarter
        r = assess_thesis_quarterly(q)
        self.assertEqual(len(r.revenue_growth), 3)          # only matched quarters count


if __name__ == "__main__":
    unittest.main()
    _ = date
