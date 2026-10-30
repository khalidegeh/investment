"""Thesis check: revenue and EPS, price ignored, thin or stale history returns UNKNOWN."""
import unittest

import numpy as np
import pandas as pd

from app.portfolio import ThesisConfig, assess_thesis, fund_result
from app.portfolio.thesis import BROKEN, INTACT, NOT_APPLICABLE, UNKNOWN, WATCH


def income(revenues: list[float], eps: list[float] | None = None) -> pd.DataFrame:
    n = len(revenues)
    idx = pd.DatetimeIndex(pd.date_range("2018-12-31", periods=n, freq="YE"), name="period_end")
    data: dict[str, list[float]] = {"revenue": revenues}
    if eps is not None:
        data["diluted_eps"] = eps
    return pd.DataFrame(data, index=idx)


class ThesisTests(unittest.TestCase):
    def test_steady_growth_is_intact(self):
        r = assess_thesis(income([100.0, 120.0, 150.0, 190.0], [1.0, 1.2, 1.5, 1.9]))
        self.assertEqual(r.status, INTACT)
        self.assertAlmostEqual(r.revenue_growth[-1], 190.0 / 150.0 - 1.0)
        self.assertAlmostEqual(r.eps_growth[-1], 1.9 / 1.5 - 1.0)
        self.assertEqual(r.as_of, "2021-12-31")

    def test_broken_needs_both_revenue_and_eps_to_fail(self):
        both = assess_thesis(income([100.0, 120.0, 118.0, 110.0], [1.0, 1.2, 1.15, 1.1]))
        self.assertEqual(both.status, BROKEN)
        self.assertTrue(any("2 straight periods" in x for x in both.reasons))

    def test_one_metric_failing_is_watch_not_broken(self):
        rev_only = assess_thesis(income([100.0, 120.0, 118.0, 110.0], [1.0, 1.2, 1.5, 1.9]))
        self.assertEqual(rev_only.status, WATCH)
        self.assertTrue(any("BROKEN needs both" in x for x in rev_only.reasons))
        eps_only = assess_thesis(income([100.0, 120.0, 150.0, 190.0], [1.0, 1.2, 1.15, 1.1]))
        self.assertEqual(eps_only.status, WATCH)

    def test_revenue_alone_failing_with_eps_missing_cannot_be_broken(self):
        r = assess_thesis(income([100.0, 120.0, 118.0, 110.0]))
        self.assertEqual(r.status, WATCH)
        self.assertTrue(any("only one of revenue and EPS" in x for x in r.reasons))

    def test_single_decline_is_watch(self):
        r = assess_thesis(income([100.0, 120.0, 150.0, 140.0], [1.0, 1.2, 1.5, 1.6]))
        self.assertEqual(r.status, WATCH)
        self.assertTrue(any("non-positive" in x for x in r.reasons))

    def test_two_slowdowns_in_a_row_is_watch(self):
        r = assess_thesis(income([100.0, 150.0, 195.0, 214.5], [1.0, 1.5, 1.95, 2.1]))
        self.assertEqual(r.status, WATCH)           # revenue +50%, +30%, +10%
        self.assertTrue(any("slowed 2 periods" in x for x in r.reasons))

    def test_one_slowdown_alone_is_intact(self):
        self.assertEqual(
            assess_thesis(income([100.0, 150.0, 195.0], [1.0, 1.5, 1.95])).status, INTACT
        )

    def test_peer_gap_triggers_watch(self):
        base = income([100.0, 120.0, 150.0], [1.0, 1.2, 1.5])          # latest +25%
        self.assertEqual(assess_thesis(base, peer_growth=0.27).status, INTACT)
        r = assess_thesis(base, peer_growth=0.40)
        self.assertEqual(r.status, WATCH)
        self.assertTrue(any("vs peers" in x for x in r.reasons))

    def test_short_or_missing_history_is_unknown(self):
        for df in (income([100.0, 120.0]), income([100.0]), pd.DataFrame({"revenue": []}),
                   pd.DataFrame({"other": [1.0]})):
            self.assertEqual(assess_thesis(df).status, UNKNOWN)

    def test_gaps_and_zero_base_do_not_invent_growth(self):
        r = assess_thesis(income([0.0, 50.0, np.nan, 80.0, 100.0]))
        self.assertEqual(r.revenue_growth, (0.25,))
        self.assertEqual(r.status, UNKNOWN)
        self.assertEqual(r.as_of, "2022-12-31")

    def test_stale_metric_is_unknown_not_carried_forward(self):
        # Revenue has no figure for the latest year; its older readings must not be used.
        r = assess_thesis(income([100.0, 120.0, 118.0, np.nan], [1.0, 1.2, 1.5, 1.9]))
        self.assertTrue(any("no comparable prior year" in x for x in r.reasons))
        self.assertEqual(r.status, INTACT)           # judged on EPS alone, and says so

    def test_config_is_respected(self):
        cfg = ThesisConfig(shrink_periods=1)
        r = assess_thesis(income([100.0, 120.0, 118.0], [1.0, 1.2, 1.1]), cfg)
        self.assertEqual(r.status, BROKEN)

    def test_fund_is_not_applicable(self):
        self.assertEqual(fund_result().status, NOT_APPLICABLE)


class AppleCaseTests(unittest.TestCase):
    """The owner sold AAPL in 2024 on slow growth. What would this rule have said?

    Revenue (USD bn) and diluted EPS are third-party figures, not Apple's filings;
    FY2024 and FY2025 revenue were not verified so they are left out.
    """

    EPS = (5.61, 6.11, 6.13, 6.08, 7.46)                  # FY2021 to FY2025

    def frame(self, upto: int) -> pd.DataFrame:
        rev = [365.8, 394.3, 383.3, np.nan, np.nan][:upto]
        idx = pd.DatetimeIndex(
            pd.date_range("2021-09-30", periods=upto, freq="YE-SEP"), name="period_end"
        )
        return pd.DataFrame({"revenue": rev, "diluted_eps": list(self.EPS[:upto])}, index=idx)

    def test_at_the_time_of_the_sale_it_was_watch_not_a_sell(self):
        r = assess_thesis(self.frame(3))                       # data through FY2023
        self.assertEqual(r.status, WATCH)
        self.assertAlmostEqual(r.revenue_growth[-1], 383.3 / 394.3 - 1.0)   # -2.8%
        self.assertNotEqual(r.status, BROKEN)

    def test_never_broken_at_any_point(self):
        for upto in (3, 4, 5):
            self.assertNotEqual(assess_thesis(self.frame(upto)).status, BROKEN, upto)

    def test_recovers_to_intact_once_earnings_grow_again(self):
        r = assess_thesis(self.frame(5))                       # EPS +22.7% in FY2025
        self.assertEqual(r.status, INTACT)
        self.assertAlmostEqual(r.eps_growth[-1], 7.46 / 6.08 - 1.0)


if __name__ == "__main__":
    unittest.main()
